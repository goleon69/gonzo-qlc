#!/usr/bin/env python3
# Gonzo - voice control for QLC+
# Copyright (C) 2026 Leonardo Gon
# This program is free software: you can redistribute it and/or modify it under the terms
# of the GNU General Public License as published by the Free Software Foundation, either
# version 3 of the License, or (at your option) any later version. See LICENSE.
"""
Gonzo - offline voice control for QLC+ via OSC (macOS).

Say "gonzo <command>" (e.g. "gonzo rosso", "gonzo scena tre").
The phrase is matched against config.yaml and the mapped OSC message is
sent to QLC+, where a Virtual Console button is bound to that OSC path.

A control page opens in the browser (http://127.0.0.1:8765): version, status,
mic level, list of received commands, buttons to fire commands by click.

Typed commands also work in the terminal (without the wake word): "rosso" + Enter.
Type "q" + Enter to quit.
"""
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from array import array
from collections import deque  # noqa
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml
from pythonosc.udp_client import SimpleUDPClient

VERSION = "1.7.0"
FROZEN = getattr(sys, "frozen", False)          # True inside the packaged app
# read-only resources (ui.html, model, default config)
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
# user-editable files (config.yaml, log): next to gonzo.py, or Application Support in the app
def _user_dir():
    if not FROZEN:
        return BASE                                 # running from source: everything here
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Gonzo"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "Gonzo"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gonzo"


USER_DIR = _user_dir()


def load_settings():
    """Choices made in the window (microphone, compact view) - settings.json."""
    try:
        with open(USER_DIR / "settings.json", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(d):
    try:
        USER_DIR.mkdir(parents=True, exist_ok=True)
        with open(USER_DIR / "settings.json", "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log(f"settings not saved: {e}", "warn")


def default_config_path():
    cfg = USER_DIR / "config.yaml"
    if FROZEN and not cfg.exists():
        USER_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy(BASE / "config.yaml", cfg)      # first run: start from the example
    return cfg

# ---------------------------------------------------------------- output
C = {"ok": "\033[92m", "warn": "\033[93m", "err": "\033[91m",
     "dim": "\033[90m", "info": "\033[96m", "end": "\033[0m"}


def log(msg, kind="info"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{C['dim']}{ts}{C['end']} {C.get(kind, '')}{msg}{C['end']}", flush=True)


def normalize(text):
    return " ".join(str(text).lower().strip().split())


# ---------------------------------------------------------------- event list (for the UI)
class EventBus:
    def __init__(self, maxlen=300):
        self.lock = threading.Lock()
        self.items = deque(maxlen=maxlen)
        self.next_id = 1

    def emit(self, type_, **data):
        with self.lock:
            ev = {"id": self.next_id, "t": datetime.now().strftime("%H:%M:%S"),
                  "type": type_, **data}
            self.next_id += 1
            self.items.append(ev)

    def since(self, last_id):
        with self.lock:
            return [e for e in self.items if e["id"] > last_id]


# ---------------------------------------------------------------- commands
class Commands:
    def __init__(self, cfg):
        self.wake = normalize(cfg["wake_word"])
        self.by_phrase = {}  # "gonzo rosso" -> command dict
        self.ordered_cfg = []  # (phrase without wake word, cmd) in config order
        for cmd in cfg["commands"]:
            if "osc" not in cmd and "action" not in cmd:
                raise ValueError(f"Command needs 'osc' or 'action': {cmd}")
            says = cmd["say"] if isinstance(cmd["say"], list) else [cmd["say"]]
            for i, s in enumerate(says):
                phrase = f"{self.wake} {normalize(s)}"
                if phrase in self.by_phrase:
                    raise ValueError(f"Duplicate phrase in config: '{phrase}'")
                self.by_phrase[phrase] = cmd
                if i == 0:
                    self.ordered_cfg.append((normalize(s), cmd))
        # longest first, so "gonzo scena dieci" wins over shorter overlaps
        self._ordered = sorted(self.by_phrase, key=lambda p: -len(p.split()))

    def grammar(self):
        return json.dumps(sorted(self.by_phrase) + ["[unk]"], ensure_ascii=False)

    def vocabulary(self):
        return sorted({w for p in self.by_phrase for w in p.split()})

    def is_prefix_of_other(self, phrase):
        return any(p != phrase and p.startswith(phrase + " ") for p in self.by_phrase)

    def find_in_tokens(self, tokens):
        """Return (phrase, start, end) of the last known phrase inside tokens."""
        best = None
        for phrase in self._ordered:
            pt = phrase.split()
            n = len(pt)
            for i in range(len(tokens) - n + 1):
                if tokens[i:i + n] == pt and (best is None or i > best[1]):
                    best = (phrase, i, i + n)
        return best

    def match_typed(self, text):
        t = normalize(text)
        for cand in (t, f"{self.wake} {t}"):
            if cand in self.by_phrase:
                return cand, self.by_phrase[cand]
        return None


# ---------------------------------------------------------------- OSC
class OscOut:
    def __init__(self, host, port, pulse_ms, dry_run):
        self.client = None if dry_run else SimpleUDPClient(host, port)
        self.pulse = pulse_ms / 1000.0
        self.target = "DRY-RUN (nothing sent)" if dry_run else f"{host}:{port}"

    def _send(self, path, value):
        if self.client:
            self.client.send_message(path, float(value))

    def fire(self, cmd):
        path = cmd["osc"]
        if "value" in cmd:                      # fixed value (e.g. a slider)
            self._send(path, cmd["value"])
        else:                                   # button: press, then release
            self._send(path, 1.0)
            threading.Timer(self.pulse, self._send, args=(path, 0.0)).start()


# ---------------------------------------------------------------- controller
class Gonzo:
    def __init__(self, cfg, osc, bus):
        self.cmds = Commands(cfg)
        r = cfg.get("recognition", {}) or {}
        self.min_conf = float(r.get("min_confidence", 0.6))
        self.cooldown = float(r.get("cooldown_s", 1.0))
        self.fast = bool(r.get("fast_trigger", False))
        self.osc = osc
        self.bus = bus
        self.paused = False
        self.running = True
        self.status = "starting"   # starting | listening | keyboard | error
        self.mic_name = ""
        self.level = 0.0
        self.config_path = ""
        self.settings = load_settings()
        self.compact = bool(self.settings.get("compact", False))
        self.mic_choice = None          # None = system default, or device name / number
        self.devices = []               # input device names (for the UI)
        self.mic_restart = threading.Event()
        self.ui_hook = None             # set by gonzo_app.py (window resize)
        self.menu_ok = False            # True when "Modifica comandi…" is in the Mac menu bar
        # noise gate: audio below this peak level (0..1) is replaced by silence. 0 = off
        self.gate = float(self.settings.get("gate", r.get("gate", 0.0)) or 0.0)
        self.gate_open = True
        self.last = {}
        self.lock = threading.Lock()

    # -- state for the UI
    def state(self):
        return {
            "version": VERSION, "wake": self.cmds.wake, "paused": self.paused,
            "status": self.status, "mic": self.mic_name, "level": round(self.level, 3),
            "osc_target": self.osc.target, "min_conf": self.min_conf,
            "config": self.config_path,
            "devices": self.devices, "compact": self.compact,
            "gate": round(self.gate, 4), "gate_open": self.gate_open, "menu_ok": self.menu_ok,
            "mic_choice": self.mic_choice if isinstance(self.mic_choice, str) else None,
            "commands": [{"say": s, "osc": c.get("osc"), "action": c.get("action")}
                         for s, c in self.cmds.ordered_cfg],
        }

    # -- choices made in the window
    def set_mic(self, name):
        self.mic_choice = name or None
        self.settings["mic"] = self.mic_choice
        save_settings(self.settings)
        self.mic_restart.set()

    def open_config(self):
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-t", self.config_path])          # TextEdit
        elif sys.platform == "win32":
            subprocess.Popen(["notepad.exe", self.config_path])        # Notepad
        else:
            subprocess.Popen(["xdg-open", self.config_path])           # default editor
        self.bus.emit("status", msg="config.yaml aperto - riavvia Gonzo dopo le modifiche")

    def set_gate(self, value):
        try:
            self.gate = min(1.0, max(0.0, float(value or 0.0)))
        except (TypeError, ValueError):
            return
        self.settings["gate"] = round(self.gate, 4)
        save_settings(self.settings)
        log(f"gate threshold: {self.gate:.3f}" if self.gate else "gate off", "info")

    def set_compact(self, on):
        self.compact = bool(on)
        self.settings["compact"] = self.compact
        save_settings(self.settings)
        if self.ui_hook:
            self.ui_hook("compact", self.compact)

    def set_paused(self, value, source):
        with self.lock:
            self.paused = value
        if value:
            log(f"PAUSED - voice commands ignored  [{source}]", "warn")
            self.bus.emit("status", msg=f"PAUSA ({source})")
        else:
            log(f"LISTENING  [{source}]", "ok")
            self.bus.emit("status", msg=f"ASCOLTO ({source})")

    # -- execute a matched command
    def execute(self, phrase, cmd, source):
        action = cmd.get("action")
        if action == "pause":
            return self.set_paused(True, source)
        if action == "resume":
            return self.set_paused(False, source)
        with self.lock:
            if self.paused and source.startswith("voice"):
                log(f"[paused] ignored: {phrase}", "dim")
                self.bus.emit("ignored", phrase=phrase, source=source, msg="in pausa")
                return
            now = time.monotonic()
            if now - self.last.get(phrase, 0.0) < self.cooldown:
                log(f"[cooldown] ignored: {phrase}", "dim")
                self.bus.emit("ignored", phrase=phrase, source=source, msg="ripetuto troppo presto")
                return
            self.last[phrase] = now
        self.osc.fire(cmd)
        log(f">> {phrase}  ->  {cmd['osc']}   [{source}]", "ok")
        self.bus.emit("fire", phrase=phrase, osc=cmd["osc"], source=source)

    # -- final recognition result from Vosk
    def on_result(self, res):
        words = [w for w in res.get("result", []) if w.get("word") != "[unk]"]
        tokens = [w["word"] for w in words]
        if not tokens:
            return
        found = self.cmds.find_in_tokens(tokens)
        heard = " ".join(tokens)
        if not found:
            log(f"heard '{heard}' - no command", "dim")
            self.bus.emit("heard", msg=heard)
            return
        phrase, a, b = found
        conf = min(w.get("conf", 0.0) for w in words[a:b])
        if conf < self.min_conf:
            log(f"low confidence {conf:.2f} for '{phrase}' - ignored", "warn")
            self.bus.emit("lowconf", phrase=phrase, conf=round(conf, 2))
            return
        self.execute(phrase, self.cmds.by_phrase[phrase], f"voice {conf:.2f}")

    # -- partial result (only used with fast_trigger)
    def on_partial(self, partial, rec):
        tokens = [t for t in normalize(partial).split() if t != "[unk]"]
        found = self.cmds.find_in_tokens(tokens)
        if found and found[2] == len(tokens) and not self.cmds.is_prefix_of_other(found[0]):
            self.execute(found[0], self.cmds.by_phrase[found[0]], "voice fast")
            rec.Reset()

    # -- keyboard input (testing / fallback)
    def keyboard_loop(self):
        for line in sys.stdin:
            t = normalize(line)
            if t in ("q", "quit", "esci"):
                break
            if not t:
                continue
            hit = self.cmds.match_typed(t)
            if hit:
                self.execute(hit[0], hit[1], "keyboard")
            else:
                log(f"unknown command: '{t}'", "warn")
        self.running = False

    # -- microphone loop
    def run_mic(self, model_path, device, samplerate, ignore_missing):
        try:
            import sounddevice as sd
        except Exception as e:          # e.g. Linux without the PortAudio library
            hint = (" - installa la libreria audio: sudo apt install libportaudio2"
                    if sys.platform.startswith("linux") else "")
            self.fail(f"Audio non disponibile ({e}){hint}")
        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except Exception as e:
            self.fail(f"Riconoscimento vocale (Vosk) non disponibile: {e}")

        SetLogLevel(-1)
        if not Path(model_path).is_dir():
            self.fail(f"Vosk model not found at {model_path} - run ./setup.sh")
        try:
            model = Model(str(model_path))
        except Exception as e:
            self.fail(f"Modello vocale non valido in {model_path}: {e}")

        if hasattr(model, "vosk_model_find_word"):
            missing = [w for w in self.cmds.vocabulary() if model.vosk_model_find_word(w) == -1]
        else:
            missing = []
            log("vocabulary check not available in this vosk version - skipped", "dim")
        if missing:
            msg = ("Words NOT in the model vocabulary (would never be recognized): "
                   + ", ".join(missing))
            log(msg, "err")
            self.bus.emit("error", msg=msg)
            if not ignore_missing:
                self.fail("Change them in config.yaml (or run with --ignore-missing).")

        self.mic_choice = device
        first = True
        while self.running:
            self.mic_restart.clear()
            if not first:                       # re-scan devices (headset plugged in later...)
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception as e:
                    log(f"audio re-scan failed: {e}", "warn")
            first = False
            stream = None
            try:
                inputs = [(i, d["name"]) for i, d in enumerate(sd.query_devices())
                          if d.get("max_input_channels", 0) > 0]
                self.devices = list(dict.fromkeys(n for _, n in inputs))
                dev = self._resolve_device(inputs)
                info = sd.query_devices(dev, "input")
                sr = int(samplerate or info["default_samplerate"])
                rec = KaldiRecognizer(model, sr, self.cmds.grammar())
                rec.SetWords(True)
                q = queue.Queue()

                def callback(indata, frames, t, status, q=q):
                    if status:
                        log(f"audio: {status}", "dim")
                    data = bytes(indata)
                    a = array("h", data)
                    peak = max(max(a), -min(a)) / 32768.0 if a else 0.0
                    self.level = peak
                    q.put((data, peak))

                stream = sd.RawInputStream(samplerate=sr, blocksize=int(sr * 0.1), device=dev,
                                           dtype="int16", channels=1, callback=callback)
                stream.start()
            except Exception as e:
                self.status, self.mic_name, self.level = "error", "", 0.0
                msg = f"Microfono non disponibile ({e}). Scegline un altro o premi ↻."
                log(msg, "err")
                self.bus.emit("error", msg=msg)
                while self.running and not self.mic_restart.wait(0.5):
                    pass
                continue

            try:
                self.mic_name = info["name"]
                self.status = "listening"
                log(f"Mic: {self.mic_name} @ {sr} Hz", "info")
                log(f"LISTENING - say '{self.cmds.wake} <command>'", "ok")
                self.bus.emit("status", msg=f"In ascolto - microfono: {self.mic_name}")
                silent = 0.0
                pre = deque(maxlen=3)           # gate pre-roll: last 0.3 s before it opens
                hold_until = 0.0                # gate stays open 0.7 s after the last loud block
                while self.running and not self.mic_restart.is_set():
                    try:
                        data, peak = q.get(timeout=0.5)
                    except queue.Empty:
                        silent += 0.5
                        if silent >= 3.0:       # device unplugged / stream died
                            msg = f"Nessun audio da '{self.mic_name}' - riprovo"
                            log(msg, "warn")
                            self.bus.emit("error", msg=msg)
                            time.sleep(1.0)
                            break
                        continue
                    silent = 0.0
                    now = time.monotonic()
                    if self.gate <= 0:
                        feed, self.gate_open = [data], True
                    elif peak >= self.gate:
                        feed = ([] if now <= hold_until else list(pre)) + [data]
                        pre.clear()
                        hold_until = now + 0.7
                        self.gate_open = True
                    elif now <= hold_until:
                        feed = [data]
                    else:                       # closed: the recognizer gets silence
                        pre.append(data)
                        feed, self.gate_open = [bytes(len(data))], False
                    for chunk in feed:
                        if rec.AcceptWaveform(chunk):
                            self.on_result(json.loads(rec.Result()))
                        elif self.fast:
                            self.on_partial(
                                json.loads(rec.PartialResult()).get("partial", ""), rec)
            finally:
                self.level = 0.0
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass

    def _resolve_device(self, inputs):
        """mic_choice (None / number / name) -> device number, or None for the system default."""
        c = self.mic_choice
        if c is None or isinstance(c, int):
            return c
        for i, n in inputs:
            if n == c:
                return i
        for i, n in inputs:
            if str(c).lower() in n.lower():
                return i
        msg = f"Microfono '{c}' non trovato - uso quello predefinito del Mac"
        log(msg, "warn")
        self.bus.emit("error", msg=msg)
        return None

    def fail(self, msg):
        self.status = "error"
        log(msg, "err")
        self.bus.emit("error", msg=msg)
        raise SystemExit(1)


# ---------------------------------------------------------------- web UI
def start_ui(app, port, open_browser):
    page = BASE / "ui.html"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype="application/json", code=200):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._send(json.dumps(obj, ensure_ascii=False), code=code)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                try:
                    self._send(page.read_bytes(), "text/html; charset=utf-8")
                except OSError:
                    self._send("ui.html not found", "text/plain", 404)
            elif u.path in ("/icon.png", "/favicon.ico"):
                try:
                    self._send((BASE / "icon.png").read_bytes(), "image/png")
                except OSError:
                    self._send("no icon", "text/plain", 404)
            elif u.path == "/api/state":
                self._json(app.state())
            elif u.path == "/api/events":
                since = int(parse_qs(u.query).get("since", ["0"])[0] or 0)
                self._json(app.bus.since(since))
            else:
                self._send("not found", "text/plain", 404)

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                body = {}
            if u.path == "/api/fire":
                hit = app.cmds.match_typed(body.get("say", ""))
                if not hit:
                    return self._json({"ok": False, "error": "unknown command"}, 400)
                app.execute(hit[0], hit[1], "click")
            elif u.path == "/api/pause":
                app.set_paused(True, "click")
            elif u.path == "/api/resume":
                app.set_paused(False, "click")
            elif u.path == "/api/mic":
                app.set_mic(body.get("device"))
            elif u.path == "/api/rescan":
                app.mic_restart.set()
            elif u.path == "/api/gate":
                app.set_gate(body.get("value"))
            elif u.path == "/api/compact":
                app.set_compact(body.get("on"))
            elif u.path == "/api/open_config":
                app.open_config()
            elif u.path == "/api/quit":
                log("quit requested from the control page", "warn")
                app.running = False
            else:
                return self._json({"ok": False}, 404)
            self._json({"ok": True})

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        log(f"Control page not started (port {port}: {e}). Is Gonzo already running?", "err")
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    log(f"Control page: {url}", "info")
    if open_browser:
        webbrowser.open(url)
    return url


# ---------------------------------------------------------------- main
def main(argv=None, embedded=False, holder=None):
    """embedded=True: called by gonzo_app.py (native window) - no browser, no keyboard.
    holder (dict) receives 'app' and 'url' (None if the page could not start)."""
    holder = holder if holder is not None else {}
    ap = argparse.ArgumentParser(description=f"Gonzo {VERSION} - voice control for QLC+ via OSC")
    ap.add_argument("--config", default=None)
    ap.add_argument("--model", default=str(BASE / "model"), help="Vosk model folder")
    ap.add_argument("--device", default=None, help="input device number or name")
    ap.add_argument("--list-devices", action="store_true", help="list audio devices and exit")
    ap.add_argument("--dry-run", action="store_true", help="do not send OSC, only print")
    ap.add_argument("--no-mic", action="store_true", help="keyboard/click only (testing)")
    ap.add_argument("--no-ui", action="store_true", help="do not start the control page")
    ap.add_argument("--no-browser", action="store_true", help="start the page but do not open it")
    ap.add_argument("--print-grammar", action="store_true", help="print phrase list and exit")
    ap.add_argument("--ignore-missing", action="store_true",
                    help="start even if some words are not in the model vocabulary")
    ap.add_argument("--version", action="version", version=f"Gonzo {VERSION}")
    args = ap.parse_args(argv)

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return

    config_path = Path(args.config) if args.config else default_config_path()
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    o = cfg.get("osc", {}) or {}
    osc = OscOut(o.get("host", "127.0.0.1"), int(o.get("port", 7700)),
                 int(o.get("pulse_ms", 80)), args.dry_run)
    bus = EventBus()
    app = Gonzo(cfg, osc, bus)
    app.config_path = str(config_path)
    holder["app"] = app

    if args.print_grammar:
        for p in sorted(app.cmds.by_phrase):
            c = app.cmds.by_phrase[p]
            print(f"{p:30s} -> {c.get('osc', 'action:' + str(c.get('action')))}")
        return

    log(f"Gonzo {VERSION} - {len(app.cmds.by_phrase)} phrases loaded, "
        f"OSC target: {osc.target}", "info")
    bus.emit("status", msg=f"Gonzo {VERSION} avviato - OSC verso {osc.target}")

    ui = cfg.get("ui", {}) or {}
    holder["url"] = None
    if not args.no_ui:
        holder["url"] = start_ui(app, int(ui.get("port", 8765)),
                                 bool(ui.get("open_browser", True))
                                 and not args.no_browser and not embedded)
    holder["ready"] = True

    if not embedded and sys.stdin is not None and sys.stdin.isatty():
        threading.Thread(target=app.keyboard_loop, daemon=True).start()
    try:
        if args.no_mic:
            app.status = "keyboard"
            log("Keyboard mode - type a command (e.g. 'rosso'), q to quit", "ok")
            bus.emit("status", msg="Solo tastiera / click (microfono spento)")
            while app.running:
                time.sleep(0.2)
        else:
            a = cfg.get("audio", {}) or {}
            # priority: --device, then the choice made in the window, then config.yaml
            device = (args.device if args.device is not None
                      else app.settings.get("mic", a.get("device")))
            if isinstance(device, str) and device.isdigit():
                device = int(device)
            app.run_mic(args.model, device, a.get("samplerate"), args.ignore_missing)
    except KeyboardInterrupt:
        pass
    except SystemExit:
        if not args.no_ui:
            log("Error shown on the control page. Press Ctrl+C to quit.", "err")
            try:
                while app.running:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
    time.sleep(0.2)  # let pending button releases go out
    log("bye", "dim")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# Gonzo - voice control for QLC+  |  Copyright (C) 2026 Leonardo Gon  |  GPL-3.0-or-later
"""Gonzo as a Mac app: same engine as gonzo.py, shown in its own native window
(no Terminal, no browser). Built into Gonzo.app by build_app.sh."""
import os
import sys
import threading
import time
import traceback

import gonzo

# inside the .app there is no Terminal: write output to a log file
if gonzo.FROZEN or sys.stdout is None or not sys.stdout.isatty():
    gonzo.USER_DIR.mkdir(parents=True, exist_ok=True)
    try:    # keep the previous session's log (e.g. last rehearsal)
        (gonzo.USER_DIR / "gonzo.log").replace(gonzo.USER_DIR / "gonzo.prev.log")
    except OSError:
        pass
    _log = open(gonzo.USER_DIR / "gonzo.log", "w", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = _log

try:
    import webview  # noqa: E402  (pywebview: native window on macOS / Windows)
except ImportError:
    webview = None  # e.g. Linux build: the control page opens in the browser

ERROR_PAGE = """<html><body style="background:#0e0f12;color:#e8e9ec;font:16px -apple-system;
padding:40px"><h2 style="color:#e74c3c">Gonzo non si è avviato</h2><pre style="white-space:pre-wrap">{}</pre>
<p>Log: {}</p></body></html>"""


def set_dock_identity():
    """Show the Gonzo icon and name in the Dock instead of Python's."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle
        info = NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = "Gonzo"
        icon = gonzo.BASE / "icon.png"
        if icon.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(icon))
            NSApplication.sharedApplication().setApplicationIconImage_(img)
    except Exception as e:      # cosmetic only - never block the app
        print(f"dock icon not set: {e}", flush=True)


_menu_target = None     # keeps the menu handler alive


def add_edit_menu_item(app):
    """Adds "Modifica comandi…" (Cmd+,) to the Edit menu of the Mac menu bar.
    If it fails, app.menu_ok stays False and the page keeps showing its own button."""
    try:
        from AppKit import NSApplication, NSMenu, NSMenuItem
        from Foundation import NSObject
        from PyObjCTools import AppHelper

        class GonzoMenuTarget(NSObject):
            def openConfig_(self, sender):
                try:
                    app.open_config()
                except Exception as e:
                    print(f"open config failed: {e}", flush=True)

        def install():
            global _menu_target
            try:
                main_menu = NSApplication.sharedApplication().mainMenu()
                if main_menu is None:
                    raise RuntimeError("no menu bar")
                edit = None
                for i in range(main_menu.numberOfItems()):
                    sub = main_menu.itemAtIndex_(i).submenu()
                    if sub is not None and str(sub.title()).lower() in ("edit", "modifica"):
                        edit = sub
                        break
                if edit is None:                # no Edit menu: create one
                    holder_item = NSMenuItem.alloc().init()
                    edit = NSMenu.alloc().initWithTitle_("Edit")
                    holder_item.setSubmenu_(edit)
                    main_menu.addItem_(holder_item)
                else:
                    edit.addItem_(NSMenuItem.separatorItem())
                _menu_target = GonzoMenuTarget.alloc().init()
                item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "Modifica comandi…", "openConfig:", ",")
                item.setTarget_(_menu_target)
                edit.addItem_(item)
                app.menu_ok = True
            except Exception as e:
                print(f"menu item not added: {e}", flush=True)

        AppHelper.callAfter(install)
    except Exception as e:
        print(f"menu item not added: {e}", flush=True)


def windows_unblock():
    """Files extracted from a downloaded zip carry Windows' "downloaded from the internet"
    mark (Zone.Identifier). .NET then refuses to load pywebview's DLLs ("Failed to resolve
    Python.Runtime.Loader.Initialize"). Remove the mark from Gonzo's own files."""
    if sys.platform != "win32" or not gonzo.FROZEN:
        return
    root = os.path.dirname(sys.executable)
    removed = 0
    for folder, _dirs, files in os.walk(root):
        for name in files:
            try:
                os.remove(os.path.join(folder, name) + ":Zone.Identifier")
                removed += 1
            except OSError:
                pass
    if removed:
        print(f"unblocked {removed} downloaded files", flush=True)


def run_in_browser(holder, url):
    """Fallback when the native window cannot start: same page in the default browser."""
    import webbrowser
    print("native window failed - using the browser:\n" + traceback.format_exc(), flush=True)
    webbrowser.open(url)
    try:
        while holder.get("app") is not None and holder["app"].running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


def main():
    windows_unblock()
    if webview is None:
        # no native window available: same app, control page in the default browser.
        # "Esci" in the page quits.
        gonzo.main([])
        return

    holder = {}

    def core():
        try:
            gonzo.main(["--no-browser"], embedded=True, holder=holder)
        except BaseException:   # noqa - report anything in the window
            holder["error"] = traceback.format_exc()
        finally:
            holder["done"] = True

    threading.Thread(target=core, daemon=True).start()

    t0 = time.time()
    while not (holder.get("ready") or holder.get("done")) and time.time() - t0 < 20:
        time.sleep(0.05)

    url = holder.get("url")
    size = {"full_w": 1200}
    if url:
        compact = bool(getattr(holder.get("app"), "compact", False))
        window = webview.create_window(f"Gonzo {gonzo.VERSION}", url,
                                       width=size["full_w"] // 2 if compact else size["full_w"],
                                       height=820, min_size=(440, 400),
                                       background_color="#0e0f12")

        def hook(kind, value):
            # "Compatta": halve the window width (left edge stays), "Espandi": restore it
            if kind != "compact":
                return
            try:
                w, h = window.width, window.height
                if value:
                    if w > 700:
                        size["full_w"] = w
                    window.resize(max(440, size["full_w"] // 2), h)
                else:
                    window.resize(size["full_w"], h)
            except Exception as e:
                print(f"window resize failed: {e}", flush=True)

        holder["app"].ui_hook = hook
    else:
        msg = holder.get("error") or ("La pagina di controllo non è partita: forse Gonzo "
                                      "è già aperto (porta 8765 occupata).")
        window = webview.create_window("Gonzo - errore",
                                       html=ERROR_PAGE.format(msg, gonzo.USER_DIR / "gonzo.log"),
                                       width=800, height=500)

    def watcher():
        if url and holder.get("app") is not None:
            try:
                window.events.shown.wait(10)
            except Exception:
                time.sleep(1.0)
            if sys.platform == "darwin":
                add_edit_menu_item(holder["app"])
        # close the window when "Esci" is pressed in the page
        while True:
            app = holder.get("app")
            if holder.get("done") and url is None:
                return
            if app is not None and not app.running:
                time.sleep(0.3)
                window.destroy()
                return
            time.sleep(0.3)

    if sys.platform == "darwin":
        set_dock_identity()
    try:
        webview.start(watcher)      # blocks until the window is closed
    except Exception:
        if not url:
            raise
        run_in_browser(holder, url)
    app = holder.get("app")
    if app is not None:
        app.running = False         # stops the mic loop
    time.sleep(0.3)


if __name__ == "__main__":
    main()

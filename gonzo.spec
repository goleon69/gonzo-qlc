# PyInstaller recipe for the Gonzo app (macOS .app, Windows .exe folder, Linux folder).
# Used by .github/workflows/build.yml - needs the speech model in ./model
import re
import sys

from PyInstaller.utils.hooks import collect_all

VERSION = re.search(r'VERSION = "([^"]+)"', open("gonzo.py", encoding="utf-8").read()).group(1)
MAC, WIN = sys.platform == "darwin", sys.platform == "win32"

datas = [("ui.html", "."), ("config.yaml", "."), ("icon.png", "."), ("model", "model"),
         ("LICENSE", "."), ("THIRD_PARTY_NOTICES.md", ".")]
binaries = []
hiddenimports = ["gonzo", "cffi", "_cffi_backend"]
packages = ["vosk", "sounddevice", "_sounddevice_data"]
if MAC or WIN:
    packages.append("webview")          # native window; Linux uses the browser
for pkg in packages:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(["gonzo_app.py"], pathex=["."], datas=datas, binaries=binaries,
             hiddenimports=hiddenimports, excludes=[] if (MAC or WIN) else ["webview"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Gonzo",
          console=not (MAC or WIN),     # Linux: start from a terminal, output stays visible
          icon="icon.icns" if MAC else ("icon.ico" if WIN else None))
coll = COLLECT(exe, a.binaries, a.datas, name="Gonzo")

if MAC:
    app = BUNDLE(
        coll,
        name="Gonzo.app",
        icon="icon.icns",
        bundle_identifier="io.github.gonzo-qlc",
        info_plist={
            "CFBundleName": "Gonzo",
            "CFBundleDisplayName": "Gonzo",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSMicrophoneUsageDescription":
                "Gonzo uses the microphone for voice commands to the lights.",
            "NSHighResolutionCapable": True,
        },
    )

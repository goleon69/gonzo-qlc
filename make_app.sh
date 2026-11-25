#!/bin/bash
# Creates Gonzo.app WITHOUT compiling (no Xcode / Command Line Tools needed).
# The app is a small launcher that runs gonzo_app.py with this folder's .venv,
# so this folder (with .venv and model) must stay where it is (the .app itself can be moved).
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"

source .venv/bin/activate
echo "Checking pywebview..."
pip install -q "pywebview>=5.0"

APP="$DIR/Gonzo.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/MacOS/Gonzo" <<EOF
#!/bin/bash
cd "$DIR"
exec "$DIR/.venv/bin/python" "$DIR/gonzo_app.py"
EOF
chmod +x "$APP/Contents/MacOS/Gonzo"
[ -f icon.icns ] && cp icon.icns "$APP/Contents/Resources/icon.icns"

cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Gonzo</string>
  <key>CFBundleDisplayName</key><string>Gonzo</string>
  <key>CFBundleIdentifier</key><string>it.leonardo.gonzo</string>
  <key>CFBundleExecutable</key><string>Gonzo</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundleShortVersionString</key><string>1.3.0</string>
  <key>CFBundleVersion</key><string>1.3.0</string>
  <key>LSMinimumSystemVersion</key><string>10.13</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Gonzo usa il microfono per i comandi vocali alle luci.</string>
</dict>
</plist>
EOF

# tidy up leftovers of the PyInstaller attempt
rm -rf build dist

# if an older copy is in Applications, replace it
for A in "/Applications/Gonzo.app" "$HOME/Applications/Gonzo.app"; do
  if [ -d "$A" ]; then
    rm -rf "$A" && cp -R "$APP" "$A" && touch "$A" && echo "Updated: $A"
  fi
done
touch "$APP"   # refresh Finder icon

echo ""
echo "Done: $APP"
echo "You can move it to Applications / the Dock. Keep THIS folder ($DIR) where it is."
open -R "$APP"

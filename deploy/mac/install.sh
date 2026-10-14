#!/bin/sh
# Installs (or refreshes) Skrift as an on-demand background app on this Mac.
#   sh deploy/mac/install.sh            → install / reload after code changes
#   sh deploy/mac/install.sh uninstall  → remove it (keeps your transcripts in data/)
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PORT="${SKRIFT_PORT:-5200}"
IDLE="${SKRIFT_IDLE_MINUTES:-10}"
PLIST="$HOME/Library/LaunchAgents/com.skrift.app.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/com.skrift.app" 2>/dev/null || true
if [ "$1" = "uninstall" ]; then rm -f "$PLIST"; rm -rf "$HOME/Desktop/Skrift.app"; echo "Skrift removed."; exit 0; fi

# Python environment (uv is installed with Homebrew)
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  uv venv --python 3.12 "$ROOT/.venv"
fi
VIRTUAL_ENV="$ROOT/.venv" uv pip install -q -r "$ROOT/requirements.txt"

# Download the speech models now so the first transcript doesn't wait on them (~4 GB once)
"$ROOT/.venv/bin/python" -c "from skrift.engines import warm; warm()"

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s#__ROOT__#$ROOT#g" -e "s#__PORT__#$PORT#g" -e "s#__IDLE__#$IDLE#g" -e "s#__HOME__#$HOME#g" \
    "$ROOT/deploy/mac/com.skrift.app.plist.template" > "$PLIST"
launchctl bootstrap "$DOMAIN" "$PLIST"

# Desktop shortcut that opens the link in Chrome (same as Shelf.app)
APP="$HOME/Desktop/Skrift.app"
rm -rf "$APP"
osacompile -o "$APP" -e "do shell script \"open -a \\\"Google Chrome\\\" http://localhost:$PORT\""
if [ -f "$ROOT/deploy/mac/Skrift.icns" ]; then
  # osacompile's Assets.car icon wins over applet.icns, so drop it and point at the .icns
  rm -f "$APP/Contents/Resources/Assets.car"
  /usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$APP/Contents/Info.plist" 2>/dev/null || true
  cp "$ROOT/deploy/mac/Skrift.icns" "$APP/Contents/Resources/applet.icns"
  codesign --force --sign - "$APP" 2>/dev/null || true
  touch "$APP"
fi
echo "Skrift is ready at http://localhost:$PORT (sleeps after $IDLE idle minutes)."

#!/bin/sh
# Theme Park World.app, without the disk image:
#
#   curl -fsSL https://github.com/cpressland/tpw-recomp/releases/latest/download/install.sh | sh
#
# Puts the latest release in /Applications (~/Applications if that isn't
# writable) and opens it. Downloaded with curl, the app carries no quarantine,
# so macOS opens it without the "Open Anyway" step a browser download needs.
set -e
REPOSITORY=${REPOSITORY:-cpressland/tpw-recomp}
URL=${URL:-"https://github.com/$REPOSITORY/releases/latest/download/Theme-Park-World.zip"}
APP="Theme Park World.app"

[ "$(uname -s)" = Darwin ] || { echo "Theme Park World's app is for macOS." >&2; exit 1; }
[ "$(uname -m)" = arm64 ] || { echo "Theme Park World needs a Mac with Apple silicon." >&2; exit 1; }
major=$(sw_vers -productVersion | cut -d. -f1)
[ "$major" -ge 26 ] || { echo "Theme Park World needs macOS 26 or later (this Mac has $(sw_vers -productVersion))." >&2; exit 1; }

DEST=${DEST:-/Applications}
[ -w "$DEST" ] || { DEST="$HOME/Applications"; mkdir -p "$DEST"; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
echo "Downloading Theme Park World..."
curl -fL --progress-bar -o "$tmp/app.zip" "$URL"
ditto -x -k "$tmp/app.zip" "$tmp"
if [ -d "$DEST/$APP" ]; then
  osascript -e 'quit app "Theme Park World"' 2>/dev/null || true
  rm -rf "$DEST/$APP"
fi
mv "$tmp/$APP" "$DEST/"
echo "Installed $DEST/$APP"
[ "${OPEN:-1}" = 0 ] || open "$DEST/$APP"

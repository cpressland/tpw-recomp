#!/bin/sh
# Theme Park World.app: the launcher (launcher.m), its icon and Info.plist.
# Nothing of the game is in it; the game is built on each Mac, into
# Application Support (tools/install/install.py), and the app loads it.
#
#   app/build-app.sh                 # -> build-app/Theme Park World.app
#   VERSION=1.2.0 app/build-app.sh   # the version it shows (default: git describe)
#
# Apple Silicon and macOS 26 on; signed ad hoc, not notarized (README).
set -e
cd "$(dirname "$0")/.."
OUT=${OUT:-build-app}
APP="$OUT/Theme Park World.app"
VERSION=${VERSION:-$(git describe --tags --always 2>/dev/null | sed 's/^v//')}
BUILD=${BUILD:-$(git rev-list --count HEAD 2>/dev/null || echo 1)}

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
clang -arch arm64 -mmacosx-version-min=26.0 -O2 -fobjc-arc -Wall -Wextra \
  -framework AppKit app/launcher.m -o "$APP/Contents/MacOS/Theme Park World"
sed -e "s/@VERSION@/$VERSION/" -e "s/@BUILD@/$BUILD/" app/Info.plist > "$APP/Contents/Info.plist"
swift app/icon.swift "$OUT/AppIcon.iconset" >/dev/null
iconutil -c icns "$OUT/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$OUT/AppIcon.iconset"
codesign --force --sign - "$APP"
echo "$APP ($VERSION)"

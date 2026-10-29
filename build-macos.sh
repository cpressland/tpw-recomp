#!/bin/sh
# Configure and build the native macOS host: the lifted C on pcrecomp's
# runtime/win32hle, a 64-bit program with no Wine.
#
#   ./build-macos.sh            # -> build-macos/tpw
#   build-macos/tpw --run       # from here, with the game in ./game
#
# Needs a lift (run_lift.py), Xcode's command line tools, and Homebrew's
# cmake, ninja, pkgconf, sdl2 and sdl2_ttf.
set -e
cd "$(dirname "$0")"
BUILD_DIR=${BUILD_DIR:-build-macos}
PCRECOMP=${PCRECOMP:-$(cd .. && pwd)/pcrecomp}
if [ ! -f "$BUILD_DIR/build.ninja" ]; then
  # shellcheck disable=SC2086  # CMAKE_ARGS is a list of flags
  cmake -S . -B "$BUILD_DIR" -G Ninja -DPCRECOMP="$PCRECOMP" $CMAKE_ARGS
fi
cmake --build "$BUILD_DIR" "$@"

#!/bin/bash
# Build the AppImage for this machine's architecture (x86_64 or aarch64).
#
#   app/linux/build-appimage.sh [--version 1.2.0] OUT
#     -> OUT/Theme-Park-World-VERSION-ARCH.AppImage
#
# What the Flatpak gets from the freedesktop SDK, the AppImage carries: zig
# (clang, a linker and glibc's headers: the player's compile), a standalone
# Python with the lift's packages, CMake, Ninja and pkgconf, SDL2 and
# SDL2_ttf built here, and Vulkan's headers. None of the game: the player's
# computer downloads the disc image and the patch and builds it, as on a Mac
# (AppRun).
#
# The app is this repository's HEAD, checked-in files only, and pcrecomp at
# app/pcrecomp.lock's commit ($PCRECOMP: a checkout of it to use instead of
# cloning). Every download is pinned by its SHA-256.
#
# Build it on an old glibc (Ubuntu 22.04) so it runs on newer ones. It needs
# git, curl, a C compiler, CMake, squashfs-tools and SDL's build
# dependencies, the packages $APT names (--apt installs them, as root).
set -euo pipefail
APT="git curl ca-certificates xz-utils build-essential cmake ninja-build pkg-config squashfs-tools file
  libx11-dev libxext-dev libxrandr-dev libxcursor-dev libxi-dev libxinerama-dev libxss-dev libxxf86vm-dev
  libxfixes-dev libwayland-dev libxkbcommon-dev wayland-protocols libdecor-0-dev libegl1-mesa-dev libgl1-mesa-dev
  libgles2-mesa-dev libdrm-dev libgbm-dev libasound2-dev libpulse-dev libpipewire-0.3-dev libudev-dev libdbus-1-dev fonts-dejavu-core"
version=
while [ $# -gt 1 ]; do
  case $1 in
    --version) version=$2; shift 2 ;;
    --apt) echo "==> apt-get install"
           apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $APT >/dev/null
           shift ;;
    *) break ;;
  esac
done
[ $# -eq 1 ] || { sed -n '2,20p' "$0"; exit 2; }
out=$(mkdir -p "$1" && cd "$1" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
repo=$(git -C "$here" rev-parse --show-toplevel)
commit=$(git -C "$repo" rev-parse HEAD)
[ -n "$version" ] || version=$(git -C "$repo" describe --tags --match 'v[0-9]*' --always | sed 's/^v//')
if [ -n "$(git -C "$repo" status --porcelain --untracked-files=no)" ]; then
  echo "note: uncommitted changes are not in the build (it takes $commit)" >&2
fi
arch=$(uname -m)
case $arch in
  x86_64)
    ZIG_SHA=70e49664a74374b48b51e6f3fdfbf437f6395d42509050588bd49abe52ba3d00
    PY_SHA=126343c54daff4068efc1ac269518d0cc202d57857794948103dd1454e8881be
    RT_SHA=2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d ;;
  aarch64)
    ZIG_SHA=ea4b09bfb22ec6f6c6ceac57ab63efb6b46e17ab08d21f69f3a48b38e1534f17
    PY_SHA=b48451865bde9a39e2f3e71d7fdcbf1f32615f50f24d78dc50d04ede6501cf81
    RT_SHA=00cbdfcf917cc6c0ff6d3347d59e0ca1f7f45a6df1a428a0d6d8a78664d87444 ;;
  *) echo "no AppImage for $arch" >&2; exit 1 ;;
esac
ZIG=zig-$arch-linux-0.16.0
PY=cpython-3.13.18+20261026-$arch-unknown-linux-gnu-install_only.tar.gz

cache=$out/cache
fetch() {   # url sha256 [name] -> $cache/<name>
  local f="$cache/${3:-$(basename "$1")}"
  if [ ! -f "$f" ]; then
    echo "==> $1"
    # A stalled download fails and is tried again rather than waiting forever.
    curl -fsSL --retry 5 --retry-all-errors --connect-timeout 30 --speed-limit 50000 --speed-time 60 \
      --create-dirs -o "$f.part" "$1"
    mv "$f.part" "$f"
  fi
  echo "$2  $f" | sha256sum -c --status || { echo "checksum mismatch: $f" >&2; rm -f "$f"; exit 1; }
}
fetch "https://ziglang.org/download/0.16.0/$ZIG.tar.xz" $ZIG_SHA
fetch "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/$PY" $PY_SHA
fetch "https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-$arch" $RT_SHA
fetch https://github.com/libsdl-org/SDL/releases/download/release-2.32.10/SDL2-2.32.10.tar.gz \
  5f5993c530f084535c65a6879e9b26ad441169b3e25d789d83287040a9ca5165
fetch https://github.com/libsdl-org/SDL_ttf/releases/download/release-2.24.0/SDL2_ttf-2.24.0.tar.gz \
  0b2bf1e7b6568adbdbc9bb924643f79d9dedafe061fa1ed687d1d9ac4e453bfd
fetch https://github.com/KhronosGroup/Vulkan-Headers/archive/refs/tags/vulkan-sdk-1.4.350.0.tar.gz \
  70270d10bf2c1e074a06ee37a50b75d332993d1b80a1d9526eeed2da6d82ed22 Vulkan-Headers-1.4.350.0.tar.gz

echo "==> Theme Park World $version ($commit), $arch"
app=$out/AppDir
u=$app/usr
rm -rf "$app" "$out/src"
mkdir -p "$u/bin" "$u/lib" "$out/src"

# This repository as checked in (never the game), and pcrecomp at the lock.
git -C "$repo" archive "$commit" | tar -x -C "$out/src" --one-top-level=tpw
pc_repository=$(sed -n 's/^repository=//p' "$repo/app/pcrecomp.lock")
pc_ref=$(sed -n 's/^ref=//p' "$repo/app/pcrecomp.lock")
if [ -n "${PCRECOMP:-}" ]; then
  [ "$(git -C "$PCRECOMP" rev-parse HEAD)" = "$pc_ref" ] || { echo "\$PCRECOMP is not at $pc_ref" >&2; exit 1; }
  git -C "$PCRECOMP" archive "$pc_ref" | tar -x -C "$out/src" --one-top-level=pcrecomp
else
  git init -q "$out/src/pcrecomp"
  git -C "$out/src/pcrecomp" fetch -q --depth 1 "https://github.com/$pc_repository.git" "$pc_ref"
  git -C "$out/src/pcrecomp" checkout -q FETCH_HEAD
  rm -rf "$out/src/pcrecomp/.git"
fi

echo "==> SDL2 and SDL2_ttf"
# SDL2 (everything it talks to opened at run time, from the player's system)
# and SDL2_ttf with its own FreeType and HarfBuzz, into usr/.
for t in SDL2-2.32.10 SDL2_ttf-2.24.0; do
  rm -rf "$out/$t" && tar -xzf "$cache/$t.tar.gz" -C "$out"
done
cmake -S "$out/SDL2-2.32.10" -B "$out/SDL2-2.32.10/b" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$u" -DCMAKE_INSTALL_LIBDIR=lib -DSDL_STATIC=OFF -DSDL_TEST=OFF -DSDL_RPATH=OFF >/dev/null
cmake --build "$out/SDL2-2.32.10/b" >/dev/null
cmake --install "$out/SDL2-2.32.10/b" >/dev/null
cmake -S "$out/SDL2_ttf-2.24.0" -B "$out/SDL2_ttf-2.24.0/b" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$u" -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_PREFIX_PATH="$u" \
  -DSDL2TTF_VENDORED=ON -DSDL2TTF_HARFBUZZ=ON -DSDL2TTF_SAMPLES=OFF -DBUILD_SHARED_LIBS=ON >/dev/null
cmake --build "$out/SDL2_ttf-2.24.0/b" >/dev/null
cmake --install "$out/SDL2_ttf-2.24.0/b" >/dev/null
rm -rf "$u/lib/cmake" "$u/share" "$u/bin/sdl2-config" "$u"/lib/*.a
# Vulkan's headers, for the game's Vulkan renderer (pcrecomp's d3d_vulkan.c):
# beside SDL's, where the game's compile looks; the loader is the player's.
tar -xzf "$cache/Vulkan-Headers-1.4.350.0.tar.gz" -C "$out"
cp -R "$out/Vulkan-Headers-vulkan-sdk-1.4.350.0/include/vulkan" "$out/Vulkan-Headers-vulkan-sdk-1.4.350.0/include/vk_video" "$u/include/"
install -Dm644 "$out/Vulkan-Headers-vulkan-sdk-1.4.350.0/LICENSE.md" "$u/share/licenses/Vulkan-Headers.md"
rm -rf "$out/Vulkan-Headers-vulkan-sdk-1.4.350.0"
# pkg-config finds them wherever the AppImage is mounted.
sed -i 's|^prefix=.*|prefix=${pcfiledir}/../..|' "$u"/lib/pkgconfig/*.pc

# The launcher.
gcc -O2 -Wall -DTPW_VERSION="\"$version\"" "$repo/app/linux/launcher.c" -I"$u/include" -L"$u/lib" \
  -lSDL2_ttf -lSDL2 -Wl,-rpath,'$ORIGIN/../lib' -o "$u/bin/theme-park-world"

echo "==> Python and its packages"
# Python with the lift's packages, CMake, Ninja and pkgconf, pruned.
tar -xzf "$cache/$PY" -C "$u"
"$u/python/bin/python3" -I -m pip install -q --disable-pip-version-check --no-compile --only-binary :all: \
  -r "$repo/app/requirements.txt" pkgconf==3.0.7.post0
P=$u/python/lib/python3.13
rm -rf "$P/site-packages/cmake/data/doc" "$P/site-packages/cmake/data/share/doc" \
  "$P"/site-packages/cmake/data/bin/{ccmake,cpack,ctest} \
  "$P"/{idlelib,tkinter,turtledemo,ensurepip,test,lib2to3} "$P/site-packages/pip" "$P"/site-packages/pip-*.dist-info \
  "$u"/python/lib/{tcl,tk,libtcl,libtk,itcl,thread}* "$u/python/share" \
  "$u"/python/bin/{idle,pip,pydoc,cmake,ccmake,cpack,ctest,pkgconf,pkg-config}* "$P"/site-packages/unicorn/lib/*.a
rm -f "$P"/lib-dynload/_tkinter*.so
find "$u/python" -name '*.a' -delete
find "$u/python" -name __pycache__ -type d -prune -exec rm -rf {} +
# pip's launchers name this machine's path, so small scripts in their place
# find the AppImage from where they are. Ninja's is its binary itself
# (python/bin/ninja, where install.py looks).
[ -x "$u/python/bin/ninja" ] && file "$u/python/bin/ninja" | grep -q ELF || { echo "no ninja binary" >&2; exit 1; }
pkgconf_bin=pkgconf/.bin/pkgconf
[ -x "$P/site-packages/$pkgconf_bin" ] || { echo "no pkgconf binary in the wheel" >&2; exit 1; }
tool() {   # name command...
  printf '#!/bin/sh\nt=$(dirname "$(readlink -f "$0")")/..\nexec %s "$@"\n' "$2" > "$u/bin/$1"
  chmod 755 "$u/bin/$1"
}
tool pkg-config "\"\$t/python/lib/python3.13/site-packages/$pkgconf_bin\""
tool ninja '"$t/python/bin/ninja"'
# CMake names itself in the build it makes by its real path, which in the
# AppImage is this run's mount. So it sits apart, in usr/cmake, and AppRun
# copies it to the game folder once (TPW_CMAKE), named by its version.
mv "$P/site-packages/cmake/data" "$u/cmake"
"$u/cmake/bin/cmake" --version | sed -n '1s/^cmake version //p' > "$u/cmake/VERSION"
[ -s "$u/cmake/VERSION" ] || { echo "no CMake version" >&2; exit 1; }
tool cmake '"$t/cmake/bin/cmake"'

# zig: the player's C compiler. UBSan off: zig turns it on in unoptimised C.
tar -xJf "$cache/$ZIG.tar.xz" -C "$u"
mv "$u/$ZIG" "$u/zig"
rm -rf "$u/zig/doc"
tool cc '"$t/zig/zig" cc -fno-sanitize=undefined'
tool c++ '"$t/zig/zig" c++ -fno-sanitize=undefined'
tool ar '"$t/zig/zig" ar'
tool ranlib '"$t/zig/zig" ranlib'

# The launcher's font (a system may have none: TPW_FONTS).
install -d "$u/share/fonts"
install -m644 /usr/share/fonts/truetype/dejavu/DejaVuSans{,-Bold}.ttf "$u/share/fonts/"
install -m644 /usr/share/doc/fonts-dejavu-core/copyright "$u/share/fonts/DejaVu-LICENSE"

# The sources the player's build compiles.
"$u/python/bin/python3" -I "$out/src/tpw/tools/install/install.py" \
  --export-sources "$u/share/theme-park-world/src" --pcrecomp "$out/src/pcrecomp"

# The AppImage's own files: AppRun, the desktop entry and the icon.
install -m755 "$here/AppRun" "$app/AppRun"
install -m644 "$here/io.cpressland.ThemeParkWorld.desktop" "$app/"
install -m644 "$here/icon-512.png" "$app/io.cpressland.ThemeParkWorld.png"
ln -sf io.cpressland.ThemeParkWorld.png "$app/.DirIcon"
install -d "$u/share/metainfo" "$u/share/applications" "$u/share/icons/hicolor/512x512/apps"
sed -e "s/{version}/$version/" -e "s/{date}/$(date -u +%F)/" "$here/io.cpressland.ThemeParkWorld.metainfo.xml" \
  > "$u/share/metainfo/io.cpressland.ThemeParkWorld.appdata.xml"
install -m644 "$here/io.cpressland.ThemeParkWorld.desktop" "$u/share/applications/"
install -m644 "$here/icon-512.png" "$u/share/icons/hicolor/512x512/apps/io.cpressland.ThemeParkWorld.png"

# Never the game: no disc image, patch or executable of it.
bad=$(find "$app" -iname '*.iso' -o -iname 'tp.exe' -o -iname 'themeparkworld20patch*' -o -iname '*.sam' | head -5)
[ -z "$bad" ] || { echo "game files in the AppImage: $bad" >&2; exit 1; }

img=$out/Theme-Park-World-$version-$arch.AppImage
echo "==> squashfs"
rm -f "$out/AppDir.squashfs" "$img"
mksquashfs "$app" "$out/AppDir.squashfs" -root-owned -noappend -comp zstd -Xcompression-level 19 -quiet
cat "$cache/runtime-$arch" "$out/AppDir.squashfs" > "$img"
chmod 755 "$img"
rm -f "$out/AppDir.squashfs"
du -sh "$u/python" "$u/zig" "$u/lib" "$u/share" "$img"
echo "==> $img"

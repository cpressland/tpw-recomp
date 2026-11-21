# Builds the SDL launcher for Windows (app/sdl-launcher/launcher.c + launcher_os_win32.c)
# with llvm-mingw's clang.
#
# usage: ./app/windows/build-launcher.ps1  # -> build-launcher/theme-park-world.exe
#
# This script has a number of requirements; read the "Building (Windows)"
# section of the readme to make sure you have everything.
#
# ---- settings ----
#   TPW_SDL_PREFIX     paths to SDL and SDL_ttf, separated by ';'  (required)
#   TPW_VERSION        the version the launcher shows              (default: none)
#   CC                 the C compiler                              (default: clang)
#
# ---- running it ----
# Point the launcher at a Python and the sources:
#   $env:TPW_PYTHON = "$PWD\.venv\Scripts\python.exe"
#   $env:TPW_SHARE  = a directory made by: install.py --export-sources <directory>
#
# This will become simpler once proper packaging for Windows is implemented.
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot)
Set-Location $repo

if (-not $env:TPW_SDL_PREFIX) {
    throw 'Set TPW_SDL_PREFIX to the SDL2 and SDL2_ttf MinGW package directories, separated by ";" (README: Building (Windows)).'
}

# As in build-windows.ps1, the user can pass either the top-level or
# x86_64-w64-mingw32 directory for both SDL and SDL_ttf.
# We also have to add both `include/SDL2` and `include/`:
# - launcher.c includes <SDL2/SDL.h>
# - SDL_ttf.h includes <SDL.h>
# TODO: consolidate the project to use SDL's own include style?
$flags = foreach ($p in $env:TPW_SDL_PREFIX -split ';' | Where-Object { $_ }) {
    $arch = Join-Path $p 'x86_64-w64-mingw32'
    $root = if (Test-Path $arch) { $arch } else { $p }
    "-I$root/include", "-I$root/include/SDL2", "-L$root/lib"
}
$cc = if ($env:CC) { $env:CC } else { 'clang' }
$out = Join-Path $repo 'build-launcher'
New-Item -ItemType Directory -Force $out | Out-Null

& $cc -O2 -Wall -mwindows "-DTPW_VERSION=`"$env:TPW_VERSION`"" @flags `
    app/sdl-launcher/launcher.c app/sdl-launcher/launcher_os_win32.c -lSDL2 -lSDL2_ttf -o "$out/theme-park-world.exe"
if ($LASTEXITCODE) { exit $LASTEXITCODE }

# Copy DLLs into the exe dir.
foreach ($p in $env:TPW_SDL_PREFIX -split ';' | Where-Object { $_ }) {
    $arch = Join-Path $p 'x86_64-w64-mingw32'
    $root = if (Test-Path $arch) { $arch } else { $p }
    Copy-Item "$root/bin/*.dll" $out
}
"built $out\theme-park-world.exe"

# Builds the SDL launcher for Windows (app/sdl-launcher/launcher.c + launcher_os_win32.c)
# with llvm-mingw's clang, and places the game's icon and version in its resources.
#
# usage: ./app/windows/build-launcher.ps1  # -> build-launcher/Theme Park World.exe
#
# This script has a number of requirements; read the "Building (Windows)"
# section of the readme to make sure you have everything.
#
# ---- settings ----
#   TPW_SDL_PREFIX     paths to SDL and SDL_ttf, separated by ';'  (required)
#   TPW_VERSION        the version the launcher shows, like 1.2.0  (default: none)
#   CC                 the C compiler                              (default: clang)
#   TPW_LAUNCHER_OUT   the directory to build into                 (default: build-launcher)
#
# ---- running it ----
# Point the launcher at a Python and the sources:
#   $env:TPW_PYTHON = "$PWD\.venv\Scripts\python.exe"
#   $env:TPW_SHARE  = a directory made by: install.py --export-sources <directory>
# app/windows/build-package.ps1 builds a properly configured package that does
# not require manually specifying the above arguments.
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
$out = if ($env:TPW_LAUNCHER_OUT) { $env:TPW_LAUNCHER_OUT } else { Join-Path $repo 'build-launcher' }
New-Item -ItemType Directory -Force $out | Out-Null
$obj = Join-Path $out 'obj'
New-Item -ItemType Directory -Force $obj | Out-Null

# The icon, name, and file version are built into the binary's resources from
# app/windows/launcher.rc.
# VERSIONINFO takes four numbers, so a version like 1.2.0 is turned into 1,2,0,0.
# With no version, the default 0.0.0 from launcher.rc is used.
$version = @()
if ($env:TPW_VERSION) {
    $nums = @(($env:TPW_VERSION -split '[^0-9]+' | Where-Object { $_ }) + @(0, 0, 0, 0))[0..3] -join ','
    $version = "-DTPW_VERSION_NUMS=$nums", "-DTPW_VERSION=$env:TPW_VERSION"
}
# llvm-mingw's windres is colocated with clang.
$windres = Join-Path (Split-Path (Get-Command $cc).Source) 'windres.exe'
& "$PSScriptRoot/make-icon.ps1" (Join-Path $obj 'icon.ico')
& $windres app/windows/launcher.rc @version -I $obj -O coff -o (Join-Path $obj 'launcher.res.o')
if ($LASTEXITCODE) { exit $LASTEXITCODE }

$exe = Join-Path $out 'Theme Park World.exe'
& $cc -O2 -Wall -mwindows "-DTPW_VERSION=`"$env:TPW_VERSION`"" @flags `
    app/sdl-launcher/launcher.c app/sdl-launcher/launcher_os_win32.c (Join-Path $obj 'launcher.res.o') `
    -lSDL2 -lSDL2_ttf -o $exe
if ($LASTEXITCODE) { exit $LASTEXITCODE }

# Copy DLLs into the exe dir.
foreach ($p in $env:TPW_SDL_PREFIX -split ';' | Where-Object { $_ }) {
    $arch = Join-Path $p 'x86_64-w64-mingw32'
    $root = if (Test-Path $arch) { $arch } else { $p }
    Copy-Item "$root/bin/*.dll" $out
}
"built $exe"

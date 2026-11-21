# Configures & builds the native Windows host with llvm-mingw's clang.
#
# usage: ./build-windows.ps1  # -> build-windows/tpw.exe
#        build-windows/tpw.exe --run --game path/to/game
#
# This script has a number of requirements; read the "Building (Windows)"
# section of the readme to make sure you have everything.
#
# ---- settings ----
# Note: All of these except for BUILD_DIR only apply to the first configure,
#       i.e. when build.ninja doesn't exist yet. After that, changing these
#       does nothing unless you also delete the build folder.
#
#   TPW_SDL_PREFIX  paths to SDL and SDL_ttf, separated by ';'  (required)
#   BUILD_DIR       where to place the output                   (default: ./build-windows)
#   PCRECOMP        where pcrecomp is checked out               (default: ../pcrecomp)
#   CC              the C compiler                              (default: clang)
#   CMAKE_ARGS      additional flags for cmake's configure
#                   step, separated by spaces.
#                   for example: -DTPW_TRACE=ON
#
# Args passed to the script itself are forwarded to `cmake --build`.
# Note: be sure to quote '--' if passing args to ninja like this.
# Unquoted -- is dropped by PowerShell.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$buildDir = if ($env:BUILD_DIR) { $env:BUILD_DIR } else { 'build-windows' }
$pcrecomp = if ($env:PCRECOMP) { $env:PCRECOMP } else { Join-Path (Split-Path $PSScriptRoot) 'pcrecomp' }
$cc = if ($env:CC) { $env:CC } else { 'clang' }

if (-not (Test-Path (Join-Path $buildDir 'build.ninja'))) {
    if (-not $env:TPW_SDL_PREFIX) {
        throw 'Set TPW_SDL_PREFIX to the SDL2 and SDL2_ttf MinGW package folders, separated by ";" (README: Building (Windows)).'
    }
    # CMake wants the package's "x86_64-w64-mingw32" directory, which contains lib\cmake.
    # The user can pass the top-level path instead, so we need to try both options here.
    $prefixes = @(foreach ($p in $env:TPW_SDL_PREFIX -split ';' | Where-Object { $_ }) {
        $arch = Join-Path $p 'x86_64-w64-mingw32'
        if (Test-Path $arch) { $arch } else { $p }
    })
    $extra = @($env:CMAKE_ARGS -split ' ' | Where-Object { $_ })
    cmake -S . -B $buildDir -G Ninja "-DCMAKE_C_COMPILER=$cc" "-DPCRECOMP=$pcrecomp" "-DCMAKE_PREFIX_PATH=$($prefixes -join ';')" @extra
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
}
cmake --build $buildDir @args
exit $LASTEXITCODE

# Builds the all-in-one Theme Park World package for Windows.
#
# The package is a zip archive containing the launcher app and everything needed
# to build the game.
#
# usage: ./app/windows/build-package.ps1 [OUT] [-Version 1.2.0] [-Commit <commit>]
#   -> OUT/Theme-Park-World-VERSION-windows-x86_64.zip  (OUT: build-package)
#
# Package contents:
#   Theme Park World.exe     the launcher (built with app/windows/build-launcher.ps1)
#   SDL2.dll and SDL2_ttf.dll
#   python\                  a standalone Python with the lift's packages,
#                            CMake, and Ninja. (see: app/requirements.txt)
#   tools\llvm-mingw\        clang, the player's compiler
#   tools\SDL2\, SDL2_ttf\   SDL's MinGW development packages
#   tools\Vulkan-Headers\    Vulkan's headers for GPU rendering
#   share\src\               sources for compilation
#
# The game itself is *not* included. The disc image & 2.0 patch are downloaded
# by the launcher, then the game is translated and compiled on the player's
# machine.
#
# -Commit (default HEAD) sets which version of the repo to use.
# The package build uses checked-in files only, and pcrecomp at the commit in
# app/pcrecomp.lock.
#
# $env:PCRECOMP overrides the locked commit with a custom checkout to use.
#
# Downloads are pinned with SHA-256 hashes.
#
# This script requires git, curl, and tar with zip support.
param(
    [string]$Out = 'build-package',
    [string]$Version,
    [string]$Commit = 'HEAD'
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$repo = Split-Path (Split-Path $PSScriptRoot)
Set-Location $repo
New-Item -ItemType Directory -Force $Out | Out-Null
$Out = (Resolve-Path $Out).Path

$commitId = git rev-parse $Commit
if (-not $Version) { $Version = (git describe --tags --match 'v[0-9]*' --always $commitId) -replace '^v', '' }
if ($Commit -eq 'HEAD' -and (git status --porcelain --untracked-files=no)) {
    Write-Warning "uncommitted changes are not in the package (it takes $commitId)"
}

$PY = 'cpython-3.13.18+20261026-x86_64-pc-windows-msvc-install_only.tar.gz'
$LLVM = 'llvm-mingw-20261020-ucrt-x86_64'
$SDL = 'SDL2-2.32.10'
$TTF = 'SDL2_ttf-2.24.0'
$VK = 'Vulkan-Headers-1.4.350.0'

$cache = Join-Path $Out 'cache'
New-Item -ItemType Directory -Force $cache | Out-Null
function Get-Pinned([string]$url, [string]$sha, [string]$name) {
    if (-not $name) { $name = [uri]::UnescapeDataString(($url -split '/')[-1]) }
    $f = Join-Path $cache $name
    if (-not (Test-Path $f)) {
        Write-Host "==> $url"
        # Stalled downloads get retried rather than waiting endlessly
        curl.exe -fsSL --retry 5 --retry-all-errors --connect-timeout 30 --speed-limit 50000 --speed-time 60 `
            -o "$f.part" $url
        Move-Item "$f.part" $f
    }
    if ((Get-FileHash -Algorithm SHA256 $f).Hash -ne $sha) {
        Remove-Item $f
        throw "checksum mismatch: $f"
    }
    $f
}

$pyFile = Get-Pinned "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/$($PY -replace '\+', '%2B')" `
    ebe90cc1dce2dd2d7afa1f0aa9b6c269c7c0174aeabdc6a801381602b7283424
$llvmFile = Get-Pinned "https://github.com/mstorsjo/llvm-mingw/releases/download/20261020/$LLVM.zip" `
    fd5462b7f5d93b2ff981fcd3f9c7e427e47f3203c64ea91c27bcc444eebd74cd
$sdlFile = Get-Pinned "https://github.com/libsdl-org/SDL/releases/download/release-2.32.10/SDL2-devel-2.32.10-mingw.tar.gz" `
    83a5d74012311edc3c0d40ea6faecbe57ad692aa033fa5dc273cc937e3938ff2
$ttfFile = Get-Pinned "https://github.com/libsdl-org/SDL_ttf/releases/download/release-2.24.0/SDL2_ttf-devel-2.24.0-mingw.tar.gz" `
    3a09e0a967ad53eca3ff2701de4d0df3369c0368739a1088a78d9d74908ef2c3
$vkFile = Get-Pinned "https://github.com/KhronosGroup/Vulkan-Headers/archive/refs/tags/vulkan-sdk-1.4.350.0.tar.gz" `
    70270d10bf2c1e074a06ee37a50b75d332993d1b80a1d9526eeed2da6d82ed22 "$VK.tar.gz"

"==> Theme Park World $Version ($commitId)"
$pkg = Join-Path $Out 'Theme Park World'
$src = Join-Path $Out 'src'
$work = Join-Path $Out 'work'
foreach ($d in $pkg, $src, $work) {
    if (Test-Path $d) { Remove-Item -Recurse -Force $d }
    New-Item -ItemType Directory $d | Out-Null
}
$tools = Join-Path $pkg 'tools'

# Copy the pcrecomp & tpw-recomp source code into the staging directory.
git archive --prefix=tpw/ -o "$work/tpw.tar" $commitId
tar -xf "$work/tpw.tar" -C $src
$lock = Get-Content app/pcrecomp.lock
$pcRepository = ($lock | Select-String '^repository=(.*)').Matches[0].Groups[1].Value
$pcRef = ($lock | Select-String '^ref=(.*)').Matches[0].Groups[1].Value
if ($env:PCRECOMP) {
    $pcHead = git -C $env:PCRECOMP rev-parse HEAD
    if ($pcHead -ne $pcRef) { Write-Warning "`$env:PCRECOMP is at $pcHead, not the lock's $pcRef" }
    git -C $env:PCRECOMP archive --prefix=pcrecomp/ -o "$work/pcrecomp.tar" $pcHead
    tar -xf "$work/pcrecomp.tar" -C $src
} else {
    git init -q "$src/pcrecomp"
    git -C "$src/pcrecomp" fetch -q --depth 1 "https://github.com/$pcRepository.git" $pcRef
    git -C "$src/pcrecomp" checkout -q FETCH_HEAD
    Remove-Item -Recurse -Force "$src/pcrecomp/.git"
}

"==> the build tools"
# llvm-mingw, x86_64 only. Other targets' libraries and programs, and the
# debugger and language tools, are left out.
tar -xf $llvmFile -C $work
$l = Join-Path $work $LLVM
Remove-Item -Recurse -Force "$l/aarch64-w64-mingw32", "$l/armv7-w64-mingw32", "$l/i686-w64-mingw32",
    "$l/python", "$l/share", "$l/lib/libear", "$l/lib/libscanbuild"
Get-ChildItem "$l/bin" | Where-Object {
    $_.Name -match '^(aarch64|arm64ec|armv7|i686)-' -or $_.Name -match '^(lldb|liblldb|libpython|clangd|clang-tidy)'
} | Remove-Item
Get-ChildItem "$l/lib/clang" -Recurse -File | Where-Object { $_.Name -match '(aarch64|arm64ec|armv7|arm|i386|i686)\.' } |
    Remove-Item
New-Item -ItemType Directory $tools | Out-Null
Move-Item $l "$tools/llvm-mingw"

# Keep only the x86_64 part of SDL's and SDL_ttf's packages (bin, include, and
# lib directories), and their licenses.
tar -xf $sdlFile -C $work
tar -xf $ttfFile -C $work
foreach ($t in @(@($SDL, 'SDL2'), @($TTF, 'SDL2_ttf'))) {
    Move-Item (Join-Path $work "$($t[0])/x86_64-w64-mingw32") "$tools/$($t[1])"
    Copy-Item (Join-Path $work "$($t[0])/LICENSE.txt") "$tools/$($t[1])/LICENSE.txt"
    Remove-Item -Recurse -Force "$tools/$($t[1])/share" -ErrorAction SilentlyContinue
    # Remove unused static libraries (*.a) but keep import libraries (*.dll.a).
    Get-ChildItem "$tools/$($t[1])/lib" -Filter '*.a' | Where-Object { $_.Name -notmatch '\.dll\.a$' } | Remove-Item
}

# Add Vulkan's headers.
tar -xf $vkFile -C $work
$v = Join-Path $work "Vulkan-Headers-vulkan-sdk-1.4.350.0"
New-Item -ItemType Directory "$tools/Vulkan-Headers" | Out-Null
Move-Item "$v/include" "$tools/Vulkan-Headers/include"
Copy-Item "$v/LICENSE.md" "$tools/Vulkan-Headers/LICENSE.md"

"==> Python and its packages"
tar -xf $pyFile -C $pkg
$P = Join-Path $pkg 'python'
& "$P/python.exe" -I -m pip install -q --disable-pip-version-check --no-warn-script-location --no-compile `
    --only-binary :all: -r app/requirements.txt

# We only need python/Scripts/ninja.exe, so delete everything else in there.
# The other launchers in there are not portable anyway, so they'd break if you
# tried to run them on another machine.
if (-not (Test-Path "$P/Scripts/ninja.exe")) { throw 'no ninja.exe in python\Scripts' }
Get-ChildItem "$P/Scripts" | Where-Object Name -ne 'ninja.exe' | Remove-Item
$CM = "$P/Lib/site-packages/cmake/data"
Remove-Item -Recurse -Force "$CM/doc", "$CM/share/doc", "$CM/bin/ccmake.exe", "$CM/bin/cpack.exe", "$CM/bin/ctest.exe",
    "$CM/bin/cmake-gui.exe", "$P/Lib/idlelib", "$P/Lib/tkinter", "$P/Lib/turtledemo", "$P/Lib/ensurepip",
    "$P/Lib/test", "$P/Lib/lib2to3", "$P/Lib/site-packages/pip", "$P/tcl", "$P/include", "$P/libs" `
    -ErrorAction SilentlyContinue
Get-ChildItem "$P/Lib/site-packages" -Filter 'pip-*.dist-info' | Remove-Item -Recurse
Get-ChildItem "$P/DLLs" | Where-Object Name -match '^(_tkinter|tcl|tk)' | Remove-Item
Get-ChildItem $P -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Get-ChildItem $P -Recurse -File | Where-Object Extension -in '.a', '.lib', '.pdb' | Remove-Item

"==> the launcher"
# Run app/windows/build-launcher.ps1 with the packaged commit, compiler, and SDL.
$launcherEnv = @{
    CC = "$tools/llvm-mingw/bin/clang.exe"
    TPW_SDL_PREFIX = "$tools/SDL2;$tools/SDL2_ttf"
    TPW_VERSION = $Version
    TPW_LAUNCHER_OUT = $pkg
}
$oldEnv = @{}
foreach ($k in $launcherEnv.Keys) {
    $oldEnv[$k] = [Environment]::GetEnvironmentVariable($k)
    [Environment]::SetEnvironmentVariable($k, $launcherEnv[$k])
}
try {
    & "$src/tpw/app/windows/build-launcher.ps1"
} finally {
    foreach ($k in $oldEnv.Keys) { [Environment]::SetEnvironmentVariable($k, $oldEnv[$k]) }
    Set-Location $repo
}
Remove-Item -Recurse -Force "$pkg/obj"

"==> the sources"
& "$P/python.exe" -I "$src/tpw/tools/install/install.py" --export-sources "$pkg/share/src" --pcrecomp "$src/pcrecomp"

# Ensure none of the original game files make it into the package.
$bad = Get-ChildItem $pkg -Recurse -File | Where-Object {
    $_.Name -match '\.(iso|sam)$' -or $_.Name -ieq 'tp.exe' -or
        $_.Name -like 'themeparkworld20patch*' -or $_.Name -like 'theme_park_world_2_0_patch*'
} | Select-Object -First 5
if ($bad) { throw "game files in the package: $($bad.FullName -join ', ')" }

"==> zip"
$zip = Join-Path $Out "Theme-Park-World-$Version-windows-x86_64.zip"
if (Test-Path $zip) { Remove-Item $zip }
tar -a -cf $zip -C $Out 'Theme Park World'
Remove-Item -Recurse -Force $work
foreach ($d in "$P", "$tools/llvm-mingw", "$tools", "$pkg") {
    "{0,6:N0} MB  {1}" -f ((Get-ChildItem $d -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), $d
}
"{0,6:N0} MB  {1}" -f ((Get-Item $zip).Length / 1MB), $zip

# Convert the launcher's linux icon (app/linux/icon-512.png) into a .ico file
# for Windows usage.
#
# usage: ./app/windows/make-icon.ps1 OUT.ico
#
# build-launcher.ps1 runs this on every build, writing to its obj directory, so
# the icon always matches the Linux one and isn't checked into the repo.
param([Parameter(Mandatory)][string]$Out)
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot)

Add-Type -AssemblyName System.Drawing
$src = [System.Drawing.Image]::FromFile((Join-Path $repo 'app/linux/icon-512.png'))
$sizes = 256, 64, 48, 32, 24, 16
$pngs = foreach ($s in $sizes) {
    $bmp = [System.Drawing.Bitmap]::new($s, $s)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.DrawImage($src, 0, 0, $s, $s)
    $g.Dispose()
    $ms = [System.IO.MemoryStream]::new()
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    , $ms.ToArray()
}
$src.Dispose()

# The .ico format: 6-byte header, 16-byte entry for each image (width/height=0
# means 256), and then the image data.
$ico = [System.IO.MemoryStream]::new()
$w = [System.IO.BinaryWriter]::new($ico)
$w.Write([uint16]0); $w.Write([uint16]1); $w.Write([uint16]$sizes.Count)
$offset = 6 + 16 * $sizes.Count
for ($i = 0; $i -lt $sizes.Count; $i++) {
    $s = $sizes[$i] % 256
    $w.Write([byte]$s); $w.Write([byte]$s); $w.Write([byte]0); $w.Write([byte]0)
    $w.Write([uint16]1); $w.Write([uint16]32)
    $w.Write([uint32]$pngs[$i].Length); $w.Write([uint32]$offset)
    $offset += $pngs[$i].Length
}
foreach ($p in $pngs) { $w.Write($p) }
$w.Flush()
# WriteAllBytes takes a path relative to .NET's working directory, which
# isn't always PowerShell's, so the path is made absolute first.
$Out = [System.IO.Path]::GetFullPath($Out, (Get-Location).Path)
[System.IO.File]::WriteAllBytes($Out, $ico.ToArray())

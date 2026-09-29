# Cartoon gear icon (no face): orange gear + outline + real center hole + shine.
# Outputs multi-size app.ico (PNG-compressed entries) and a 256px preview PNG.
Add-Type -AssemblyName System.Drawing

$size = 256
$bmp = New-Object System.Drawing.Bitmap($size, $size)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias

$dark   = [System.Drawing.Color]::FromArgb(255, 78, 52, 46)     # 4E342E outline brown
$orange = [System.Drawing.Color]::FromArgb(255, 255, 167, 38)   # FFA726 gear orange
$inner  = [System.Drawing.Color]::FromArgb(255, 245, 138, 7)    # F58A07 inner ring

$c = 128.0    # center
$R1 = 74.0    # root radius
$R2 = 100.0   # tip radius
$Rmid = 89.0  # transition radius

function Pt([double]$radius, [double]$deg) {
    $rad = $deg * [Math]::PI / 180.0
    return (New-Object System.Drawing.PointF(($script:c + $radius * [Math]::Cos($rad)), ($script:c + $radius * [Math]::Sin($rad))))
}

# gear outline: 8 teeth, 14 deg tip width, arc transition at root
$pts = New-Object 'System.Collections.Generic.List[System.Drawing.PointF]'
for ($i = 0; $i -lt 8; $i++) {
    $T = $i * 45.0
    $pts.Add((Pt $R2 ($T - 7)))
    $pts.Add((Pt $R2 ($T + 7)))
    $pts.Add((Pt $Rmid ($T + 9)))
    $pts.Add((Pt $R1 ($T + 11)))
    foreach ($a in 15, 19, 23, 27, 31) { $pts.Add((Pt $R1 ($T + $a))) }
    $next = $T + 45.0
    $pts.Add((Pt $Rmid ($next - 9)))
}
$path = New-Object System.Drawing.Drawing2D.GraphicsPath
$path.AddPolygon($pts.ToArray())

# outline stroke + fill (half of the stroke is covered by fill, leaving a 5px outline)
$pen = New-Object System.Drawing.Pen($dark, 10)
$pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
$g.DrawPath($pen, $path)
$g.FillPath((New-Object System.Drawing.SolidBrush($orange)), $path)

# slightly darker inner ring for depth
$ringPen = New-Object System.Drawing.Pen($inner, 6)
$g.DrawEllipse($ringPen, ($c - 56), ($c - 56), 112, 112)

# real transparent center hole + hole edge outline
$g.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
$g.FillEllipse([System.Drawing.Brushes]::Transparent, ($c - 34), ($c - 34), 68, 68)
$g.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceOver
$holePen = New-Object System.Drawing.Pen($dark, 8)
$g.DrawEllipse($holePen, ($c - 32), ($c - 32), 64, 64)

# highlights
$shine = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(120, 255, 255, 255))
$g.FillEllipse($shine, ($c - 68), ($c - 66), 38, 22)
$g.FillEllipse($shine, ($c + 34), ($c + 52), 22, 13)

$g.Dispose()

# export: 256 preview PNG + multi-size ICO into this script's folder,
# and copy app.ico into ..\程序 as well when that folder exists
$outDir = $PSScriptRoot
$progDir = Join-Path (Split-Path $PSScriptRoot -Parent) "程序"
$bmp.Save((Join-Path $outDir "_icon_preview.png"), [System.Drawing.Imaging.ImageFormat]::Png)

$sizes = 256, 128, 64, 48, 32, 16
$pngs = @()
foreach ($s in $sizes) {
    if ($s -eq 256) { $b = $bmp }
    else {
        $b = New-Object System.Drawing.Bitmap($s, $s)
        $gg = [System.Drawing.Graphics]::FromImage($b)
        $gg.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $gg.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $gg.DrawImage($bmp, 0, 0, $s, $s)
        $gg.Dispose()
    }
    $ms = New-Object System.IO.MemoryStream
    $b.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $pngs += , @{ Size = $s; Bytes = $ms.ToArray() }
    if ($s -ne 256) { $b.Dispose() }
}

$icoPath = Join-Path $outDir "app.ico"
$fs = [System.IO.File]::Create($icoPath)
$bw = New-Object System.IO.BinaryWriter($fs)
$bw.Write([uint16]0); $bw.Write([uint16]1); $bw.Write([uint16]$pngs.Count)
$offset = 6 + 16 * $pngs.Count
foreach ($p in $pngs) {
    $dim = if ($p.Size -ge 256) { 0 } else { $p.Size }
    $bw.Write([byte]$dim); $bw.Write([byte]$dim); $bw.Write([byte]0); $bw.Write([byte]0)
    $bw.Write([uint16]1); $bw.Write([uint16]32)
    $bw.Write([uint32]$p.Bytes.Length); $bw.Write([uint32]$offset)
    $offset += $p.Bytes.Length
}
foreach ($p in $pngs) { $bw.Write($p.Bytes) }
$bw.Flush(); $bw.Close(); $fs.Close()
$bmp.Dispose()
if (Test-Path $progDir) { Copy-Item (Join-Path $outDir "app.ico") $progDir -Force }
Write-Output ("OK app.ico=" + (Get-Item $icoPath).Length + "B, sizes=" + ($pngs.Count))

<?php
// ogimg.php - ausschnittsgenaues Vorschaubild (og:image) fuer geteilte Links.
// Aufruf: ogimg.php?c=ZOOM/LAT/LNG  (gleiches Format wie der Permalink der App).
// Schneidet den Ausschnitt aus dem vom Build erzeugten preview_full.png
// (ganze Radar-Domain, ohne Fussleiste) und klebt footer.png unten dran.
// Ohne/mit ungueltigem Parameter: liefert das Standard-preview.png.

$W = 1200; $H = 630; $BAR = 66;
$LW = 2.6; $LE = 12.5; $LS = 43.6; $LN = 49.5;   // Radar-Domain (wie DST_* im Build)

header('Content-Type: image/png');
header('Cache-Control: public, max-age=300');      // 5 min - wie der Build-Takt

function fallback() {
    if (is_file(__DIR__ . '/preview.png')) { readfile(__DIR__ . '/preview.png'); }
    exit;
}

if (!isset($_GET['c']) || !preg_match('~^(\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)$~', $_GET['c'], $m)) {
    fallback();
}
$z = max(6, min(13, (float)$m[1]));
$lat = max($LS, min($LN, (float)$m[2]));
$lng = max($LW, min($LE, (float)$m[3]));

$full = @imagecreatefrompng(__DIR__ . '/preview_full.png');
$foot = @imagecreatefrompng(__DIR__ . '/footer.png');
if (!$full) { fallback(); }
$FW = imagesx($full); $FH = imagesy($full);

// Sichtbare Breite in Grad bei Zoom z (WebMercator-Massstab, 1200 px breit)
$lonSpan = 360.0 * $W / (256.0 * pow(2, $z));
$latSpan = $lonSpan * cos(deg2rad($lat)) * ($H - $BAR) / $W;   // Kartenteil ohne Fussleiste
// Nicht weiter raus als die Domain
$lonSpan = min($lonSpan, $LE - $LW);
$latSpan = min($latSpan, $LN - $LS);

// Ausschnitt in Domain-Pixel umrechnen (preview_full ist plattkarte/linear in Grad)
$x0 = ($lng - $lonSpan / 2 - $LW) / ($LE - $LW) * $FW;
$x1 = ($lng + $lonSpan / 2 - $LW) / ($LE - $LW) * $FW;
$y0 = ($LN - ($lat + $latSpan / 2)) / ($LN - $LS) * $FH;
$y1 = ($LN - ($lat - $latSpan / 2)) / ($LN - $LS) * $FH;
// an den Domain-Rand klemmen (Ausschnitt verschieben statt verzerren)
$w = $x1 - $x0; $h = $y1 - $y0;
if ($x0 < 0) { $x0 = 0; }            if ($x0 + $w > $FW) { $x0 = $FW - $w; }
if ($y0 < 0) { $y0 = 0; }            if ($y0 + $h > $FH) { $y0 = $FH - $h; }

$out = imagecreatetruecolor($W, $H);
imagecopyresampled($out, $full, 0, 0, (int)round($x0), (int)round($y0),
                   $W, $H - $BAR, (int)round($w), (int)round($h));
if ($foot) { imagecopy($out, $foot, 0, $H - $BAR, 0, 0, $W, $BAR); }
imagepng($out, null, 6);

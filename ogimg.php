<?php
// ogimg.php - ausschnittsgenaues Vorschaubild (og:image) fuer geteilte Links.
// Aufruf: ogimg.php?c=ZOOM/LAT/LNG  (Format des App-Permalinks).
// Schneidet den Ausschnitt aus preview_full.png (Hintergrund + Radar, vom Build alle
// 5 Min erneuert) und zeichnet DANACH in Ausgabe-Aufloesung: Schweizer Grenze,
// Ortsnamen (zoomabhaengig, aus places.js/fplaces.js), einen Pin auf dem geteilten
// Punkt sowie die Fussleiste mit dem Ortsnamen. Ohne ?c=: Standard-preview.png.

$W = 1200; $H = 630; $BAR = 66; $MH = $H - $BAR;   // MH = Kartenteil ohne Fussleiste
$LW = 2.6; $LE = 12.5; $LS = 43.6; $LN = 49.5;      // Radar-Domain (wie DST_* im Build)
$FONT  = __DIR__ . '/fonts/DejaVuSans.ttf';
$FONTB = __DIR__ . '/fonts/DejaVuSans-Bold.ttf';

header('Content-Type: image/png');
header('Cache-Control: public, max-age=300');

function fallback() { if (is_file(__DIR__.'/preview.png')) readfile(__DIR__.'/preview.png'); exit; }

if (!isset($_GET['c']) || !preg_match('~^(\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)$~', $_GET['c'], $m)) fallback();
$z   = max(6, min(13, (float)$m[1]));
$lat = max($LS, min($LN, (float)$m[2]));
$lng = max($LW, min($LE, (float)$m[3]));

$full = @imagecreatefrompng(__DIR__ . '/preview_full.png');
$foot = @imagecreatefrompng(__DIR__ . '/footer.png');
if (!$full) fallback();
$FW = imagesx($full); $FH = imagesy($full);

// ---- Ausschnitt in Grad (WebMercator-Massstab bei Zoom z, Plattkarte genaehert) ----
$lonSpan = min(360.0 * $W / (256.0 * pow(2, $z)), $LE - $LW);
$latSpan = min($lonSpan * cos(deg2rad($lat)) * $MH / $W, $LN - $LS);
$x0d = $lng - $lonSpan/2; $y0d = $lat + $latSpan/2;           // West / Nord des Ausschnitts
if ($x0d < $LW) $x0d = $LW;  if ($x0d + $lonSpan > $LE) $x0d = $LE - $lonSpan;
if ($y0d > $LN) $y0d = $LN;  if ($y0d - $latSpan < $LS) $y0d = $LS + $latSpan;

// Grad -> Ausgabepixel
function PX($lo){ global $x0d,$lonSpan,$W;  return ($lo - $x0d) / $lonSpan * $W; }
function PY($la){ global $y0d,$latSpan,$MH; return ($y0d - $la) / $latSpan * $MH; }

// ---- 1) Radar/Hintergrund zuschneiden ----
$out = imagecreatetruecolor($W, $H);
imagecopyresampled($out, $full, 0, 0,
    (int)round(($x0d-$LW)/($LE-$LW)*$FW), (int)round(($LN-$y0d)/($LN-$LS)*$FH),
    $W, $MH, (int)round($lonSpan/($LE-$LW)*$FW), (int)round($latSpan/($LN-$LS)*$FH));

// ---- Kartendaten aus den App-Dateien lesen ----
function loadArr($file, $var) {
    $s = @file_get_contents(__DIR__ . '/' . $file); if (!$s) return array();
    if (!preg_match('~window\.' . $var . '=(\[.*?\]);~s', $s, $m)) return array();
    $a = json_decode($m[1], true); return is_array($a) ? $a : array();
}

// ---- 2) Schweizer Grenze (vektorbasiert -> bei jedem Zoom scharf) ----
$border = loadArr('places.js', 'CH_BORDER');
if ($border) {
    $gc = imagecolorallocatealpha($out, 120, 128, 118, 40);
    $n = count($border);
    for ($i = 1; $i < $n; $i++) {
        $ax=PX($border[$i-1][1]); $ay=PY($border[$i-1][0]);
        $bx=PX($border[$i][1]);   $by=PY($border[$i][0]);
        if (($ax<-50&&$bx<-50)||($ax>$W+50&&$bx>$W+50)||($ay<-50&&$by<-50)||($ay>$MH+50&&$by>$MH+50)) continue;
        imageline($out, (int)$ax,(int)$ay,(int)$bx,(int)$by, $gc);
    }
}

// ---- 3) Ortsnamen (zoomabhaengig, mit einfachem Kollisionsschutz) ----
$places = array_merge(loadArr('places.js', 'CITIES'), loadArr('fplaces.js', 'FCITIES'));
usort($places, function($a,$b){ return $a[3] - $b[3]; });     // wichtigste zuerst
$txt  = imagecolorallocate($out, 84, 88, 92);
$halo = imagecolorallocatealpha($out, 255, 255, 255, 30);
$dotc = imagecolorallocate($out, 84, 88, 92);
$ring = imagecolorallocate($out, 255, 255, 255);
$boxes = array(); $drawn = 0;
$useTTF = is_file($FONT);
foreach ($places as $p) {
    if ($drawn >= 12) break;
    if ($p[3] > $z) continue;                                  // erst ab dieser Zoomstufe
    $px = PX($p[2]); $py = PY($p[1]);
    if ($px < 14 || $px > $W-14 || $py < 16 || $py > $MH-30) continue;
    $name = $p[0];
    $tw = $useTTF ? (function($n){ global $FONT; $b=imagettfbbox(20,0,$GLOBALS['FONT'],$n); return $b[2]-$b[0]; })($name)
                  : strlen($name)*10;
    $bx0=$px-$tw/2-6; $bx1=$px+$tw/2+6; $by0=$py-4; $by1=$py+36;
    $ok = true;
    foreach ($boxes as $b) { if ($bx0<$b[1] && $bx1>$b[0] && $by0<$b[3] && $by1>$b[2]) { $ok=false; break; } }
    if (!$ok) continue;
    $boxes[] = array($bx0,$bx1,$by0,$by1); $drawn++;
    imagefilledellipse($out,(int)$px,(int)$py,11,11,$ring);    // Punkt mit weissem Ring
    imagefilledellipse($out,(int)$px,(int)$py,7,7,$dotc);
    $tx = (int)($px - $tw/2); $ty = (int)($py + 28);
    if ($useTTF) {
        foreach (array(array(-1,0),array(1,0),array(0,-1),array(0,1)) as $o)
            imagettftext($out,20,0,$tx+$o[0],$ty+$o[1],$halo,$FONT,$name);
        imagettftext($out,20,0,$tx,$ty,$txt,$FONT,$name);
    } else {
        imagestring($out,4,$tx,$ty-12,$name,$txt);
    }
}

// ---- 4) Pin auf dem geteilten Punkt (Bildmitte des Kartenteils) ----
$cx=(int)PX($lng); $cy=(int)PY($lat);
$grn = imagecolorallocate($out, 52, 168, 83);
imagefilledellipse($out,$cx,$cy,26,26,$ring);
imagefilledellipse($out,$cx,$cy,18,18,$grn);
imagefilledellipse($out,$cx,$cy,6,6,$ring);

// ---- 5) Fussleiste + Ortsname des geteilten Punkts ----
if ($foot) imagecopy($out, $foot, 0, $H-$BAR, 0, 0, $W, $BAR);
$near = null; $bd = 1e9; $co = cos(deg2rad($lat));
foreach (array_merge($places, loadArr('places.js','PLACES')) as $p) {
    $d = ($p[1]-$lat)*($p[1]-$lat) + ($p[2]-$lng)*$co*($p[2]-$lng)*$co;
    if ($d < $bd) { $bd = $d; $near = $p[0]; }
}
if ($near !== null && $bd < 0.01 && is_file($FONTB)) {         // ~10 km Umkreis
    $b = imagettfbbox(24, 0, $FONTB, $near);
    $nx = (int)(($W - ($b[2]-$b[0])) / 2);
    imagettftext($out, 24, 0, $nx, $H-$BAR+42, imagecolorallocate($out,240,244,238), $FONTB, $near);
}
imagepng($out, null, 6);

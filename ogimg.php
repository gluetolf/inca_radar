<?php
// ogimg.php - ausschnittsgenaues Vorschaubild (og:image) fuer geteilte Links.
// Aufruf: ogimg.php?c=ZOOM/LAT/LNG  (Format des App-Permalinks).
// Stapelt die Schichten selbst - Hintergrund, CH-Flaeche, Landesgrenzen, Seen, Fluesse
// (Natural Earth, geo_bg.json), CH-Kontur, RADAR (radar_full.png, alle 5 Min neu),
// Ortslabels, Pin, Fussleiste mit Ortsname. Ohne/mit ungueltigem ?c=: preview.png.
error_reporting(0); ini_set('display_errors', '0');            // Notices wuerden das PNG zerstoeren

$W = 1200; $H = 630; $MH = $H;                                 // Karte vollflaechig
$LW = 2.6; $LE = 12.5; $LS = 43.6; $LN = 49.5;                 // Radar-Domain (wie DST_* im Build)
$FONT  = __DIR__ . '/fonts/DejaVuSans.ttf';
$FONTB = __DIR__ . '/fonts/DejaVuSans-Bold.ttf';

header('Content-Type: image/png');
header('Cache-Control: public, max-age=300');

function fallback() { if (is_file(__DIR__.'/preview.png')) readfile(__DIR__.'/preview.png'); exit; }

if (!isset($_GET['c']) || !preg_match('~^(\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)$~', $_GET['c'], $m)) fallback();
$z   = max(6, min(13, (float)$m[1]));
$lat = max($LS, min($LN, (float)$m[2]));
$lng = max($LW, min($LE, (float)$m[3]));

$rad = @imagecreatefrompng(__DIR__ . '/radar_full.png');       // transparentes Radar, ganze Domain
if (!$rad) fallback();
$FW = imagesx($rad); $FH = imagesy($rad);

// ---- Ausschnitt in Grad (WebMercator-Massstab bei Zoom z, Plattkarte genaehert) ----
$lonSpan = min(360.0 * $W / (256.0 * pow(2, $z)), $LE - $LW);
$latSpan = min($lonSpan * cos(deg2rad($lat)) * $MH / $W, $LN - $LS);
$x0d = $lng - $lonSpan/2; $y0d = $lat + $latSpan/2;
if ($x0d < $LW) $x0d = $LW;  if ($x0d + $lonSpan > $LE) $x0d = $LE - $lonSpan;
if ($y0d > $LN) $y0d = $LN;  if ($y0d - $latSpan < $LS) $y0d = $LS + $latSpan;

function PX($lo){ global $x0d,$lonSpan,$W;  return ($lo - $x0d) / $lonSpan * $W; }
function PY($la){ global $y0d,$latSpan,$MH; return ($y0d - $la) / $latSpan * $MH; }

function loadArr($file, $var) {
    $s = @file_get_contents(__DIR__ . '/' . $file); if (!$s) return array();
    if (!preg_match('~window\.' . $var . '=(\[.*?\]);~s', $s, $m)) return array();
    $a = json_decode($m[1], true); return is_array($a) ? $a : array();
}
function drawSeg($im, $seg, $col) {                            // Linienzug [lon,lat]
    global $W, $MH;
    $n = count($seg);
    for ($i = 1; $i < $n; $i++) {
        $ax=PX($seg[$i-1][0]); $ay=PY($seg[$i-1][1]);
        $bx=PX($seg[$i][0]);   $by=PY($seg[$i][1]);
        if (($ax<-80&&$bx<-80)||($ax>$W+80&&$bx>$W+80)||($ay<-80&&$by<-80)||($ay>$MH+80&&$by>$MH+80)) continue;
        imageline($im,(int)$ax,(int)$ay,(int)$bx,(int)$by,$col);
    }
}

// ---- 1) Hintergrund + CH-Flaeche ----
$out = imagecreatetruecolor($W, $H);
imagealphablending($out, true);
imagefilledrectangle($out, 0, 0, $W, $H, imagecolorallocate($out, 226, 231, 222));
$chb = loadArr('places.js', 'CH_BORDER');                      // Achtung: [lat, lon]
if ($chb) {
    $pts = array();
    foreach ($chb as $p) { $pts[] = (int)PX($p[1]); $pts[] = (int)PY($p[0]); }
    imagefilledpolygon($out, $pts, count($chb), imagecolorallocate($out, 242, 245, 239));
}

// ---- 2) Landesgrenzen, Seen, Fluesse (Natural Earth) ----
$gs = @file_get_contents(__DIR__ . '/geo_bg.json');
$geo = $gs ? json_decode($gs, true) : null;
if (is_array($geo)) {
    $bordC = imagecolorallocatealpha($out, 152, 160, 150, 50);
    $lakeF = imagecolorallocate($out, 199, 220, 232);
    $lakeO = imagecolorallocate($out, 168, 197, 215);
    $rivC  = imagecolorallocatealpha($out, 176, 205, 223, 20);
    foreach ((isset($geo['borders'])?$geo['borders']:array()) as $seg) drawSeg($out, $seg, $bordC);
    foreach ((isset($geo['lakes'])?$geo['lakes']:array()) as $poly) {
        $pts = array(); $vis = false;
        foreach ($poly as $p) {
            $x = PX($p[0]); $y = PY($p[1]);
            $pts[] = (int)$x; $pts[] = (int)$y;
            if ($x > -80 && $x < $W+80 && $y > -80 && $y < $MH+80) $vis = true;
        }
        if ($vis && count($pts) >= 8) {
            imagefilledpolygon($out, $pts, (int)(count($pts)/2), $lakeF);
            imagepolygon($out, $pts, (int)(count($pts)/2), $lakeO);
        }
    }
    foreach ((isset($geo['rivers'])?$geo['rivers']:array()) as $seg) drawSeg($out, $seg, $rivC);
}

// ---- 3) CH-Kontur ----
if ($chb) {
    $gc = imagecolorallocatealpha($out, 120, 128, 118, 40);
    $n = count($chb);
    for ($i = 1; $i < $n; $i++) {
        $ax=PX($chb[$i-1][1]); $ay=PY($chb[$i-1][0]);
        $bx=PX($chb[$i][1]);   $by=PY($chb[$i][0]);
        if (($ax<-50&&$bx<-50)||($ax>$W+50&&$bx>$W+50)||($ay<-50&&$by<-50)||($ay>$MH+50&&$by>$MH+50)) continue;
        imageline($out,(int)$ax,(int)$ay,(int)$bx,(int)$by,$gc);
    }
}

// ---- 4) RADAR (transparent) ueber die Karte ----
imagecopyresampled($out, $rad, 0, 0,
    (int)round(($x0d-$LW)/($LE-$LW)*$FW), (int)round(($LN-$y0d)/($LN-$LS)*$FH),
    $W, $MH, (int)round($lonSpan/($LE-$LW)*$FW), (int)round($latSpan/($LN-$LS)*$FH));

// ---- 5) Ortsnamen (zoomabhaengig, einfacher Kollisionsschutz) ----
$places = array_merge(loadArr('places.js', 'CITIES'), loadArr('fplaces.js', 'FCITIES'));
usort($places, function($a,$b){ return $a[3] - $b[3]; });
$txt  = imagecolorallocate($out, 84, 88, 92);
$halo = imagecolorallocatealpha($out, 255, 255, 255, 30);
$dotc = imagecolorallocate($out, 84, 88, 92);
$ring = imagecolorallocate($out, 255, 255, 255);
$boxes = array(); $drawn = 0;
$useTTF = is_file($FONT);
foreach ($places as $p) {
    if ($drawn >= 12) break;
    if ($p[3] > $z) continue;
    $px = PX($p[2]); $py = PY($p[1]);
    if ($px < 14 || $px > $W-14 || $py < 16 || $py > $MH-30) continue;
    $name = $p[0];
    if ($useTTF) { $b = imagettfbbox(20, 0, $FONT, $name); $tw = $b[2]-$b[0]; }
    else { $tw = strlen($name)*10; }
    $bx0=$px-$tw/2-6; $bx1=$px+$tw/2+6; $by0=$py-4; $by1=$py+36;
    $ok = true;
    foreach ($boxes as $b2) { if ($bx0<$b2[1] && $bx1>$b2[0] && $by0<$b2[3] && $by1>$b2[2]) { $ok=false; break; } }
    if (!$ok) continue;
    $boxes[] = array($bx0,$bx1,$by0,$by1); $drawn++;
    imagefilledellipse($out,(int)$px,(int)$py,11,11,$ring);
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

// ---- 6) Pin auf dem geteilten Punkt ----
$cx=(int)PX($lng); $cy=(int)PY($lat);
$grn = imagecolorallocate($out, 52, 168, 83);
imagefilledellipse($out,$cx,$cy,26,26,$ring);
imagefilledellipse($out,$cx,$cy,18,18,$grn);
imagefilledellipse($out,$cx,$cy,6,6,$ring);

// ---- 7) Marken-Karte (Headline = Ort) oben links + CTA-Button unten rechts ----
function roundRect($im, $x0, $y0, $x1, $y1, $r, $col) {
    imagefilledrectangle($im, $x0+$r, $y0, $x1-$r, $y1, $col);
    imagefilledrectangle($im, $x0, $y0+$r, $x1, $y1-$r, $col);
    imagefilledellipse($im, $x0+$r, $y0+$r, 2*$r, 2*$r, $col);
    imagefilledellipse($im, $x1-$r, $y0+$r, 2*$r, 2*$r, $col);
    imagefilledellipse($im, $x0+$r, $y1-$r, 2*$r, 2*$r, $col);
    imagefilledellipse($im, $x1-$r, $y1-$r, 2*$r, 2*$r, $col);
}
$near = null; $bd = 1e9; $co = cos(deg2rad($lat));
foreach (array_merge($places, loadArr('places.js','PLACES')) as $p) {
    $d = ($p[1]-$lat)*($p[1]-$lat) + ($p[2]-$lng)*$co*($p[2]-$lng)*$co;
    if ($d < $bd) { $bd = $d; $near = $p[0]; }
}
$ort = ($near !== null && $bd < 0.01) ? $near : "Schweiz";
$stand = '';
$sf = @stat(__DIR__ . '/radar_full.png');
if ($sf) { $dtz = new DateTime('@' . $sf['mtime']); $dtz->setTimezone(new DateTimeZone('Europe/Zurich'));
           $stand = 'Stand ' . $dtz->format('d.m.y H:i'); }
if (is_file($FONTB) && is_file($FONT)) {
    $cardBg = imagecolorallocatealpha($out, 255, 255, 255, 12);
    $dark   = imagecolorallocate($out, 24, 30, 22);
    $brandc = imagecolorallocate($out, 40, 46, 38);
    $grey   = imagecolorallocate($out, 120, 128, 118);
    $green  = imagecolorallocate($out, 52, 168, 83);
    $white  = imagecolorallocate($out, 255, 255, 255);
    $b1 = imagettfbbox(26, 0, $FONTB, "Niederschlagsradar");
    $b2 = imagettfbbox(44, 0, $FONTB, $ort);
    $b3 = $stand ? imagettfbbox(20, 0, $FONT, $stand) : array(0,0,0,0);
    $cw = (int)max(($b1[2]-$b1[0]) + 46, $b2[2]-$b2[0], $b3[2]-$b3[0]) + 56;
    roundRect($out, 24, 24, 24 + $cw, 174, 18, $cardBg);
    imagefilledellipse($out, 61, 57, 22, 22, $green);
    imagettftext($out, 26, 0, 84, 66, $brandc, $FONTB, "Niederschlagsradar");
    imagettftext($out, 44, 0, 52, 124, $dark, $FONTB, $ort);
    if ($stand) imagettftext($out, 20, 0, 52, 158, $grey, $FONT, $stand);
    $ct = "Radar live ansehen  >";
    $bc = imagettfbbox(24, 0, $FONTB, $ct);
    $cwid = $bc[2]-$bc[0];
    $bx1 = $W - 24; $bx0 = $bx1 - $cwid - 56; $by1 = $H - 24; $by0 = $by1 - 56;
    roundRect($out, $bx0, $by0, $bx1, $by1, 28, $green);
    imagettftext($out, 24, 0, $bx0 + 28, $by0 + 38, $white, $FONTB, $ct);
}
// EigerMaker-Logo unten links (dezent; erscheint nur, wenn logo.png vorhanden ist)
$lg = @imagecreatefrompng(__DIR__ . '/logo.png');
if ($lg) {
    $lh = 40; $lw = (int)(imagesx($lg) * $lh / imagesy($lg));
    if ($lw > 220) { $lw = 220; $lh = (int)(imagesy($lg) * $lw / imagesx($lg)); }
    $chx0 = 24; $chy1 = $H - 24; $chx1 = $chx0 + $lw + 28; $chy0 = $chy1 - $lh - 24;
    roundRect($out, $chx0, $chy0, $chx1, $chy1, 14, imagecolorallocatealpha($out, 255, 255, 255, 15));
    imagecopyresampled($out, $lg, $chx0 + 14, $chy0 + 12, 0, 0, $lw, $lh, imagesx($lg), imagesy($lg));
}
imagepng($out, null, 6);

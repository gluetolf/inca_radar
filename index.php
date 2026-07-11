<?php
// index.php - liefert index.html aus. Bei geteilten Links mit ?c=ZOOM/LAT/LNG werden
// Titel, Beschreibung und Vorschaubild ORTSBEZOGEN gesetzt (z.B. "Niederschlagsradar -
// Interlaken", og:image -> ogimg.php mit dem exakten Ausschnitt). Menschen sehen die
// App unveraendert; Link-Scraper (WhatsApp, Slack, X, ...) bekommen die passende Karte.
error_reporting(0); ini_set('display_errors', '0');

$html = @file_get_contents(__DIR__ . '/index.html');
if ($html === false) { http_response_code(503); exit('setup'); }

if (isset($_GET['c']) && preg_match('~^(\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)$~', $_GET['c'], $m)) {
    $lat = (float)$m[2]; $lng = (float)$m[3];
    $c    = htmlspecialchars($_GET['c'], ENT_QUOTES);
    $base = 'https://eigermaker.ch/radar/';

    // Naechster Ortsname aus den eigenen Kartendaten (places.js / fplaces.js)
    $near = null; $bd = 1e9; $co = cos(deg2rad($lat));
    foreach (array('places.js' => array('CITIES', 'PLACES'), 'fplaces.js' => array('FCITIES')) as $file => $vars) {
        $s = @file_get_contents(__DIR__ . '/' . $file); if (!$s) continue;
        foreach ($vars as $var) {
            if (!preg_match('~window\.' . $var . '=(\[.*?\]);~s', $s, $mm)) continue;
            $arr = json_decode($mm[1], true); if (!is_array($arr)) continue;
            foreach ($arr as $p) {
                $d = ($p[1]-$lat)*($p[1]-$lat) + ($p[2]-$lng)*$co*($p[2]-$lng)*$co;
                if ($d < $bd) { $bd = $d; $near = $p[0]; }
            }
        }
    }
    $ort = ($near !== null && $bd < 0.02) ? $near : null;      // ~15 km, auch Ausland (FCITIES)

    $title = $ort ? "Niederschlagsradar – " . $ort : "Niederschlagsradar Schweiz – Radar & Prognose";
    $desc  = $ort ? "Aktueller Niederschlag bei " . $ort . " – Live-Radar mit Kurzfrist-Prognose (MeteoSchweiz, DWD, Météo-France)."
                  : "Animiertes Live-Radar mit Kurzfrist-Prognose. Quellen: MeteoSchweiz, DWD, Météo-France.";
    $alt   = $ort ? "Niederschlagsradar-Ausschnitt bei " . $ort : "Aktuelles Niederschlagsradar der Schweiz";
    $img   = $base . 'ogimg.php?c=' . $c;
    $tH = htmlspecialchars($title, ENT_QUOTES); $dH = htmlspecialchars($desc, ENT_QUOTES);
    $aH = htmlspecialchars($alt, ENT_QUOTES);

    // Tags generisch ersetzen (robust gegen kuenftige index.html-Aenderungen)
    $set = function($html, $pat, $val) { return preg_replace($pat, '${1}' . $val . '${2}', $html, 1); };
    $html = $set($html, '~(<title>)[^<]*(</title>)~', $tH);
    $html = $set($html, '~(<meta name="description" content=")[^"]*(")~', $dH);
    $html = $set($html, '~(<meta property="og:title" content=")[^"]*(")~', $tH);
    $html = $set($html, '~(<meta property="og:description" content=")[^"]*(")~', $dH);
    $html = $set($html, '~(<meta property="og:url" content=")[^"]*(")~', $base . '?c=' . $c);
    $html = $set($html, '~(<meta property="og:image" content=")[^"]*(")~', $img);
    $html = $set($html, '~(<meta property="og:image:alt" content=")[^"]*(")~', $aH);
    $html = $set($html, '~(<meta name="twitter:title" content=")[^"]*(")~', $tH);
    $html = $set($html, '~(<meta name="twitter:description" content=")[^"]*(")~', $dH);
    $html = $set($html, '~(<meta name="twitter:image" content=")[^"]*(")~', $img);
}
// Kein Caching der HTML-Seite: die installierte PWA (v.a. iOS) soll immer die aktuelle
// Version holen, nicht eine alte gecachte. Statische Dateien (Icons, JS) duerfen gecacht bleiben.
header('Cache-Control: no-cache, no-store, must-revalidate');
header('Pragma: no-cache');
header('Expires: 0');
header('Content-Type: text/html; charset=utf-8');
echo $html;

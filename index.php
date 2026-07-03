<?php
// index.php - liefert index.html aus und ersetzt bei geteilten Links mit ?c=ZOOM/LAT/LNG
// die Open-Graph-Tags durch ausschnittsbezogene (og:image -> ogimg.php).
// Menschen sehen die App unveraendert (das JavaScript liest ?c= selbst und springt hin);
// Link-Scraper (WhatsApp & Co.) bekommen das passende Vorschaubild zum Ausschnitt.

$html = @file_get_contents(__DIR__ . '/index.html');
if ($html === false) { http_response_code(503); exit('setup'); }

if (isset($_GET['c']) && preg_match('~^(\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)$~', $_GET['c'])) {
    $c    = htmlspecialchars($_GET['c'], ENT_QUOTES);
    $base = 'https://eigermaker.ch/radar/';
    $html = str_replace(
        '<meta property="og:image" content="' . $base . 'preview.png">',
        '<meta property="og:image" content="' . $base . 'ogimg.php?c=' . $c . '">',
        $html);
    $html = str_replace(
        '<meta property="og:url" content="' . $base . '">',
        '<meta property="og:url" content="' . $base . '?c=' . $c . '">',
        $html);
}
header('Content-Type: text/html; charset=utf-8');
echo $html;

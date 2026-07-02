# INCA Radar – Niederschlagsradar & Prognose für die Schweiz

Animierte Niederschlagskarte: gemessenes Radar der letzten ~2 Stunden plus eine
Kurzfrist‑Prognose aus drei Wettermodellen – live auf **https://eigermaker.ch/radar/**.

## Wie es funktioniert

```
cron-job.org (alle 5 min, UTC-Minuten 4,9,14,…)
      │  workflow_dispatch (GitHub API)
      ▼
GitHub Actions (.github/workflows/inca.yml)
      │  python build.py
      ▼
   ./site/   (index.html, places.js, peaks.js, fplaces.js,
      │       frames.json, r_*.png Radar, f_*.png Prognose)
      ▼
SFTP (lftp+sshpass, METANET Port 2121)  →  eigermaker.ch/radar
```

### Vergangenheit (Radar)
- **MeteoSchweiz OGD** `ch.meteoschweiz.ogd-radar-precip` (RZC, ODIM‑HDF5, 5‑Minuten‑Takt).
- Abruf über den STAC‑`/search`‑Endpunkt per **POST** (wird vom CDN nicht gecacht → frischeste Daten).
- Die letzten `RADAR_FRAMES` (Standard 24 ≈ 2 h) werden auf ein gemeinsames
  WGS84‑Raster (2.6–12.5°E / 43.6–49.5°N, ~1,1 km) gerendert.

### Zukunft (Prognose)
Pro Zeitschritt ein **gewichteter Mittelwert** aus bis zu drei Modellen,
in der ersten Stunde ans letzte Radarbild „verankert" (weicher Übergang):
- **ICON‑CH1** – MeteoSchweiz (STAC/NetCDF)
- **ICON‑D2** – DWD Open Data (GRIB2, 15‑Minuten‑Felder)
- **AROME** – Météo‑France (WCS, `TOTAL_PRECIPITATION …_PT1H`, Vorlaufzeiten via
  `subset=time(...)`; fällt bei unfertig publizierten Läufen automatisch auf den
  letzten vollständigen Lauf zurück)

Fällt ein Modell aus, rechnen die übrigen weiter; fällt alles aus, wird
wenigstens das Radar publiziert.

## Dateien im Repo

| Datei | Zweck |
|---|---|
| `build.py` | Orchestrierung: Radar holen, Modelle holen, Bilder + `frames.json` nach `site/` |
| `inca_core.py` | Kernfunktionen: STAC/WCS‑Abrufe, ODIM/NetCDF/GRIB‑Leser, Reprojektion, Einfärbung |
| `index.html` | Viewer (Leaflet, eine Datei): Animation, Zeitleiste, Labels, Punkt‑Anzeige |
| `places.js` | Kartendaten: `PLACES` (~4000 CH‑Orte, GeoNames), `CH_BORDER`, `CITIES` (kuratierte Städte inkl. Auslands‑Exonyme), `LAKES` |
| `peaks.js` | `PEAKS`: kuratierte Gipfel mit Höhe + Bekanntheitswert (aus GeoNames präzisiert) |
| `fplaces.js` | `FCITIES`: Auslandsorte im Radar‑Gebiet + europäische Hauptstädte (generiert) |
| `peaks_refine.py` | Einmal‑Skript (lokal): exakte Gipfelkoordinaten aus GeoNames → `peaks.js` |
| `places_foreign.py` | Einmal‑Skript (lokal): Auslandsorte aus GeoNames `cities500` → `fplaces.js` |
| `requirements.txt` | Python‑Abhängigkeiten für den Build |
| `.github/workflows/inca.yml` | Build‑ & Deploy‑Workflow (nur `workflow_dispatch`) |

**Merkregel Cache‑Versionierung:** Ändert sich `places.js`, `peaks.js` oder
`fplaces.js`, in `index.html` die jeweilige `?v=`‑Nummer hochzählen.

## GitHub Secrets

| Secret | Inhalt |
|---|---|
| `FTP_SERVER` | SFTP‑Host (z. B. `xxx.metanet.ch`) |
| `FTP_USERNAME` | **Systembenutzer** des Hostings (SFTP geht nur mit diesem) |
| `FTP_PASSWORD` | dessen Passwort |
| `FTP_SERVER_DIR` | Zielverzeichnis, z. B. `eigermaker.ch/radar` |
| `SFTP_PORT` | optional; leer = 2121 (METANET‑Standard) |
| `METEOFRANCE_TOKEN` | API‑Token für den AROME‑WCS |

Voraussetzung bei METANET/Plesk: SSH‑Zugriff aktiviert
(Websites & Domains → Webhosting‑Zugang → `/bin/bash (chrooted)`).

## Viewer‑Funktionen

- **Zeitleiste** Vergangenheit→Prognose, Auto‑Play mit Bild‑Puffern (kein Flackern
  auf Mobilgeräten), „Stand … vor N Min" mit ehrlicher Warnung
  **„⚠ MeteoSchweiz‑Daten verzögert"**, wenn die Quelle hängt (>20 min).
- **Punkt‑Anzeige** (Fadenkreuz/Standort): Ort per Reverse‑Geocoding, Niederschlags‑
  klasse am Punkt für jedes Bild der Animation.
- **Eigenes Beschriftungssystem** (Schrift Montserrat, wie die Basiskarte), mit
  Kollisionsschutz in festen Kartenpixeln (stabil beim Verschieben) und
  Priorität **Orte > Seen > Gipfel**:
  - **Orte:** kuratierte Städte (Zoom‑gestaffelt) + dynamische CH‑Orte ab Zoom 11/12
    + Auslandsorte/Hauptstädte aus `fplaces.js` (Einwohner‑gestaffelt, Fernfeld ab Zoom 9).
  - **Seen:** kursiv, flächengebunden – weichen bei Konflikten aus (21 Kandidaten‑
    Positionen) statt auszublenden; ab Zoom 9.
  - **Gipfel:** △ + „Name Höhe", hellgrau, leicht schräg, nach **Bekanntheit**
    gestaffelt (12 Regions‑Flaggschiffe ab Zoom 8); Label klappt bei Platzmangel
    auf die linke Seite. Seen‑ und Gipfellabels liegen **unter** dem Niederschlag
    (Teil der Basiskarte), Orte darüber.
- Dark‑Mode, Ortssuche, Label‑Klick zentriert und pinnt den Ortsnamen; auf dem
  Handy sitzt der kompakte Titel unten über der Zeitleiste.

## Datenquellen & Grenzen

- Radar & ICON‑CH1: © MeteoSchweiz (Open Government Data) ·
  ICON‑D2: © DWD · AROME: © Météo‑France.
- Das kostenlose OGD‑Radar wird bei hoher Last (verbreitete Gewitter) zeitweise
  **verzögert publiziert** (beobachtet: bis ~1 h Rückstau, arbeitet sich selbst ab).
  Der Viewer zeigt das transparent an.
- **Radarschatten:** In tiefen Alpentälern (z. B. Interlaken) unterschätzt das
  Radar Niederschlag systematisch – physikalische Grenze des Messverfahrens.
  Das bodenkorrigierte INCA‑Nowcasting, das dies beheben würde, ist derzeit
  **nicht** als Open Data verfügbar (Stand Juli 2026; alle 33
  `ch.meteoschweiz.*`‑Collections geprüft).

## Lokal bauen (optional)

```bash
pip install -r requirements.txt
METEOFRANCE_TOKEN=... python build.py     # erzeugt ./site/
```

Nützliche Umgebungsvariablen: `RADAR_FRAMES` (24), `FC_HOURS`, `MODEL_TIMEOUT` (150 s),
`RADAR_FLOOR` (0.1 mm/h Anzeigeschwelle Radar), `INCA_STAC`, `AROME_WCS`.

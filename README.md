# Niederschlagsradar Schweiz (eigermaker.ch/radar)

Animiertes Niederschlagsradar für die Schweiz im LANDI-/MeteoSchweiz-Stil: ein
fensterfüllendes, animiertes Kartenoverlay, das **Vergangenheit (echtes Radar)**
und **Zukunft (Vorhersage)** auf derselben Leaflet-Karte zeigt.

- **Live:** https://eigermaker.ch/radar/
- **Hosting:** statische Seite, per GitHub Actions gebaut und via FTP zu METAhost deployt
- **Aktualisierung:** alle ~5 Minuten (extern angestoßen, siehe *Deployment*)

---

## Funktionsweise

Alle Datenquellen werden auf ein **gemeinsames WGS84-Raster** umgerechnet
(West/Ost/Süd/Nord = 2.6 / 12.5 / 43.6 / 49.5 Grad, Maschung 0.01° ≈ 1,1 km,
EPSG:4326) und als PNG-Frames gerendert. Der Viewer animiert diese Frames.

**Vergangenheit → jetzt:** offizielles MeteoSchweiz-Radar
(`ch.meteoschweiz.ogd-radar-precip`, ODIM-HDF5, 5-Minuten-Takt). Standard: 24 Frames (~2 h).

**Zukunft:** pixelweiser **gewichteter Mittelwert** aus drei Modellen, jeweils auf
15-Minuten-Schritte gebracht:

| Modell | Quelle | Auflösung | Reichweite (Default) |
|---|---|---|---|
| ICON-CH1 | MeteoSchweiz STAC (GRIB2) | ~1 km, stündlich | +30 h |
| ICON-D2 | DWD Open Data (GRIB2) | ~2,2 km, 15-min | +12 h |
| AROME France HD | Météo-France WCS (GRIB2) | ~1,3 km, stündlich | +12 h |

Die ersten 60 Minuten der Vorhersage werden weich ans letzte Radarbild „verankert"
(`BLEND_MIN`), damit der Übergang von Radar zu Modell nicht springt. AROME wird zum
Ostrand hin ausgeblendet (Feathering), damit keine harte Kante entsteht.

---

## Datenquellen & Lizenzen

Bitte bei jeder Nutzung die Quellen nennen:

- **MeteoSchweiz** – Radar & ICON-CH1 (Quelle: MeteoSchweiz / OGD)
- **Deutscher Wetterdienst (DWD)** – ICON-D2, CC BY 4.0 („Quelle: Deutscher Wetterdienst")
- **Météo-France** – AROME (© Météo-France)
- **Ortsdaten** – GeoNames, CC BY 4.0 (Schweizer Ortschaften & Gemeinden)
- **Basiskarte** – © OpenStreetMap-Mitwirkende, © CARTO
- **Ortssuche/Reverse-Geocoding im Ausland** – © OpenStreetMap (Nominatim)

---

## Der Viewer (`index.html`)

Eine einzelne, eigenständige HTML-Datei (Leaflet + eingebetteter Ortsdatensatz).
Funktionen:

- **Animation:** Abspielen/Scrubben, proportionale Geschwindigkeit (5-Min-Radar
  läuft 1×, 15-Min-Vorhersage 3×), Tempo-Voreinstellungen.
- **Zeitachse:** relative Tageslabels („Heute/Morgen/Gestern"), Tagestrenner auf der
  Zeitleiste, Frische-Anzeige oben rechts („Stand HH:MM · vor N Min").
- **Hell/Dunkel-Umschaltung.**
- **Fadenkreuz-Modell (wie Windy/RainViewer):** kein Tipp-Pin, sondern ein festes
  Fadenkreuz in der Kartenmitte. Der Chip zeigt immer **Ort + Niederschlagswert +
  Koordinaten** für die Mitte. Antippen/Suchen zentriert dorthin.
  - **Ortsanzeige gestuft:** ≤ 3 km → Ortschaft, ≤ 12 km → Gemeinde, weiter weg →
    nichts (statt eines irreführend entfernten Orts).
  - **Im Ausland:** Ortsname wird beim Loslassen per OpenStreetMap nachgeladen
    (entprellt + gecached, schont den Dienst).
  - **Koordinaten:** in/nahe der Schweiz **CH95/LV95** (E ~2,6 Mio / N ~1,2 Mio,
    swisstopo-Näherungsformel), sonst WGS84.
- **Ortssuche (🔍):** Sofortsuche über ~4044 Schweizer Ortschaften (offline,
  umlaut-tolerant: „zueri" findet Zürich). **Enter** springt direkt zum ersten
  Treffer. Über „… weltweit suchen" auch Orte im Ausland (Nominatim, nur auf Klick).
- **Zoom:** bis Stufe 13; Mausrad/Doppelklick/Pinch zoomen immer **zur Kartenmitte**
  (Position bleibt stabil). Das Radarbild wird ab der Datenauflösung hochskaliert.
- **Geteilte Position:** die Adresse trägt die aktuelle Ansicht als Hash
  `#zoom/lat/lng` (z. B. `…/radar/#13/46.68630/7.86320`). Adresse kopieren = teilen;
  beim Öffnen springt die App direkt dorthin.
- **Standort (⌖):** zentriert per Browser-Geolocation auf den eigenen Standort.

---

## Dateien im Repo

| Datei | Zweck |
|---|---|
| `build.py` | Orchestriert Radar (Vergangenheit) + 3-Modell-Mittelwert (Zukunft), rendert PNG-Frames, schreibt `frames.json`, kopiert `index.html` nach `site/`. |
| `inca_core.py` | Kern: Raster-Definition, Farbskala, Laden/Reprojektion von Radar, ICON-CH1, ICON-D2 und AROME. |
| `index.html` | Der komplette Viewer (inkl. eingebettetem Ortsdatensatz). |
| `requirements.txt` | Python-Abhängigkeiten (h5py, numpy, scipy, eccodes, cfgrib, rasterio, pyproj, pillow …). |
| `.github/workflows/inca.yml` | GitHub-Actions-Workflow (Build + FTP-Deploy). |
| `README.md` | Diese Datei. |

Ausgabe des Builds landet in `site/` (`index.html`, `frames.json`, `r*.png` Radar,
`f*.png` Vorhersage) – das ist genau das, was zum Webspace hochgeladen wird.

---

## Deployment

1. **GitHub Actions** (`.github/workflows/inca.yml`) führt `python build.py` aus und
   erzeugt das Verzeichnis `site/`.
2. **FTP-Upload** via `SamKirkland/FTP-Deploy-Action` (Protokoll FTPS, `local-dir: ./site/`).

**Benötigte Repository-Secrets:**

| Secret | Bedeutung |
|---|---|
| `FTP_SERVER` | FTP-Hostname (METAhost) |
| `FTP_USERNAME` | FTP-Benutzer |
| `FTP_PASSWORD` | FTP-Passwort |
| `FTP_SERVER_DIR` | Zielordner auf dem Webspace |
| `METEOFRANCE_TOKEN` | Météo-France API-Key (AROME, nicht ablaufend) |

**Auslösung alle 5 Minuten:** GitHubs eigener `schedule`-Trigger ist unzuverlässig
(Verzögerungen, Aussetzer) und deaktiviert Workflows nach 60 Tagen ohne Commit.
Deshalb wird der Build **extern** über [cron-job.org](https://cron-job.org) angestoßen,
das alle 5 Minuten einen `workflow_dispatch` auslöst:

```
POST https://api.github.com/repos/<user>/<repo>/actions/workflows/inca.yml/dispatches
Header: Authorization: Bearer <Fine-grained PAT, Actions: write>
        Accept: application/vnd.github+json
        X-GitHub-Api-Version: 2022-11-28
Body:   {"ref":"main"}
```

Der Workflow braucht daher nur `workflow_dispatch` (kein `schedule`).

---

## Caching (kein Zeitstempel in der URL)

Damit Browser keine veralteten Daten zeigen, tragen **nur die Datenabrufe** ein
Token:

- `frames.json` wird mit `cache:'no-store'` geholt,
- die Radarbilder über `?v=<Build-Zeit>` (neuer Build = neue Bild-URL).

Diese Tokens stehen **nie in der Seitenadresse** – die geteilte URL bleibt sauber.
Der Reload-Knopf (⟳) lädt einfach neu, ohne die URL/Position zu verändern.

> **Hinweis:** Eine `.htaccess` mit `Header`/`Cache-Control`-Direktiven ist auf
> METAhost **nicht** möglich (der Server quittiert sie mit „403 Forbidden"). Sie wird
> daher bewusst **nicht** verwendet und auch nicht mit deployt. Die Token-Lösung oben
> erfüllt denselben Zweck.

---

## Änderungen vornehmen

- **Viewer ändern:** `index.html` bearbeiten und ins Repo legen (bzw. hochladen). Beim
  nächsten Build wird sie automatisch mit ausgeliefert.
- **Ortsdatensatz:** ist in `index.html` eingebettet (`window.PLACES`). Quelle:
  GeoNames/PLZ-Daten, bereinigt (Postfach-/Firmen-Spezial-PLZ entfernt).

### Wichtige Stellgrößen

**Im Workflow (Umgebungsvariablen für `build.py`):**

| Variable | Default | Bedeutung |
|---|---|---|
| `RADAR_FRAMES` | 24 | Radar-Frames (~2 h bei 5-Min-Takt) |
| `ICON_HOURS` | 30 | ICON-CH1 bis +X h (max 33) |
| `ICOND2_HOURS` | 12 | ICON-D2 bis +X h |
| `AROME_HOURS` | 12 | AROME bis +X h |
| `BLEND_MIN` | 60 | Radar-Verankerung der Vorhersage (Min) |
| `DISPLAY_FLOOR` | 0.3 | Werte darunter werden nicht eingefärbt (mm/h) |
| `EDGE_FADE` | 0.5 | weiche Kante knapp über der Untergrenze |
| `AROME_W` | 1.0 | Grundgewicht von AROME im Mittelwert |
| `AROME_FEATHER_PX` | 25 | Breite der weichen AROME-Ostkante (Pixel) |

**Im Viewer (`index.html`, JS-Konstanten):**

| Konstante | Default | Bedeutung |
|---|---|---|
| `CLOSE_KM` | 3 | bis hierher: Ortschaft anzeigen |
| `FAR_KM` | 12 | bis hierher: Gemeinde anzeigen, danach nichts |
| `maxZoom` | 13 | maximale Zoomstufe |

---

## Ideen / mögliche Erweiterungen

- 5-Minuten-Interpolation der Vorhersage (kosmetisch).
- ICON-Ensemble-Mittel statt Einzelmodell.
- **INCA-Nowcasting** (1 km / 5 min) von MeteoSchweiz, sobald als Open Data verfügbar –
  das wäre die eigentliche Qualitätssteigerung im Kurzfristbereich.

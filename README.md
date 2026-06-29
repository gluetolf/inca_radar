# Niederschlagsradar Schweiz (LANDI-Stil)

Animierte, fensterfüllende Niederschlagskarte für die Schweiz: **gemessenes Radar
(Vergangenheit)** nahtlos verbunden mit einer **Modell-Vorhersage (Zukunft)** auf
einer einzigen Leaflet-Karte. Wird als statische Seite alle 10 Minuten automatisch
neu gebaut und per FTP veröffentlicht.

**Live:** https://eigermaker.ch/radar/

---

## Überblick: Woher die Daten kommen

| Zeitraum | Quelle | Auflösung | Takt |
|---|---|---|---|
| **Vergangenheit → jetzt** | MeteoSchweiz Radar `ch.meteoschweiz.ogd-radar-precip` (RZC, ODIM-HDF5) | 1 km | 5 Min |
| **Zukunft** | **Mittelwert** aus drei Modellen (siehe unten) | 1–2,2 km | 15 Min |

Die Zukunft ist ein **gewichtetes Mittel pro Bildpunkt** aus:

| Modell | Betreiber | Auflösung | Reichweite | Bezug |
|---|---|---|---|---|
| **ICON-CH1** | MeteoSchweiz (STAC, GRIB2) | 1 km, stündlich | bis +30 h | TOT_PREC |
| **ICON-D2** | DWD (opendata, GRIB2) | 2,2 km, 15-Min | bis +12 h | TOT_PREC |
| **AROME France HD** | Météo-France (WCS, GRIB2) | 1,3 km, stündlich | bis +12 h (von +42 h verfügbar) | TOTAL_PRECIPITATION |

**Kombinationsregel:** Je Bildpunkt wird über alle Modelle gemittelt, die dort einen
Wert liefern. Wo nur zwei (oder eines) Daten haben, wird eben aus diesen gemittelt –
es gibt also keine harte Kante, wo ein Modell endet. CH1 und AROME (stündlich) werden
linear auf 15-Minuten-Schritte interpoliert, damit jeder Animationsschritt denselben
Charakter hat (kein „Pulsieren"). ICON-D2 liefert die 15 Minuten nativ.

---

## Wichtige Eigenschaften

- **Weicher AROME-Rand.** AROME deckt Frankreich und Umland ab und reicht nach
  Westen/Norden/Süden bis zum Kartenrand. Nur im Osten (über Österreich) endet sein
  Modellgebiet. Dort läuft AROMEs Gewicht über einen schmalen Streifen sanft auf 0 –
  das Ergebnis wird stufenlos wieder zum CH1+D2-Mittel, **ohne sichtbare Kante**.
- **Radar-Verankerung.** In den ersten 60 Minuten der Vorhersage wird zum letzten
  echten Radarbild hin übergeblendet, damit der Übergang Messung → Prognose fließt.
- **Anzeige-Untergrenze 0,3 mm/h.** Sehr leichter Niederschlag (< 0,3 mm/h) wird
  **nicht** eingefärbt, damit keine unrealistisch großen, blassen Flächen entstehen.
  Gilt einheitlich für Radar und Prognose.
- **Robuste Laufauswahl.** Es wird jeweils der neueste, **ausreichend veröffentlichte**
  Modelllauf genommen (frisch gestartete, halb-publizierte Läufe werden übersprungen):
  ICON-CH1 ≥ 12 Vorlaufzeiten, ICON-D2 ≥ 6 Dateien, AROME ≥ 6 Leads.
- **Selbstheilend.** Fällt ein Modell aus, läuft der Build mit den übrigen weiter – die
  Karte ist nie leer.

---

## Farbskala

Diskrete Stufen wie beim klassischen Radar. Schwelle = obere Grenze der Stufe (mm/h):

| von–bis (mm/h) | Farbe |
|---|---|
| < 0,3 | (transparent, nicht angezeigt) |
| 0,3 – 1 | Blau |
| 1 – 2 | Türkis |
| 2 – 5 | Grün |
| 5 – 10 | Gelb |
| 10 – 20 | Orange |
| 20 – 50 | Rot |
| > 50 | Magenta |

Die Skala in `inca_core.py` (`SCALE`) und im Viewer (`SCALE_JS` in `index.html`)
müssen übereinstimmen, weil die Punkt-Mengenanzeige die Pixelfarbe zurückrechnet.

---

## Viewer (`index.html`)

- Heller/dunkler Kartenstil, Play/Scrubber/Tempo (langsam/normal/schnell)
- „Jetzt"-Trennlinie zwischen Vergangenheit und Vorhersage; Start 30 Min vor jetzt
- Städtebeschriftung (Interlaken grün hervorgehoben)
- **Punkt-Mengenanzeige:** Karte antippen → Chip mit mm/h-Bereich am gewählten Ort
- „Kein Niederschlag"-Hinweis, weiche Überblendung zwischen Bildern (Crossfade)
- **⟳ Neu laden** (umgeht den Browser-Cache), **⌖ Standort** (Geolocation)
- Cache-Buster an den Bild-URLs, damit immer die frischen Karten geladen werden

---

## Aufbau / Dateien

| Datei | Zweck |
|---|---|
| `inca_core.py` | Kernlogik: Radar lesen, ICON-CH1/ICON-D2/AROME holen, reprojizieren, einfärben |
| `build.py` | Ablauf: Radar (Vergangenheit) + 3-Modell-Mittel (Zukunft) → `site/` |
| `index.html` | Leaflet-Viewer (wird beim Build nach `site/` kopiert) |
| `requirements.txt` | Python-Abhängigkeiten |
| `.github/workflows/inca.yml` | Automatik: Build + FTP-Upload |
| `Data4Web_Legend_PLZ.csv` | nur für den optionalen data4web-Notfall-Fallback |

**Gemeinsames Raster:** EPSG:4326, West/Ost/Süd/Nord = 2,6 / 12,5 / 43,6 / 49,5 Grad,
0,01° Auflösung. Alle Quellen werden darauf reprojiziert.

---

## Deployment

GitHub Actions baut die Seite und lädt sie per FTP zu METAhost hoch.

- **Auslöser:** alle 10 Minuten (Cron) + manuell („Run workflow").
- **Ablauf:** `python build.py` erzeugt `site/` (index.html, frames.json, PNGs) →
  Upload via FTP-Deploy-Action.

> **Hinweis:** GitHub deaktiviert geplante Workflows nach 60 Tagen ohne Commit.
> Wenn die Automatik stoppt, im Repo einen kleinen Commit machen (oder einmal
> „Run workflow" drücken).

### Benötigte GitHub-Secrets

| Secret | Zweck |
|---|---|
| `FTP_SERVER` | FTP-Host |
| `FTP_USERNAME` | FTP-Benutzer |
| `FTP_PASSWORD` | FTP-Passwort |
| `FTP_SERVER_DIR` | Zielverzeichnis auf dem Server |
| `METEOFRANCE_TOKEN` | **Dauerhafter** Météo-France API-Key (für AROME) |

**Météo-France API-Key:** Im Portal https://portail-api.meteofrance.fr das
„AROME-Modell (v1.0)" (mit WCS) abonnieren → „Générer Token" → Typ **API Key** mit
Dauer **0** (läuft nicht ab) → den langen Schlüssel als Secret `METEOFRANCE_TOKEN`
hinterlegen. Der Code hängt ihn als Header `apikey` an jede WCS-Anfrage; eine
Token-Erneuerung ist nicht nötig.

Im Workflow muss der Build-Schritt das Secret durchreichen:

    - name: Radardaten holen und Karten bauen
      env:
        METEOFRANCE_TOKEN: ${{ secrets.METEOFRANCE_TOKEN }}
      run: python build.py

---

## Einstellbare Variablen (Umgebungsvariablen)

Alles hat sinnvolle Standardwerte; setzen ist optional (z. B. im Workflow unter `env:`).

| Variable | Standard | Wirkung |
|---|---|---|
| `DISPLAY_FLOOR` | `0.3` | Anzeige-Untergrenze in mm/h; darunter transparent |
| `AROME_W` | `1.0` | Grundgewicht von AROME im Mittel (kleiner = weniger Einfluss) |
| `AROME_FEATHER_PX` | `25` | Breite des weichen AROME-Ostrands in Pixeln (~0,25°) |
| `AROME_HOURS` | `12` | AROME-Vorhersage bis +X h |
| `ICON_HOURS` | `30` | ICON-CH1-Vorhersage bis +X h (begrenzt die Zeitachse) |
| `ICOND2_HOURS` | `12` | ICON-D2-Vorhersage bis +X h |
| `BLEND_MIN` | `60` | Dauer der Radar-Verankerung der Vorhersage (Minuten) |
| `RADAR_FRAMES` | `24` | Anzahl Radarbilder (Vergangenheit), 24 × 5 Min = 2 h |
| `RADAR_COLLECTION` | `ch.meteoschweiz.ogd-radar-precip` | Radar-Datensatz |
| `ICON_COLLECTION` | `ch.meteoschweiz.ogd-forecasting-icon-ch1` | ICON-CH1-Datensatz |
| `AROME_VAR` | `TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE` | AROME-Variable |
| `INCA_STAC` | `https://data.geo.admin.ch/api/stac/v1` | MeteoSchweiz STAC-API |
| `DWD_ICOND2_BASE` | `https://opendata.dwd.de/weather/nwp/icon-d2/grib` | DWD opendata |

---

## Lokal testen

    pip install -r requirements.txt
    export METEOFRANCE_TOKEN="…"   # für AROME (sonst läuft es ohne AROME mit CH1+D2)
    python build.py                # erzeugt ./site/
    # site/index.html im Browser öffnen

Hinweis: Der Build braucht echten Internetzugang zu data.geo.admin.ch,
opendata.dwd.de und public-api.meteofrance.fr.

---

## Lizenzen / Quellenangabe

Pflichtangaben (im Viewer-Footer und in der Kartenattribution enthalten):

- **MeteoSchweiz** – „Quelle: MeteoSchweiz" (Radar, ICON-CH1)
- **Deutscher Wetterdienst (DWD)** – CC BY 4.0, „Quelle: Deutscher Wetterdienst" (ICON-D2)
- **Météo-France** – © Météo-France (AROME)
- Karten: © OpenStreetMap, © CARTO

---

## Ausblick (optional)

- **INCA-Nowcasting (MeteoSchweiz, 1 km / 5 Min)**, sobald als Open Data verfügbar –
  das wäre das eigentliche Qualitäts-Upgrade für die ersten Stunden.
- 5-Minuten-Interpolation der Vorhersage (rein kosmetisch).
- ICON-Ensemble-Mittel für noch mehr Fläche/Robustheit.

# Niederschlagsradar Schweiz

Animiertes Niederschlagsradar im Stil der MeteoSchweiz-/LANDI-Karte, das
**Messung (Vergangenheit)** und **Vorhersage (Zukunft)** nahtlos auf einer
einzigen Karte zeigt. Die Seite ist eine rein statische Website, die alle
~10 Minuten von einem GitHub-Actions-Workflow neu erzeugt und per FTP
veröffentlicht wird.

**Live:** https://eigermaker.ch/radar/

---

## Funktionsweise

Die Animation besteht aus zwei aneinandergehängten Teilen, beide auf demselben
WGS84-Raster und mit derselben Radar-Farbskala:

| Zeitbereich | Quelle | Auflösung | Format |
|-------------|--------|-----------|--------|
| **Vergangenheit → jetzt** | MeteoSchweiz-Radar `ch.meteoschweiz.ogd-radar-precip` (RZC) | 1 km, **5 Min** | ODIM-HDF5 |
| **Zukunft (Nahbereich)** | **Mittelwert** aus ICON-CH1 + ICON-D2 | 1–2 km, **15 Min** | GRIB2 |
| **Zukunft (späterer Verlauf)** | ICON-CH1 allein | 1 km, stündlich | GRIB2 |
| **Notfall-Rückfall** | MeteoSchweiz-Lokalprognose `ch.meteoschweiz.ogd-local-forecasting` | Punktraster | CSV |

### Vorhersage = selbst gerechneter Mehrmodell-Zusammenzug

Die Zukunft wird aus zwei frei verfügbaren Modellen kombiniert (analog zu dem,
was Open-Meteo intern tut, hier aber selbst gerechnet):

- **ICON-CH1** — MeteoSchweiz, 1 km, stündlich, bis +33 h. Höchste räumliche
  Auflösung, die für die ganze Schweiz offen verfügbar ist.
- **ICON-D2** — Deutscher Wetterdienst (DWD), 2,2 km, **15-minütig**, bis +12 h
  (konfigurierbar). Bringt die feine Zeitauflösung.

**Kombinationsregel (pro Zeitpunkt und pro Bildpunkt):**

1. Liefern **beide** Modelle einen Wert → **Mittelwert**.
2. Liefert nur **eines** einen Wert → genau **dieses** (Fallback).
3. Liefert **keines** → transparent.

Fällt eine ganze Quelle aus (z. B. DWD nicht erreichbar), läuft die Vorhersage
automatisch nur mit dem anderen Modell weiter; fallen beide aus, greift die
data4web-Lokalprognose als letzter Rückfall.

Zwei Glättungen sorgen für einen ruhigen Verlauf:

- **CH1 auf 15 Minuten interpoliert** — damit jeder Schritt denselben Charakter
  hat und kein stündliches „Pulsieren" entsteht.
- **Radar-Verankerung am Übergang** — in der ersten Stunde wird die Vorhersage
  zum letzten gemessenen Radarbild hin überblendet (Standard 60 Min, per
  `BLEND_MIN` einstellbar), damit die Naht zwischen Messung und Modell nicht
  springt.

---

## Datenfluss

```
GitHub Actions (Cron ~alle 10 Min)
        │
        ▼
   python build.py
        │  ├─ Radar:   data.geo.admin.ch  (STAC → ODIM-HDF5)
        │  ├─ ICON-CH1: data.geo.admin.ch (STAC → GRIB2)
        │  └─ ICON-D2:  opendata.dwd.de    (GRIB2 .bz2)
        ▼
   ./site/  (index.html, frames.json, r*.png, f*.png)
        │
        ▼  FTP-Deploy (SamKirkland/FTP-Deploy-Action)
        ▼
   METAhost  →  https://eigermaker.ch/radar/
```

Wichtig: Der **GitHub-Actions-Runner** hat vollen Internetzugang und erreicht
`data.geo.admin.ch` und `opendata.dwd.de`. Der Build läuft also nur dort live —
lokal lassen sich die Renderer mit Beispieldateien testen (siehe unten).

---

## Projektdateien

| Datei | Zweck |
|-------|-------|
| `build.py` | Orchestriert den Lauf: Radar + Vorhersage kombinieren, PNGs und `frames.json` erzeugen, `index.html` kopieren. |
| `inca_core.py` | Kernlogik: Datenabruf (STAC/DWD), Dekodierung (HDF5/GRIB2), Umprojektion auf das gemeinsame Raster, Farbskala. |
| `index.html` | Der Viewer (Leaflet): Animation, Bedienung, Punkt-Abfrage, Standort, Cache-Busting. |
| `requirements.txt` | Python-Abhängigkeiten. |
| `Data4Web_Legend_PLZ.csv` | PLZ→Koordinaten für den data4web-Rückfall. |
| `.github/workflows/inca.yml` | Workflow: Build + FTP-Deploy. |

Erzeugte Ausgaben (nicht eingecheckt, liegen in `./site/`):
`index.html`, `frames.json`, `r00.png…` (Radar), `f00.png…` (Vorhersage).

---

## Farbskala (mm/h)

Diskrete Stufen wie beim klassischen Radar, von leichtem Niederschlag (hellblau)
bis Gewitter (rot/magenta):

`0,05 · 0,3 · 1 · 2 · 5 · 10 · 20 · 50 · >50`  mm/h

Definiert in `inca_core.py` (`SCALE`) und gespiegelt im Viewer (`SCALE_JS`) —
**beide müssen übereinstimmen**, sonst stimmt die Mengenanzeige am angetippten
Punkt nicht.

---

## Viewer-Funktionen

- Abspielen / Pause / Bild-für-Bild, Geschwindigkeit (langsam/normal/schnell)
- Start 30 Minuten vor „jetzt", „Jetzt"-Trenner in der Zeitleiste
- Hell-/Dunkel-Modus, Städtebeschriftung, Legende
- **Punkt-Abfrage:** Karte antippen → Niederschlagsmenge an diesem Ort für die
  gewählte Zeit (aus der Pixelfarbe ausgelesen)
- **„kein Niederschlag"-Hinweis** auf trockenen Bildern
- **Standort-Knopf** (⌖): zentriert per Geolocation auf den eigenen Standort
- **Refresh-Knopf** (⟳): erzwingt einen Hard-Reload (umgeht den Browser-Cache)
- Weiche Überblendung zwischen den Bildern

### Cache-Busting

Die Bilddateien heißen bei jedem Build gleich (`r00.png`, `f00.png` …). Damit
mobile Browser nicht alte Bilder aus dem Cache zeigen, hängt der Viewer an jede
Bild-URL ein Versions-Kürzel `?v=<Build-Zeit>` (Feld `v` in `frames.json`). Die
Seite frischt sich dadurch beim automatischen 5-Minuten-Update von selbst auf;
der ⟳-Knopf erzwingt zusätzlich einen vollständigen Hard-Reload.

---

## Deployment (GitHub Actions + FTP)

Der Workflow `.github/workflows/inca.yml` installiert die Abhängigkeiten, führt
`python build.py` aus und lädt `./site/` per FTP hoch. Benötigte
**Repository-Secrets** (Settings → Secrets and variables → Actions):

| Secret | Bedeutung |
|--------|-----------|
| `FTP_SERVER` | FTP-Host (z. B. `3db.ch`) |
| `FTP_USERNAME` | FTP-Benutzer |
| `FTP_PASSWORD` | FTP-Passwort |
| `FTP_SERVER_DIR` | Zielverzeichnis (z. B. `/eigermaker.ch/radar/`) |

Manuell auslösen: Tab **Actions** → Workflow → **Run workflow**.

---

## Konfiguration (Umgebungsvariablen)

Alle optional, mit sinnvollen Standardwerten:

| Variable | Standard | Wirkung |
|----------|----------|---------|
| `RADAR_FRAMES` | `24` | Anzahl Radarbilder (~2 h bei 5-Min-Takt) |
| `ICON_HOURS` | `30` | ICON-CH1 bis +X h (max. 33) |
| `ICOND2_HOURS` | `12` | ICON-D2 (15-Min) bis +X h |
| `BLEND_MIN` | `60` | Dauer der Radar-Verankerung der Vorhersage (Min) |
| `FC_HOURS` | `24` | Stunden für den data4web-Rückfall |
| `INCA_SITE` | `site` | Ausgabeverzeichnis |
| `ICON_COLLECTION` | `ch.meteoschweiz.ogd-forecasting-icon-ch1` | ICON-CH1-STAC-Collection |
| `DWD_ICOND2_BASE` | `https://opendata.dwd.de/weather/nwp/icon-d2/grib` | DWD-Basis-URL |
| `INCA_STAC` | `https://data.geo.admin.ch/api/stac/v1` | STAC-API |

---

## Lokaler Test / Offline

Der Live-Abruf braucht offenes Internet (am besten im Actions-Runner). Lokal
lassen sich die Renderer mit Beispieldateien prüfen:

```bash
pip install -r requirements.txt
python build.py --radar beispiel.h5 --fc beispiel.csv
# Ergebnis in ./site/  (index.html im Browser öffnen)
```

`eccodes` benötigt die ecCodes-Bibliothek (über das Pip-Paket meist enthalten;
unter Debian/Ubuntu sonst `apt install libeccodes0`).

---

## Datenquellen & Lizenzen

- **MeteoSchweiz** — Radar und ICON-CH1, Open Government Data, frei mit
  Quellenangabe „Quelle: MeteoSchweiz".
- **Deutscher Wetterdienst (DWD)** — ICON-D2, Open Data, **CC BY 4.0**,
  Quellenangabe „Quelle: Deutscher Wetterdienst".
- Kartenhintergrund: © OpenStreetMap-Mitwirkende, © CARTO.

Die Quellenangaben sind im Viewer (Kartenattribution und Untertitel) hinterlegt.

---

## Grenzen & Ausblick

- **Räumlich** ist 1 km (ICON-CH1) das offene Maximum für die ganze Schweiz —
  feiner geht es derzeit frei nicht.
- **5-Minuten-Vorhersage** wäre nur durch zeitliche Interpolation der 15-Min-
  Schritte möglich (kosmetisch, keine echten Zusatzdaten). Echte 5-Min-Felder
  liefert nur radar-/beobachtungsbasiertes Nowcasting.
- **MeteoSchweiz-INCA-Nowcasting** (1 km / 5 Min, beobachtungsgestützt) wäre der
  große Sprung in beiden Dimensionen — als offene Daten aber noch nicht
  verfügbar (frühestens ~2026). Sobald freigegeben: idealer Ersatz für den
  Nahbereich.
- **Météo-France AROME** (1,3 km, 15 Min) könnte den Westen schärfen — deckt
  aber nur die Westschweiz ab (Stufe 2).

---

*Privates Hobbyprojekt. Keine amtliche Wetterwarnung — im Ernstfall gelten die
offiziellen Angaben von MeteoSchweiz.*

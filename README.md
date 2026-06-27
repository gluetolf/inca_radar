# Swiss Precipitation Radar (INCA)

An animated precipitation radar for Switzerland, showing **now and the next 6 hours**
in 5-minute steps. It uses the official **INCA nowcasting data from MeteoSwiss**
(open government data).

The conversion runs automatically in the cloud (GitHub Actions): every ~10 minutes it
fetches the latest INCA run, turns it into coloured, georeferenced images, and uploads
the result to a static web host over FTP. The host only serves static files — no
server-side code, no admin rights, and your own computer never has to be running.

**Data source: MeteoSwiss** (Open Government Data — free to reuse with attribution).

## How it works

1. `build.py` downloads the latest INCA precipitation file (`RP`/`RR`, mm/h) from the
   MeteoSwiss STAC API at `data.geo.admin.ch`.
2. `inca_core.py` decodes the NetCDF-4/HDF5 grid, reprojects it from the Swiss
   coordinate system to WGS84, and renders one transparent PNG overlay per 5-minute
   step (0 h … +6 h), plus a small `frames.json` manifest.
3. The output folder `site/` (HTML viewer + `frames.json` + PNGs) is published via FTP.
4. `index.html` is a Leaflet map that animates the overlays, with a play/scrub
   timeline, intensity legend, and Swiss city labels.

Everything in `site/` is static, so any plain web host can serve it.

## Repository contents

- `index.html` — the radar viewer (front-end)
- `build.py` — fetches INCA and builds the `site/` folder
- `inca_core.py` — NetCDF → PNG conversion and STAC lookup
- `requirements.txt` — Python dependencies (used by CI, not by the web host)
- `.github/workflows/inca.yml` — the scheduled build + FTP deploy
- `RP_INCA_*.nc` — a sample INCA file for local testing (optional)

## Setup

1. **Create a public repository** and add the files above (the workflow goes to
   `.github/workflows/inca.yml`). A public repo gives unlimited free Actions minutes;
   credentials live in encrypted secrets, not in the code.

2. **Add repository secrets** (Settings → Secrets and variables → Actions):
   - `FTP_SERVER` — your host's FTP address
   - `FTP_USERNAME` — FTP user
   - `FTP_PASSWORD` — FTP password
   - `FTP_SERVER_DIR` — target folder on the host, **with a trailing slash**
     (e.g. `/httpdocs/radar/`)
   - `INCA_COLLECTION` — *optional*, the exact STAC collection id, only needed if
     auto-discovery fails

3. **Run it once** from the Actions tab (`Run workflow`), then open your site at the
   folder you deployed to (e.g. `https://example.com/radar/`). After that it updates
   on its own.

## Local test (optional)

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python build.py --file RP_INCA_202106280700.nc   # uses the bundled sample
cd site && python -m http.server 8000            # http://127.0.0.1:8000
```

## Configuration (environment variables)

- `INCA_STEP_MIN` — output time step in minutes (default `5`)
- `INCA_COLLECTION` — pin the STAC collection id (otherwise auto-discovered)
- `INCA_SITE` — output directory (default `site`)

## Notes

- **Schedule is best-effort.** GitHub runs scheduled jobs "as soon as possible";
  the 10-minute interval can stretch to 15–20 minutes and a run is occasionally
  skipped. Fine for a personal radar.
- **The STAC collection** for INCA may need pinning via `INCA_COLLECTION`. List all
  collections at `https://data.geo.admin.ch/api/stac/v1/collections` and search for
  "inca" / "nowcasting".
- **FTP vs FTPS.** The workflow uses `protocol: ftps`; switch to `ftp` in
  `inca.yml` if your host needs plain FTP.
- **`rasterio`** (with GDAL) is the heaviest dependency; it installs cleanly on the
  GitHub Ubuntu runners.

## Attribution & licence

Precipitation data © **MeteoSwiss**, provided as Open Government Data and free to
reuse provided the source is credited ("Source: MeteoSwiss"). Base map tiles ©
OpenStreetMap contributors, © CARTO. The attribution is shown on the map.

This is an independent project and is **not affiliated with or endorsed by MeteoSwiss**.

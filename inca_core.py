#!/usr/bin/env python3
"""
inca_core.py - Kernlogik fuer das kombinierte Niederschlagsradar.

Liefert die Bausteine, die build.py zusammensetzt. Alle Felder werden auf ein
gemeinsames WGS84-Raster (siehe DST_*) umprojiziert und mit derselben
Radar-Farbskala (SCALE) eingefaerbt, damit Messung und Vorhersage nahtlos
ineinander uebergehen.

Datenquellen:
  - Vergangenheit/jetzt : MeteoSchweiz-Radar (ODIM-HDF5, RZC, mm/h)   -> radar_grid / render_radar
  - Zukunft (1 km, stdl.): MeteoSchweiz ICON-CH1 (GRIB2, STAC)        -> icon_ch1_fields
  - Zukunft (2 km,15 Min): DWD ICON-D2 (GRIB2, Open Data)             -> icond2_fields
  - Rueckfall            : MeteoSchweiz-Lokalprognose (data4web CSV)  -> render_forecast
  - (ungenutzt, bereit)  : INCA-Nowcasting (NetCDF)                   -> render_nowcast

Den eigentlichen Mehrmodell-Zusammenzug (Mittelwert/Fallback) macht build.py;
hier werden nur die einzelnen Modelle zu Feldern {Zeit: Raster mm/h} aufbereitet.

Quellen: MeteoSchweiz (OGD) und Deutscher Wetterdienst (CC BY 4.0).
"""
import os, json, csv, glob, datetime as dt
import numpy as np
import h5py                                   # ODIM-HDF5 (Radar)
import rasterio
from rasterio.transform import Affine
from rasterio.warp import reproject, Resampling   # Umprojektion auf das Zielraster
from rasterio.crs import CRS
from PIL import Image
from pyproj import Transformer
from scipy.interpolate import griddata        # nur fuer den data4web-Rueckfall

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- gemeinsames Zielraster (WGS84/EPSG:4326), deckt die Radardomaene ab -----
# Alle Quellen werden hierauf umprojiziert, damit sie deckungsgleich sind.
DST_W, DST_E, DST_S, DST_N = 2.6, 12.5, 43.6, 49.5      # West, Ost, Sued, Nord (Grad)
DST_RES = 0.01                                          # Rastermaschung ~1,1 km
DW = int(round((DST_E - DST_W) / DST_RES))             # Breite in Pixeln
DH = int(round((DST_N - DST_S) / DST_RES))             # Hoehe in Pixeln
DST_TRANSFORM = Affine(DST_RES, 0, DST_W, 0, -DST_RES, DST_N)   # Pixel->Koordinate (Zeile 0 = Nord)
DST_CRS = CRS.from_epsg(4326)
BOUNDS = [[DST_S, DST_W], [DST_N, DST_E]]              # fuer Leaflet (imageOverlay)

# ---- Radar-Farbskala: mm/h -> RGBA --------------------------------------------
# Diskrete Stufen wie beim klassischen Radar (hellblau = leicht ... magenta = Gewitter).
# MUSS mit SCALE_JS in index.html uebereinstimmen (Punkt-Mengenanzeige).
# Schwelle = obere Grenze der Stufe; (R, G, B, Alpha).
SCALE = [
    (0.05, (165, 215, 255, 150)),
    (0.3,  (110, 175, 248, 170)),
    (1.0,  (45,  110, 225, 188)),
    (2.0,  (40,  180, 170, 200)),
    (5.0,  (95,  200,  80, 210)),
    (10.0, (230, 215,  70, 220)),
    (20.0, (240, 150,  55, 228)),
    (50.0, (225,  55,  50, 238)),
    (1e9,  (170,  25, 110, 246)),
]


def colorize(arr):
    """2D-Feld (mm/h, NaN = keine Daten) -> RGBA-Bild nach SCALE.
    Werte <= 0 und NaN bleiben transparent (Alpha 0)."""
    h, w = arr.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)         # Start: alles transparent
    a = np.nan_to_num(arr, nan=0.0)                    # NaN -> 0 (faellt in keine Stufe)
    prev = 0.0
    for thr, col in SCALE:                             # jede Stufe (prev, thr] einfaerben
        rgba[(a > prev) & (a <= thr)] = col
        prev = thr
    return rgba


# ===================== RADAR (ODIM-HDF5, RZC) =========================
def radar_grid(h5path):
    """RZC-ODIM-Datei -> (datetime_utc, Feld(DH,DW) mm/h auf dem gemeinsamen Raster)."""
    with h5py.File(h5path, "r") as f:
        data = f["dataset1/data1/data"][:].astype("float64")
        w = dict(f["dataset1/data1/what"].attrs)
        where = dict(f["where"].attrs)
        what = dict(f["what"].attrs)

    # Rohwerte -> physikalische Werte (mm/h); Sonderfaelle behandeln:
    gain = float(w.get("gain", 1.0)); offset = float(w.get("offset", 0.0))
    nodata = float(w.get("nodata", np.nan)); undetect = float(w.get("undetect", np.inf))
    vals = data * gain + offset
    if not np.isnan(nodata):
        vals[data == nodata] = np.nan        # kein Messwert -> transparent
    vals[np.isnan(data)] = np.nan
    if not np.isinf(undetect):
        vals[data == undetect] = 0.0         # gemessen, aber kein Niederschlag -> 0
    else:
        vals[np.isinf(data)] = 0.0

    # Quell-Projektion (LV95/somerc) und Eckkoordinaten aus der Datei lesen ...
    proj = where["projdef"].decode() if isinstance(where["projdef"], bytes) else where["projdef"]
    src_crs = CRS.from_proj4(proj)
    nx = int(where.get("xsize", data.shape[1])); ny = int(where.get("ysize", data.shape[0]))
    # ... die Ecken stehen geografisch drin -> in LV95-Meter umrechnen fuer das Affine
    t = Transformer.from_crs(4326, src_crs, always_xy=True)
    ul_e, ul_n = t.transform(float(where["UL_lon"]), float(where["UL_lat"]))   # oben links
    lr_e, lr_n = t.transform(float(where["LR_lon"]), float(where["LR_lat"]))   # unten rechts
    dx = (lr_e - ul_e) / nx
    dy = (ul_n - lr_n) / ny
    src_transform = Affine(dx, 0, ul_e, 0, -dy, ul_n)   # ODIM: Zeile 0 = Nord

    # auf das gemeinsame WGS84-Raster umprojizieren
    dst = np.full((DH, DW), np.nan, dtype="float32")
    reproject(source=vals.astype("float32"), destination=dst,
              src_transform=src_transform, src_crs=src_crs,
              dst_transform=DST_TRANSFORM, dst_crs=DST_CRS,
              resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)

    # Zeitstempel (UTC) aus den Metadaten
    d = (what.get("date") or where.get("date"))
    tm = (what.get("time"))
    d = d.decode() if isinstance(d, bytes) else d
    tm = tm.decode() if isinstance(tm, bytes) else tm
    when = dt.datetime.strptime(d + tm[:4], "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)
    return when, dst


def render_radar(h5path, out_png):
    """RZC-ODIM-Datei -> eingefaerbtes PNG. Rueckgabe: (datetime_utc, max_mmh)."""
    when, dst = radar_grid(h5path)
    Image.fromarray(colorize(dst), "RGBA").save(out_png)
    mxv = np.nanmax(dst)
    return when, (round(float(mxv), 1) if np.isfinite(mxv) else 0.0)


# ===================== LOKALPROGNOSE (data4web CSV) ===================
_PLZ_LONLAT = None

def _load_plz_lonlat():
    global _PLZ_LONLAT
    if _PLZ_LONLAT is not None:
        return _PLZ_LONLAT
    path = os.path.join(HERE, "Data4Web_Legend_PLZ.csv")
    tr = Transformer.from_crs(2056, 4326, always_xy=True)
    m = {}
    with open(path, newline="", encoding="latin-1") as f:
        for row in csv.DictReader(f, delimiter=";"):
            try:
                plz = int(row["POSTAL_CODE_ID"])
                if plz == -1:
                    continue
                E = float(row["E_COORD_NU"]); N = float(row["N_COORD_NU"])
            except (ValueError, KeyError):
                continue
            lon, lat = tr.transform(E, N)
            m[plz] = (lon, lat)
    _PLZ_LONLAT = m
    return m


def parse_forecast_csv(csv_path, max_hours=24):
    """data4web-Niederschlags-CSV lesen -> dict: valid_time(utc) -> (lons,lats,vals).
    Nur PLZ-Punkte (LocationType 2), nur die naechsten max_hours Stunden."""
    plz = _load_plz_lonlat()
    by_time = {}
    with open(csv_path, newline="") as f:
        r = csv.reader(f, delimiter=";"); next(r, None)
        for parts in r:
            if len(parts) < 4:
                continue
            lid, ltype, date, val = parts[0], parts[1], parts[2], parts[3]
            if ltype != "2":
                continue
            try:
                pid = int(lid); v = float(val)
            except ValueError:
                continue
            if pid not in plz:
                continue
            by_time.setdefault(date, []).append((pid, v))
    # in (lons,lats,vals) umwandeln, zeitlich begrenzen
    out = {}
    for date, items in by_time.items():
        try:
            when = dt.datetime.strptime(date, "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        lons = np.array([plz[p][0] for p, _ in items])
        lats = np.array([plz[p][1] for p, _ in items])
        vals = np.array([v for _, v in items], dtype="float32")
        out[when] = (lons, lats, vals)
    return out


_GX = _GY = _GPTS = None

def render_forecast(lons, lats, vals, out_png, max_dist_deg=0.07):
    """Punktwerte auf das gemeinsame Raster interpolieren -> PNG.
    Zellen, die weiter als max_dist_deg vom naechsten Datenpunkt entfernt sind,
    werden ausgeblendet (schneidet die Flaeche auf die Schweiz zu)."""
    global _GX, _GY, _GPTS
    if _GX is None:
        gx = DST_W + (np.arange(DW) + 0.5) * DST_RES
        gy = DST_N - (np.arange(DH) + 0.5) * DST_RES
        _GX, _GY = np.meshgrid(gx, gy)
        _GPTS = np.column_stack([_GX.ravel(), _GY.ravel()])
    grid = griddata((lons, lats), vals, (_GX, _GY), method="linear")  # ausserhalb Huelle -> NaN
    # Zuschnitt: Zellen ohne nahen Datenpunkt verwerfen
    from scipy.spatial import cKDTree
    tree = cKDTree(np.column_stack([lons, lats]))
    dist, _ = tree.query(_GPTS, k=1)
    grid.ravel()[dist > max_dist_deg] = np.nan
    Image.fromarray(colorize(grid), "RGBA").save(out_png)
    mx = float(np.nanmax(vals)) if len(vals) and np.isfinite(np.nanmax(vals)) else 0.0
    return round(mx, 1)


# ===================== ICON-CH1 (GRIB2, Modellvorhersage) ============
ICON_COLLECTION = os.environ.get("ICON_COLLECTION", "ch.meteoschweiz.ogd-forecasting-icon-ch1")


def _post_json(url, body, timeout=120):
    import urllib.request, json as _json
    req = urllib.request.Request(url, data=_json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _json.load(r)


def _iso_dur_hours(s):
    """ISO-8601-Dauer (z. B. P0DT3H0M0S) -> Stunden (float)."""
    import re
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return None
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return d * 24 + h + mi / 60.0 + se / 3600.0


def _icon_lonlat(constants_path):
    """CLON/CLAT (Zellmittelpunkte) aus der ICON-Konstantendatei lesen (Grad)."""
    import eccodes as ec
    lon = lat = None
    f = open(constants_path, "rb")
    while True:
        gid = ec.codes_grib_new_from_file(f)
        if gid is None:
            break
        try:
            sn = ec.codes_get(gid, "shortName").lower()
        except Exception:
            sn = ""
        vals = ec.codes_get_values(gid)
        if "lon" in sn or "clon" in sn:
            lon = np.array(vals)
        elif "lat" in sn or "clat" in sn:
            lat = np.array(vals)
        ec.codes_release(gid)
    f.close()
    if lon is None or lat is None:
        raise RuntimeError("CLON/CLAT in der Konstantendatei nicht gefunden")
    if np.nanmax(np.abs(lon)) < 6.3 and np.nanmax(np.abs(lat)) < 1.6:  # Radiant -> Grad
        lon = np.degrees(lon); lat = np.degrees(lat)
    return lon, lat


def _icon_values(grib_path):
    """Werte der ersten GRIB-Nachricht (TOT_PREC, kumuliert in mm)."""
    import eccodes as ec
    f = open(grib_path, "rb")
    gid = ec.codes_grib_new_from_file(f)
    vals = np.array(ec.codes_get_values(gid))
    ec.codes_release(gid); f.close()
    return vals


def _icon_constants_href():
    data = _get_json(f"{STAC}/collections/{ICON_COLLECTION}/assets")
    assets = data.get("assets", data)
    items = assets.items() if isinstance(assets, dict) else [(a.get("id", ""), a) for a in assets]
    keys = []
    for k, a in items:
        href = a.get("href", "") if isinstance(a, dict) else ""
        keys.append(k)
        blob = (str(k) + " " + str(href)).lower()
        if "horizontal" in blob and ".grib2" in blob:
            return href
    raise RuntimeError("Horizontale Konstantendatei nicht gefunden. Asset-IDs: %s" % keys[:25])


_ICON_IDX = _ICON_MASK = None

def icon_ch1_fields(tmp, max_hours=30, now=None):
    """Neueste ICON-CH1-TOT_PREC-Vorhersage -> dict {datetime_utc: Feld(DH,DW) mm/h}.
    Stuendlich, entkumuliert, geglaettet, ausserhalb der Domaene NaN."""
    global _ICON_IDX, _ICON_MASK
    from scipy.spatial import cKDTree
    from scipy.ndimage import gaussian_filter
    # 1) Gitter-Geometrie aus der (statischen) Konstanten-Datei: lon/lat je Zelle
    chref = _icon_constants_href()
    cfile = download(chref, os.path.join(tmp, "icon_const.grib2"))
    lon, lat = _icon_lonlat(cfile)
    print(f"ICON-CH1-Gitter: {len(lon)} Zellen, lon {lon.min():.2f}..{lon.max():.2f}, lat {lat.min():.2f}..{lat.max():.2f}")

    # STAC-Suche nach den TOT_PREC-Assets (deterministischer Lauf)
    def _search(extra):
        body = {"collections": [ICON_COLLECTION], "forecast:variable": "TOT_PREC",
                "forecast:perturbed": False, "limit": 100}
        body.update(extra)
        return _post_json(f"{STAC}/search", body).get("features", [])

    # 2) Neuesten verfuegbaren Lauf finden. Die API unterstuetzt weder "latest"
    #    noch Sortierung, daher die Lauf-Zeitpunkte im 3-Stunden-Raster rueckwaerts
    #    durchprobieren (15:00, 12:00, ...) und den ersten veroeffentlichten nehmen.
    feats, chosen = [], None
    base = (now or dt.datetime.now(dt.timezone.utc)).replace(minute=0, second=0, microsecond=0)
    base = base - dt.timedelta(hours=base.hour % 3)
    for k in range(0, 13):                                  # bis ~36 h zurueck
        ref = base - dt.timedelta(hours=3 * k)
        refiso = ref.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            fs = _search({"forecast:reference_datetime": refiso})
        except Exception:
            fs = []
        if fs:
            feats, chosen = fs, refiso
            break
    if not feats:
        feats = _search({})                                # Notnagel
    # 3) (Referenz, Vorlaufzeit, GRIB-URL) je Asset sammeln
    recs = []
    for ft in feats:
        p = ft.get("properties", {})
        ref = p.get("forecast:reference_datetime") or p.get("datetime")
        hz = _iso_dur_hours(p.get("forecast:horizon", ""))     # ISO-Dauer -> Stunden
        href = next((a.get("href") for a in ft.get("assets", {}).values()
                     if ".grib2" in str(a.get("href", "")).lower()), None)
        if ref and hz is not None and href:
            recs.append((ref, hz, href))
    if not recs:
        raise RuntimeError(f"Keine ICON-CH1-TOT_PREC-Assets gefunden (Features: {len(feats)})")
    latest = max(r[0] for r in recs)                       # nur den neuesten Lauf nehmen
    series = sorted((hz, href) for ref, hz, href in recs if ref == latest)
    ref_dt = dt.datetime.fromisoformat(latest.replace("Z", "+00:00"))
    print(f"ICON-CH1-Referenz: {latest}  Vorlaufzeiten: {len(series)}")

    # 4) Zuordnung Dreiecksgitter-Zelle -> Zielpixel (einmalig, dann zwischengespeichert).
    #    Fuer jedes Zielpixel die naechste ICON-Zelle suchen (Nearest-Neighbor via KDTree).
    if _ICON_IDX is None:
        gx = DST_W + (np.arange(DW) + 0.5) * DST_RES
        gy = DST_N - (np.arange(DH) + 0.5) * DST_RES
        GX, GY = np.meshgrid(gx, gy)
        tree = cKDTree(np.column_stack([lon, lat]))
        dist, idx = tree.query(np.column_stack([GX.ravel(), GY.ravel()]), k=1)
        _ICON_IDX = idx
        _ICON_MASK = dist > 0.02                           # weiter als ~2 km -> ausserhalb der Domaene

    # 5) Pro Vorlaufzeit: TOT_PREC ist aufsummiert -> entkumulieren (Differenz),
    #    auf das Raster legen, glaetten, ausserhalb maskieren.
    fields = {}; prev = None
    for hz, href in series:
        if hz > max_hours:
            break
        cur = _icon_values(download(href, os.path.join(tmp, f"icon_{int(hz):03d}.grib2")))
        precip = cur - prev if prev is not None else cur.copy()    # mm in dieser Stunde = mm/h
        prev = cur
        if hz <= 0:
            continue                                       # +0 h ist nur die Nulllinie
        precip = np.clip(precip, 0, None)                  # numerisches Rauschen abschneiden
        field = precip[_ICON_IDX].astype("float32").reshape(DH, DW)
        field = gaussian_filter(field, sigma=1.1)          # weiche Verlaeufe (App-Look)
        field = field.reshape(-1); field[_ICON_MASK] = np.nan
        fields[ref_dt + dt.timedelta(hours=hz)] = field.reshape(DH, DW)
    return fields


# ===================== ICON-D2 (DWD Open Data, 15-Min) ===============
DWD_ICOND2_BASE = os.environ.get("DWD_ICOND2_BASE", "https://opendata.dwd.de/weather/nwp/icon-d2/grib")


def _http(url, timeout=120):
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def icond2_fields(tmp, max_hours=12, now=None):
    """Neueste DWD-ICON-D2-TOT_PREC (regulaeres Gitter, 15-Min) -> dict {datetime: Feld(DH,DW) mm/h}.
    Entkumuliert und auf mm/h umgerechnet; ausserhalb der Domaene NaN."""
    import bz2, re, eccodes as ec
    from scipy.ndimage import gaussian_filter
    # 1) Neuesten veroeffentlichten DWD-Lauf finden. Die Lauf-Verzeichnisse heissen
    #    nur nach der Stunde (00,03,...,21); das Datum steht im Dateinamen. Daher die
    #    3-Stunden-Laeufe rueckwaerts durchgehen und das Verzeichnis-Listing pruefen.
    base = (now or dt.datetime.now(dt.timezone.utc)).replace(minute=0, second=0, microsecond=0)
    base = base - dt.timedelta(hours=base.hour % 3)
    chosen, files = None, []
    for k in range(0, 6):                                   # bis ~15 h zurueck (Publikationslag)
        run = base - dt.timedelta(hours=3 * k)
        url = f"{DWD_ICOND2_BASE}/{run:%H}/tot_prec/"
        datestr = run.strftime("%Y%m%d%H")
        try:
            html = _http(url).decode("utf-8", "ignore")     # Verzeichnis-Listing (HTML)
        except Exception:
            continue
        names = re.findall(r'href="([^"]+)"', html)
        # regulaeres Lat/Lon-Gitter (einfacher als das Dreiecksgitter), tot_prec, dieser Lauf
        sel = [n for n in names if "regular-lat-lon" in n and "tot_prec" in n
               and n.endswith(".bz2") and datestr in n]
        if sel:
            chosen, files = run, sorted(set(sel)); break
    if not files:
        raise RuntimeError("Keine ICON-D2 tot_prec-Dateien (regular-lat-lon) gefunden")
    # Vorlaufstunde aus dem Dateinamen (_NNN_) -> nur bis max_hours laden (Bandbreite)
    def _hh(n):
        m = re.search(r"_(\d{3})_2d_tot_prec", n)
        return int(m.group(1)) if m else 999
    files = [n for n in files if _hh(n) <= max_hours]
    print(f"ICON-D2-Lauf: {chosen:%Y-%m-%dT%H:%M}Z  Dateien: {len(files)} (bis +{max_hours}h)")

    # 2) Jede Datei herunterladen, bz2-entpacken, alle GRIB-Nachrichten lesen.
    #    Eine stuendliche Datei enthaelt 4 Nachrichten (volle Stunde + :15/:30/:45).
    raw = []          # (validtime, akkumuliertes Feld auf DST)
    for n in files:
        full = n if n.startswith("http") else (f"{DWD_ICOND2_BASE}/{chosen:%H}/tot_prec/" + n.split("/")[-1])
        try:
            g = bz2.decompress(_http(full))
        except Exception as e:
            print("  D2-Datei uebersprungen:", e); continue
        p = os.path.join(tmp, "d2.grib2"); open(p, "wb").write(g)
        f = open(p, "rb")
        while True:
            gid = ec.codes_grib_new_from_file(f)
            if gid is None:
                break
            # Gitter-Geometrie aus den GRIB-Schluesseln (regulaeres Lat/Lon)
            Ni = ec.codes_get(gid, "Ni"); Nj = ec.codes_get(gid, "Nj")          # Spalten/Zeilen
            lat0 = ec.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
            lon0 = ec.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
            di = ec.codes_get(gid, "iDirectionIncrementInDegrees")              # Schrittweite Laenge
            dj = ec.codes_get(gid, "jDirectionIncrementInDegrees")              # Schrittweite Breite
            js = ec.codes_get(gid, "jScansPositively")                          # Scan-Richtung Nord/Sued
            vd = ec.codes_get(gid, "validityDate"); vt = ec.codes_get(gid, "validityTime")
            vals = np.array(ec.codes_get_values(gid), dtype="float32").reshape(Nj, Ni)
            ec.codes_release(gid)
            if lon0 > 180:                                   # 0..360 -> -180..180
                lon0 -= 360.0
            if js == 1:                                      # Sued zuerst -> nach Nord oben drehen
                vals = vals[::-1, :]; top = lat0 + (Nj - 1) * dj
            else:
                top = lat0
            # Affine fuer das Quellraster (Zeile 0 = Nord) und auf DST umprojizieren
            src_t = Affine(di, 0, lon0 - di / 2, 0, -dj, top + dj / 2)
            dst = np.full((DH, DW), np.nan, "float32")
            reproject(vals, dst, src_transform=src_t, src_crs=CRS.from_epsg(4326),
                      dst_transform=DST_TRANSFORM, dst_crs=DST_CRS,
                      resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)
            when = dt.datetime.strptime(f"{int(vd):08d}{int(vt):04d}", "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)
            raw.append((when, dst))
        f.close()
    raw.sort(key=lambda x: x[0])
    if not raw:
        raise RuntimeError("ICON-D2: keine GRIB-Nachrichten gelesen")
    # 3) TOT_PREC ist aufsummiert -> Differenz aufeinanderfolgender Zeitschritte,
    #    geteilt durch den Abstand in Stunden = Rate in mm/h (15 Min -> x4).
    fields = {}; prev = None; prevt = None
    for when, acc in raw:
        if prev is not None:
            dthr = max((when - prevt).total_seconds() / 3600.0, 1e-6)
            rate = np.clip(acc - prev, 0, None) / dthr
            fields[when] = gaussian_filter(rate, sigma=0.8)   # leicht glaetten
        prev, prevt = acc, when
    print(f"ICON-D2: {len(fields)} Felder (15-Min)")
    return fields


# ===================== NOWCAST (INCA, gerastert, NetCDF) =============
def _nowcast_src(ds):
    """src_crs und src_transform aus chx/chy + grid_mapping der NetCDF lesen."""
    from rasterio.crs import CRS as _CRS
    chx = ds.variables["chx"][:]; chy = ds.variables["chy"][:]
    gm = ds.variables["grid_mapping"]
    g = lambda k, d=None: (float(gm.getncattr(k)) if k in gm.ncattrs() else d)
    proj = ("+proj=somerc +lat_0=%.10f +lon_0=%.10f +k_0=1 +x_0=%.1f +y_0=%.1f "
            "+ellps=bessel +towgs84=674.374,15.056,405.346,0,0,0,0 +units=m +no_defs" % (
            g("latitude_of_projection_center", 46.95240555555556),
            g("longitude_of_projection_center", 7.439583333333333),
            g("false_easting", 2600000.0), g("false_northing", 1200000.0)))
    dx = float(chx[1] - chx[0]); dy = float(chy[1] - chy[0])
    e_min = float(chx[0]) - dx / 2.0
    n_max = float(chy[-1]) + abs(dy) / 2.0      # chy aufsteigend (Sued->Nord)
    transform = Affine(dx, 0, e_min, 0, -abs(dy), n_max)
    return _CRS.from_proj4(proj), transform, (chy[1] > chy[0])


def render_nowcast(nc_path, out_dir, prefix="f", step_min=5, max_min=360):
    """INCA-Nowcast-NetCDF -> Reihe eingefaerbter PNGs auf dem gemeinsamen Raster.
    Rueckgabe: Liste (datetime_utc, dateiname, max_mmh) ab +step bis +max_min."""
    import netCDF4 as _nc
    ds = _nc.Dataset(nc_path)
    var = next(ds.variables[v] for v in ds.variables if ds.variables[v].ndim == 3)
    data = np.ma.filled(var[:].astype("float32"), np.nan)
    tvar = ds.variables["time"]
    base = dt.datetime.strptime(tvar.units.split("since")[1].strip()[:19], "%Y-%m-%d %H:%M:%S")
    base = base.replace(tzinfo=dt.timezone.utc)
    secs = np.array(tvar[:])
    src_crs, src_transform, north_up = _nowcast_src(ds)
    every = max(1, int(round(step_min / 5.0)))   # Datei ist 5-Min, ggf. ausduennen
    out = []
    n = 0
    for i in range(data.shape[0]):
        minute = secs[i] / 60.0
        if minute <= 0 or minute > max_min or (i % every):
            continue
        field = data[i][::-1, :] if north_up else data[i]
        dst = np.full((DH, DW), np.nan, "float32")
        reproject(field, dst, src_transform=src_transform, src_crs=src_crs,
                  dst_transform=DST_TRANSFORM, dst_crs=DST_CRS,
                  resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)
        fn = f"{prefix}{n:02d}.png"
        Image.fromarray(colorize(dst), "RGBA").save(os.path.join(out_dir, fn))
        when = base + dt.timedelta(seconds=float(secs[i]))
        mx = float(np.nanmax(data[i])) if np.isfinite(np.nanmax(data[i])) else 0.0
        out.append((when, fn, round(mx, 1)))
        n += 1
    ds.close()
    return out


# ===================== STAC-Abruf (data.geo.admin.ch) =================
STAC = os.environ.get("INCA_STAC", "https://data.geo.admin.ch/api/stac/v1")
RADAR_COLLECTION = os.environ.get("RADAR_COLLECTION", "ch.meteoschweiz.ogd-radar-precip")
FC_COLLECTION = os.environ.get("FC_COLLECTION", "ch.meteoschweiz.ogd-local-forecasting")
NOWCAST_COLLECTION = os.environ.get("NOWCAST_COLLECTION", "")  # leer -> Kandidaten testen
NOWCAST_CANDIDATES = [
    "ch.meteoschweiz.ogd-nowcasting",
    "ch.meteoschweiz.ogd-nowcasting-precip",
    "ch.meteoschweiz.ogd-nowcasting-inca",
    "ch.meteoschweiz.ogd-forecasting-nowcasting",
]


def _get_json(url, timeout=60):
    import urllib.request
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def download(href, dest, timeout=180):
    import urllib.request
    urllib.request.urlretrieve(href, dest)
    return dest


def _rzc_time(fname):
    """Zeitstempel aus RZC-Dateiname lesen: RZC<YY><DOY><HHMM>... -> datetime UTC."""
    import re
    m = re.search(r"RZC(\d{2})(\d{3})(\d{4})", os.path.basename(fname).upper())
    if not m:
        return None
    yy, doy, hhmm = int(m.group(1)), int(m.group(2)), m.group(3)
    base = dt.datetime(2000 + yy, 1, 1, int(hhmm[:2]), int(hhmm[2:]), tzinfo=dt.timezone.utc)
    return base + dt.timedelta(days=doy - 1)


def radar_latest_assets(limit=24):
    """Liste (datetime, href) der letzten RZC-Radarbilder (neueste zuerst).
    Sammelt ALLE RZC-Assets ueber alle Items (egal ob viele Items oder ein Item
    mit vielen Assets), sortiert nach Zeit und liefert die juengsten 5-Min-Bilder."""
    data = _get_json(f"{STAC}/collections/{RADAR_COLLECTION}/items?limit=200")
    found = {}   # datetime -> href (dedupe)
    for feat in data.get("features", []):
        for k, a in feat.get("assets", {}).items():
            href = a.get("href", "")
            base = os.path.basename(href).upper()
            if base.startswith("RZC") and href.lower().endswith(".h5"):
                when = _rzc_time(base)
                if when is not None:
                    found[when] = href
    times = sorted(found, reverse=True)[:limit]
    return [(t.isoformat(), found[t]) for t in times]


def _all_collection_ids():
    """Alle Collection-IDs der STAC-API (mit Pagination) holen."""
    url = f"{STAC}/collections?limit=100"
    ids = []
    for _ in range(30):
        data = _get_json(url)
        for col in data.get("collections", []):
            cid = col.get("id", "")
            if cid:
                ids.append(cid)
        nxt = next((l.get("href") for l in data.get("links", []) if l.get("rel") == "next"), None)
        if not nxt:
            break
        url = nxt
    return ids


def nowcast_latest_asset():
    """(href) der neuesten Nowcast-Niederschlags-NetCDF (RR/RP-INCA).
    Erkennt die Nowcast-Collection automatisch aus der STAC-Liste (Name enthaelt
    'nowcast' oder 'inca'); faellt auf bekannte Kandidaten zurueck."""
    import re
    if NOWCAST_COLLECTION:
        ids = [NOWCAST_COLLECTION]
    else:
        try:
            disc = [c for c in _all_collection_ids()
                    if "nowcast" in c.lower() or "inca" in c.lower()]
            if disc:
                print("Nowcast-Kandidaten erkannt:", disc)
            ids = disc or NOWCAST_CANDIDATES
        except Exception as e:
            print("Collection-Liste nicht abrufbar:", e)
            ids = NOWCAST_CANDIDATES
    last_err = None
    for cid in ids:
        try:
            data = _get_json(f"{STAC}/collections/{cid}/items?limit=50")
        except Exception as e:
            last_err = e
            continue
        best = None
        for feat in data.get("features", []):
            for k, a in feat.get("assets", {}).items():
                href = a.get("href", ""); low = os.path.basename(href).lower()
                if low.endswith(".nc") and ("rr" in low or "rp" in low) and "inca" in low:
                    m = re.search(r"(\d{12})", low)
                    ts = m.group(1) if m else low
                    if best is None or ts > best[0]:
                        best = (ts, href)
        if best:
            print(f"Nowcast-Collection: {cid}  Lauf: {best[0]}")
            return best[1]
    raise RuntimeError(f"Keine Nowcast-Niederschlagsdaten gefunden (getestet: {ids}; {last_err})")


def forecast_latest_precip_asset():
    """(href) der Niederschlags-CSV des NEUESTEN Vorhersagelaufs.
    Sammelt rre150-Assets ueber mehrere Items und waehlt den hoechsten
    Referenz-Zeitstempel aus dem Dateinamen (vnut12.lssw.<YYYYMMDDHHMM>.rre150...)."""
    import re
    data = _get_json(f"{STAC}/collections/{FC_COLLECTION}/items?limit=50&sortby=-datetime")
    best = None  # (ref_timestamp, href)
    for feat in data.get("features", []):
        for k, a in feat.get("assets", {}).items():
            href = a.get("href", "")
            low = href.lower()
            if "rre150" in low and low.endswith(".csv"):
                m = re.search(r"\.(\d{12})\.rre150", os.path.basename(low))
                ts = m.group(1) if m else os.path.basename(low)
                if best is None or ts > best[0]:
                    best = (ts, href)
    if best is None:
        raise RuntimeError(f"Keine Niederschlags-CSV (rre150) in {FC_COLLECTION}.")
    print("Prognose-Lauf (Referenz):", best[0])
    return best[1]

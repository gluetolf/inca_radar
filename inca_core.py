#!/usr/bin/env python3
"""
inca_core.py - Kernlogik fuer das kombinierte Niederschlagsradar.

Vergangenheit/Gegenwart: offizielles MeteoSchweiz-Radar (ODIM-HDF5, RZC, mm/h)
Zukunft:                 MeteoSchweiz-Lokalprognose (data4web CSV, interpoliert)
Beide werden auf dasselbe WGS84-Raster gerendert und mit derselben Radar-Farbskala
eingefaerbt, damit der Uebergang nahtlos ist.

Quelle der Daten: MeteoSchweiz (Open Government Data, frei mit Quellenangabe).
"""
import os, json, csv, glob, datetime as dt
import numpy as np
import h5py
import rasterio
from rasterio.transform import Affine
from rasterio.warp import reproject, Resampling
from rasterio.crs import CRS
from PIL import Image
from pyproj import Transformer
from scipy.interpolate import griddata

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- gemeinsames Zielraster (WGS84), deckt die Radardomaene ab -------
DST_W, DST_E, DST_S, DST_N = 2.6, 12.5, 43.6, 49.5
DST_RES = 0.01
DW = int(round((DST_E - DST_W) / DST_RES))
DH = int(round((DST_N - DST_S) / DST_RES))
DST_TRANSFORM = Affine(DST_RES, 0, DST_W, 0, -DST_RES, DST_N)
DST_CRS = CRS.from_epsg(4326)
BOUNDS = [[DST_S, DST_W], [DST_N, DST_E]]

# ---- Radar-Farbskala mm/h -> RGBA (blau leicht ... rot/magenta Gewitter) ----
SCALE = [
    (0.1,  (160, 210, 255, 130)),
    (0.5,  (90,  160, 245, 160)),
    (1.0,  (45,  110, 225, 180)),
    (2.0,  (40,  180, 170, 195)),
    (5.0,  (95,  200,  80, 205)),
    (10.0, (230, 215,  70, 215)),
    (20.0, (240, 150,  55, 225)),
    (50.0, (225,  55,  50, 235)),
    (1e9,  (170,  25, 110, 245)),
]


def colorize(arr):
    h, w = arr.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    a = np.nan_to_num(arr, nan=0.0)
    prev = 0.0
    for thr, col in SCALE:
        rgba[(a > prev) & (a <= thr)] = col
        prev = thr
    return rgba


# ===================== RADAR (ODIM-HDF5, RZC) =========================
def render_radar(h5path, out_png):
    """RZC-ODIM-Datei -> eingefaerbtes PNG auf dem gemeinsamen Raster.
    Rueckgabe: (datetime_utc, max_mmh)."""
    with h5py.File(h5path, "r") as f:
        data = f["dataset1/data1/data"][:].astype("float64")
        w = dict(f["dataset1/data1/what"].attrs)
        where = dict(f["where"].attrs)
        what = dict(f["what"].attrs)

    gain = float(w.get("gain", 1.0)); offset = float(w.get("offset", 0.0))
    nodata = float(w.get("nodata", np.nan)); undetect = float(w.get("undetect", np.inf))
    vals = data * gain + offset
    if not np.isnan(nodata):
        vals[data == nodata] = np.nan
    vals[np.isnan(data)] = np.nan
    if not np.isinf(undetect):
        vals[data == undetect] = 0.0
    else:
        vals[np.isinf(data)] = 0.0

    proj = where["projdef"].decode() if isinstance(where["projdef"], bytes) else where["projdef"]
    src_crs = CRS.from_proj4(proj)
    nx = int(where.get("xsize", data.shape[1])); ny = int(where.get("ysize", data.shape[0]))
    # LV95-Ecken aus den geografischen Eckpunkten bestimmen
    t = Transformer.from_crs(4326, src_crs, always_xy=True)
    ul_e, ul_n = t.transform(float(where["UL_lon"]), float(where["UL_lat"]))
    lr_e, lr_n = t.transform(float(where["LR_lon"]), float(where["LR_lat"]))
    dx = (lr_e - ul_e) / nx
    dy = (ul_n - lr_n) / ny
    src_transform = Affine(dx, 0, ul_e, 0, -dy, ul_n)   # ODIM: Zeile 0 = Nord

    dst = np.full((DH, DW), np.nan, dtype="float32")
    reproject(source=vals.astype("float32"), destination=dst,
              src_transform=src_transform, src_crs=src_crs,
              dst_transform=DST_TRANSFORM, dst_crs=DST_CRS,
              resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)
    Image.fromarray(colorize(dst), "RGBA").save(out_png)

    d = (what.get("date") or where.get("date"))
    tm = (what.get("time"))
    d = d.decode() if isinstance(d, bytes) else d
    tm = tm.decode() if isinstance(tm, bytes) else tm
    when = dt.datetime.strptime(d + tm[:4], "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)
    mx = float(np.nanmax(vals)) if np.isfinite(np.nanmax(vals)) else 0.0
    return when, round(mx, 1)


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

def icon_forecast_frames(out_dir, tmp, prefix="f", max_hours=24, now=None):
    """Neueste ICON-CH1-TOT_PREC-Vorhersage (deterministisch) -> PNG-Reihe.
    Rueckgabe: Liste (datetime_utc, dateiname, max_mmh). Stuendlich, entkumuliert."""
    global _ICON_IDX, _ICON_MASK
    from scipy.spatial import cKDTree
    # 1) Gitter-Koordinaten (Konstanten)
    chref = _icon_constants_href()
    cfile = download(chref, os.path.join(tmp, "icon_const.grib2"))
    lon, lat = _icon_lonlat(cfile)
    print(f"ICON-Gitter: {len(lon)} Zellen, lon {lon.min():.2f}..{lon.max():.2f}, lat {lat.min():.2f}..{lat.max():.2f}")

    # 2) TOT_PREC-Assets (deterministisch) der NEUESTEN Referenz suchen
    def _search(extra):
        body = {"collections": [ICON_COLLECTION], "forecast:variable": "TOT_PREC",
                "forecast:perturbed": False, "limit": 100}
        body.update(extra)
        return _post_json(f"{STAC}/search", body).get("features", [])

    feats, chosen = [], None
    base = (now or dt.datetime.now(dt.timezone.utc)).replace(minute=0, second=0, microsecond=0)
    base = base - dt.timedelta(hours=base.hour % 3)        # auf 3-Stunden-Raster
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
    print(f"ICON-Suche: {len(feats)} Features, gewaehlte Referenz {chosen}")
    if feats:
        print("  Properties-Schluessel:", list(feats[0].get("properties", {}).keys())[:10])
    recs = []
    for ft in feats:
        p = ft.get("properties", {})
        ref = p.get("forecast:reference_datetime") or p.get("datetime")
        hz = _iso_dur_hours(p.get("forecast:horizon", ""))
        href = next((a.get("href") for a in ft.get("assets", {}).values()
                     if ".grib2" in str(a.get("href", "")).lower()), None)
        if ref and hz is not None and href:
            recs.append((ref, hz, href))
    if not recs:
        sample = feats[0] if feats else {}
        raise RuntimeError(f"Keine ICON-TOT_PREC-Assets gefunden (Features: {len(feats)}; "
                           f"Properties: {list(sample.get('properties', {}).keys())[:10]}; "
                           f"Assets: {list(sample.get('assets', {}).keys())[:5]})")
    latest = max(r[0] for r in recs)
    series = sorted((hz, href) for ref, hz, href in recs if ref == latest)
    ref_dt = dt.datetime.fromisoformat(latest.replace("Z", "+00:00"))
    print(f"ICON-Referenz: {latest}  Vorlaufzeiten: {len(series)} (bis +{int(series[-1][0])}h)")

    # 3) Zuordnung Gitterzelle -> Zielpixel (einmalig)
    if _ICON_IDX is None:
        gx = DST_W + (np.arange(DW) + 0.5) * DST_RES
        gy = DST_N - (np.arange(DH) + 0.5) * DST_RES
        GX, GY = np.meshgrid(gx, gy)
        tree = cKDTree(np.column_stack([lon, lat]))
        dist, idx = tree.query(np.column_stack([GX.ravel(), GY.ravel()]), k=1)
        _ICON_IDX = idx
        _ICON_MASK = dist > 0.02   # ausserhalb der ICON-Domaene -> transparent

    # 4) Entkumulieren + rendern
    out = []; prev = None; n = 0
    for hz, href in series:
        if hz > max_hours:
            break
        cur = _icon_values(download(href, os.path.join(tmp, f"icon_{int(hz):03d}.grib2")))
        precip = cur - prev if prev is not None else cur.copy()
        prev = cur
        if hz <= 0:
            continue
        precip = np.clip(precip, 0, None)
        field = precip[_ICON_IDX].astype("float32")
        field[_ICON_MASK] = np.nan
        grid = field.reshape(DH, DW)
        fn = f"{prefix}{n:02d}.png"
        Image.fromarray(colorize(grid), "RGBA").save(os.path.join(out_dir, fn))
        when = ref_dt + dt.timedelta(hours=hz)
        mx = float(np.nanmax(precip)) if precip.size else 0.0
        out.append((when, fn, round(mx, 1)))
        n += 1
    return out


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

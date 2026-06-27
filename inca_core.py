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


_GX = _GY = None

def render_forecast(lons, lats, vals, out_png):
    """Punktwerte auf das gemeinsame Raster interpolieren -> PNG. Rueckgabe max_mmh."""
    global _GX, _GY
    if _GX is None:
        gx = DST_W + (np.arange(DW) + 0.5) * DST_RES
        gy = DST_N - (np.arange(DH) + 0.5) * DST_RES
        _GX, _GY = np.meshgrid(gx, gy)
    grid = griddata((lons, lats), vals, (_GX, _GY), method="linear")  # ausserhalb Huelle -> NaN
    Image.fromarray(colorize(grid), "RGBA").save(out_png)
    mx = float(np.nanmax(vals)) if len(vals) and np.isfinite(np.nanmax(vals)) else 0.0
    return round(mx, 1)


# ===================== STAC-Abruf (data.geo.admin.ch) =================
STAC = os.environ.get("INCA_STAC", "https://data.geo.admin.ch/api/stac/v1")
RADAR_COLLECTION = os.environ.get("RADAR_COLLECTION", "ch.meteoschweiz.ogd-radar-precip")
FC_COLLECTION = os.environ.get("FC_COLLECTION", "ch.meteoschweiz.ogd-local-forecasting")


def _get_json(url, timeout=60):
    import urllib.request
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def download(href, dest, timeout=180):
    import urllib.request
    urllib.request.urlretrieve(href, dest)
    return dest


def radar_latest_assets(limit=24):
    """Liste (datetime, href) der letzten RZC-Radarbilder (neueste zuerst)."""
    data = _get_json(f"{STAC}/collections/{RADAR_COLLECTION}/items?limit={limit*3}")
    out = []
    for feat in data.get("features", []):
        for k, a in feat.get("assets", {}).items():
            href = a.get("href", "")
            base = os.path.basename(href).upper()
            if base.startswith("RZC") and href.lower().endswith(".h5"):
                dtime = feat.get("properties", {}).get("datetime")
                out.append((dtime, href))
                break
    return out[:limit]


def forecast_latest_precip_asset():
    """(reference_datetime, href) der neuesten Niederschlags-CSV der Lokalprognose."""
    data = _get_json(f"{STAC}/collections/{FC_COLLECTION}/items?limit=1")
    feats = data.get("features", [])
    if not feats:
        raise RuntimeError(f"Keine Items in {FC_COLLECTION}.")
    assets = feats[0].get("assets", {})
    cand = [(k, a) for k, a in assets.items()
            if "rre150" in k.lower() and a.get("href", "").lower().endswith(".csv")]
    if not cand:
        cand = [(k, a) for k, a in assets.items()
                if "rre150" in a.get("href", "").lower()]
    if not cand:
        raise RuntimeError(f"Keine Niederschlags-CSV (rre150) in {FC_COLLECTION}. "
                           f"Assets-Beispiel: {list(assets)[:5]}")
    cand.sort(key=lambda kv: kv[0])   # nach Name -> juengster Zeitstempel zuletzt
    return cand[-1][1]["href"]

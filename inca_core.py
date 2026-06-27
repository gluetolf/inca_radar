#!/usr/bin/env python3
"""
inca_core.py - Kernlogik fuer das INCA-Niederschlagsradar.

Enthaelt:
  - convert_file(nc_path, out_dir, step_minutes)  : NetCDF -> PNG-Overlays + frames.json
  - stac_find_latest()                            : neuestes INCA-Item via STAC finden
  - download(href, dest)                          : Datei herunterladen

Datenquelle: MeteoSchweiz, Open Government Data (frei nutzbar, Quellenangabe).
"""
import os, json, datetime as dt
import numpy as np
import h5py
import rasterio
from rasterio.transform import Affine
from rasterio.warp import reproject, Resampling, calculate_default_transform
from rasterio.crs import CRS
from PIL import Image

# ---- Konfiguration ----------------------------------------------------
STAC_BASE = os.environ.get("INCA_STAC", "https://data.geo.admin.ch/api/stac/v1")
# Optional fest vorgeben; leer = Auto-Discovery ueber /collections
INCA_COLLECTION = os.environ.get("INCA_COLLECTION", "").strip()

# Farbskala mm/h -> RGBA (blau = leicht ... rot/magenta = Gewitter)
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


def _crs_from_grid_mapping(gm_attrs):
    fe = float(np.ravel(gm_attrs.get("false_easting", [600000.0]))[0])
    return CRS.from_epsg(2056) if fe > 1_000_000 else CRS.from_epsg(21781)


def _pick_var(f):
    for cand in ("RP", "RR"):
        if cand in f:
            return cand
    for k in f.keys():
        ln = f[k].attrs.get("long_name", b"")
        ln = ln.decode() if isinstance(ln, bytes) else str(ln)
        if "precipitation rate" in ln.lower():
            return k
    raise ValueError(f"Keine Niederschlagsvariable gefunden. Vorhanden: {list(f.keys())}")


def reference_time_of(nc_path):
    """Referenzzeitpunkt (UTC, ISO) aus der Datei lesen - fuer den Cache-Schluessel."""
    with h5py.File(nc_path, "r") as f:
        tu = f["time"].attrs.get("units", b"")
        tu = tu.decode() if isinstance(tu, bytes) else tu
    base = dt.datetime.strptime(tu.split("since")[-1].strip()[:19], "%Y-%m-%d %H:%M:%S")
    return base.replace(tzinfo=dt.timezone.utc)


def convert_file(nc_path, out_dir, step_minutes=5):
    """Wandelt eine INCA-Datei in PNG-Overlays + frames.json in out_dir um."""
    os.makedirs(out_dir, exist_ok=True)
    frames = []
    with h5py.File(nc_path, "r") as f:
        var = _pick_var(f)
        v = f[var]
        fill = float(np.ravel(v.attrs.get("_FillValue", [-999.0]))[0])
        chx = f["chx"][:].astype(float)
        chy = f["chy"][:].astype(float)
        tsec = f["time"][:].astype(float)
        tu = f["time"].attrs.get("units", b"")
        tu = tu.decode() if isinstance(tu, bytes) else tu
        src_crs = _crs_from_grid_mapping(dict(f["grid_mapping"].attrs))
        sample = f[var][0]
    base = dt.datetime.strptime(tu.split("since")[-1].strip()[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)

    dx, dy = abs(round(chx[1] - chx[0])), abs(round(chy[1] - chy[0]))
    flip = chy[0] < chy[-1]
    y_top = chy.max() + dy / 2.0
    x_left = chx.min() - dx / 2.0
    src_transform = Affine(dx, 0, x_left, 0, -dy, y_top)
    ny, nx = sample.shape

    dst_crs = CRS.from_epsg(4326)
    dst_transform, dw, dh = calculate_default_transform(
        src_crs, dst_crs, nx, ny,
        left=x_left, bottom=y_top - dy * ny, right=x_left + dx * nx, top=y_top)
    west = dst_transform.c
    north = dst_transform.f
    east = west + dst_transform.a * dw
    south = north + dst_transform.e * dh
    bounds = [[south, west], [north, east]]

    granule_min = max(1, int(round((tsec[1] - tsec[0]) / 60))) if len(tsec) > 1 else 5
    every = max(1, step_minutes // granule_min)
    sel = list(range(0, len(tsec), every))

    with h5py.File(nc_path, "r") as f:
        for i in sel:
            arr = f[var][i].astype("float32")
            arr[arr == fill] = np.nan
            if flip:
                arr = arr[::-1, :]
            dst = np.full((dh, dw), np.nan, dtype="float32")
            reproject(source=arr, destination=dst,
                      src_transform=src_transform, src_crs=src_crs,
                      dst_transform=dst_transform, dst_crs=dst_crs,
                      resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)
            fname = f"f{i:03d}.png"
            Image.fromarray(colorize(dst), "RGBA").save(os.path.join(out_dir, fname))
            ts = base + dt.timedelta(seconds=float(tsec[i]))
            mx = float(np.nanmax(arr)) if np.isfinite(np.nanmax(arr)) else 0.0
            frames.append({"file": fname, "time": ts.isoformat(),
                           "lead_min": int(round(tsec[i] / 60)), "max_mmh": round(mx, 1)})

    manifest = {
        "source": "MeteoSchweiz INCA (Open Government Data)",
        "variable": "Total precipitation rate (mm/h)",
        "reference_time": base.isoformat(),
        "bounds": bounds,
        "frames": frames,
    }
    with open(os.path.join(out_dir, "frames.json"), "w") as fp:
        json.dump(manifest, fp)
    return manifest


# ---- STAC-Abruf -------------------------------------------------------
def _get_json(url, timeout=60):
    import urllib.request
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _discover_collection():
    """INCA-Niederschlags-Collection automatisch finden (oder fest gesetzte ID)."""
    if INCA_COLLECTION:
        return INCA_COLLECTION
    data = _get_json(f"{STAC_BASE}/collections")
    cols = data.get("collections", [])
    def score(c):
        text = (c.get("id", "") + " " + str(c.get("title", "") or "")).lower()
        s = 0
        if "inca" in text or "nowcast" in text:
            s += 5
        if "precip" in text or "niederschlag" in text or "rr" in text or "rp" in text:
            s += 3
        if "type" in text or "snow" in text or "schnee" in text:
            s -= 4
        return s
    ranked = sorted(cols, key=score, reverse=True)
    if ranked and score(ranked[0]) > 0:
        return ranked[0]["id"]
    raise RuntimeError("Keine INCA-Collection gefunden. Bitte INCA_COLLECTION setzen. "
                       f"Siehe {STAC_BASE}/collections")


def stac_find_latest():
    """Gibt (reference_datetime_utc, asset_href) des neuesten INCA-Niederschlags zurueck."""
    cid = _discover_collection()
    items = _get_json(f"{STAC_BASE}/collections/{cid}/items?limit=1")
    feats = items.get("features", [])
    if not feats:
        raise RuntimeError(f"Keine Items in Collection {cid}.")
    feat = feats[0]
    dtime = feat.get("properties", {}).get("datetime") or feat.get("properties", {}).get("forecast:reference_datetime")
    assets = feat.get("assets", {})
    def asc(kv):
        k, a = kv
        href = a.get("href", "").lower()
        key = k.lower()
        s = 0
        if href.endswith(".nc"):
            s += 3
        if key.startswith("rp") or key.startswith("rr") or "precip" in key:
            s += 3
        if "type" in key or "snow" in key or key.startswith("nt") or key.startswith("pt"):
            s -= 4
        return s
    cands = sorted(assets.items(), key=asc, reverse=True)
    if not cands or asc(cands[0]) <= 0:
        raise RuntimeError(f"Kein passendes Niederschlags-Asset in {cid}. Assets: {list(assets)}")
    return dtime, cands[0][1]["href"]


def download(href, dest, timeout=120):
    import urllib.request
    urllib.request.urlretrieve(href, dest)
    return dest

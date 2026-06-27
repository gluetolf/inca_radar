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
import re

def _get_json(url, timeout=60):
    import urllib.request
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _tokens(s):
    return set(t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t)


def _all_collections():
    """Alle Collections holen (mit Pagination)."""
    cols, url, pages = [], f"{STAC_BASE}/collections?limit=100", 0
    while url and pages < 60:
        data = _get_json(url)
        cols.extend(data.get("collections", []))
        url = next((l.get("href") for l in data.get("links", []) if l.get("rel") == "next"), None)
        pages += 1
    return cols


_BAD = ("type", "snow", "snowfall", "schnee", "temperature", "temperatur",
        "sunshine", "sonnenschein", "accumulation", "snowline")


def _score_collection(c):
    cid = (c.get("id") or "").lower()
    if not cid.startswith("ch.meteoschweiz"):
        return -999
    toks = _tokens(cid + " " + str(c.get("title", "")) + " " + str(c.get("description", "")))
    s = 0
    if "inca" in toks: s += 6
    if "nowcasting" in toks or "nowcast" in toks: s += 4
    if {"precip", "precipitation", "niederschlag", "rr"} & toks: s += 3
    if "rate" in toks: s += 1
    for b in _BAD:
        if b in toks: s -= 5
    return s


def _discover_collection():
    if INCA_COLLECTION:
        print("INCA_COLLECTION fest gesetzt:", INCA_COLLECTION)
        return INCA_COLLECTION
    cols = _all_collections()
    scored = sorted(((_score_collection(c), c.get("id")) for c in cols), reverse=True)
    mete = [(s, i) for s, i in scored if (i or "").lower().startswith("ch.meteoschweiz")]
    print("Gefundene MeteoSchweiz-Collections (Top 20 nach Eignung):")
    for s, i in mete[:20]:
        print(f"   score={s:>3}  {i}")
    if scored and scored[0][0] >= 6:
        print("Automatisch gewaehlt:", scored[0][1])
        return scored[0][1]
    raise RuntimeError(
        "Keine eindeutige INCA-Niederschlags-Collection gefunden. "
        "Bitte die passende ID aus der obigen Liste als Secret INCA_COLLECTION setzen.")


def _asset_candidates(assets):
    out = []
    for k, a in assets.items():
        href = (a.get("href") or "")
        if not href.lower().endswith(".nc"):
            continue
        toks = _tokens(k + " " + href)
        s = 4
        if {"rr", "precip", "precipitation"} & toks: s += 3
        if "rate" in toks: s += 1
        for b in _BAD + ("pt", "nt"):
            if b in toks: s -= 5
        out.append((s, k, a))
    return sorted(out, reverse=True)


def stac_find_latest():
    """(reference_datetime_utc, asset_href) des neuesten INCA-Niederschlags."""
    cid = _discover_collection()
    items = _get_json(f"{STAC_BASE}/collections/{cid}/items?limit=1")
    feats = items.get("features", [])
    if not feats:
        raise RuntimeError(f"Keine Items in Collection {cid}.")
    feat = feats[0]
    props = feat.get("properties", {})
    dtime = props.get("datetime") or props.get("forecast:reference_datetime")
    cands = _asset_candidates(feat.get("assets", {}))
    if not cands:
        raise RuntimeError(f"Kein NetCDF-Niederschlags-Asset in {cid}. "
                           f"Assets: {list(feat.get('assets', {}))}")
    print("Gewaehltes Asset:", cands[0][1])
    return dtime, cands[0][2]["href"]


def download(href, dest, timeout=180):
    import urllib.request
    urllib.request.urlretrieve(href, dest)
    return dest

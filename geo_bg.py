#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
geo_bg.py  –  EINMALIG lokal ausfuehren (wie peaks_refine.py / places_foreign.py).

Holt gemeinfreie Geodaten von Natural Earth (Public Domain) und schreibt
geo_bg.json mit Seen, Fluessen und Landesgrenzen im Radar-Gebiet. Daraus
zeichnen build.py (preview.png) und ogimg.php (geteilte Ausschnitte) den
Karten-Hintergrund des Vorschaubilds - vektorbasiert, bei jedem Zoom scharf.

Vorbereitung:  pip install pyshp
Aufruf:        python3 geo_bg.py          # schreibt geo_bg.json
Danach:        geo_bg.json ins Repo legen (gleicher Ordner wie places.js).
"""

import io, json, math, sys, zipfile, urllib.request

BASE = "https://naciscdn.org/naturalearth/10m/physical/"
BASE_CULT = "https://naciscdn.org/naturalearth/10m/cultural/"
SETS = [
    ("lakes",   BASE + "ne_10m_lakes.zip",                      "polygon"),
    ("lakes",   BASE + "ne_10m_lakes_europe.zip",               "polygon"),
    ("rivers",  BASE + "ne_10m_rivers_lake_centerlines.zip",    "line"),
    ("rivers",  BASE + "ne_10m_rivers_europe.zip",              "line"),
    ("borders", BASE_CULT + "ne_10m_admin_0_boundary_lines_land.zip", "line"),
]

# Radar-Gebiet (wie DST_* in inca_core.py), leicht gepuffert
W, E, S, N = 2.4, 12.7, 43.4, 49.7
SIMPLIFY = 0.004        # Grad (~400 m): Punkte duenner machen, Dateigroesse klein halten


def inside(lon, lat):
    return W <= lon <= E and S <= lat <= N


def thin(pts):
    """Punkte ausduennen: naechsten Punkt erst ab SIMPLIFY Abstand uebernehmen."""
    out = [pts[0]]
    for p in pts[1:-1]:
        q = out[-1]
        if abs(p[0]-q[0]) + abs(p[1]-q[1]) >= SIMPLIFY:
            out.append(p)
    out.append(pts[-1])
    return out


def load(url):
    print("Lade", url.rsplit("/", 1)[-1], "...")
    data = urllib.request.urlopen(url, timeout=300).read()
    zf = zipfile.ZipFile(io.BytesIO(data))
    shp = [n for n in zf.namelist() if n.endswith(".shp")][0]
    dbf = shp[:-4] + ".dbf"
    import shapefile                      # pyshp
    return shapefile.Reader(shp=io.BytesIO(zf.read(shp)), dbf=io.BytesIO(zf.read(dbf)))


def main():
    try:
        import shapefile  # noqa
    except ImportError:
        sys.exit("Bitte zuerst:  pip install pyshp")

    out = {"lakes": [], "rivers": [], "borders": []}
    for key, url, kind in SETS:
        try:
            rd = load(url)
        except Exception as e:
            print("  uebersprungen (", e, ")")
            continue
        for sh in rd.shapes():
            if not sh.points:
                continue
            # grobe Box-Vorpruefung
            xs = [p[0] for p in sh.points]; ys = [p[1] for p in sh.points]
            if max(xs) < W or min(xs) > E or max(ys) < S or min(ys) > N:
                continue
            parts = list(sh.parts) + [len(sh.points)]
            for i in range(len(parts) - 1):
                seg = [(round(p[0], 4), round(p[1], 4)) for p in sh.points[parts[i]:parts[i+1]]]
                if len(seg) < 2:
                    continue
                if not any(inside(lo, la) for lo, la in seg):
                    continue
                seg = thin(seg)
                if kind == "polygon" and len(seg) >= 4:
                    out[key].append(seg)
                elif kind == "line" and len(seg) >= 2:
                    out[key].append(seg)

    js = json.dumps(out, separators=(",", ":"))
    open("geo_bg.json", "w", encoding="utf-8").write(js)
    print(f"\n=== Bericht ===")
    print(f"Seen-Polygone: {len(out['lakes'])}  Fluss-Linien: {len(out['rivers'])}  Grenz-Linien: {len(out['borders'])}")
    print(f"geo_bg.json: {len(js)//1024} KB")
    print("-> geo_bg.json ins Repo legen (gleicher Ordner wie places.js).")


if __name__ == "__main__":
    main()

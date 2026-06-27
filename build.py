#!/usr/bin/env python3
"""
build.py - erzeugt das statische Radar-Site-Verzeichnis (./site).

  python build.py                # neuesten INCA-Lauf von data.geo.admin.ch holen
  python build.py --file x.nc    # lokale INCA-Datei verwenden (Offline-Test)

Ausgabe in ./site/ :  index.html, frames.json, f000.png ...
Diese Dateien werden anschliessend per FTP auf METAhost geladen.
Datenquelle: MeteoSchweiz (Open Government Data).
"""
import os, sys, glob, shutil
import inca_core

OUT = os.environ.get("INCA_SITE", "site")
STEP_MINUTES = int(os.environ.get("INCA_STEP_MIN", "5"))
HERE = os.path.dirname(os.path.abspath(__file__))


def build(nc_path=None):
    os.makedirs(OUT, exist_ok=True)
    # alte PNGs entfernen, damit keine veralteten Frames liegen bleiben
    for old in glob.glob(os.path.join(OUT, "f*.png")):
        os.remove(old)

    if nc_path:
        print("Verwende lokale Datei:", nc_path)
        src = nc_path
        cleanup = False
    else:
        dtime, href = inca_core.stac_find_latest()
        print("Neuester INCA-Lauf:", dtime)
        print("Lade:", href)
        src = os.path.join(OUT, "_inca.nc")
        inca_core.download(href, src)
        cleanup = True

    manifest = inca_core.convert_file(src, OUT, step_minutes=STEP_MINUTES)
    if cleanup and os.path.exists(src):
        os.remove(src)

    shutil.copyfile(os.path.join(HERE, "index.html"), os.path.join(OUT, "index.html"))
    print(f"OK: {len(manifest['frames'])} Frames, Referenz {manifest['reference_time']} -> {OUT}/")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--file":
        build(sys.argv[2])
    else:
        build()

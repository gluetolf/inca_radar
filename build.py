#!/usr/bin/env python3
"""
build.py - erzeugt das statische Radar-Site-Verzeichnis (./site).

Kombiniert:
  Vergangenheit/jetzt -> offizielles MeteoSchweiz-Radar (ogd-radar-precip)
  Zukunft             -> MeteoSchweiz-Lokalprognose (ogd-local-forecasting)

  python build.py                         # live von data.geo.admin.ch
  python build.py --radar x.h5 --fc y.csv # lokale Dateien (Offline-Test)

Ausgabe in ./site/: index.html, frames.json, r*.png (Radar), f*.png (Prognose).
Quelle: MeteoSchweiz (Open Government Data).
"""
import os, sys, glob, json, shutil, tempfile, datetime as dt
import inca_core as c

OUT = os.environ.get("INCA_SITE", "site")
RADAR_FRAMES = int(os.environ.get("RADAR_FRAMES", "24"))   # ~2 h bei 5-Min-Takt
FC_HOURS = int(os.environ.get("FC_HOURS", "24"))           # Rueckfall-Vorhersagestunden
ICON_HOURS = int(os.environ.get("ICON_HOURS", "24"))       # ICON-CH1 bis +X h (max 33)


def _clean():
    os.makedirs(OUT, exist_ok=True)
    for f in glob.glob(os.path.join(OUT, "*.png")):
        os.remove(f)


def build(local_radar=None, local_fc=None, local_icon_dir=None):
    _clean()
    frames, now = [], None
    tmp = tempfile.mkdtemp(prefix="inca-")

    # ---- Radar: Vergangenheit -> jetzt ----
    try:
        if local_radar:
            assets = [(None, local_radar)]
        else:
            assets = c.radar_latest_assets(RADAR_FRAMES)
            print(f"Radar-Assets gefunden: {len(assets)}")
        rendered = []
        for i, (dtime, href) in enumerate(assets):
            try:
                src = href if os.path.exists(str(href)) else c.download(href, os.path.join(tmp, f"r{i}.h5"))
                fn = f"r{i:02d}.png"
                when, mx = c.render_radar(src, os.path.join(OUT, fn))
                rendered.append((when, fn, mx))
            except Exception as e:
                print("  Radarbild uebersprungen:", e)
        rendered.sort(key=lambda x: x[0])
        for when, fn, mx in rendered:
            frames.append({"file": fn, "time": when.isoformat(), "kind": "radar", "max_mmh": mx})
        if rendered:
            now = rendered[-1][0]
            span = f"{rendered[0][0].strftime('%H:%M')}–{rendered[-1][0].strftime('%H:%M')} UTC"
            print(f"Radar: {len(rendered)} Bilder ({span})")
        else:
            print("Radar: 0 Bilder")
    except Exception as e:
        print("Radar-Teil fehlgeschlagen:", e)

    # ---- Zukunft: primaer ICON-CH1 (gerastert), sonst Lokalprognose ----
    future_done = False
    try:
        if local_icon_dir:
            raise RuntimeError("lokaler ICON-Test nicht implementiert")
        series = c.icon_forecast_frames(OUT, tmp, prefix="f", max_hours=ICON_HOURS, now=now)
        wet = 0; detail = []
        for when, fn, mx in series:
            if now is not None and when <= now:
                try: os.remove(os.path.join(OUT, fn))
                except OSError: pass
                continue
            if mx > 0: wet += 1
            lead = round((when - now).total_seconds() / 3600) if now else 0
            detail.append(f"+{lead}h:{mx}")
            frames.append({"file": fn, "time": when.isoformat(), "kind": "forecast", "max_mmh": mx})
        nfc = sum(1 for f in frames if f["kind"] == "forecast")
        allmax = max((m for *_, m in series), default=0)
        print(f"ICON-CH1: {nfc} Bilder (stuendlich), davon {wet} mit Niederschlag, max {allmax} mm/h")
        print("ICON je Vorlaufzeit (h:max mm/h):  " + "  ".join(detail))
        future_done = nfc > 0
    except Exception as e:
        import traceback; traceback.print_exc()
        print("ICON nicht verfuegbar, Rueckfall auf Lokalprognose:", e)

    # ---- Rueckfall: Lokalprognose (data4web), nur wenn keine ICON-Daten ----
    if not future_done:
        try:
            if local_fc:
                fcsv = local_fc
            else:
                href = c.forecast_latest_precip_asset()
                print("Prognose-CSV:", href)
                fcsv = c.download(href, os.path.join(tmp, "fc.csv"))
            fc = c.parse_forecast_csv(fcsv, max_hours=FC_HOURS)
            times = sorted(fc)
            if now is not None:
                cutoff = now + dt.timedelta(hours=FC_HOURS)
                times = [t for t in times if now < t <= cutoff]
            else:
                times = times[:FC_HOURS]
            wet = 0
            for i, t in enumerate(times):
                lons, lats, vals = fc[t]
                fn = f"f{i:02d}.png"
                mx = c.render_forecast(lons, lats, vals, os.path.join(OUT, fn))
                if mx > 0:
                    wet += 1
                frames.append({"file": fn, "time": t.isoformat(), "kind": "forecast", "max_mmh": mx})
            allmax = max((fr["max_mmh"] for fr in frames if fr["kind"] == "forecast"), default=0)
            print(f"Lokalprognose (Rueckfall): {len(times)} Bilder, davon {wet} mit Niederschlag, max {allmax} mm/h")
        except Exception as e:
            print("Prognose-Teil fehlgeschlagen:", e)

    if not frames:
        raise SystemExit("Keine Bilder erzeugt (Radar und Prognose beide fehlgeschlagen).")

    frames.sort(key=lambda fr: fr["time"])
    manifest = {
        "source": "MeteoSchweiz: Radar (Vergangenheit) + ICON-CH1/Prognose (Zukunft)",
        "bounds": c.BOUNDS,
        "now": now.isoformat() if now else None,
        "frames": frames,
    }
    json.dump(manifest, open(os.path.join(OUT, "frames.json"), "w"))
    shutil.copyfile(os.path.join(c.HERE, "index.html"), os.path.join(OUT, "index.html"))
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"OK: {len(frames)} Frames -> {OUT}/  (now={manifest['now']})")


if __name__ == "__main__":
    a = sys.argv[1:]
    lr = a[a.index("--radar") + 1] if "--radar" in a else None
    lf = a[a.index("--fc") + 1] if "--fc" in a else None
    build(local_radar=lr, local_fc=lf)

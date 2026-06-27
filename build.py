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
import warnings
warnings.filterwarnings("ignore", message="Mean of empty slice")
warnings.filterwarnings("ignore", message="invalid value encountered")
import inca_core as c

OUT = os.environ.get("INCA_SITE", "site")
RADAR_FRAMES = int(os.environ.get("RADAR_FRAMES", "24"))   # ~2 h bei 5-Min-Takt
FC_HOURS = int(os.environ.get("FC_HOURS", "24"))           # Rueckfall-Vorhersagestunden
ICON_HOURS = int(os.environ.get("ICON_HOURS", "30"))       # ICON-CH1 bis +X h (max 33)
ICOND2_HOURS = int(os.environ.get("ICOND2_HOURS", "12"))   # ICON-D2 (15-Min) bis +X h


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

    # ---- Zukunft: Mittelwert ICON-CH1 + ICON-D2, sonst das funktionierende Modell ----
    future_done = False
    try:
        import numpy as np
        from PIL import Image
        if local_icon_dir:
            raise RuntimeError("lokaler ICON-Test nicht implementiert")
        ch1, d2 = {}, {}
        try:
            ch1 = c.icon_ch1_fields(tmp, max_hours=ICON_HOURS, now=now)
        except Exception as e:
            import traceback; traceback.print_exc(); print("ICON-CH1 fehlgeschlagen:", e)
        try:
            d2 = c.icond2_fields(tmp, max_hours=ICOND2_HOURS, now=now)
        except Exception as e:
            import traceback; traceback.print_exc(); print("ICON-D2 fehlgeschlagen:", e)

        times = sorted(set(ch1) | set(d2))
        if now is not None:
            cutoff = now + dt.timedelta(hours=ICON_HOURS)
            times = [t for t in times if now < t <= cutoff]
        wet = n = both = 0; detail = []
        for t in times:
            parts = [a for a in (ch1.get(t), d2.get(t)) if a is not None]
            if not parts:
                continue
            if len(parts) > 1:                                  # beide -> Mittelwert je Pixel
                both += 1
                with np.errstate(all="ignore"):
                    field = np.nanmean(np.stack(parts), axis=0)
            else:                                               # nur eines -> Fallback
                field = parts[0]
            fn = f"f{n:02d}.png"
            Image.fromarray(c.colorize(field), "RGBA").save(os.path.join(OUT, fn))
            mxv = np.nanmax(field)
            mx = round(float(mxv), 1) if np.isfinite(mxv) else 0.0
            if mx > 0:
                wet += 1
            tag = "CH1+D2" if (t in ch1 and t in d2) else ("CH1" if t in ch1 else "D2")
            lead = round((t - now).total_seconds() / 3600, 2) if now else 0
            detail.append(f"+{lead}h[{tag}]:{mx}")
            frames.append({"file": fn, "time": t.isoformat(), "kind": "forecast", "max_mmh": mx})
            n += 1
        print(f"Vorhersage: {n} Bilder (CH1={len(ch1)}, D2={len(d2)}, gemittelt={both}), "
              f"davon {wet} mit Niederschlag")
        if detail:
            print("Vorhersage je Schritt: " + "  ".join(detail[:48]))
        future_done = n > 0
    except Exception as e:
        import traceback; traceback.print_exc()
        print("Vorhersage-Kombination fehlgeschlagen, Rueckfall auf Lokalprognose:", e)

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
        "source": "Radar & ICON-CH1: MeteoSchweiz · ICON-D2: DWD (Vorhersage = Mittelwert)",
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

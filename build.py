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
BLEND_MIN = float(os.environ.get("BLEND_MIN", "60"))       # Radar-Verankerung der Vorhersage (Min)


def _clean():
    os.makedirs(OUT, exist_ok=True)
    for f in glob.glob(os.path.join(OUT, "*.png")):
        os.remove(f)


def build(local_radar=None, local_fc=None, local_icon_dir=None):
    _clean()
    frames, now = [], None
    last_radar = None
    tmp = tempfile.mkdtemp(prefix="inca-")

    # ---- Radar: Vergangenheit -> jetzt ----
    try:
        if local_radar:
            assets = [(None, local_radar)]
        else:
            assets = c.radar_latest_assets(RADAR_FRAMES)
            print(f"Radar-Assets gefunden: {len(assets)}")
        rendered = []; radar_src = {}
        for i, (dtime, href) in enumerate(assets):
            try:
                src = href if os.path.exists(str(href)) else c.download(href, os.path.join(tmp, f"r{i}.h5"))
                fn = f"r{i:02d}.png"
                when, mx = c.render_radar(src, os.path.join(OUT, fn))
                rendered.append((when, fn, mx)); radar_src[when] = src
            except Exception as e:
                print("  Radarbild uebersprungen:", e)
        rendered.sort(key=lambda x: x[0])
        for when, fn, mx in rendered:
            frames.append({"file": fn, "time": when.isoformat(), "kind": "radar", "max_mmh": mx})
        last_radar = None
        if rendered:
            now = rendered[-1][0]
            try:
                _, last_radar = c.radar_grid(radar_src[now])     # rohes Feld fuer den Uebergang
            except Exception as e:
                print("  letztes Radarfeld nicht verfuegbar:", e)
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

        # ICON-CH1 zeitlich auf die D2-Schritte interpolieren -> gleichmaessige Vorhersage
        import bisect
        ch1_t = sorted(ch1)
        def ch1_at(t):
            if t in ch1:
                return ch1[t]
            if not ch1_t or t < ch1_t[0] or t > ch1_t[-1]:
                return None
            i = bisect.bisect_left(ch1_t, t)
            t0, t1 = ch1_t[i - 1], ch1_t[i]
            f = (t - t0).total_seconds() / (t1 - t0).total_seconds()
            return (1 - f) * ch1[t0] + f * ch1[t1]

        times = sorted(set(ch1) | set(d2))
        if now is not None:
            cutoff = now + dt.timedelta(hours=ICON_HOURS)
            times = [t for t in times if now < t <= cutoff]
        wet = n = both = anchored = 0; detail = []
        for t in times:
            a = ch1_at(t); b = d2.get(t)
            parts = [x for x in (a, b) if x is not None]
            if not parts:
                continue
            interp = "i" if (a is not None and t not in ch1) else ""
            if len(parts) > 1:                                  # beide -> Mittelwert je Pixel
                both += 1
                with np.errstate(all="ignore"):
                    field = np.nanmean(np.stack(parts), axis=0)
            else:                                               # nur eines -> Fallback
                field = parts[0]
            # Uebergang: in den ersten BLEND_MIN Minuten ans Radar anlehnen (weiche Naht)
            if last_radar is not None and now is not None:
                lead_min = (t - now).total_seconds() / 60.0
                if 0 < lead_min < BLEND_MIN:
                    w = 1.0 - lead_min / BLEND_MIN              # 1 -> 0 ueber die erste Stunde
                    overlap = np.isfinite(last_radar) & np.isfinite(field)
                    field = np.where(overlap, w * last_radar + (1 - w) * field, field)
                    anchored += 1
            fn = f"f{n:02d}.png"
            Image.fromarray(c.colorize(field), "RGBA").save(os.path.join(OUT, fn))
            mxv = np.nanmax(field)
            mx = round(float(mxv), 1) if np.isfinite(mxv) else 0.0
            if mx > 0:
                wet += 1
            tag = ("CH1" + interp + "+D2") if (a is not None and b is not None) else (("CH1" + interp) if a is not None else "D2")
            lead = round((t - now).total_seconds() / 3600, 2) if now else 0
            detail.append(f"+{lead}h[{tag}]:{mx}")
            frames.append({"file": fn, "time": t.isoformat(), "kind": "forecast", "max_mmh": mx})
            n += 1
        print(f"Vorhersage: {n} Bilder (CH1={len(ch1)}, D2={len(d2)}, gemittelt={both}, "
              f"radarverankert={anchored}), davon {wet} mit Niederschlag")
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

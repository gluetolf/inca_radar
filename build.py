#!/usr/bin/env python3
"""
build.py - erzeugt das statische Radar-Site-Verzeichnis (./site).

Setzt die Bausteine aus inca_core zu einer Animation zusammen:
  Vergangenheit/jetzt -> MeteoSchweiz-Radar (5-Min)
  Zukunft             -> Mittelwert aus ICON-CH1 (MeteoSchweiz) + ICON-D2 (DWD),
                         pro Bildpunkt gemittelt wo beide liefern, sonst das eine
                         Modell; CH1 wird auf 15 Min interpoliert und der Uebergang
                         in der ersten Stunde ans Radar angelehnt.
  Rueckfall           -> MeteoSchweiz-Lokalprognose (data4web CSV), falls beide
                         Modelle ausfallen.

Aufruf:
  python build.py                          # live (braucht offenes Internet)
  python build.py --radar x.h5 --fc y.csv  # lokale Dateien (Offline-Test)

Ausgabe in ./site/: index.html, frames.json, r*.png (Radar), f*.png (Vorhersage).
Quellen: MeteoSchweiz (OGD) und Deutscher Wetterdienst (CC BY 4.0).
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
AROME_HOURS = int(os.environ.get("AROME_HOURS", "12"))     # AROME (Météo-France) bis +X h
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
            assets = [(None, local_radar)]                      # Offline-Test: eine Datei
        else:
            assets = c.radar_latest_assets(RADAR_FRAMES)        # die letzten N 5-Min-Bilder
            print(f"Radar-Assets gefunden: {len(assets)}")
        rendered = []; radar_src = {}                           # radar_src: Zeit -> Quelldatei
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
            if mx < c.DISPLAY_FLOOR:                          # nichts Sichtbares -> als trocken melden
                mx = 0.0
            frames.append({"file": fn, "time": when.isoformat(), "kind": "radar", "max_mmh": mx})
        last_radar = None
        if rendered:
            now = rendered[-1][0]                               # "jetzt" = juengstes Radarbild
            try:
                # rohes Feld des letzten Radarbilds -> dient als Anker fuer den Uebergang
                _, last_radar = c.radar_grid(radar_src[now])
            except Exception as e:
                print("  letztes Radarfeld nicht verfuegbar:", e)
            span = f"{rendered[0][0].strftime('%H:%M')}–{rendered[-1][0].strftime('%H:%M')} UTC"
            print(f"Radar: {len(rendered)} Bilder ({span})")
        else:
            print("Radar: 0 Bilder")
    except Exception as e:
        print("Radar-Teil fehlgeschlagen:", e)

    # ---- Zukunft: Mittelwert aus ICON-CH1 + ICON-D2 + AROME, sonst die vorhandenen ----
    future_done = False
    try:
        import numpy as np
        from PIL import Image
        if local_icon_dir:
            raise RuntimeError("lokaler ICON-Test nicht implementiert")
        # Alle Modelle unabhaengig holen. Faellt eines aus, bleiben die anderen -> Fallback.
        ch1, d2, arome = {}, {}, {}      # je {Zeit: Raster mm/h}
        try:
            ch1 = c.icon_ch1_fields(tmp, max_hours=ICON_HOURS, now=now)
        except Exception as e:
            import traceback; traceback.print_exc(); print("ICON-CH1 fehlgeschlagen:", e)
        try:
            d2 = c.icond2_fields(tmp, max_hours=ICOND2_HOURS, now=now)
        except Exception as e:
            import traceback; traceback.print_exc(); print("ICON-D2 fehlgeschlagen:", e)
        try:
            arome = c.arome_fields(tmp, max_hours=AROME_HOURS, now=now)
        except Exception as e:
            import traceback; traceback.print_exc(); print("AROME fehlgeschlagen:", e)

        # CH1 und AROME sind stuendlich -> linear auf die 15-Min-Schritte interpolieren,
        # damit jeder Schritt denselben Charakter hat (kein "Pulsieren").
        import bisect
        def interp_factory(d):
            ts = sorted(d)
            def at(t):
                if t in d:
                    return d[t]
                if not ts or t < ts[0] or t > ts[-1]:
                    return None
                i = bisect.bisect_left(ts, t)
                t0, t1 = ts[i - 1], ts[i]
                f = (t - t0).total_seconds() / (t1 - t0).total_seconds()
                return (1 - f) * d[t0] + f * d[t1]
            return at
        ch1_at = interp_factory(ch1)
        arome_at = interp_factory(arome)

        # AROME deckt nur einen Ausschnitt ab. Damit an seiner Datengrenze keine harte
        # Kante entsteht, AROMEs Gewicht zum Rand hin weich auf 0 laufen lassen: Abstand
        # jedes Pixels zum Datenrand -> Rampe ueber AROME_FEATHER_PX Pixel (1 innen, 0 am Rand).
        AROME_W = float(os.environ.get("AROME_W", "1.0"))            # Grundgewicht von AROME
        AROME_FEATHER_PX = float(os.environ.get("AROME_FEATHER_PX", "25"))   # Randbreite (~0,25°)
        arome_feather = None
        if arome:
            from scipy.ndimage import distance_transform_edt
            amask = np.isfinite(next(iter(arome.values())))         # AROME-Datenmaske (Gebiet)
            dist = distance_transform_edt(amask)                    # Pixelabstand zum Rand (innen)
            arome_feather = (AROME_W * np.clip(dist / max(AROME_FEATHER_PX, 1), 0, 1)).astype("float32")

        # Zeitachse = alle Zeitpunkte aller Modelle, auf das Vorhersagefenster begrenzt
        times = sorted(set(ch1) | set(d2) | set(arome))
        if now is not None:
            cutoff = now + dt.timedelta(hours=ICON_HOURS)
            times = [t for t in times if now < t <= cutoff]
        wet = n = meaned = anchored = with_a = 0; detail = []
        for t in times:
            a = ch1_at(t); b = d2.get(t); cc = arome_at(t)      # CH1, D2, AROME (ggf. interpoliert)
            parts = [x for x in (a, b, cc) if x is not None]
            if not parts:
                continue
            # Gewichtetes Mittel je Pixel: CH1 und D2 mit Gewicht 1, AROME mit dem weichen
            # Randgewicht (innen 1, am Datenrand 0). So wird das Ergebnis dort, wo AROME
            # auslaeuft, stufenlos wieder zum CH1+D2-Mittel -> keine harte Kante. Wo nur ein
            # Modell vorhanden ist, bleibt dessen Wert (Fallback).
            num = np.zeros((c.DH, c.DW), "float32")
            den = np.zeros((c.DH, c.DW), "float32")
            nmodels = 0
            for f, w in ((a, None), (b, None), (cc, arome_feather)):
                if f is None:
                    continue
                nmodels += 1
                m = np.isfinite(f)
                wt = m.astype("float32") if w is None else np.where(m, w, 0.0).astype("float32")
                num += np.where(m, f, 0.0) * wt
                den += wt
            field = np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan).astype("float32")
            if nmodels > 1:
                meaned += 1
            if cc is not None:
                with_a += 1
            # Uebergang glaetten: in den ersten BLEND_MIN Minuten zum letzten Radarbild ueberblenden
            if last_radar is not None and now is not None:
                lead_min = (t - now).total_seconds() / 60.0
                if 0 < lead_min < BLEND_MIN:
                    w = 1.0 - lead_min / BLEND_MIN
                    overlap = np.isfinite(last_radar) & np.isfinite(field)
                    field = np.where(overlap, w * last_radar + (1 - w) * field, field)
                    anchored += 1
            # einfaerben, als PNG speichern, Frame vermerken
            fn = f"f{n:02d}.png"
            Image.fromarray(c.colorize(field), "RGBA").save(os.path.join(OUT, fn))
            mxv = np.nanmax(field)
            mx = round(float(mxv), 1) if np.isfinite(mxv) else 0.0   # fuer den "trocken"-Hinweis
            if mx < c.DISPLAY_FLOOR:                                  # nichts Sichtbares -> als trocken melden
                mx = 0.0
            if mx > 0:
                wet += 1
            # Diagnose-Etikett: welche Quelle(n) das Bild gespeist haben (i = interpoliert)
            srcs = []
            if a is not None:  srcs.append("CH1" + ("i" if t not in ch1 else ""))
            if b is not None:  srcs.append("D2")
            if cc is not None: srcs.append("A" + ("i" if t not in arome else ""))
            lead = round((t - now).total_seconds() / 3600, 2) if now else 0
            detail.append(f"+{lead}h[{'+'.join(srcs)}]:{mx}")
            frames.append({"file": fn, "time": t.isoformat(), "kind": "forecast", "max_mmh": mx})
            n += 1
        print(f"Vorhersage: {n} Bilder (CH1={len(ch1)}, D2={len(d2)}, AROME={len(arome)}, "
              f"gemittelt={meaned}, mit_AROME={with_a}, radarverankert={anchored}), davon {wet} mit Niederschlag")
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
        "source": "Radar & ICON-CH1: MeteoSchweiz · ICON-D2: DWD · AROME: Météo-France (Vorhersage = Mittelwert)",
        "bounds": c.BOUNDS,
        "now": now.isoformat() if now else None,
        "v": int(dt.datetime.now(dt.timezone.utc).timestamp()),   # Cache-Buster pro Build
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

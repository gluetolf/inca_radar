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
import concurrent.futures as cf
import inca_core as c


def _make_share_preview(radar_png, when, out_path):
    """Erzeugt drei Dateien fuers Link-Teilen (Open Graph):
    - preview.png       1200x630, CH-zentriert, mit Fussleiste  -> Standard-og:image
    - preview_full.png  ganze Radar-Domain OHNE Fussleiste      -> Rohmaterial fuer ogimg.php
    - footer.png        1200x66-Markenleiste mit Zeitstempel    -> klebt ogimg.php unten dran
    So kann der Server (ogimg.php) fuer geteilte ?c=-Links den exakten Ausschnitt zuschneiden."""
    import re
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1200, 630
    LW, LE, LS, LN = c.DST_W, c.DST_E, c.DST_S, c.DST_N        # Geografie des Radar-Ausschnitts
    im = Image.open(radar_png).convert("RGBA")
    sc = W / im.width
    nh = int(im.height * sc)
    im = im.resize((W, nh), Image.LANCZOS)
    full = Image.new("RGB", (W, nh), (226, 231, 222))          # ruhiger Papier-Hintergrund
    d = ImageDraw.Draw(full)
    try:                                                        # Schweizer Grenze aus places.js
        pj = open(os.path.join(c.HERE, "places.js"), encoding="utf-8").read()
        pts = json.loads(re.search(r"window\.CH_BORDER=(\[\[.*?\]\]);", pj).group(1))
        px = [((lon - LW) / (LE - LW) * W, (LN - lat) / (LN - LS) * nh) for lat, lon in pts]
        d.polygon(px, fill=(242, 245, 239), outline=(148, 156, 144))
    except Exception:
        pass
    full.paste(im, (0, 0), im)                                  # Radar darueber
    full.save(os.path.join(os.path.dirname(out_path), "preview_full.png"), "PNG", optimize=True)
    # Fussleiste (Marke + Stand) separat rendern
    try:
        f1 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
        f2 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    except Exception:
        f1 = f2 = ImageFont.load_default()
    try:
        from zoneinfo import ZoneInfo
        stand = when.astimezone(ZoneInfo("Europe/Zurich")).strftime("%H:%M")
    except Exception:
        stand = when.strftime("%H:%M UTC")
    bar = 66
    foot = Image.new("RGB", (W, bar), (24, 30, 22))
    d3 = ImageDraw.Draw(foot)
    d3.ellipse([24, 24, 42, 42], fill=(74, 222, 128))
    d3.text((58, 16), "Niederschlagsradar Schweiz", font=f1, fill=(240, 244, 238))
    try:
        d3.text((W - 24, 22), f"Stand {stand} · eigermaker.ch/radar", font=f2, fill=(180, 190, 178), anchor="ra")
    except Exception:
        d3.text((W - 460, 22), f"Stand {stand} · eigermaker.ch/radar", font=f2, fill=(180, 190, 178))
    foot.save(os.path.join(os.path.dirname(out_path), "footer.png"), "PNG", optimize=True)
    # Standard-Vorschau: CH-zentrierter Zuschnitt + Fussleiste
    cy = int((LN - 46.9) / (LN - LS) * nh)
    top = max(0, min(nh - H, cy - H // 2))
    out = full.crop((0, top, W, top + H))
    out.paste(foot, (0, H - bar))
    out.save(out_path, "PNG", optimize=True)


def _model_worker(fn, d, h, now, q):
    """Holt EIN Modell in einem eigenen Prozess; legt Ergebnis (oder Fehler) in die Queue."""
    try:
        q.put(fn(d, h, now))
    except Exception as e:
        import traceback; traceback.print_exc()
        q.put({"__error__": repr(e)})

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
    hail_max_all = 0.0
    hail_files = {}
    hail_cells = []
    tmp = tempfile.mkdtemp(prefix="inca-")

    # ---- Radar: Vergangenheit -> jetzt ----
    try:
        if local_radar:
            assets = [(None, local_radar)]                      # Offline-Test: eine Datei
        else:
            assets = c.radar_latest_assets(RADAR_FRAMES)        # die letzten N 5-Min-Bilder
            print(f"Radar-Assets gefunden: {len(assets)}")
        rendered = []; radar_src = {}                           # radar_src: Zeit -> Quelldatei
        # Quelldateien PARALLEL herunterladen (reines Netzwerk), danach seriell rendern (h5py).
        def _dl(item):
            i, (dtime, href) = item
            try:
                if os.path.exists(str(href)):
                    return i, href
                return i, c.download(href, os.path.join(tmp, f"r{i}.h5"))
            except Exception as e:
                print("  Radar-Download uebersprungen:", e); return i, None
        src_by_i = {}
        if assets:
            with cf.ThreadPoolExecutor(max_workers=min(12, len(assets))) as ex:
                for i, src in ex.map(_dl, list(enumerate(assets))):
                    src_by_i[i] = src
        # Hagel (POH/BZC, gleicher 5-Min-Takt) passend zu den Radarzeiten laden
        hail_by_t = {}
        if not local_radar:
            try:
                hmap = c.hail_assets(RADAR_FRAMES + 4)
                def _dlh(item):
                    j, (t, href) = item
                    try:
                        return t, c.download(href, os.path.join(tmp, f"h{j}.h5"))
                    except Exception:
                        return t, None
                with cf.ThreadPoolExecutor(max_workers=8) as ex:
                    for t, p_ in ex.map(_dlh, list(enumerate(sorted(hmap.items())))):
                        if p_:
                            hail_by_t[t] = p_
            except Exception as e:
                print("  Hagel (POH) nicht verfuegbar:", e)
        hail_max_all = 0.0
        for i, (dtime, href) in enumerate(assets):
            src = src_by_i.get(i)
            if not src:
                continue
            try:
                tmp_png = os.path.join(OUT, f"_r{i:02d}.png")
                when, mx = c.render_radar(src, tmp_png)
                fn = "r_" + when.strftime("%Y%m%dT%H%MZ") + ".png"   # stabiler Name je Messzeit
                os.replace(tmp_png, os.path.join(OUT, fn))
                rendered.append((when, fn, mx)); radar_src[when] = src
            except Exception as e:
                print("  Radarbild uebersprungen:", e)
        # Hagel als EIGENE Layer-Bilder rendern (h_*.png, gleiche 5-Min-Zeiten wie das Radar).
        hail_files = {}
        hail_cells = []
        radar_times = {}                                   # nur zu Radarzeiten vorhandene Hagelbilder anbieten
        for _w, _fn, _mx in rendered:
            radar_times[_w] = True
        for _t, _hp in sorted(hail_by_t.items()):
            if _t not in radar_times:
                continue
            try:
                _tmp = os.path.join(OUT, f"_h_{_t:%H%M}.png")
                _hw, _hmx, _hcells = c.render_hail(_hp, _tmp)
                hail_max_all = max(hail_max_all, _hmx)
                if _hcells:
                    hail_cells = _hcells                       # sortierte Zeiten -> am Ende die neuesten Zellen
                if os.path.exists(_tmp):                   # nur geschrieben, wenn Schraffur vorhanden
                    _hfn = "h_" + _t.strftime("%Y%m%dT%H%MZ") + ".png"
                    os.replace(_tmp, os.path.join(OUT, _hfn))
                    hail_files[_t.isoformat()] = _hfn
            except Exception as e:
                print("  Hagelbild uebersprungen:", e)
        if hail_by_t:
            print(f"Hagel (POH): max {hail_max_all:.0f}%  |  {len(hail_files)} Layer-Bilder")
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
            try:                                                # Vorschaubild fuers Link-Teilen
                _make_share_preview(os.path.join(OUT, rendered[-1][1]), rendered[-1][0],
                                    os.path.join(OUT, "preview.png"))
                print("Vorschaubild: preview.png (og:image)")
            except Exception as e:
                print("Vorschaubild fehlgeschlagen:", e)
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
        # Die drei Modelle sind unabhaengig -> PARALLEL in eigenen Prozessen holen
        # (eigene Temp-Unterordner; Prozesse statt Threads, da eccodes nicht threadsicher).
        # Faellt eines aus, bleiben die anderen; bei Parallel-Problemen seriell nachholen.
        ch1, d2, arome = {}, {}, {}      # je {Zeit: Raster mm/h}
        d_ch1 = os.path.join(tmp, "ch1"); d_d2 = os.path.join(tmp, "d2"); d_ar = os.path.join(tmp, "arome")
        for d in (d_ch1, d_d2, d_ar):
            os.makedirs(d, exist_ok=True)
        specs = [("ICON-CH1", c.icon_ch1_fields, d_ch1, ICON_HOURS),
                 ("ICON-D2",  c.icond2_fields,  d_d2,  ICOND2_HOURS),
                 ("AROME",    c.arome_fields,   d_ar,  AROME_HOURS)]
        res = {}
        t_models = dt.datetime.now()
        import time as _t, multiprocessing as mp
        MODEL_TIMEOUT = float(os.environ.get("MODEL_TIMEOUT", "150"))   # hartes Limit je Modell (Sekunden)
        try:
            ctx = mp.get_context("fork")
            procs = []
            for name, fn, d, h in specs:                          # alle drei gleichzeitig starten
                q = ctx.Queue()
                p = ctx.Process(target=_model_worker, args=(fn, d, h, now, q), daemon=True)
                p.start(); procs.append((name, p, q))
            deadline = _t.time() + MODEL_TIMEOUT                  # gemeinsame Frist -> Gesamtzeit gedeckelt
            for name, p, q in procs:
                remaining = max(1.0, deadline - _t.time())
                out = None
                try:
                    out = q.get(timeout=remaining)               # erst Ergebnis holen (Deadlock-Schutz bei grossen Feldern)
                except Exception:
                    out = None
                if isinstance(out, dict) and "__error__" in out:
                    print(name, "fehlgeschlagen:", out["__error__"]); out = None
                elif out is None and p.is_alive():
                    print(name, f"Zeitlimit {int(MODEL_TIMEOUT)}s ueberschritten -> abgebrochen")
                if p.is_alive():
                    p.terminate()
                p.join(5)
                res[name] = out or {}
        except Exception as e:                                    # Rueckfall: seriell, falls die Prozesse nicht gehen
            import traceback; traceback.print_exc(); print("Parallel-Abruf nicht moeglich, seriell:", e)
            for name, fn, d, h in specs:
                if res.get(name):
                    continue
                try:
                    res[name] = fn(d, h, now)
                except Exception as e2:
                    import traceback; traceback.print_exc(); print(name, "fehlgeschlagen:", e2)
        ch1   = res.get("ICON-CH1", {}) or {}
        d2    = res.get("ICON-D2",  {}) or {}
        arome = res.get("AROME",    {}) or {}
        print(f"Modelle geholt in {(dt.datetime.now()-t_models).total_seconds():.0f}s "
              f"(CH1={len(ch1)}, D2={len(d2)}, AROME={len(arome)})")

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
            fn = "f_" + t.strftime("%Y%m%dT%H%MZ") + ".png"   # stabiler Name je Gueltigkeitszeit
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
                fn = "f_" + t.strftime("%Y%m%dT%H%MZ") + ".png"   # stabiler Name je Gueltigkeitszeit
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
        "hail": {"max": round(hail_max_all), "files": hail_files,
                 "cells": [list(c_) for c_ in hail_cells]},   # neueste Hagelzellen [lat, lon, POH%] fuer den Spring-Hinweis
        "frames": frames,
    }
    json.dump(manifest, open(os.path.join(OUT, "frames.json"), "w"))
    shutil.copyfile(os.path.join(c.HERE, "index.html"), os.path.join(OUT, "index.html"))
    _aux = []
    _pj = os.path.join(c.HERE, "places.js")                    # ausgelagerte Kartendaten mitkopieren
    if os.path.exists(_pj):
        shutil.copyfile(_pj, os.path.join(OUT, "places.js")); _aux.append("places.js")
    _pk = os.path.join(c.HERE, "peaks.js")                     # Gipfeldaten mitkopieren
    if os.path.exists(_pk):
        shutil.copyfile(_pk, os.path.join(OUT, "peaks.js")); _aux.append("peaks.js")
    _fp = os.path.join(c.HERE, "fplaces.js")                   # Auslandsorte mitkopieren
    if os.path.exists(_fp):
        shutil.copyfile(_fp, os.path.join(OUT, "fplaces.js")); _aux.append("fplaces.js")
    for _sf in ("index.php", "ogimg.php", ".htaccess"):        # Server-Dateien fuer Link-Vorschau
        _sp = os.path.join(c.HERE, _sf)
        if os.path.exists(_sp):
            shutil.copyfile(_sp, os.path.join(OUT, _sf)); _aux.append(_sf)
    print("Mitkopiert ins site/:", ", ".join(_aux) if _aux else "(keine Zusatzdateien gefunden!)")
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"OK: {len(frames)} Frames -> {OUT}/  (now={manifest['now']})")


if __name__ == "__main__":
    a = sys.argv[1:]
    lr = a[a.index("--radar") + 1] if "--radar" in a else None
    lf = a[a.index("--fc") + 1] if "--fc" in a else None
    build(local_radar=lr, local_fc=lf)

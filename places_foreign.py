#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
places_foreign.py  –  EINMALIG lokal ausfuehren (wie peaks_refine.py).

Holt Auslandsorte fuer das Radar-Gebiet automatisch aus GeoNames (cities500:
alle Orte weltweit mit >= 500 Einwohnern) und schreibt fplaces.js
(window.FCITIES). Die Einwohnerzahl bestimmt, ab welcher Zoomstufe ein Ort
erscheint - Grossstaedte frueh, Kleinstaedte beim Reinzoomen, grenznahe
Doerfer zuletzt. Schweizer Orte werden uebersprungen (dafuer gibt es PLACES).

Aufruf:
    python3 places_foreign.py            # schreibt fplaces.js
"""

import io, sys, math, json, zipfile, urllib.request

GEONAMES_URL = "https://download.geonames.org/export/dump/cities500.zip"

# Radar-Bildausschnitt der App (wie DST_W/E/S/N in inca_core.py)
W, E, S, N = 2.6, 12.5, 43.6, 49.5
# "grenznah": erweiterte Schweiz-Box - hier erscheinen auch kleine Orte (>=1000 Ew.)
NW, NE, NS, NN = 5.3, 11.1, 45.2, 48.4

# Einwohner -> ab welcher Zoomstufe das Label erscheint (wie mz bei CITIES)
def mz_for(pop):
    if pop >= 750000: return 6
    if pop >= 250000: return 7
    if pop >=  90000: return 8
    if pop >=  35000: return 9
    if pop >=  12000: return 10
    if pop >=   4000: return 11
    return 12

# Bereits kuratierte Auslandsorte in places.js (deutsche Exonyme) -> Naehe-Duplikate ausschliessen
CURATED = [
 ("Mailand",45.464,9.190),("München",48.137,11.575),("Lyon",45.764,4.836),("Turin",45.070,7.687),
 ("Stuttgart",48.776,9.183),("Strassburg",48.573,7.752),("Genua",44.407,8.934),("Venedig",45.438,12.327),
 ("Bologna",44.494,11.343),("Florenz",43.770,11.256),("Nürnberg",49.454,11.077),("Karlsruhe",49.007,8.404),
 ("Verona",45.438,10.992),("Innsbruck",47.269,11.404),("Nizza",43.701,7.268),
 ("Grenoble",45.188,5.724),("Dijon",47.322,5.041),("Besançon",47.238,6.024),("Mulhouse",47.750,7.335),
 ("Freiburg i.Br.",47.999,7.842),("Ulm",48.401,9.987),("Augsburg",48.371,10.898),("Annecy",45.899,6.129),
 ("Aosta",45.737,7.315),("Como",45.808,9.085),("Bergamo",45.698,9.677),("Brescia",45.539,10.220),
 ("Trient",46.067,11.121),("Bozen",46.498,11.354),("Parma",44.801,10.328),
 ("Konstanz",47.660,9.175),("Bregenz",47.503,9.747),("Friedrichshafen",47.654,9.479),("Ravensburg",47.782,9.611),
 ("Kempten",47.726,10.313),("Colmar",48.079,7.358),("Chambéry",45.564,5.918),("Chamonix",45.924,6.869),
 ("Domodossola",46.114,8.293),("Varese",45.820,8.825),("Lecco",45.856,9.393),("Novara",45.447,8.622),
 ("Belfort",47.640,6.863),("Pontarlier",46.903,6.355),
 ("Annemasse",46.194,6.235),("Thonon-les-Bains",46.371,6.481),("Évian-les-Bains",46.401,6.588),
 ("Lörrach",47.615,7.661),("Waldshut",47.623,8.214),("Singen",47.762,8.840),("Lindau",47.546,9.684),
 ("Feldkirch",47.239,9.598),("Landeck",47.139,10.567),("Sondrio",46.169,9.879),("Ivrea",45.467,7.876),
 ("Verbania",45.936,8.554),("Luino",46.002,8.742),("Chiavenna",46.320,9.397),("Tirano",46.216,10.169),
]

def near_curated(lat, lon):
    for _, cla, clo in CURATED:
        if abs(lat-cla) < 0.035 and abs((lon-clo)*math.cos(math.radians(lat))) < 0.035:
            return True
    return False

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "fplaces.js"
    print("Lade GeoNames cities500.zip ...")
    data = urllib.request.urlopen(GEONAMES_URL, timeout=180).read()
    zf = zipfile.ZipFile(io.BytesIO(data))
    raw = zf.read("cities500.txt").decode("utf-8")

    rows = []; skipped_ch = 0; deduped = 0
    for line in raw.splitlines():
        f = line.split("\t")
        if len(f) < 15: continue
        try:
            lat, lon = float(f[4]), float(f[5])
            pop = int(f[14] or 0)
        except ValueError:
            continue
        if not (W <= lon <= E and S <= lat <= N): continue          # ausserhalb Radar-Gebiet
        if f[8] == "CH": skipped_ch += 1; continue                  # Schweiz: eigener Datensatz
        if f[6] != "P" or not f[7].startswith("PPL"): continue      # nur bewohnte Orte
        if f[7] in ("PPLX","PPLW","PPLQ","PPLH"): continue          # keine Ortsteile/Wuestungen
        grenznah = (NW <= lon <= NE and NS <= lat <= NN)
        if pop < 2000 and not grenznah: continue                    # kleine Orte nur grenznah
        if near_curated(lat, lon): deduped += 1; continue           # kuratierte Exonyme behalten Vorrang
        rows.append((round(lat,4), round(lon,4), f[1], mz_for(pop), pop))

    rows.sort(key=lambda r: (r[3], -r[4]))                          # wichtigste zuerst
    CAP = 9000
    if len(rows) > CAP:
        print(f"  Hinweis: {len(rows)} Orte gefunden -> auf {CAP} gekappt (kleinste zuletzt eingestufte entfallen)")
        rows = rows[:CAP]
    lines = ["// Auslandsorte aus GeoNames (cities1000), automatisch erzeugt von places_foreign.py",
             "// [ Name, lat, lon, abZoom ]  - Einblendstufe nach Einwohnerzahl",
             "window.FCITIES=["]
    for lat, lon, name, mz, pop in rows:
        lines.append(f'[{json.dumps(name, ensure_ascii=False)},{lat},{lon},{mz}],')
    lines.append("];")
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")

    from collections import Counter
    cnt = Counter(r[3] for r in rows)
    print(f"\n=== Bericht ===")
    print(f"Auslandsorte im Radar-Gebiet: {len(rows)}  (CH uebersprungen: {skipped_ch}, Duplikate zu kuratierten: {deduped})")
    print("Je Zoomstufe:", dict(sorted(cnt.items())))
    print(f"Geschrieben nach: {out}  ({len('\\n'.join(lines))//1024} KB)")
    print("-> fplaces.js ins Repo legen (gleicher Ordner wie places.js) und hochladen.")

if __name__ == "__main__":
    main()

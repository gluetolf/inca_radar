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


# Europaeische Hauptstaedte [Name, lat, lon] - fix ab Zoom 6, unabhaengig vom Radar-Gebiet.
# (Bern/Vaduz fehlen bewusst: stehen bereits in CITIES.)
CAPITALS = [
 ("Paris",48.8566,2.3522),("London",51.5074,-0.1278),("Madrid",40.4168,-3.7038),("Rom",41.9028,12.4964),
 ("Berlin",52.5200,13.4050),("Wien",48.2082,16.3738),("Prag",50.0755,14.4378),("Brüssel",50.8503,4.3517),
 ("Amsterdam",52.3676,4.9041),("Luxemburg",49.6116,6.1319),("Lissabon",38.7223,-9.1393),("Dublin",53.3498,-6.2603),
 ("Kopenhagen",55.6761,12.5683),("Oslo",59.9139,10.7522),("Stockholm",59.3293,18.0686),("Helsinki",60.1699,24.9384),
 ("Warschau",52.2297,21.0122),("Budapest",47.4979,19.0402),("Bratislava",48.1486,17.1077),("Ljubljana",46.0569,14.5058),
 ("Zagreb",45.8150,15.9819),("Belgrad",44.7866,20.4489),("Sarajevo",43.8563,18.4131),("Podgorica",42.4304,19.2594),
 ("Tirana",41.3275,19.8187),("Skopje",41.9973,21.4280),("Sofia",42.6977,23.3219),("Bukarest",44.4268,26.1025),
 ("Athen",37.9838,23.7275),("Chișinău",47.0105,28.8638),("Kiew",50.4501,30.5234),("Minsk",53.9006,27.5590),
 ("Moskau",55.7558,37.6173),("Riga",56.9496,24.1052),("Vilnius",54.6872,25.2797),("Tallinn",59.4370,24.7536),
 ("Reykjavik",64.1466,-21.9426),("Monaco",43.7384,7.4246),("San Marino",43.9424,12.4578),
 ("Andorra la Vella",42.5063,1.5218),("Valletta",35.8989,14.5146),("Nikosia",35.1856,33.3823),
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
        mz = mz_for(pop)
        if not grenznah: mz = max(mz, 9)                             # Fernfeld: fruehestens ab Zoom 9
        rows.append((round(lat,4), round(lon,4), f[1], mz, pop))

    # cities500-Eintraege, die praktisch AUF einer Hauptstadt liegen (Monaco, San Marino), entfernen
    def near_cap(lat, lon):
        for _, cla, clo in CAPITALS:
            if abs(lat-cla) < 0.03 and abs((lon-clo)*math.cos(math.radians(lat))) < 0.03:
                return True
        return False
    rows = [r for r in rows if not near_cap(r[0], r[1])]
    rows += [(round(cla,4), round(clo,4), name, 6, 10**9) for name, cla, clo in CAPITALS]
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

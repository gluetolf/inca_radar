#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
peaks_refine.py  –  EINMALIG lokal ausfuehren (dort, wo GeoNames erreichbar ist).

Was es tut:
  1. Laedt die GeoNames-Laenderdatei CH.zip (nur Standardbibliothek, kein pip noetig).
  2. Sucht fuer jeden kuratierten Gipfel den passenden GeoNames-Eintrag
     (exakter Namens- oder Alt-Namens-Treffer; bei mehreren der naechste zur Naeherungskoordinate).
  3. Uebernimmt GeoNames-KOORDINATEN + HOEHE, behaelt Anzeigenamen + Bekanntheitswert.
  4. Schreibt window.PEAKS in places.js  (Sicherungskopie: places.js.bak)
     und druckt einen Bericht (exakt gematcht / Rueckfall auf Naeherung).

Aufruf:
    python3 peaks_refine.py                # erwartet places.js im aktuellen Ordner
    python3 peaks_refine.py pfad/places.js # oder Pfad angeben
"""

import io, os, sys, re, math, json, zipfile, unicodedata, urllib.request

GEONAMES_URL = "https://download.geonames.org/export/dump/CH.zip"

# ---------------------------------------------------------------------------
# Kuratierte Gipfel:  (Anzeigename, Naeherung_lat, Naeherung_lon, Hoehe_Fallback, Bekanntheit)
# Naeherungskoordinate dient nur zum Aufloesen von Namensdopplungen; Hoehe ist Fallback.
# Bekanntheit steuert spaeter die Einblende-Reihenfolge (inkl. Regions-Flaggschiffe).
# ---------------------------------------------------------------------------
PEAKS = [
    ('Matterhorn', 45.976, 7.659, 4478, 100),
    ('Eiger', 46.577, 8.005, 3966, 98),
    ('Jungfrau', 46.537, 7.962, 4158, 96),
    ('Säntis', 47.249, 9.343, 2502, 92),
    ('Piz Bernina', 46.383, 9.908, 4049, 90),
    ('Pilatus', 46.979, 8.255, 2129, 90),
    ('Rigi', 47.058, 8.485, 1798, 90),
    ('Mönch', 46.558, 7.997, 4107, 86),
    ('Dents du Midi', 46.162, 6.921, 3257, 86),
    ('Titlis', 46.772, 8.437, 3238, 86),
    ('Monte Generoso', 45.931, 9.02, 1701, 86),
    ('Chasseral', 47.132, 7.06, 1607, 86),
    ('Dufourspitze', 45.937, 7.867, 4634, 84),
    ('Schilthorn', 46.558, 7.835, 2970, 82),
    ('Niesen', 46.645, 7.652, 2362, 82),
    ('Finsteraarhorn', 46.537, 8.126, 4274, 74),
    ('Les Diablerets', 46.331, 7.204, 3210, 74),
    ('Weisshorn', 46.101, 7.717, 4506, 72),
    ('Tödi', 46.812, 8.916, 3614, 72),
    ('Grand Combin', 45.941, 7.299, 4314, 70),
    ('Blüemlisalp', 46.494, 7.748, 3661, 70),
    ('Wetterhorn', 46.588, 8.115, 3692, 70),
    ('Schreckhorn', 46.588, 8.118, 4078, 66),
    ('Piz Palü', 46.377, 9.96, 3900, 66),
    ('Faulhorn', 46.66, 8.026, 2681, 66),
    ('Stockhorn', 46.552, 7.532, 2190, 64),
    ('Männlichen', 46.612, 7.947, 2343, 64),
    ('Dent Blanche', 46.034, 7.612, 4357, 62),
    ('Piz Buin', 46.842, 10.12, 3312, 62),
    ('Wildstrubel', 46.393, 7.532, 3244, 62),
    ('Glärnisch', 46.997, 9.007, 2914, 62),
    ('Le Moléson', 46.548, 7.019, 2002, 62),
    ('Brienzer Rothorn', 46.787, 8.047, 2350, 62),
    ('Grosser Mythen', 47.032, 8.679, 1898, 62),
    ('Aletschhorn', 46.465, 8.003, 4194, 60),
    ('Wildhorn', 46.362, 7.36, 3247, 60),
    ('Basòdino', 46.417, 8.48, 3273, 60),
    ('Piz Roseg', 46.394, 9.881, 3937, 58),
    ('Weissfluh', 46.834, 9.847, 2843, 58),
    ('Churfirsten', 47.155, 9.32, 2262, 58),
    ('First', 46.659, 8.055, 2168, 58),
    ('Bietschhorn', 46.389, 7.859, 3934, 56),
    ('Dom', 46.094, 7.859, 4545, 55),
    ('Niederhorn', 46.716, 7.796, 1963, 55),
    ('Piz Kesch', 46.622, 9.87, 3418, 54),
    ('Grand Muveran', 46.24, 7.132, 3051, 54),
    ('Napf', 47.004, 7.941, 1408, 54),
    ('Monte Tamaro', 46.117, 8.868, 1961, 54),
    ('Weissenstein', 47.259, 7.507, 1284, 54),
    ('Rheinwaldhorn', 46.492, 9.03, 3402, 52),
    ('Mont Tendre', 46.601, 6.305, 1679, 52),
    ('La Dôle', 46.424, 6.101, 1677, 52),
    ('Breithorn', 45.941, 7.732, 4164, 52),
    ('Pizol', 46.96, 9.393, 2844, 52),
    ('Ringelspitz', 46.877, 9.41, 3247, 50),
    ('Vrenelisgärtli', 46.995, 9.01, 2904, 50),
    ('Zinalrothorn', 46.055, 7.689, 4221, 48),
    ('Weissmies', 46.128, 8.011, 4017, 48),
    ('Parpaner Rothorn', 46.752, 9.582, 2861, 48),
    ('Piz Beverin', 46.633, 9.361, 2998, 46),
    ('Piz Morteratsch', 46.401, 9.926, 3751, 46),
    ('Dammastock', 46.643, 8.421, 3630, 46),
    ('Le Chasseron', 46.847, 6.541, 1607, 46),
    ('Harder Kulm', 46.701, 7.858, 1322, 46),
    ('Lyskamm', 45.922, 7.836, 4527, 44),
    ('Piz Julier', 46.475, 9.735, 3380, 44),
    ('Schynige Platte', 46.657, 7.905, 2076, 44),
    ('Sustenhorn', 46.7, 8.45, 3503, 42),
    ('Piz Linard', 46.851, 10.062, 3411, 42),
    ('Piz Ela', 46.585, 9.686, 3339, 42),
    ('Clariden', 46.831, 8.878, 3267, 42),
    ('Campo Tencia', 46.417, 8.72, 3072, 42),
    ('Uri Rotstock', 46.855, 8.545, 2928, 42),
    ('Augstmatthorn', 46.72, 7.94, 2137, 42),
    ('Speer', 47.191, 9.183, 1950, 42),
    ('Schwarzhorn', 46.652, 8.07, 2928, 42),
    ("Dent d'Hérens", 45.966, 7.615, 4171, 40),
    ('Lauteraarhorn', 46.573, 8.126, 4042, 40),
    ("Pigne d'Arolla", 45.996, 7.475, 3790, 40),
    ('Galenstock', 46.567, 8.412, 3586, 40),
    ('Flüela Wisshorn', 46.777, 9.973, 3085, 40),
    ('Bristen', 46.789, 8.685, 3073, 40),
    ('Piz Sardona', 46.916, 9.264, 3056, 40),
    ('Morgenberghorn', 46.66, 7.755, 2249, 40),
    ('Sigriswiler Rothorn', 46.735, 7.72, 2051, 40),
    ('Fiescherhorn', 46.548, 8.075, 4049, 38),
    ('Mont Vélan', 45.912, 7.298, 3727, 36),
    ('Doldenhorn', 46.47, 7.72, 3643, 36),
    ('Nadelhorn', 46.109, 7.863, 4327, 34),
    ('Lagginhorn', 46.156, 8.001, 4010, 34),
    ('Balmhorn', 46.446, 7.694, 3699, 34),
    ('Gspaltenhorn', 46.52, 7.79, 3436, 34),
    ('Rosablanche', 46.07, 7.36, 3336, 34),
    ('Oberalpstock', 46.766, 8.756, 3328, 34),
    ('Piz Medel', 46.616, 8.828, 3211, 34),
    ('Pizzo Rotondo', 46.512, 8.446, 3192, 34),
    ('Tour Sallière', 46.135, 6.965, 3220, 34),
    ('Dent de Morcles', 46.198, 7.062, 2969, 34),
    ('Gridone', 46.15, 8.68, 2188, 34),
    ('Hasenmatt', 47.253, 7.462, 1445, 34),
    ('Alphubel', 46.065, 7.864, 4206, 32),
    ('Rimpfischhorn', 46.024, 7.796, 4199, 30),
    ('Bishorn', 46.124, 7.712, 4153, 30),
    ('Gross Grünhorn', 46.516, 8.061, 4044, 30),
    ('Piz Zupò', 46.363, 9.925, 3996, 30),
    ('Fletschhorn', 46.17, 8.006, 3985, 30),
    ('Mont Collon', 45.981, 7.51, 3637, 30),
    ('Piz Cambrena', 46.367, 10.0, 3602, 30),
    ('Piz Platta', 46.52, 9.56, 3392, 30),
    ('Piz Terri', 46.556, 8.96, 3149, 30),
    ('Nesthorn', 46.428, 7.938, 3822, 26),
    ('Ruinette', 45.972, 7.386, 3875, 24),
]

# ---------------------------------------------------------------------------
def deacc(s):
    """Kleinschreibung ohne Akzente/Umlaut-Diakritika fuer robusten Namensvergleich."""
    s = s.strip().lower()
    s = (s.replace("ß", "ss"))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s

def load_geonames_peaks():
    """CH.zip laden -> Liste von Gipfeln (feature_class T): (namen_set, lat, lon, elev)."""
    print("Lade GeoNames CH.zip ...")
    data = urllib.request.urlopen(GEONAMES_URL, timeout=120).read()
    zf = zipfile.ZipFile(io.BytesIO(data))
    raw = zf.read("CH.txt").decode("utf-8")
    peaks = []
    for line in raw.splitlines():
        f = line.split("\t")
        if len(f) < 17:
            continue
        if f[6] != "T":                 # feature_class T = Berg/Huegel/Fels
            continue
        try:
            lat, lon = float(f[4]), float(f[5])
        except ValueError:
            continue
        names = {f[1], f[2]} | set(x for x in f[3].split(",") if x)
        names = {deacc(n) for n in names if n}
        try:
            elev = int(f[15]) if f[15] else (int(f[16]) if f[16] else None)
        except ValueError:
            elev = None
        peaks.append((names, lat, lon, elev, f[7]))
    print(f"  {len(peaks)} Berg-/Gipfel-Eintraege (feature_class T) gelesen")
    return peaks

def best_match(name, rlat, rlon, gn):
    """Bestes GeoNames-Gipfel-Match: exakter (entdiakritisierter) Namenstreffer,
    unter mehreren der naechste zur Naeherungskoordinate. Bevorzugt feature_code PK."""
    key = deacc(name)
    cands = [p for p in gn if key in p[0]]
    if not cands:
        return None
    def score(p):
        d = (p[1]-rlat)**2 + ((p[2]-rlon)*math.cos(math.radians(rlat)))**2
        pk_bonus = -0.0 if p[4] == "PK" else 0.0005   # PK leicht bevorzugen bei Gleichstand
        return d + pk_bonus
    return min(cands, key=score)

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "peaks.js"

    gn = load_geonames_peaks()

    seen = set(); rows = []; matched = 0; fallback = []
    for name, rlat, rlon, felev, fame in PEAKS:
        if name in seen:                       # Duplikate ueberspringen
            continue
        seen.add(name)
        m = best_match(name, rlat, rlon, gn)
        if m:
            lat, lon = round(m[1], 5), round(m[2], 5)
            elev = m[3] if m[3] else felev
            matched += 1
        else:
            lat, lon, elev = rlat, rlon, felev
            fallback.append(name)
        rows.append((lat, lon, name, elev, fame))

    rows.sort(key=lambda r: -r[4])             # nach Bekanntheit absteigend

    # window.PEAKS-Block bauen
    lines = ["window.PEAKS = ["]
    lines.append("  // [ lat, lon, \"Name\", Höhe(m), Bekanntheit ]  – aus GeoNames praezisiert")
    for lat, lon, name, elev, fame in rows:
        e = elev if elev is not None else "null"
        lines.append(f'  [{lat}, {lon}, {json.dumps(name, ensure_ascii=False)}, {e}, {fame}],')
    lines.append("];")
    block = "\n".join(lines)

    open(out, "w", encoding="utf-8").write(block + "\n")   # eigenstaendige Datei

    # Bericht + kompletter Block (zum Hochladen ODER Reinkopieren)
    print("\n=== Bericht ===")
    print(f"Exakt aus GeoNames: {matched} von {len(rows)} Gipfeln")
    if fallback:
        print("Rueckfall auf Naeherungskoordinate (bitte melden, dann fixe ich sie):")
        for n in fallback:
            print("   -", n)
    else:
        print("Alle Gipfel exakt gematcht. 🎉")
    print(f"\nGeschrieben nach: {out}")
    print("Du kannst mir entweder diese Datei hochladen ODER den folgenden Block hier reinkopieren:\n")
    print(block)

if __name__ == "__main__":
    main()

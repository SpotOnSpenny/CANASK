"""Builder for static/assets/das_city_coords.json — the DAS Explorer's city gazetteer.

The city bubble map needs a lat/lon for every distinct (city, province) pair in the
das_* tables, keyed exactly as the pivot API emits city row keys ("Montréal, QC" —
the DB's verbatim accented spelling). No coordinates exist anywhere else in the app,
so this is a maintained asset: re-run `flask build-das-gazetteer` after monthly DAS
ingests introduce new cities, then commit the refreshed JSON.

Coordinates come from a GeoNames Canada dump (https://download.geonames.org/export/
dump/CA.zip, CC-BY 4.0), matched with normalization (case/accents/punctuation,
St./Ste. ↔ Saint-/Sainte-) applied to BOTH sides at match time only — the emitted
keys always stay verbatim. Populated places win over administrative areas, bigger
population wins within a name. Anything still unmatched is printed for a MANUAL_COORDS
entry; the visible "not shown" indicator on the map keeps the gaps honest meanwhile.
"""

import io
import json
import unicodedata
import zipfile
from pathlib import Path

from data_viz import db
from data_viz.database.models import DasSamples, DasQuant

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
ASSET_PATH = Path(__file__).resolve().parent / "static" / "assets" / "das_city_coords.json"

# GeoNames admin1 codes for Canada are numeric strings, not postal abbreviations.
GEONAMES_ADMIN1 = {
    "01": "AB", "02": "BC", "03": "MB", "04": "NB", "05": "NL", "07": "NS",
    "08": "ON", "09": "PE", "10": "QC", "11": "SK", "12": "YT", "13": "NT", "14": "NU",
}

# Stragglers the GeoNames match can't resolve. Keys are the verbatim DB "City, PR"
# form; values are (lat, lon). Add entries here when the build prints unmatched names.
MANUAL_COORDS = {
    "Kebaowek, QC": (46.7834, -78.9829),               # Eagle Village First Nation, at Kipawa
    "M'chigeeng First Nation, ON": (45.83, -82.16),    # Manitoulin Island (formerly West Bay)
    "North Bay Po Main, ON": (46.3168, -79.4663),      # postal-outlet form of North Bay
    "Pessamit, QC": (48.9413, -68.646),                # Innu name; GeoNames has Betsiamites
    "Rosslyn, ON": (48.37, -89.42),                    # rural community in Oliver Paipoonge
    "Sackville, NS": (44.776, -63.6787),               # Lower Sackville (plain Sackville is NB)
    "Scanterbury, MB": (50.42, -96.46),                # Brokenhead Ojibway Nation area
    "Uashat, QC": (50.2167, -66.4333),                 # Innu community within Sept-Îles
    "Waywayseecappo, MB": (50.72, -100.85),            # First Nation north of Rossburn
}


def _norm(name):
    """Matching-only normalization: casefold, strip accents, punctuation → spaces,
    St./Ste. → Saint/Sainte. Never applied to emitted keys."""
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.casefold().replace("’", "").replace("'", "")  # God's -> gods (GeoNames drops them)
    name = name.replace("-", " ").replace(".", " ")
    tokens = [{"st": "saint", "ste": "sainte"}.get(t, t) for t in name.split()]
    return " ".join(tokens)


def _load_geonames(path):
    """(province, normalized name) → (priority, population, lat, lon). Indexes every
    name variant (name, asciiname, alternatenames); populated places (class P) beat
    admin areas (class A) — municipalities like La Pêche only exist as the latter —
    and within a class the biggest population wins."""
    index = {}
    with zipfile.ZipFile(path) as archive, archive.open("CA.txt") as raw:
        for line in io.TextIOWrapper(raw, encoding="utf-8"):
            fields = line.rstrip("\n").split("\t")
            feature_class = fields[6]
            if feature_class not in ("P", "A"):
                continue
            province = GEONAMES_ADMIN1.get(fields[10])
            if province is None:
                continue
            candidate = (1 if feature_class == "P" else 0, int(fields[14] or 0),
                         float(fields[4]), float(fields[5]))
            names = {fields[1], fields[2], *fields[3].split(",")}
            for name in names:
                if not name:
                    continue
                key = (province, _norm(name))
                if candidate[:2] > index.get(key, (-1, -1))[:2]:
                    index[key] = candidate
    return index


def build_gazetteer(file=None):
    """Regenerate the gazetteer asset from the DB's distinct (city, province) pairs.
    Returns (mapped count, unmatched keys)."""
    path = Path(file) if file else OUTPUT_DIR / "geonames_CA.zip"
    if not path.is_absolute():
        path = OUTPUT_DIR / path
    index = _load_geonames(path)

    pairs = set()
    for model in (DasSamples, DasQuant):  # NPS carries no city column
        pairs.update(db.session.query(model.city, model.province)
                     .filter(model.city.isnot(None), model.province.isnot(None))
                     .distinct().all())

    coords, unmatched = {}, []
    for city, province in sorted(pairs):
        key = f"{city}, {province}"
        if key in MANUAL_COORDS:
            lat, lon = MANUAL_COORDS[key]
        else:
            hit = index.get((province, _norm(city)))
            if hit is None:
                unmatched.append(key)
                continue
            lat, lon = hit[2], hit[3]
        coords[key] = [round(lat, 4), round(lon, 4)]

    ASSET_PATH.write_text(
        json.dumps(coords, ensure_ascii=False, sort_keys=True, indent=0) + "\n",
        encoding="utf-8")
    print(f"{len(coords)} cities mapped ({ASSET_PATH.stat().st_size / 1024:.0f} KB), "
          f"{len(unmatched)} unmatched")
    for key in unmatched:
        print(f"  unmatched: {key}  (add to MANUAL_COORDS)")
    return len(coords), unmatched

"""One-off builder for static/assets/geojsons/canada-provinces.geojson (DAS Explorer maps).

Run locally (not in-container), like rewind_geo.py:

    python data_viz/build_canada_provinces_geo.py

Needs network (Natural Earth download) and node/npx (mapshaper does topology-aware
simplification, so shared provincial borders stay gap-free — per-ring simplifiers
like Douglas-Peucker leave slivers between neighbours). The committed geojson is
the authoritative asset; this script exists so it can be rebuilt or re-tuned.

Source: Natural Earth 1:50m Admin-1 states/provinces (public domain), filtered to
the 13 Canadian jurisdictions. Output schema per feature:

    properties: {"code": "AB", "name": "Alberta"}   # code = 2-letter Prov/Terr,
                                                    # exactly the DB's das_* values

The choropleth trace uses featureidkey "properties.code". Rings are rewound to the
legacy (non-RFC-7946, exterior-clockwise) order Plotly requires — same reason
rewind_geo.py exists — and coordinates are rounded to 3 decimals (~110 m) to keep
the payload small.
"""

import sys

# Running this file directly puts data_viz/ first on sys.path, where email.py would
# shadow the stdlib email package that urllib.request imports. Drop it — this script
# only uses the standard library.
sys.path.pop(0)

import json
import pathlib
import tempfile
import urllib.request
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from geo_build_utils import normalize_geometry, run_mapshaper, write_collection  # noqa: E402

NE_URL = "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_1_states_provinces.zip"
SIMPLIFY = "35%"  # mapshaper retention; ~60 KB output. Raise for fidelity, lower for size.
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "data_viz" / "static" / "assets" / "geojsons" / "canada-provinces.geojson"
CACHE_ZIP = REPO_ROOT / "output" / "ne_50m_admin_1_states_provinces.zip"

EXPECTED_CODES = {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}



def main():
    CACHE_ZIP.parent.mkdir(exist_ok=True)
    if not CACHE_ZIP.exists():
        print(f"downloading {NE_URL}")
        urllib.request.urlretrieve(NE_URL, CACHE_ZIP)

    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(CACHE_ZIP).extractall(tmp)
        shp = next(pathlib.Path(tmp).glob("*.shp"))
        raw = pathlib.Path(tmp) / "canada.geojson"
        run_mapshaper(
            [str(shp),
             "-filter", 'iso_a2 === "CA"',
             "-simplify", SIMPLIFY, "keep-shapes",
             "-filter-fields", "iso_3166_2,name_en",
             "-o", "format=geojson", "precision=0.001", str(raw)]
        )
        data = json.loads(raw.read_text())

    features = []
    for feature in data["features"]:
        code = feature["properties"]["iso_3166_2"].removeprefix("CA-")
        geometry = normalize_geometry(feature["geometry"])
        features.append({
            "type": "Feature",
            "properties": {"code": code, "name": feature["properties"]["name_en"]},
            "geometry": geometry,
        })
    features.sort(key=lambda f: f["properties"]["code"])

    codes = {f["properties"]["code"] for f in features}
    assert codes == EXPECTED_CODES, f"unexpected codes: {codes ^ EXPECTED_CODES}"

    write_collection(OUT_PATH, features)


if __name__ == "__main__":
    main()

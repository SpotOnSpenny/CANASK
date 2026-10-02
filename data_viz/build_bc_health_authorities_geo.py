"""One-off builder for static/assets/geojsons/british-columbia.geojson (BC health-authority maps).

Run locally (not in-container), like build_canada_provinces_geo.py:

    python data_viz/build_bc_health_authorities_geo.py

Needs network (BC Data Catalogue WFS download, cached under output/) and node/npx
(mapshaper does topology-aware simplification, so the shared authority borders stay
gap-free). The committed geojson is the authoritative asset; this script exists so it
can be rebuilt or re-tuned.

Source: BC Data Catalogue "Health Authority Boundaries"
(WHSE_ADMIN_BOUNDARIES.BCHA_HEALTH_AUTHORITY_BNDRY_SP, Open Government Licence - BC),
fetched as GeoJSON in EPSG:4326. The original hand-drawn asset had ~150 vertices per
authority and turned Vancouver Island into a blob fused to the mainland; this keeps the
real coastline at a web-friendly size. Output schema per feature:

    properties: {"ENGNAME": "Interior"}   # _BC_HEALTH_AUTHORITIES in generate_visuals, minus the
                                          # province-wide "British Columbia" row (no polygon)

"Vancouver Island" in the source is renamed to "Island" to match the Coroners data. Tiny
islets are dropped (they add bytes, not meaning, at province scale), rings are rewound to
the legacy exterior-clockwise order Plotly requires, and coordinates are rounded to 3
decimals (~110 m).
"""

import sys

# Running this file directly puts data_viz/ first on sys.path, where email.py would shadow the
# stdlib email package that urllib.request imports. Drop it before the stdlib imports; it is put back
# below, once urllib.request has loaded the real email package, so geo_build_utils can be imported.
sys.path.pop(0)

import json
import pathlib
import tempfile
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from geo_build_utils import (check_geojson, check_names, download_cached,  # noqa: E402
                             normalize_geometry, run_mapshaper, write_collection)

LAYER = "WHSE_ADMIN_BOUNDARIES.BCHA_HEALTH_AUTHORITY_BNDRY_SP"
WFS_URL = (
    f"https://openmaps.gov.bc.ca/geo/pub/{LAYER}/ows?service=WFS&version=2.0.0"
    f"&request=GetFeature&typeName={LAYER}&outputFormat=json&srsName=EPSG:4326"
)
SIMPLIFY = "2%"  # mapshaper retention; see the size printed at the end. Raise for fidelity.
MIN_ISLAND_AREA = "4km2"  # islets smaller than this are dropped (mapshaper -filter-islands)
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "data_viz" / "static" / "assets" / "geojsons" / "british-columbia.geojson"
CACHE_RAW = REPO_ROOT / "output" / "bc_health_authority_boundaries.geojson"

RENAMES = {"Vancouver Island": "Island"}
EXPECTED_NAMES = {"Interior", "Fraser", "Vancouver Coastal", "Island", "Northern"}



def main():
    download_cached(WFS_URL, CACHE_RAW, check_geojson)

    with tempfile.TemporaryDirectory() as tmp:
        simplified = pathlib.Path(tmp) / "bc.geojson"
        run_mapshaper(
            [str(CACHE_RAW),
             "-filter-islands", f"min-area={MIN_ISLAND_AREA}", "remove-empty",
             "-simplify", SIMPLIFY, "keep-shapes",
             "-filter-fields", "HLTH_AUTHORITY_NAME",
             "-o", "format=geojson", "precision=0.001", str(simplified)]
        )
        data = json.loads(simplified.read_text())

    features = []
    for feature in data["features"]:
        name = feature["properties"]["HLTH_AUTHORITY_NAME"]
        geometry = normalize_geometry(feature["geometry"])
        features.append({
            "type": "Feature",
            "properties": {"ENGNAME": RENAMES.get(name, name)},
            "geometry": geometry,
        })
    features.sort(key=lambda f: f["properties"]["ENGNAME"])

    check_names(features, EXPECTED_NAMES)

    write_collection(OUT_PATH, features)


if __name__ == "__main__":
    main()

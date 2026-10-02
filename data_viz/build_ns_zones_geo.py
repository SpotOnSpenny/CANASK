"""One-off builder for static/assets/geojsons/nova-scotia.geojson (NS health-zone maps).

Run locally (not in-container), like the other build_*_geo.py scripts:

    python data_viz/build_ns_zones_geo.py

Needs network (Nova Scotia Open Data download, cached under output/) and node/npx (mapshaper).
The committed geojson is the authoritative asset; this script exists so it can be rebuilt
or re-tuned.

Source: Nova Scotia Open Data "Nova Scotia Health Authority Management Zones" (dataset
s3ax-gi3m, Open Government Licence - Nova Scotia), GeoJSON export in WGS84 -- the real
coastline, unlike the generalized Statistics Canada polygons the previous asset used (which
turned Cape Breton into a blob). Output schema per feature: properties {"ENGNAME": "Central"}
-- the four zone names exactly as the NS cleaner emits them (_NS_ZONES in generate_visuals).
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

SOURCE_URL = "https://data.novascotia.ca/api/geospatial/s3ax-gi3m?method=export&format=GeoJSON"
SIMPLIFY = "20%"            # mapshaper retention of the 60k-vertex source; see the printed size
MIN_ISLAND_AREA = "1km2"
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "data_viz" / "static" / "assets" / "geojsons" / "nova-scotia.geojson"
CACHE_RAW = REPO_ROOT / "output" / "ns_health_zones.geojson"

EXPECTED_ZONES = {"Western", "Northern", "Eastern", "Central"}


def main():
    download_cached(SOURCE_URL, CACHE_RAW, check_geojson)

    with tempfile.TemporaryDirectory() as tmp:
        simplified = pathlib.Path(tmp) / "ns.geojson"
        run_mapshaper([str(CACHE_RAW),
                       "-filter-islands", f"min-area={MIN_ISLAND_AREA}", "remove-empty",
                       "-simplify", SIMPLIFY, "keep-shapes",
                       "-filter-fields", "name",
                       "-o", "format=geojson", "precision=0.001", str(simplified)])
        data = json.loads(simplified.read_text())

    features = [{
        "type": "Feature",
        "properties": {"ENGNAME": feature["properties"]["name"]},
        "geometry": normalize_geometry(feature["geometry"]),
    } for feature in data["features"]]
    features.sort(key=lambda f: f["properties"]["ENGNAME"])

    check_names(features, EXPECTED_ZONES)
    write_collection(OUT_PATH, features)


if __name__ == "__main__":
    main()

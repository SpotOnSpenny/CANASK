"""One-off builder for static/assets/geojsons/ontario.geojson (Ontario public-health-unit maps).

Run locally (not in-container), like the other build_*_geo.py scripts:

    python data_viz/build_ontario_phu_geo.py

Needs network (Statistics Canada download, cached under output/) and node/npx (mapshaper).
The committed geojson is the authoritative asset; this script exists so it can be rebuilt
or re-tuned.

Source: Statistics Canada Health Region Boundary Files, 2023 edition, Ontario public health
units, CARTOGRAPHIC version (HR_035b23a) -- clipped to the coastline, so the Great Lakes,
Georgian Bay and Hudson Bay are water, not polygon. (The previous asset came from the
DIGITAL version, whose polygons extend into the water and made the province a blob.)
The file is in Lambert conformal conic; mapshaper reprojects it to WGS84.

Output schema per feature: properties {"HR_UID": "3595", "ENGNAME": "Toronto Public Health"}
-- ENGNAME is the ODPRN column name the Ontario cleaner emits (NOT StatCan's ENGNAME), carried
over by HR_UID from the previous asset via PHU_NAMES below. StatCan renumbered Huron Perth
3539 -> 3550 in the 2023 edition; the name is the same.
"""

import sys

# Running this file directly puts data_viz/ first on sys.path, where email.py would shadow the
# stdlib email package that urllib.request imports. Drop it before the stdlib imports.
sys.path.pop(0)

import json
import pathlib
import tempfile
import urllib.request
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from geo_build_utils import normalize_geometry, run_mapshaper, write_collection  # noqa: E402

SOURCE_URL = "https://www150.statcan.gc.ca/pub/82-402-x/2024001/hrbf-flrs/carto/ArcGIS/HR_035b23a_e.zip"
SIMPLIFY = "1%"             # mapshaper retention of the 3.9M-vertex source; see the printed size
MIN_ISLAND_AREA = "2km2"    # islets below this are dropped (bytes, not meaning, at province scale)
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "data_viz" / "static" / "assets" / "geojsons" / "ontario.geojson"
CACHE_ZIP = REPO_ROOT / "output" / "HR_035b23a_e.zip"

# HR_UID -> the health-unit name the ODPRN data uses (what the cleaner emits as geo).
PHU_NAMES = {
    "3526": "Algoma Public Health",
    "3527": "Brant County Health Unit",
    "3530": "Durham Region Health Department",
    "3533": "Grey Bruce Health Unit",
    "3534": "Haldimand-Norfolk Health Unit",
    "3535": "Haliburton, Kawartha, Pine Ridge District Health Unit",
    "3536": "Halton Region Public Health",
    "3537": "City of Hamilton Public Health Services",
    "3538": "Hastings Prince Edward Public Health",
    "3540": "Chatham-Kent Public Health",
    "3541": "Kingston, Frontenac and Lennox & Addington Health Unit",
    "3542": "Lambton Public Health",
    "3543": "Leeds, Grenville & Lanark District Health Unit",
    "3544": "Middlesex-London Health Unit",
    "3546": "Niagara Region Public Health",
    "3547": "North Bay Parry Sound District Health Unit",
    "3549": "Northwestern Health Unit",
    "3550": "Huron Perth Health Unit",
    "3551": "Ottawa Public Health",
    "3553": "Peel Public Health",
    "3555": "Peterborough Public Health",
    "3556": "Porcupine Health Unit",
    "3557": "Renfrew County and District Health Unit",
    "3558": "Eastern Ontario Health Unit",
    "3560": "Simcoe Muskoka District Health Unit",
    "3561": "Sudbury and District Health",
    "3562": "Thunder Bay District Health Unit",
    "3563": "Timiskaming Health Unit",
    "3565": "Region of Waterloo Public Health",
    "3566": "Wellington-Dufferin-Guelph Public Health",
    "3568": "Windsor-Essex County Health Unit",
    "3570": "York Region Public Health",
    "3575": "Southwestern Public Health",
    "3595": "Toronto Public Health",
}


def main():
    CACHE_ZIP.parent.mkdir(exist_ok=True)
    if not CACHE_ZIP.exists():
        print(f"downloading {SOURCE_URL}")
        urllib.request.urlretrieve(SOURCE_URL, CACHE_ZIP)

    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(CACHE_ZIP).extractall(tmp)
        shp = next(pathlib.Path(tmp).glob("*.shp"))
        raw = pathlib.Path(tmp) / "ontario.geojson"
        run_mapshaper([str(shp), "-proj", "wgs84",
                       "-filter-islands", f"min-area={MIN_ISLAND_AREA}", "remove-empty",
                       "-simplify", SIMPLIFY, "keep-shapes",
                       "-filter-fields", "HR_UID",
                       "-o", "format=geojson", "precision=0.001", str(raw)])
        data = json.loads(raw.read_text())

    features = []
    for feature in data["features"]:
        uid = str(feature["properties"]["HR_UID"])
        features.append({
            "type": "Feature",
            "properties": {"HR_UID": uid, "ENGNAME": PHU_NAMES[uid]},
            "geometry": normalize_geometry(feature["geometry"]),
        })
    features.sort(key=lambda f: f["properties"]["ENGNAME"])

    names = {f["properties"]["ENGNAME"] for f in features}
    assert names == set(PHU_NAMES.values()), f"unexpected names: {names ^ set(PHU_NAMES.values())}"
    write_collection(OUT_PATH, features)


if __name__ == "__main__":
    main()

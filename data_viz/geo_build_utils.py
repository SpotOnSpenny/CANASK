"""Shared helpers for the one-off geojson builders (build_*_geo.py).

Every province/national map asset is produced the same way: fetch an authoritative boundary
layer, let mapshaper do topology-aware simplification (shared borders stay gap-free), then
rewind rings to the legacy exterior-clockwise order Plotly requires and round coordinates to
keep the payload small. These helpers hold that common tail so each builder is just its
source + naming rules.

Only the standard library is used (the builders run locally, outside the containers).
"""

import json
import subprocess


def ring_area(ring):
    """Signed shoelace area in coordinate space: > 0 means counterclockwise."""
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        area += x1 * y2 - x2 * y1
    return area / 2.0


def rewind_polygon(rings):
    """Legacy winding (what Plotly wants): exterior clockwise, holes counterclockwise."""
    fixed = []
    for i, ring in enumerate(rings):
        ccw = ring_area(ring) > 0
        wants_ccw = i > 0  # ring 0 is the exterior
        fixed.append(list(reversed(ring)) if ccw != wants_ccw else ring)
    return fixed


def round_coords(rings, places=3):
    return [[[round(x, places), round(y, places)] for x, y in ring] for ring in rings]


def normalize_geometry(geometry, places=3):
    """Round + rewind a Polygon/MultiPolygon geometry in place and return it."""
    if geometry["type"] == "Polygon":
        geometry["coordinates"] = rewind_polygon(round_coords(geometry["coordinates"], places))
    elif geometry["type"] == "MultiPolygon":
        geometry["coordinates"] = [rewind_polygon(round_coords(p, places)) for p in geometry["coordinates"]]
    else:
        raise ValueError(f"unexpected geometry type {geometry['type']}")
    return geometry


def run_mapshaper(args):
    """Run mapshaper via npx (node is a local dev dependency, not a container one)."""
    subprocess.run(["npx", "-y", "mapshaper", *args], check=True)


def write_collection(path, features):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")))
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB, {len(features)} features)")

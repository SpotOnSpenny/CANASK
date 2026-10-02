"""The committed province geojsons against the names the cleaners emit, plus the winding helpers
that built them. The client draws an unmatched or mis-wound region blank without an error, so
these are the only checks that catch it."""
import ast
import json
from pathlib import Path

import pytest

from data_viz.generate_visuals import _BC_HEALTH_AUTHORITIES, _NS_ZONES
from data_viz.geo_build_utils import check_names, normalize_geometry, rewind_polygon, ring_area

ROOT = Path(__file__).resolve().parents[2]
GEOJSONS = ROOT / "data_viz" / "static" / "assets" / "geojsons"
# The province maps the builders produce (nova-scotia-prerewind.geojson is an obsolete leftover).
BUILT = ["british-columbia", "nova-scotia", "ontario", "canada-provinces"]


def _features(name):
    return json.loads((GEOJSONS / f"{name}.geojson").read_text())["features"]


def _engnames(name):
    return sorted(f["properties"]["ENGNAME"] for f in _features(name))


def _ontario_phu_names():
    # Read PHU_NAMES without importing the builder (it rewrites sys.path at import time).
    tree = ast.parse((ROOT / "data_viz" / "build_ontario_phu_geo.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "PHU_NAMES" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("PHU_NAMES not found")


class TestProvinceNamesMatchTheCleaners:
    def test_bc_health_authorities(self):
        # The province-wide row has no polygon (the Trend view starts it legend-only).
        expected = sorted(ha for ha in _BC_HEALTH_AUTHORITIES if ha != "British Columbia")
        assert _engnames("british-columbia") == expected

    def test_nova_scotia_zones(self):
        assert _engnames("nova-scotia") == sorted(_NS_ZONES)

    def test_ontario_public_health_units(self):
        phu = _ontario_phu_names()
        assert _engnames("ontario") == sorted(phu.values())
        assert {f["properties"]["HR_UID"]: f["properties"]["ENGNAME"] for f in _features("ontario")} == phu

    def test_canada_province_codes(self):
        codes = sorted(f["properties"]["code"] for f in _features("canada-provinces"))
        assert codes == sorted(["AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"])


def _polygons(geometry):
    return [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]


@pytest.mark.parametrize("name", BUILT)
def test_committed_assets_use_legacy_winding(name):
    # Plotly's legacy order: exterior rings clockwise (negative area), holes counterclockwise.
    for feature in _features(name):
        for rings in _polygons(feature["geometry"]):
            assert ring_area(rings[0]) < 0, feature["properties"]
            assert all(ring_area(hole) > 0 for hole in rings[1:]), feature["properties"]


SQUARE_CCW = [[0, 0], [1, 0], [1, 1], [0, 1]]
HOLE_CW = [[0.25, 0.25], [0.25, 0.75], [0.75, 0.75], [0.75, 0.25]]


class TestWinding:
    def test_ring_area_sign(self):
        assert ring_area(SQUARE_CCW) == 1.0
        assert ring_area(list(reversed(SQUARE_CCW))) == -1.0

    def test_ccw_exterior_and_cw_hole_are_both_reversed(self):
        exterior, hole = rewind_polygon([SQUARE_CCW, HOLE_CW])
        assert ring_area(exterior) < 0 and ring_area(hole) > 0

    def test_already_legacy_rings_untouched(self):
        rings = [list(reversed(SQUARE_CCW)), list(reversed(HOLE_CW))]
        assert rewind_polygon(rings) == rings

    def test_normalize_rounds_and_rewinds_multipolygon(self):
        geometry = {"type": "MultiPolygon", "coordinates": [[[[0.00049, 0], [1, 0], [1, 1], [0, 1]]]]}
        (rings,) = normalize_geometry(geometry)["coordinates"]
        assert rings[0][-1] == [0.0, 0] and ring_area(rings[0]) < 0

    def test_unexpected_geometry_type_raises(self):
        with pytest.raises(ValueError, match="LineString"):
            normalize_geometry({"type": "LineString", "coordinates": []})


class TestCheckNames:
    FEATURES = [{"properties": {"ENGNAME": "A"}}, {"properties": {"ENGNAME": "B"}}]

    def test_exact_match_passes(self):
        check_names(self.FEATURES, {"A", "B"})

    def test_duplicate_feature_fails_even_though_the_sets_match(self):
        with pytest.raises(SystemExit):
            check_names(self.FEATURES + [{"properties": {"ENGNAME": "A"}}], {"A", "B"})

    def test_missing_name_fails(self):
        with pytest.raises(SystemExit):
            check_names(self.FEATURES[:1], {"A", "B"})

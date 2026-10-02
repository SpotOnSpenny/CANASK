"""derive_drill_chain: derived state recomputed on every define-visuals, so its mapping
is a contract with both the read path and the client."""
import glob
import json
import os

import pytest

from data_viz.visual_definitions import MANIFEST_DIR, derive_drill_chain, visual_options_problems


@pytest.mark.parametrize("shape,dim2,expected", [
    ("geo_series", None, ["geo"]),
    ("geo_series", "substance", ["geo", "dimension2"]),
    ("pie_nested", None, ["geo", "time", "dimension2"]),
    ("pie_nested", "substance", ["geo", "time", "dimension2"]),
    ("regional", "result", ["geo", "time", "dimension", "dimension2"]),
    ("flat_series", None, []),
    ("flat_series", "substance", ["dimension2"]),
    ("category_treemap", "drug_category", []),
    ("map_none", None, []),
    ("das_table", None, []),
    (None, None, []),
])
def test_drill_chain(shape, dim2, expected):
    assert derive_drill_chain(shape, dim2) == expected


def _heatmap(**options):
    base = {"counts-title": "Deaths", "counts-y-axis-title": "Deaths", "table-counts-row": "Deaths"}
    return {"shape": "geo_series", "level": 1, "chart_type": "heatmap", "data_types": ["counts"],
            "visual_options": {**base, **options}}


class TestVisualOptionsProblems:
    def test_every_real_manifest_entry_is_valid(self):
        for path in sorted(glob.glob(os.path.join(MANIFEST_DIR, "*.json"))):
            with open(path) as handle:
                for entry in json.load(handle)["visuals"]:
                    assert visual_options_problems(entry) == [], (os.path.basename(path), entry["visual_id"])

    def test_valid_switchable_heatmap(self):
        assert visual_options_problems(_heatmap(time_grains=["year", "month"],
                                                chart_types=["heatmap", "line"])) == []

    def test_no_switch_options_is_fine(self):
        assert visual_options_problems({"shape": "flat_series"}) == []

    @pytest.mark.parametrize("grains", ["year", "quarterly", [], ["months"], ["Year"], [None]])
    def test_malformed_time_grains(self, grains):
        (problem,) = visual_options_problems(_heatmap(time_grains=grains))
        assert "time_grains" in problem

    @pytest.mark.parametrize("chart_types", ["line", ["heatmap", "bar"]])
    def test_malformed_chart_types(self, chart_types):
        (problem,) = visual_options_problems(_heatmap(chart_types=chart_types))
        assert "chart_types must be a list" in problem

    def test_chart_types_must_include_own_chart_type(self):
        (problem,) = visual_options_problems(_heatmap(chart_types=["line"]))
        assert "must include" in problem

    def test_chart_types_only_on_level_one_geo_series(self):
        entry = _heatmap(chart_types=["heatmap", "line"])
        entry["level"] = 2
        assert "level-1 geo_series" in visual_options_problems(entry)[0]

    def test_trend_view_needs_its_title_options(self):
        entry = _heatmap(chart_types=["heatmap", "line"])
        del entry["visual_options"]["counts-title"]
        (problem,) = visual_options_problems(entry)
        assert "counts-title" in problem

    def test_trend_view_needs_counts_first(self):
        entry = _heatmap(chart_types=["heatmap", "line"])
        entry["data_types"] = ["rates", "counts"]
        assert "counts" in visual_options_problems(entry)[0]

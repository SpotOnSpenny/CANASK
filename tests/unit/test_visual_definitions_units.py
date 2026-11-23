"""derive_drill_chain: derived state recomputed on every define-visuals, so its mapping
is a contract with both the read path and the client."""
import glob
import json
import os

import pytest

from data_viz.visual_definitions import (MANIFEST_DIR, data_source_problems, derive_drill_chain,
                                         manifest_problems, visual_options_problems)


@pytest.mark.parametrize("shape,dim2,expected", [
    ("geo_series", None, ["geo"]),
    ("geo_series", "substance", ["geo", "dimension2"]),
    ("pie_nested", None, ["geo", "time", "dimension2"]),
    ("pie_nested", "substance", ["geo", "time", "dimension2"]),
    ("regional", "result", ["geo", "time", "dimension", "dimension2"]),
    ("flat_series", None, []),
    ("flat_series", "substance", ["dimension2"]),
    ("category_treemap", "drug_category", []),
    ("expected_actual_bar", "sample_outcome", []),
    ("found_substance_series", "found_substance", []),
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


_RATES_KEYS = {"heatmap-rates-title": "Deaths per 100k", "rates-title": "Deaths per 100k",
               "rates-y-axis-title": "Deaths per 100k", "table-rates-row": "replace_me"}


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

    def test_default_grain_must_be_one_of_the_declared_grains(self):
        ok = {"shape": "found_substance_series", "visual_options": {"time_grains": ["quarter", "month"],
                                                                     "default_grain": "month"}}
        assert visual_options_problems(ok) == []
        bad = {"shape": "found_substance_series", "visual_options": {"time_grains": ["quarter", "month"],
                                                                      "default_grain": "year"}}
        (problem,) = visual_options_problems(bad)
        assert "default_grain" in problem
        alone = {"shape": "found_substance_series", "visual_options": {"default_grain": "month"}}
        (problem,) = visual_options_problems(alone)
        assert "default_grain" in problem

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

    def test_rates_first_trend_view_is_fine_with_rates_keys(self):
        entry = _heatmap(**_RATES_KEYS, chart_types=["heatmap", "line"])
        entry["data_types"] = ["rates", "counts"]
        assert visual_options_problems(entry) == []

    def test_trend_view_needs_title_options_for_every_measure(self):
        entry = _heatmap(**_RATES_KEYS, chart_types=["heatmap", "line"])
        entry["data_types"] = ["rates", "counts"]
        del entry["visual_options"]["table-rates-row"]
        (problem,) = visual_options_problems(entry)
        assert "table-rates-row" in problem

    def test_rates_heatmap_needs_its_map_title(self):
        entry = _heatmap(**_RATES_KEYS)
        entry["data_types"] = ["rates", "counts"]
        del entry["visual_options"]["heatmap-rates-title"]
        (problem,) = visual_options_problems(entry)
        assert "heatmap-rates-title" in problem

    def test_heatmap_measures_limited_to_counts_and_rates(self):
        entry = _heatmap()
        entry["data_types"] = ["counts", "percentages"]
        (problem,) = visual_options_problems(entry)
        assert "percentages" in problem


class TestDataSourceProblems:
    def test_every_real_manifest_source_block_is_valid(self):
        for path in sorted(glob.glob(os.path.join(MANIFEST_DIR, "*.json"))):
            with open(path) as handle:
                assert data_source_problems(json.load(handle).get("data_source")) == [], \
                    os.path.basename(path)

    def test_sourceless_manifest_is_fine(self):
        assert data_source_problems(None) == []

    def test_name_is_required(self):
        assert data_source_problems({"link": "https://example.org"}) != []

    @pytest.mark.parametrize("value", ["true", 1, None])
    def test_restricted_must_be_a_real_boolean(self, value):
        assert data_source_problems({"name": "x", "restricted": value}) != []

    def test_unknown_key_is_a_problem(self):
        # A misspelled "restricted" must not silently leave the source open.
        (problem,) = data_source_problems({"name": "x", "restriced": True})
        assert "restriced" in problem


def test_drug_checking_source_is_declared_restricted():
    """The national drug checking data is under a data sharing agreement: only members of a
    group holding the source may see it, site admins included. This pins the manifest switch --
    removing it is a deliberate, reviewed act, not a refactoring accident."""
    with open(os.path.join(MANIFEST_DIR, "drugchecking.json")) as handle:
        assert json.load(handle)["data_source"].get("restricted") is True


def test_no_restricted_source_is_listed_as_an_alias():
    """reconcile_source_aliases deletes alias rows and moves their groups onto the canonical
    source, so a restricted source listed as an alias would lose its restriction on the next
    publish (it refuses at run time; this catches it at review time, without a database)."""
    from data_viz.auth.auth_helpers import SOURCE_ALIASES
    aliases = {alias for names in SOURCE_ALIASES.values() for alias in names}
    restricted = set()
    for path in sorted(glob.glob(os.path.join(MANIFEST_DIR, "*.json"))):
        with open(path) as handle:
            data_source = json.load(handle).get("data_source") or {}
        if data_source.get("restricted") is True:
            restricted.add(data_source["name"])
    assert "Pan-Canadian Drug Checking Data Harmonization" in restricted   # the loop found it
    assert restricted & aliases == set()


class TestManifestProblems:
    """The TOP level of a manifest. data_source_problems is strict inside the `data_source`
    block, which only helps if the block is found under exactly that key."""

    def test_every_real_manifest_passes(self):
        paths = sorted(glob.glob(os.path.join(MANIFEST_DIR, "*.json")))
        assert paths
        for path in paths:
            with open(path) as handle:
                assert manifest_problems(json.load(handle)) == [], os.path.basename(path)

    def test_minimal_manifest_is_fine(self):
        assert manifest_problems({"data_source": {"name": "x"}, "visuals": []}) == []

    def test_comment_key_is_allowed(self):
        assert manifest_problems({"_comment": "why", "data_source": {"name": "x"},
                                  "visuals": []}) == []

    def test_explicit_null_data_source_is_the_sourceless_manifest(self):
        assert manifest_problems({"data_source": None, "visuals": []}) == []

    def test_missing_data_source_key_is_a_problem(self):
        # Absent is not the same as null: a key lost in a merge must not read as "sourceless".
        (problem,) = manifest_problems({"visuals": []})
        assert "data_source" in problem

    def test_misspelled_data_source_key_is_reported_both_ways(self):
        problems = manifest_problems({"data_sources": {"name": "x", "restricted": True},
                                      "visuals": []})
        assert len(problems) == 2
        assert any("data_sources" in problem and "unknown" in problem for problem in problems)
        assert any('missing "data_source"' in problem for problem in problems)

    def test_restricted_one_level_too_high_is_a_problem(self):
        (problem,) = manifest_problems({"restricted": True, "data_source": {"name": "x"},
                                        "visuals": []})
        assert "restricted" in problem

    @pytest.mark.parametrize("visuals", [None, {}, "all"])
    def test_visuals_must_be_a_list(self, visuals):
        (problem,) = manifest_problems({"data_source": {"name": "x"}, "visuals": visuals})
        assert "visuals" in problem

    def test_missing_visuals_is_a_problem(self):
        (problem,) = manifest_problems({"data_source": {"name": "x"}})
        assert "visuals" in problem

    @pytest.mark.parametrize("manifest", [[], "manifest", None])
    def test_a_manifest_must_be_an_object(self, manifest):
        (problem,) = manifest_problems(manifest)
        assert "must be an object" in problem

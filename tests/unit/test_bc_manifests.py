"""The BC (and NS heatmap) manifests that opt into the time-grain / chart-type / measure switches."""
import json
from pathlib import Path

VISUALS_DIR = Path(__file__).resolve().parents[2] / "app_config" / "visuals"

GRAIN_VISUALS = {
    "bc-coroners-service.json": [
        "drug_death_heatmap",
        "drug_toxicity_deaths_by_age",
    ],
    "bccsu.json": [
        "drug_supply_by_year",
        "fent_benz_by_year",
        "opioid_types_by_year",
    ],
}


def _entry(filename, visual_id):
    manifest = json.loads((VISUALS_DIR / filename).read_text())
    matches = [v for v in manifest["visuals"] if v["visual_id"] == visual_id]
    assert len(matches) == 1, f"{visual_id} not unique in {filename}"
    return matches[0]


def test_time_grains_declared_on_all_five_visuals():
    for filename, visual_ids in GRAIN_VISUALS.items():
        for visual_id in visual_ids:
            options = _entry(filename, visual_id)["visual_options"]
            assert options["time_grains"] == ["year", "quarter", "month"], visual_id


def test_heatmap_offers_line_chart_with_line_renderer_keys():
    options = _entry("bc-coroners-service.json", "drug_death_heatmap")["visual_options"]
    assert options["chart_types"] == ["heatmap", "line"]
    for key in ("counts-title", "counts-y-axis-title", "table-title", "table-counts-row"):
        assert options.get(key), key


def test_health_area_heatmaps_open_on_rates_with_counts_alongside():
    for filename, visual_id in (("bc-coroners-service.json", "drug_death_heatmap"),
                                ("ns-rates-fatalities.json", "deaths_by_health_zone")):
        entry = _entry(filename, visual_id)
        assert entry["data_types"] == ["rates", "counts"], visual_id
        assert entry["visual_options"].get("heatmap-rates-title"), visual_id


def test_bc_heatmap_trend_view_has_rates_keys():
    options = _entry("bc-coroners-service.json", "drug_death_heatmap")["visual_options"]
    for key in ("rates-title", "rates-y-axis-title", "table-rates-row"):
        assert options.get(key), key

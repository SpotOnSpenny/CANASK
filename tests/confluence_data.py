"""Shared seed for the Confluence integration + route tests: one Saskatchewan flat_series
visual, one Saskatchewan health-authority heatmap, the DAS access gate, three drug codes
(one classified by subclass, two only reachable via explicit codes), and four samples."""
from datetime import date

from tests.factories import (
    make_das_drug,
    make_das_sample,
    make_data_source,
    make_datapoint,
    make_visual,
    make_visual_query,
)


def das_gate(visibility="public"):
    return make_visual(province="canada-das", name="das_explorer", visibility=visibility,
                       vis_type="das_table", data_shape="das_table", metric=None)


def seed_confluence(visibility="public", gate_visibility=None):
    """`visibility` applies to the province visuals, and to the DAS gate too unless
    `gate_visibility` sets the gate separately (to exercise each half of the access check)."""
    gate = das_gate(gate_visibility or visibility)
    source = make_data_source(name="Saskatchewan Coroners Service", link="https://sk.example.org",
                              about="SK about")
    source.last_updated_str = "June, 2026"
    source.data_until_str = "June, 2026"
    flat = make_visual(province="saskatchewan", name="deaths_by_opioid_type",
                       visibility=visibility, data_source=source,
                       vis_type="flat_series", data_shape="flat_series", chart_type="bar",
                       metric="deaths", geo_type="province", dimension2_type="drug_type",
                       key_kind="suffix_y", level="1", data_types="counts",
                       menu_parent="Deaths", menu_name="Opioid Deaths by Drug Type")
    make_visual_query(flat, "geo", "Saskatchewan")
    for year, value in (("2024", 10), ("2025", 20), ("2026", 30)):
        make_datapoint(source, geo="Saskatchewan", geo_type="province", time_frame=year,
                       data_metric="deaths", data_value=value,
                       dimension2_type="drug_type", dimension2_value="Fentanyl")
    heat = make_visual(province="saskatchewan", name="drug_death_heatmap",
                       visibility=visibility, data_source=source,
                       vis_type="geo_series", data_shape="geo_series", chart_type="heatmap",
                       metric="deaths", geo_type="health_authority", key_kind="constant",
                       level="1", data_types="counts", menu_parent="Deaths",
                       menu_name="Drug Toxicity Deaths by Health Authority")
    for geo, year, value in (("Saskatoon", "2025", 5), ("Saskatoon", "2026", 6),
                             ("Regina", "2025", 7), ("Regina", "2026", 8)):
        make_datapoint(source, geo=geo, geo_type="health_authority", time_frame=year,
                       data_metric="deaths", data_value=value)
    # A drill child and a metric-less map: both must be excluded from supported_visuals.
    make_visual(province="saskatchewan", name="deaths_by_sex_line", visibility=visibility,
                data_source=source, vis_type="geo_series", data_shape="geo_series",
                chart_type="line", metric="deaths", geo_type="health_authority",
                level="2", vis_parent_name="drug_death_heatmap")
    make_visual(province="saskatchewan", name="structural_map", visibility=visibility,
                data_source=source, vis_type="map_none", data_shape="map_none",
                chart_type="map", metric=None, level="1")

    fent = make_das_drug(code="FENT", display_name="Fentanyl",
                         pharm_subclass="Fentanyl & analogues")
    pflfent = make_das_drug(code="PFLFENT", display_name="para-Fluorofentanyl")
    coc_hcl = make_das_drug(code="COC_HCL", display_name="Cocaine")
    make_das_sample(sample_number="S-1", province="SK", city="Saskatoon", drugs=[fent],
                    date_received=date(2025, 3, 10), date_returned=date(2025, 4, 20))
    make_das_sample(sample_number="S-2", province="SK", city="Saskatoon",
                    drugs=[pflfent, coc_hcl],
                    date_received=date(2025, 6, 1), date_returned=date(2025, 7, 10))
    make_das_sample(sample_number="S-3", province="SK", city="Regina", drugs=[coc_hcl],
                    date_received=date(2026, 1, 5), date_returned=date(2026, 2, 15))
    make_das_sample(sample_number="S-4", province="ON", city="Toronto", drugs=[fent],
                    date_received=date(2025, 3, 1), date_returned=date(2025, 4, 2))
    return {"gate": gate, "source": source, "flat": flat, "heat": heat}


def seed_month_visuals(source=None, months=(), heat_months=None):
    """A Saskatchewan month-grain flat visual (one Fentanyl fact per month in `months`) and a
    month-grain Saskatoon/Regina heatmap (over `heat_months`, default the same months). Neither
    declares time_grains, so their grains come from the facts' own tags."""
    source = source or make_data_source(name="SK Monthly", link="https://sk.example.org/m",
                                        about="monthly")
    flat = make_visual(province="saskatchewan", name="monthly_deaths", visibility="public",
                       data_source=source, vis_type="flat_series", data_shape="flat_series",
                       chart_type="line", metric="deaths", geo_type="province",
                       dimension2_type="drug_type", key_kind="suffix_y", level="1",
                       data_types="counts", menu_parent="Deaths", menu_name="Monthly deaths")
    make_visual_query(flat, "geo", "Saskatchewan")
    for month in months:
        make_datapoint(source, geo="Saskatchewan", geo_type="province", time_frame=month,
                       time_frame_type="month", data_metric="deaths", data_value=1,
                       dimension2_type="drug_type", dimension2_value="Fentanyl")
    heat = make_visual(province="saskatchewan", name="monthly_heatmap", visibility="public",
                       data_source=source, vis_type="geo_series", data_shape="geo_series",
                       chart_type="heatmap", metric="deaths", geo_type="health_authority",
                       key_kind="constant", level="1", data_types="counts",
                       menu_parent="Deaths", menu_name="Monthly heatmap")
    for month in (months if heat_months is None else heat_months):
        make_datapoint(source, geo="Saskatoon", geo_type="health_authority", time_frame=month,
                       time_frame_type="month", data_metric="deaths", data_value=1)
    return {"source": source, "flat": flat, "heat": heat}


ALL_GRAINS = ["year", "quarter", "month"]
# 2025-01 .. 2026-03 at each grain, keyed the way the cleaners write them.
GRAIN_FRAMES = {
    "year": ["2025", "2026"],
    "quarter": ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4", "2026-Q1"],
    "month": [f"2025-{m:02d}" for m in range(1, 13)] + ["2026-01", "2026-02", "2026-03"],
}


def seed_grain_visuals(source=None, time_grains=ALL_GRAINS):
    """A Saskatchewan flat visual and Saskatoon heatmap carrying facts at all three grains
    (GRAIN_FRAMES, each tagged with its own time_frame_type) and declaring `time_grains`."""
    source = source or make_data_source(name="SK Multi-grain", link="https://sk.example.org/g",
                                        about="grains")
    options = {"time_grains": list(time_grains)}
    flat = make_visual(province="saskatchewan", name="grain_deaths", visibility="public",
                       data_source=source, vis_type="flat_series", data_shape="flat_series",
                       chart_type="line", metric="deaths", geo_type="province",
                       dimension2_type="drug_type", key_kind="suffix_y", level="1",
                       data_types="counts", menu_parent="Deaths", menu_name="Deaths by period",
                       visual_options=options)
    make_visual_query(flat, "geo", "Saskatchewan")
    heat = make_visual(province="saskatchewan", name="grain_heatmap", visibility="public",
                       data_source=source, vis_type="geo_series", data_shape="geo_series",
                       chart_type="heatmap", metric="deaths", geo_type="health_authority",
                       key_kind="constant", level="1", data_types="counts",
                       menu_parent="Deaths", menu_name="Heatmap by period", visual_options=options)
    for grain, frames in GRAIN_FRAMES.items():
        for frame in frames:
            make_datapoint(source, geo="Saskatchewan", geo_type="province", time_frame=frame,
                           time_frame_type=grain, data_metric="deaths", data_value=1,
                           dimension2_type="drug_type", dimension2_value="Fentanyl")
            make_datapoint(source, geo="Saskatoon", geo_type="health_authority",
                           time_frame=frame, time_frame_type=grain, data_metric="deaths",
                           data_value=1)
    return {"source": source, "flat": flat, "heat": heat}

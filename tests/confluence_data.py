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


def seed_confluence(visibility="public"):
    gate = das_gate(visibility)
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

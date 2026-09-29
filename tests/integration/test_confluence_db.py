"""Confluence payload assembly on Postgres (to_char period dims, the group-membership
subquery, and the V1 fact selectors it composes)."""
import pytest

from data_viz import confluence as cf
from data_viz.das_explorer import query_pivot
from data_viz.database.models import DasSamples
from data_viz.visual_generic import visual_block, visual_dimension_values

from tests.confluence_data import seed_confluence
from tests.factories import make_user


@pytest.fixture()
def seeded(db_session):
    return seed_confluence()


@pytest.fixture()
def admin(db_session):
    return make_user(site_admin=True)


class TestQueryPivotExtraWhere:
    def test_extra_where_narrows(self, seeded):
        result = query_pivot("id_all", "province", None, {}, "samples",
                             extra_where=[DasSamples.city == "Regina"])
        assert dict(zip(result["rows"], (r[0] for r in result["cells"]))) == {"SK": 1.0}

    def test_default_is_a_noop(self, seeded):
        result = query_pivot("id_all", "province", None, {}, "samples")
        assert dict(zip(result["rows"], (r[0] for r in result["cells"]))) == {"SK": 3.0, "ON": 1.0}


class TestVisualDimensionValues:
    def test_flat_visual_returns_its_dimension_values(self, seeded):
        assert visual_dimension_values(seeded["flat"]) == {"Fentanyl"}

    def test_heatmap_without_dimensions_is_empty(self, seeded):
        assert visual_dimension_values(seeded["heat"]) == set()


class TestSupportedVisuals:
    def test_only_level_one_flat_and_geo_series(self, seeded, admin):
        names = [v.name for v in cf.supported_visuals(admin, "saskatchewan")]
        assert names == ["deaths_by_opioid_type", "drug_death_heatmap"]

    def test_hidden_for_anonymous_when_private(self, db_session):
        seed_confluence(visibility="private")
        assert cf.supported_visuals(None, "saskatchewan") == []


class TestDasCoverage:
    def test_received_basis_censors_two_months_each_end(self, seeded):
        # Samples received before the first return month exist (returned later), so the overlay
        # window opens CENSOR_MONTHS early for received; those periods are flagged incomplete.
        assert cf.das_coverage("received") == {"first_month": "2025-04", "overlap_from": "2025-02",
                                               "last_month": "2026-02", "complete_through": "2025-12"}

    def test_returned_basis(self, seeded):
        coverage = cf.das_coverage("returned")
        assert coverage["complete_through"] == "2026-02"
        assert coverage["overlap_from"] == "2025-04"

    def test_empty_table(self, db_session):
        assert cf.das_coverage("received") is None


class TestSeriesPayload:
    def test_series_mode_counts_by_group(self, seeded, admin):
        payload = cf.build_confluence_payload("saskatchewan", seeded["flat"],
                                              ["fentanyl", "cocaine"], "group", "received", None)
        assert payload["grain"] == "year"
        assert payload["province"] == {"slug": "saskatchewan", "label": "Saskatchewan", "code": "SK"}
        assert [p["key"] for p in payload["periods"]] == ["2025", "2026"]   # 2024 has no DAS overlap
        das = payload["das"]
        assert das["mode"] == "series"
        assert das["series"]["fentanyl"] == {"2025": 2.0}          # S-1 (subclass) + S-2 (code)
        assert das["series"]["cocaine"] == {"2025": 1.0, "2026": 1.0}
        assert das["keys"] == [{"key": "fentanyl", "label": "Fentanyl & analogues"},
                               {"key": "cocaine", "label": "Cocaine"}]
        assert das["coverage"]["complete_through"] == "2025-12"
        assert das["truncated"] is False

    def test_periods_flag_completeness(self, seeded, admin):
        payload = cf.build_confluence_payload("saskatchewan", seeded["flat"],
                                              ["fentanyl"], "group", "received", None)
        # Coverage starts 2025-04, so neither year is fully inside the complete window.
        assert [p["das_complete"] for p in payload["periods"]] == [False, False]

    def test_expression_narrows(self, seeded, admin):
        payload = cf.build_confluence_payload("saskatchewan", seeded["flat"],
                                              ["fentanyl"], "group", "received", "fentanyl NOT *")
        assert payload["das"]["series"]["fentanyl"] == {"2025": 1.0}   # S-2 also has cocaine

    def test_empty_keys_means_all_samples(self, seeded, admin):
        payload = cf.build_confluence_payload("saskatchewan", seeded["flat"],
                                              [], "group", "received", None)
        assert payload["das"]["keys"] == [{"key": "all", "label": "All samples"}]
        assert payload["das"]["series"]["all"] == {"2025": 2.0, "2026": 1.0}

    def test_family_level_unions_groups(self, seeded, admin):
        payload = cf.build_confluence_payload("saskatchewan", seeded["flat"],
                                              ["opioids", "stimulants"], "family", "received", None)
        assert payload["das"]["series"]["opioids"] == {"2025": 2.0}
        assert payload["das"]["series"]["stimulants"] == {"2025": 1.0, "2026": 1.0}

    def test_returned_basis_bins_by_return_month(self, seeded, admin):
        payload = cf.build_confluence_payload("saskatchewan", seeded["flat"],
                                              ["cocaine"], "group", "returned", None)
        assert payload["das"]["basis"] == "returned"
        assert payload["das"]["series"]["cocaine"] == {"2025": 1.0, "2026": 1.0}

    def test_visual_block_is_the_province_api_block(self, seeded, admin):
        payload = cf.build_confluence_payload("saskatchewan", seeded["flat"],
                                              [], "group", "received", None)
        assert payload["visual"] == visual_block(seeded["flat"])
        assert payload["visual"]["data_source"]["name"] == "Saskatchewan Coroners Service"
        # The client labels from the payload, never from the live controls.
        assert payload["visual_label"] == "Opioid Deaths by Drug Type"
        assert payload["visual_metric"] == "deaths"


class TestCitiesPayload:
    def test_cities_mode_keys_by_city_and_period(self, seeded, admin):
        payload = cf.build_confluence_payload("saskatchewan", seeded["heat"],
                                              ["fentanyl", "cocaine"], "group", "received", None)
        das = payload["das"]
        assert das["mode"] == "cities"
        assert das["cities"] == {"Saskatoon, SK": {"2025": 2.0}, "Regina, SK": {"2026": 1.0}}
        assert "series" not in das

    def test_cities_mode_all_samples(self, seeded, admin):
        payload = cf.build_confluence_payload("saskatchewan", seeded["heat"],
                                              [], "group", "received", None)
        assert payload["das"]["cities"]["Saskatoon, SK"] == {"2025": 2.0}


class TestConfluenceConfig:
    def test_lists_supported_visuals_with_resolved_terms(self, seeded, admin):
        config = cf.confluence_config(admin)
        sk = config["provinces"]["saskatchewan"]
        assert sk["label"] == "Saskatchewan" and sk["code"] == "SK"
        by_id = {v["id"]: v for v in sk["visuals"]}
        assert by_id["deaths_by_opioid_type"]["terms_resolved"] == ["fentanyl"]
        assert by_id["deaths_by_opioid_type"]["shape"] == "flat_series"
        assert by_id["drug_death_heatmap"]["terms_resolved"] == []
        assert "ontario" not in config["provinces"]
        assert config["groups"]["fentanyl"]["family"] == "opioids"
        assert "opioids" in config["families"]

    def test_anonymous_sees_nothing_when_private(self, db_session):
        seed_confluence(visibility="private")
        assert cf.confluence_config(None)["provinces"] == {}

"""Confluence payload assembly on Postgres (to_char period dims, the group-membership
subquery, and the V1 fact selectors it composes)."""
from datetime import date

import pytest

from data_viz import confluence as cf
from data_viz.das_explorer import query_pivot
from data_viz.database.models import DasSamples
from data_viz.visual_generic import visual_block, visual_facets

from tests.confluence_data import seed_confluence, seed_grain_visuals, seed_month_visuals
from tests.factories import make_das_drug, make_das_sample, make_user


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
        assert visual_facets(seeded["flat"])[0] == {"Fentanyl"}

    def test_heatmap_without_dimensions_is_empty(self, seeded):
        assert visual_facets(seeded["heat"])[0] == set()

    def test_grains_come_paired_with_their_data_type(self, seeded):
        # The data type lets available_grains count only a heatmap's counts.
        grain_types = visual_facets(seeded["flat"])[1]
        assert grain_types and all(g == "year" and dt in ("counts", "rates") for g, dt in grain_types)


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
        assert das["series"]["fentanyl"] == {"2025": 2.0, "2026": 0}   # S-1 (subclass) + S-2 (code)
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
        assert payload["das"]["undated"] == {"fentanyl": 0}

    def test_expression_narrows(self, seeded, admin):
        payload = cf.build_confluence_payload("saskatchewan", seeded["flat"],
                                              ["fentanyl"], "group", "received", "fentanyl NOT *")
        assert payload["das"]["series"]["fentanyl"] == {"2025": 1.0, "2026": 0}   # S-2 also has cocaine

    def test_empty_keys_means_all_samples(self, seeded, admin):
        payload = cf.build_confluence_payload("saskatchewan", seeded["flat"],
                                              [], "group", "received", None)
        assert payload["das"]["keys"] == [{"key": "all", "label": "All samples"}]
        assert payload["das"]["series"]["all"] == {"2025": 2.0, "2026": 1.0}

    def test_family_level_unions_groups(self, seeded, admin):
        payload = cf.build_confluence_payload("saskatchewan", seeded["flat"],
                                              ["opioids", "stimulants"], "family", "received", None)
        assert payload["das"]["series"]["opioids"] == {"2025": 2.0, "2026": 0}
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
        assert das["series"] == {}
        assert das["undated"] == {"all": 0}

    def test_cities_mode_all_samples(self, seeded, admin):
        payload = cf.build_confluence_payload("saskatchewan", seeded["heat"],
                                              [], "group", "received", None)
        assert payload["das"]["cities"]["Saskatoon, SK"] == {"2025": 2.0}


class TestZeroFillAndClip:
    def test_covered_period_with_no_matching_samples_is_zero(self, seeded, admin):
        # 2026 is inside DAS coverage but has no fentanyl sample: a real 0, not a gap.
        payload = cf.build_confluence_payload("saskatchewan", seeded["flat"],
                                              ["fentanyl"], "group", "received", None)
        assert payload["das"]["series"]["fentanyl"]["2026"] == 0

    def test_das_periods_outside_the_visual_are_clipped(self, seeded, admin):
        coc = make_das_drug(code="COC", display_name="Cocaine base")
        make_das_sample(sample_number="S-27", province="SK", city="Moose Jaw", drugs=[coc],
                        date_received=date(2027, 6, 1), date_returned=date(2027, 7, 1))
        flat = cf.build_confluence_payload("saskatchewan", seeded["flat"],
                                           [], "group", "received", None)
        assert set(flat["das"]["series"]["all"]) == {"2025", "2026"}   # the visual has no 2027
        heat = cf.build_confluence_payload("saskatchewan", seeded["heat"],
                                           [], "group", "received", None)
        assert "Moose Jaw, SK" not in heat["das"]["cities"]

    def test_undated_samples_are_counted_not_plotted(self, seeded, admin):
        coc = make_das_drug(code="COC", display_name="Cocaine base")
        make_das_sample(sample_number="S-ND", province="SK", city="Regina", drugs=[coc],
                        date_received=None, date_returned=date(2025, 5, 1))
        flat = cf.build_confluence_payload("saskatchewan", seeded["flat"],
                                           [], "group", "received", None)
        assert "Unknown" not in flat["das"]["series"]["all"]
        assert flat["das"]["undated"] == {"all": 1}
        heat = cf.build_confluence_payload("saskatchewan", seeded["heat"],
                                           [], "group", "received", None)
        assert heat["das"]["cities"]["Regina, SK"] == {"2026": 1.0}
        assert heat["das"]["undated"] == {"all": 1}


class TestMonthGrain:
    def test_month_visual_aligns_and_flags_both_ends(self, seeded, admin):
        months = [f"2025-{m:02d}" for m in range(1, 13)] + ["2026-01", "2026-02", "2026-03"]
        monthly = seed_month_visuals(months=months)
        payload = cf.build_confluence_payload("saskatchewan", monthly["flat"],
                                              [], "group", "received", None)
        assert payload["grain"] == "month"
        # Received coverage opens two months before the first return month (2025-04) and ends at
        # the last one (2026-02); the two months at each end are incomplete.
        flags = {p["key"]: p["das_complete"] for p in payload["periods"]}
        assert list(flags) == [f"2025-{m:02d}" for m in range(2, 13)] + ["2026-01", "2026-02"]
        assert [k for k, done in flags.items() if not done] == ["2025-02", "2025-03",
                                                                "2026-01", "2026-02"]
        series = payload["das"]["series"]["all"]
        assert series["2025-03"] == 1.0 and series["2025-06"] == 1.0 and series["2026-01"] == 1.0
        assert series["2025-04"] == 0 and sum(series.values()) == 3.0

    def test_more_periods_than_the_default_pivot_caps(self, db_session, admin):
        # 45 received-months: past both PIVOT_MAX_ROWS (40) and PIVOT_MAX_COLS (15). The date-kind
        # ordering keeps the NEWEST periods, so a default cap would drop the oldest months.
        coc = make_das_drug(code="COC", display_name="Cocaine base")
        months = [cf.shift_month("2020-01", i) for i in range(45)]
        for i, month in enumerate(months):
            year, mon = (int(x) for x in month.split("-"))
            ret_year, ret_mon = (int(x) for x in cf.shift_month(month, 1).split("-"))
            make_das_sample(sample_number=f"M-{i}", province="SK", city="Saskatoon", drugs=[coc],
                            date_received=date(year, mon, 5), date_returned=date(ret_year, ret_mon, 5))
        monthly = seed_month_visuals(months=months)
        flat = cf.build_confluence_payload("saskatchewan", monthly["flat"],
                                           [], "group", "received", None)
        assert flat["das"]["series"]["all"]["2020-01"] == 1.0
        assert flat["das"]["truncated"] is False
        heat = cf.build_confluence_payload("saskatchewan", monthly["heat"],
                                           [], "group", "received", None)
        assert heat["das"]["cities"]["Saskatoon, SK"]["2020-01"] == 1.0
        assert heat["das"]["truncated"] is False


class TestQuarterGrain:
    @pytest.fixture()
    def grains(self, seeded):
        # A second Q2-2025 sample (May) so the quarter must SUM two DAS months (S-2 is June).
        fent = make_das_drug(code="FENT2", display_name="Fentanyl (2)",
                             pharm_subclass="Fentanyl & analogues")
        make_das_sample(sample_number="S-5", province="SK", city="Saskatoon", drugs=[fent],
                        date_received=date(2025, 5, 15), date_returned=date(2025, 6, 20))
        return seed_grain_visuals()

    def test_quarter_series_sums_das_months_into_quarters(self, grains, admin):
        payload = cf.build_confluence_payload("saskatchewan", grains["flat"], [], "group",
                                              "received", None, grain="quarter")
        assert payload["grain"] == "quarter"
        assert payload["grains"] == ["year", "quarter", "month"]
        # Received coverage: overlap 2025-02 .. 2026-02, complete 2025-04 .. 2025-12.
        flags = {p["key"]: p["das_complete"] for p in payload["periods"]}
        assert flags == {"2025-Q1": False, "2025-Q2": True, "2025-Q3": True, "2025-Q4": True,
                         "2026-Q1": False}
        # S-1 (Mar) -> Q1; S-5 (May) + S-2 (Jun) -> Q2; S-3 (Jan 2026) -> 2026-Q1; ON's S-4 out.
        assert payload["das"]["series"]["all"] == {"2025-Q1": 1.0, "2025-Q2": 2.0, "2025-Q3": 0,
                                                   "2025-Q4": 0, "2026-Q1": 1.0}

    def test_quarter_returned_basis(self, grains, admin):
        payload = cf.build_confluence_payload("saskatchewan", grains["flat"], [], "group",
                                              "returned", None, grain="quarter")
        # Returned: S-1 Apr + S-5 Jun -> Q2, S-2 Jul -> Q3, S-3 Feb 2026 -> 2026-Q1.
        assert payload["das"]["series"]["all"] == {"2025-Q2": 2.0, "2025-Q3": 1.0, "2025-Q4": 0,
                                                   "2026-Q1": 1.0}

    def test_quarter_cities(self, grains, admin):
        payload = cf.build_confluence_payload("saskatchewan", grains["heat"], [], "group",
                                              "received", None, grain="quarter")
        assert payload["das"]["cities"] == {"Saskatoon, SK": {"2025-Q1": 1.0, "2025-Q2": 2.0},
                                            "Regina, SK": {"2026-Q1": 1.0}}

    def test_quarter_dim_in_the_explorer_pivot(self, grains):
        result = query_pivot("id_all", "quarter_received", None, {"province": ["SK"]}, "samples")
        assert dict(zip(result["rows"], (r[0] for r in result["cells"]))) == \
            {"2025-Q1": 1.0, "2025-Q2": 2.0, "2026-Q1": 1.0}

    def test_visual_facts_are_filtered_to_the_grain(self, grains, admin):
        payload = cf.build_confluence_payload("saskatchewan", grains["flat"], [], "group",
                                              "received", None, grain="month")
        assert {f["g"] for f in payload["visual"]["facts"]} == {"month"}
        assert payload["grain"] == "month"

    def test_default_is_the_first_available_grain(self, grains, admin):
        payload = cf.build_confluence_payload("saskatchewan", grains["flat"], [], "group",
                                              "received", None)
        assert payload["grain"] == "year"

    def test_grain_outside_the_declared_list_is_unalignable(self, seeded, admin):
        multi = seed_grain_visuals(time_grains=["year"])
        assert cf.available_grains(multi["flat"], visual_block(multi["flat"])["facts"]) == ["year"]
        with pytest.raises(cf.UnalignableVisualError):
            cf.build_confluence_payload("saskatchewan", multi["flat"], [], "group",
                                        "received", None, grain="quarter")


class TestConfluenceConfig:
    def test_lists_supported_visuals_with_resolved_terms(self, seeded, admin):
        config = cf.confluence_config(admin)
        sk = config["provinces"]["saskatchewan"]
        assert sk["label"] == "Saskatchewan" and sk["code"] == "SK"
        by_id = {v["id"]: v for v in sk["visuals"]}
        assert by_id["deaths_by_opioid_type"]["terms_resolved"] == ["fentanyl"]
        assert by_id["deaths_by_opioid_type"]["shape"] == "flat_series"
        assert by_id["drug_death_heatmap"]["terms_resolved"] == []
        assert by_id["deaths_by_opioid_type"]["grains"] == ["year"]
        assert "ontario" not in config["provinces"]
        assert config["groups"]["fentanyl"]["family"] == "opioids"
        assert "opioids" in config["families"]
        assert "other" not in config["families"]   # no member groups -> no chip

    def test_grains_are_the_available_grains(self, seeded, admin):
        seed_grain_visuals(time_grains=["month", "year"])   # quarter facts present, not declared
        sk = cf.confluence_config(admin)["provinces"]["saskatchewan"]
        by_id = {v["id"]: v for v in sk["visuals"]}
        assert by_id["grain_deaths"]["grains"] == ["year", "month"]
        assert by_id["grain_heatmap"]["grains"] == ["year", "month"]

    def test_undeclared_grains_come_from_the_facts(self, seeded, admin):
        seed_month_visuals(months=["2025-04", "2025-05"])
        sk = cf.confluence_config(admin)["provinces"]["saskatchewan"]
        by_id = {v["id"]: v for v in sk["visuals"]}
        assert by_id["monthly_deaths"]["grains"] == ["month"]
        assert by_id["monthly_heatmap"]["grains"] == ["month"]
        assert by_id["monthly_deaths"]["terms_resolved"] == ["fentanyl"]

    def test_anonymous_sees_nothing_when_private(self, db_session):
        seed_confluence(visibility="private")
        assert cf.confluence_config(None)["provinces"] == {}

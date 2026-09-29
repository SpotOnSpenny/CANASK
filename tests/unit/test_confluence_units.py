"""Confluence's pure alignment rules: no DB, no Flask request. The DB-backed half
(payload assembly, das coverage) lives in tests/integration/test_confluence_db.py."""
import pytest
from sqlalchemy.dialects import postgresql

from data_viz import confluence as cf
from data_viz.main import active_provinces
from data_viz.provinces import PROVINCE_CODES, PROVINCE_LABELS


def compiled(clause):
    return str(clause.compile(dialect=postgresql.dialect(),
                              compile_kwargs={"literal_binds": True}))


# A small, self-contained crosswalk so these tests don't drift with the real file.
CROSSWALK = {
    "families": {"opioids": {"label": "Opioids"}, "stimulants": {"label": "Stimulants"}},
    "groups": {
        "fentanyl": {"label": "Fentanyl & analogues", "family": "opioids",
                     "subclasses": ["Fentanyl & analogues"], "codes": ["PFLFENT"],
                     "exclude_codes": []},
        "other_opioids": {"label": "Other opioids", "family": "opioids",
                          "subclasses": ["Opiates"], "codes": [], "exclude_codes": ["NALOX"]},
        "cocaine": {"label": "Cocaine", "family": "stimulants", "subclasses": [],
                    "codes": ["COC", "COCBAS"], "exclude_codes": []},
    },
    "terms": {"opioids": ["opioids"], "fentanyl": ["fentanyl"],
              "furanyl fentanyl": ["fentanyl"], "cocaine": ["cocaine"]},
}


class TestProvinces:
    def test_labels_and_codes_cover_exactly_the_active_provinces(self):
        assert set(PROVINCE_LABELS) == set(active_provinces)
        assert set(PROVINCE_CODES) == set(active_provinces)

    def test_codes_are_unique_two_letter_uppercase(self):
        codes = list(PROVINCE_CODES.values())
        assert len(set(codes)) == 13
        assert all(len(c) == 2 and c.isupper() for c in codes)
        assert PROVINCE_CODES["saskatchewan"] == "SK"
        assert PROVINCE_LABELS["saskatchewan"] == "Saskatchewan"

    def test_generate_visuals_reuses_the_labels(self):
        from data_viz.generate_visuals import PROVINCE_DISPLAY
        assert PROVINCE_DISPLAY is PROVINCE_LABELS


class TestCrosswalkValidation:
    def test_real_file_loads_and_validates(self):
        data = cf.load_substance_groups()
        assert "fentanyl" in data["groups"]
        assert data["groups"]["fentanyl"]["family"] in data["families"]
        for key in ("opioids", "stimulants"):
            assert key in data["families"]

    def test_unknown_family_rejected(self):
        bad = {"families": {}, "groups": {"g": {"label": "G", "family": "nope", "codes": ["X"]}},
               "terms": {}}
        with pytest.raises(ValueError, match="family"):
            cf.validate_substance_groups(bad)

    def test_group_without_codes_or_subclasses_rejected(self):
        bad = {"families": {"f": {"label": "F"}},
               "groups": {"g": {"label": "G", "family": "f"}}, "terms": {}}
        with pytest.raises(ValueError, match="neither"):
            cf.validate_substance_groups(bad)

    def test_term_naming_unknown_key_rejected(self):
        bad = {"families": {"f": {"label": "F"}},
               "groups": {"g": {"label": "G", "family": "f", "codes": ["X"]}},
               "terms": {"x": ["missing"]}}
        with pytest.raises(ValueError, match="term"):
            cf.validate_substance_groups(bad)

    def test_group_and_family_keys_must_not_collide(self):
        bad = {"families": {"same": {"label": "S"}},
               "groups": {"same": {"label": "S", "family": "same", "codes": ["X"]}}, "terms": {}}
        with pytest.raises(ValueError, match="collide"):
            cf.validate_substance_groups(bad)


class TestResolveTerms:
    def test_case_and_whitespace_insensitive(self):
        assert cf.resolve_terms(["Fentanyl", " OPIOIDS "], CROSSWALK) == {"fentanyl", "opioids"}

    def test_unknown_values_ignored(self):
        assert cf.resolve_terms(["Fentanyl", "Alcohol", None], CROSSWALK) == {"fentanyl"}

    def test_empty(self):
        assert cf.resolve_terms([], CROSSWALK) == set()


class TestKeysAtLevel:
    def test_group_level_expands_families_in_crosswalk_order(self):
        assert cf.keys_at_level({"opioids", "cocaine"}, "group", CROSSWALK) == \
            ["fentanyl", "other_opioids", "cocaine"]

    def test_family_level_collapses_groups(self):
        assert cf.keys_at_level({"fentanyl", "cocaine"}, "family", CROSSWALK) == \
            ["opioids", "stimulants"]

    def test_family_level_keeps_families(self):
        assert cf.keys_at_level({"stimulants"}, "family", CROSSWALK) == ["stimulants"]

    def test_bad_level(self):
        with pytest.raises(ValueError):
            cf.keys_at_level({"fentanyl"}, "nope", CROSSWALK)


class TestKeyLabel:
    def test_group_and_family_labels(self):
        assert cf.key_label("fentanyl", CROSSWALK) == "Fentanyl & analogues"
        assert cf.key_label("opioids", CROSSWALK) == "Opioids"

    def test_all_samples_label(self):
        assert cf.key_label(cf.ALL_KEY, CROSSWALK) == "All samples"


class TestGroupClause:
    def test_group_clause_names_codes_and_subclasses(self):
        sql = compiled(cf.group_clause(["fentanyl"], "group", CROSSWALK))
        assert "das_sample_drugs" in sql and "das_drug_codes" in sql
        assert "'PFLFENT'" in sql and "'Fentanyl & analogues'" in sql
        assert "sample_number IN" in sql

    def test_exclude_codes_appear_as_not_in(self):
        sql = compiled(cf.group_clause(["other_opioids"], "group", CROSSWALK))
        assert "NOT IN" in sql and "'NALOX'" in sql

    def test_family_is_union_of_its_groups(self):
        sql = compiled(cf.group_clause(["opioids"], "family", CROSSWALK))
        assert "'PFLFENT'" in sql and "'Opiates'" in sql
        assert "'COC'" not in sql

    def test_unknown_key_rejected(self):
        with pytest.raises(ValueError):
            cf.group_clause(["nope"], "group", CROSSWALK)


class TestDetectGrain:
    def test_year(self):
        assert cf.detect_grain([{"dt": "counts", "t": "2024"}, {"dt": "counts", "t": "2025"}]) == "year"

    def test_month(self):
        assert cf.detect_grain([{"dt": "counts", "t": "2026-01"}]) == "month"

    def test_additional_rows_ignored(self):
        facts = [{"dt": "counts", "t": "2024"}, {"dt": "additional_rows", "t": "weird"}]
        assert cf.detect_grain(facts) == "year"

    @pytest.mark.parametrize("facts", [
        [], [{"dt": "counts", "t": "2024"}, {"dt": "counts", "t": "2024-05"}],
        [{"dt": "counts", "t": "2024 Q1"}],
    ])
    def test_mixed_or_unknown_raises(self, facts):
        with pytest.raises(ValueError):
            cf.detect_grain(facts)


class TestPeriods:
    def test_visual_periods_sorted_distinct(self):
        facts = [{"dt": "counts", "t": "2025"}, {"dt": "counts", "t": "2023"},
                 {"dt": "counts", "t": "2025"}, {"dt": "additional_rows", "t": "2030"}]
        assert cf.visual_periods(facts) == ["2023", "2025"]

    def test_period_bounds(self):
        assert cf.period_bounds("2025", "year") == ("2025-01", "2025-12")
        assert cf.period_bounds("2025-03", "month") == ("2025-03", "2025-03")

    def test_shift_month_across_year_boundaries(self):
        assert cf.shift_month("2026-01", -2) == "2025-11"
        assert cf.shift_month("2025-12", 1) == "2026-01"
        assert cf.shift_month("2025-06", 0) == "2025-06"

    def test_das_dim(self):
        assert cf.das_dim("year", "received") == "year_received"
        assert cf.das_dim("month", "returned") == "month_returned"


COVERAGE = {"first_month": "2025-01", "overlap_from": "2024-11", "last_month": "2026-07",
            "complete_through": "2026-05"}


class TestPeriodComplete:
    def test_year_fully_inside_window(self):
        assert cf.period_complete("2025", "year", COVERAGE) is True

    def test_year_straddling_complete_through(self):
        assert cf.period_complete("2026", "year", COVERAGE) is False

    def test_month_before_first(self):
        assert cf.period_complete("2024-12", "month", COVERAGE) is False

    def test_month_at_complete_through(self):
        assert cf.period_complete("2026-05", "month", COVERAGE) is True
        assert cf.period_complete("2026-06", "month", COVERAGE) is False

    def test_no_coverage(self):
        assert cf.period_complete("2025", "year", None) is False


class TestOverlappingPeriods:
    def test_keeps_periods_touching_the_window(self):
        # overlap_from (2024-11) lets 2024 in, flagged incomplete; 2023 stays out.
        assert cf.overlapping_periods(["2023", "2024", "2025", "2026"], "year", COVERAGE) == \
            ["2024", "2025", "2026"]

    def test_month_grain(self):
        months = ["2024-10", "2024-12", "2025-01", "2026-07", "2026-08"]
        assert cf.overlapping_periods(months, "month", COVERAGE) == ["2024-12", "2025-01", "2026-07"]

    def test_leading_received_months_are_incomplete_not_hidden(self):
        assert cf.period_complete("2024-12", "month", COVERAGE) is False

    def test_no_coverage_means_nothing(self):
        assert cf.overlapping_periods(["2025"], "year", None) == []


class TestPivotAdapters:
    def test_series_from_pivot(self):
        pivot = {"rows": ["2025", "2026"], "cols": [], "cells": [[3.0], [None]]}
        assert cf.series_from_pivot(pivot) == {"2025": 3.0}

    def test_cities_from_pivot_omits_null_cells(self):
        pivot = {"rows": ["Regina, SK", "Saskatoon, SK"], "cols": ["2025", "2026"],
                 "cells": [[1.0, None], [None, 4.0]]}
        assert cf.cities_from_pivot(pivot) == {"Regina, SK": {"2025": 1.0},
                                              "Saskatoon, SK": {"2026": 4.0}}


class TestCrosswalkCoverage:
    """Pin the V1 vocabulary the real crosswalk must keep resolving. These are the actual
    dimension values in the DB (Health Infobase, BC/SK/NS coroners, drug checking); a rename on
    either side must fail here, not silently un-check a chip on the page."""

    @pytest.mark.parametrize("value, key", [
        ("opioids", "opioids"), ("stimulants", "stimulants"),
        ("Fentanyl", "fentanyl"), ("Fentanyl analogues", "fentanyl"),
        ("Para-fluorofentanyl", "fentanyl"), ("Carfentanyl", "fentanyl"),
        ("Furanyl Fentanyl", "fentanyl"),
        ("Non-fentanyl opioids", "other_opioids"), ("Other opioid", "other_opioids"),
        ("Hydromorphone", "other_opioids"), ("Opioid - total", "opioids"),
        ("Cocaine", "cocaine"), ("Methamphetamine", "methamphetamine"),
        ("Meth/amph", "methamphetamine"), ("Other stimulants", "mdma"),
        ("Benzodiazepines", "benzos"), ("Benzodiazepine - nonpharmaceutical", "benzos"),
        ("Medetomidine", "medetomidine"),
    ])
    def test_real_terms_resolve(self, value, key):
        assert key in cf.resolve_terms([value])

    def test_unclassified_high_volume_codes_are_covered(self):
        groups = cf.load_substance_groups()["groups"]
        listed = {c for g in groups.values() for c in g["codes"]}
        for code in ("COCBAS", "COC_HCL", "COC_FB", "FENT_FB", "METH_HCL", "MDMA_HCL", "CARFENT_FB"):
            assert code in listed

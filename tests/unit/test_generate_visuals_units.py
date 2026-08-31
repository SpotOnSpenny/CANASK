"""Pure cleaning helpers in the V1 generation layer. The 3-state convention is the
load-bearing invariant: reported float (incl. genuine 0) / SUPPRESSED sentinel /
None (not reported -> no fact row). Collapsing any of these into 0 corrupts charts."""
import math

import pytest

from data_viz.generate_visuals import (
    SUPPRESSED,
    _coroners_clean_cell,
    _derived_count,
    _derived_rate,
    _emit_fact,
    _infobase_cell,
    _split_value,
    additional_metric,
)


class TestInfobaseCell:
    @pytest.mark.parametrize("raw,expected", [
        ("47", 47.0),
        (12, 12.0),
        ("0", 0.0),                    # genuine zero is reported, not a gap
        ("12.5", 12.5),
        ("85%", 85.0),                 # percent sign stripped
        ("1\xa0234".replace("\xa0", ""), 1234.0),
    ])
    def test_reported(self, raw, expected):
        assert _infobase_cell(raw) == expected

    def test_suppressed_sentinel_passes_through(self):
        assert _infobase_cell("Suppr.") == SUPPRESSED

    @pytest.mark.parametrize("raw", [None, "", "   ", "n/a", float("nan")])
    def test_not_reported(self, raw):
        assert _infobase_cell(raw) is None

    def test_nbsp_stripped(self):
        assert _infobase_cell("\xa047\xa0") == 47.0


class TestDerivedCount:
    def test_derives_rounded_count(self):
        assert _derived_count(25.0, 200.0) == 50

    def test_suppression_propagates_from_either_input(self):
        assert _derived_count(SUPPRESSED, 200.0) == SUPPRESSED
        assert _derived_count(25.0, SUPPRESSED) == SUPPRESSED

    def test_none_propagates(self):
        assert _derived_count(None, 200.0) is None
        assert _derived_count(25.0, None) is None

    def test_zero_percent_is_zero_not_gap(self):
        assert _derived_count(0.0, 200.0) == 0


class TestDerivedRate:
    def test_per_100k(self):
        assert _derived_rate(50, 1_000_000) == 5.0

    def test_rounds_to_two_decimals(self):
        assert _derived_rate(7, 3_000_000) == round(7 / 3_000_000 * 100000, 2)

    def test_suppressed_count_propagates(self):
        assert _derived_rate(SUPPRESSED, 1_000_000) == SUPPRESSED

    def test_none_count_or_missing_population_skips(self):
        assert _derived_rate(None, 1_000_000) is None
        assert _derived_rate(50, None) is None
        assert _derived_rate(50, 0) is None  # falsy population must not divide


class TestCoronersCleanCell:
    @pytest.mark.parametrize("raw,expected", [
        ("47", 47.0),
        ("85%", 85.0),
        ("\xa012\xa0", 12.0),
        (3, 3),
        (2.5, 2.5),
    ])
    def test_reported(self, raw, expected):
        assert _coroners_clean_cell(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "n/a", None, float("nan")])
    def test_blank_or_non_numeric_is_none(self, raw):
        # No suppression marker in this workbook: blanks are gaps, never fabricated 0s.
        assert _coroners_clean_cell(raw) is None


class TestEmitFact:
    class _StubVisual:
        def __init__(self):
            self.calls = []

        def fact(self, geo, time_frame, value, **kw):
            self.calls.append((geo, time_frame, value, kw))

    def test_none_emits_nothing(self):
        stub = self._StubVisual()
        _emit_fact(stub, "ontario", "2024", None)
        assert stub.calls == []

    def test_zero_and_suppressed_pass_through(self):
        stub = self._StubVisual()
        _emit_fact(stub, "ontario", "2024", 0)
        _emit_fact(stub, "ontario", "2024", SUPPRESSED, dimension="opioids")
        assert stub.calls[0][2] == 0
        assert stub.calls[1][2] == SUPPRESSED
        assert stub.calls[1][3] == {"dimension": "opioids"}


class TestSplitValue:
    def test_numeric_string_fills_both_columns(self):
        assert _split_value("47") == (47.0, "47")

    def test_non_numeric_string_is_text_only(self):
        assert _split_value("n/a") == (None, "n/a")

    def test_number_is_float_only(self):
        assert _split_value(12) == (12.0, None)

    def test_none(self):
        assert _split_value(None) == (None, None)


class TestAdditionalMetric:
    @pytest.mark.parametrize("label,expected", [
        ("Total Deaths", "total_deaths"),
        ("Total Opioid Deaths", "total_opioid_deaths"),
        ("Samples Tested", "total_samples_tested"),
    ])
    def test_stable_names(self, label, expected):
        assert additional_metric(label) == expected


# ---- Drug-checking expected-vs-actual helpers ----
import pandas

from data_viz.generate_visuals import (
    _drugcheck_repair_shifted,
    _norm_substance,
    classify_drugcheck_sample,
)

_STRIP_COLS = [
    "Fentanyl test strip", "Benzodiazepine test strip", "Nitazene test strip",
    "Xylazine test strip", "MDMA Test strip", "Medetomidine test strip",
]


def _sample(expected="Ketamine", **overrides):
    """One checked-sample row: every strip Not done, no FTIR identifications; override per test."""
    row = {"Expected Drug (1)": expected}
    for col in _STRIP_COLS:
        row[col] = "Not done"
    for i in range(1, 6):
        row[f"FTIR ({i})"] = None
    row.update(overrides)
    return row


class TestNormSubstance:
    @pytest.mark.parametrize("raw,expected", [
        ("Kétamine", "ketamine"),                    # accents decomposed and dropped
        ("Cocaïne", "cocaine"),
        ("Para-fluorofentanyl", "parafluorofentanyl"),  # punctuation removed
        ("Désalkylgidazépam", "desalkylgidazepam"),
        ("Cocaine HCl", "cocainehcl"),               # salt form kept, case folded
        ("Crack (cocaïne base)", "crackcocainebase"),
        ("MDMA - (3,4-Methylenedioxymethamphetamine)", "mdma34methylenedioxymethamphetamine"),
    ])
    def test_variants_collapse(self, raw, expected):
        assert _norm_substance(raw) == expected

    @pytest.mark.parametrize("raw", [None, float("nan")])
    def test_missing_is_empty(self, raw):
        assert _norm_substance(raw) == ""


class TestDrugcheckRepairShifted:
    # Column order mirrors the workbook: everything from "Expected Drug (2)" onward is shifted two
    # left in the broken rows, so strip vocab sits in the expected-drug-2 slot and FTIR names
    # pollute the last two strip columns.
    _COLS = (["Expected Drug (1)", "Expected Drug (2)", "Drug Category (2)"]
             + _STRIP_COLS + ["FTIR (1)", "FTIR (1.1) drug category"])

    def _frame(self):
        shifted = ["Speed", "Negative", "Not done", "Not done", "Not done", "Not done",
                   "Not done", "Caféine", "Stimulants", None, None]
        clean = ["MDMA", None, None, "Negative", "Not done", "Not done", "Not done",
                 "Not done", "Not done", "MDMA", "Stimulants"]
        return pandas.DataFrame([shifted, clean], columns=self._COLS)

    def test_shifted_row_realigned(self):
        repaired = _drugcheck_repair_shifted(self._frame())
        row = repaired.iloc[0]
        assert row["Fentanyl test strip"] == "Negative"
        assert row["MDMA Test strip"] == "Not done"        # FTIR name pollution gone
        assert row["Medetomidine test strip"] == "Not done"
        assert row["FTIR (1)"] == "Caféine"
        assert row["FTIR (1.1) drug category"] == "Stimulants"
        assert pandas.isna(row["Expected Drug (2)"])
        assert pandas.isna(row["Drug Category (2)"])

    def test_clean_row_untouched(self):
        repaired = _drugcheck_repair_shifted(self._frame())
        assert list(repaired.iloc[1]) == list(self._frame().iloc[1])

    def test_no_expected2_column_is_noop(self):
        df = pandas.DataFrame([["Ketamine"]], columns=["Expected Drug (1)"])
        assert _drugcheck_repair_shifted(df).equals(df)


class TestClassifyDrugcheckSample:
    def test_expected_only_via_ftir(self):
        row = _sample("Ketamine", **{"FTIR (1)": "Ketamine HCl"})
        assert classify_drugcheck_sample(row) == ("Ketamine", "expected_only")

    def test_accented_expected_drug(self):
        row = _sample("Kétamine", **{"FTIR (1)": "Kétamine"})
        assert classify_drugcheck_sample(row) == ("Ketamine", "expected_only")

    def test_expected_plus_via_strips(self):
        row = _sample("Fentanyl", **{"Fentanyl test strip": "Positive",
                                     "Benzodiazepine test strip": "Positive"})
        assert classify_drugcheck_sample(row) == ("Fentanyl", "expected_plus")

    def test_expected_plus_via_ftir(self):
        row = _sample("Cocaine", **{"FTIR (1)": "Cocaine HCL", "FTIR (2)": "Carfentanil HCL"})
        assert classify_drugcheck_sample(row) == ("Cocaine", "expected_plus")

    def test_not_expected(self):
        row = _sample("MDMA", **{"MDMA Test strip": "Negative", "FTIR (1)": "Caféine"})
        assert classify_drugcheck_sample(row) == ("MDMA", "not_expected")

    def test_fentanyl_analog_is_a_match_not_an_adulterant(self):
        row = _sample("Fentanyl", **{"FTIR (1)": "Parafluorofentanyl"})
        assert classify_drugcheck_sample(row) == ("Fentanyl", "expected_only")

    def test_fentanyl_found_in_other_drug_is_noteworthy(self):
        row = _sample("Cocaine", **{"FTIR (1)": "Cocaine", "Fentanyl test strip": "Positive"})
        assert classify_drugcheck_sample(row) == ("Cocaine", "expected_plus")

    # Cocaine vs crack match strictly by salt form; a bare "Cocaine" is ambiguous -> both.
    def test_crack_does_not_match_powder_form(self):
        row = _sample("Crack", **{"FTIR (1)": "Cocaine HCL"})
        assert classify_drugcheck_sample(row) == ("Crack cocaine", "not_expected")

    def test_crack_matches_base_form(self):
        row = _sample("Crack", **{"FTIR (1)": "Cocaine freebase"})
        assert classify_drugcheck_sample(row) == ("Crack cocaine", "expected_only")

    def test_cocaine_does_not_match_base_form(self):
        row = _sample("Cocaine", **{"FTIR (1)": "Cocaine freebase"})
        assert classify_drugcheck_sample(row) == ("Cocaine", "not_expected")

    def test_bare_cocaine_matches_both_forms(self):
        assert classify_drugcheck_sample(
            _sample("Crack", **{"FTIR (1)": "Cocaine"})) == ("Crack cocaine", "expected_only")
        assert classify_drugcheck_sample(
            _sample("Cocaine", **{"FTIR (1)": "Cocaine"})) == ("Cocaine", "expected_only")

    @pytest.mark.parametrize("expected", ["LSD", "Speed", "2C-B", "Unknown substance"])
    def test_uncharted_expected_drug_excluded(self, expected):
        assert classify_drugcheck_sample(_sample(expected, **{"FTIR (1)": "Caffeine"})) is None

    def test_no_usable_tests_excluded(self):
        row = _sample("Ketamine", **{"Xylazine test strip": "Not available"})
        assert classify_drugcheck_sample(row) is None

    def test_inconclusive_ftir_alone_excluded(self):
        row = _sample("Ketamine", **{"FTIR (1)": "Inconclusive"})
        assert classify_drugcheck_sample(row) is None

    def test_negative_strip_alone_is_usable(self):
        # The target's own strip that actually ran (Negative) keeps the sample in the denominator.
        row = _sample("Fentanyl", **{"Fentanyl test strip": "Negative"})
        assert classify_drugcheck_sample(row) == ("Fentanyl", "not_expected")

    def test_adulterant_strip_alone_cannot_conclude_not_expected(self):
        # A benzo strip says nothing about cocaine: with no test capable of detecting the
        # expected drug, the sample is excluded rather than counted as "not expected".
        for result in ("Positive", "Negative"):
            row = _sample("Cocaine", **{"Benzodiazepine test strip": result})
            assert classify_drugcheck_sample(row) is None

    def test_ftir_makes_any_target_testable(self):
        # A conclusive FTIR identification can detect every target, so its absence of a cocaine
        # match IS evidence -- the sample stays in the denominator.
        row = _sample("Cocaine", **{"FTIR (1)": "Caffeine"})
        assert classify_drugcheck_sample(row) == ("Cocaine", "not_expected")

    def test_dedicated_strip_makes_its_target_testable(self):
        # MDMA has a dedicated strip: a Negative on it alone concludes not_expected, while the
        # same lone strip result proves nothing about a target it cannot detect (covered above).
        row = _sample("MDMA", **{"MDMA Test strip": "Negative"})
        assert classify_drugcheck_sample(row) == ("MDMA", "not_expected")

    def test_every_canon_target_has_ftir_match_entry(self):
        # classify's found-check indexes _DRUGCHECK_FTIR_MATCH by canon target; a new target row
        # without a match set would silently classify everything as not_expected (.get fallback).
        from data_viz.generate_visuals import _DRUGCHECK_FTIR_MATCH, _DRUGCHECK_TARGET_CANON
        assert set(_DRUGCHECK_TARGET_CANON.values()) <= set(_DRUGCHECK_FTIR_MATCH)


class TestDrugcheckMonths:
    def test_normal_dates_parse_to_month(self):
        import pandas
        from data_viz.generate_visuals import _drugcheck_months
        out = _drugcheck_months(pandas.Series(["6/25/2024", "2025-01-15"]))
        assert list(out) == ["2024-06", "2025-01"]

    def test_implausible_dates_dropped(self):
        # format='mixed' happily parses a bare year or a fat-fingered year to a real date;
        # the sanity window turns those into NaN instead of charting them.
        import pandas
        from data_viz.generate_visuals import _drugcheck_months
        out = _drugcheck_months(pandas.Series(["1024", "6/25/2924", "6/25/2024"]))
        assert out.isna().tolist() == [True, True, False]

    def test_unparseable_dates_coerce_to_nan(self):
        import pandas
        from data_viz.generate_visuals import _drugcheck_months
        out = _drugcheck_months(pandas.Series(["not a date", None]))
        assert out.isna().all()

"""Pure cleaning helpers in the V1 generation layer. The 3-state convention is the
load-bearing invariant: reported float (incl. genuine 0) / SUPPRESSED sentinel /
None (not reported -> no fact row). Collapsing any of these into 0 corrupts charts."""
import math

import pandas
import pytest

from data_viz.generate_visuals import (
    SUPPRESSED,
    _coroners_clean_cell,
    _coroners_frames_from_sheets,
    _coroners_month_key,
    _coroners_month_series,
    _derived_count,
    _derived_rate,
    _emit_fact,
    _emit_month_and_quarter,
    _bccsu_buckets,
    _infobase_cell,
    _period_keys,
    _quarter_of,
    _split_value,
    _sum_complete_quarters,
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


# ---- BC Coroners month / quarter grain helpers ----
class TestCoronersMonthKey:
    @pytest.mark.parametrize("raw,expected", [
        ("2025 Apr", "2025-04"),
        ("2025\xa0Apr", "2025-04"),
        (" 2026\xa0Jan\xa0", "2026-01"),
        ("2025 Dec", "2025-12"),
        ("2025Apr", "2025-04"),          # nbsp stripped upstream leaves no space
        ("2025 April", "2025-04"),
        ("2025 Sept", "2025-09"),
        ("2025 apr", "2025-04"),
        ("2025-04", "2025-04"),  # idempotent
    ])
    def test_normalises(self, raw, expected):
        assert _coroners_month_key(raw) == expected

    @pytest.mark.parametrize("raw", ["2025", "Fentanyl detected", "2023 Number", "2023 Marginal", "2023 Decreased", "2025 Mayhem", "", "2025-13", "2025-Q2", None])
    def test_rejects_non_months(self, raw):
        with pytest.raises(ValueError):
            _coroners_month_key(raw)


class TestQuarterOf:
    @pytest.mark.parametrize("month,quarter", [
        ("2025-01", "2025-Q1"), ("2025-03", "2025-Q1"), ("2025-04", "2025-Q2"),
        ("2025-09", "2025-Q3"), ("2025-12", "2025-Q4"),
    ])
    def test_quarter_of(self, month, quarter):
        assert _quarter_of(month) == quarter


def _months(start_year, start_month, values):
    out, y, m = {}, start_year, start_month
    for v in values:
        out[f"{y}-{m:02d}"] = v
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


class TestSumCompleteQuarters:
    def test_thirteen_months_apr_to_apr_is_exactly_four_quarters(self):
        # The workbook's last-13-months window: Apr 2025 .. Apr 2026. The trailing Apr is a partial Q2.
        series = _months(2025, 4, range(1, 14))
        assert _sum_complete_quarters(series) == {
            "2025-Q2": 1 + 2 + 3, "2025-Q3": 4 + 5 + 6, "2025-Q4": 7 + 8 + 9, "2026-Q1": 10 + 11 + 12,
        }

    def test_none_month_drops_its_quarter(self):
        series = _months(2025, 1, [1, None, 3, 4, 5, 6])
        assert _sum_complete_quarters(series) == {"2025-Q2": 15}

    def test_missing_month_is_a_gap_not_a_partial_sum(self):
        series = _months(2025, 1, [1, 2, 3, 4, 5, 6])
        del series["2025-05"]
        assert _sum_complete_quarters(series) == {"2025-Q1": 6}

    def test_zeros_sum_to_zero(self):
        assert _sum_complete_quarters(_months(2025, 1, [0, 0, 0])) == {"2025-Q1": 0}

    def test_empty(self):
        assert _sum_complete_quarters({}) == {}


def _sheet(title, header, rows):
    """A sheet as the workbook reads at header=0: title column, junk index, label column, periods."""
    cols = [title, "Unnamed: 1"] + [f"Unnamed: {i}" for i in range(2, 2 + len(header))]
    body = [[None, None] + header, [None, None] + [None] * len(header)]
    body += [[None, label] + values for label, values in rows]
    return pandas.DataFrame(body, columns=cols[:2 + len(header)])


class TestCoronersFramesFromSheets:
    def test_year_and_month_grains_tagged_and_months_normalised(self):
        sheets = {
            "Table 1": _sheet("Deaths by HA", ["2024", "2025"], [("Fraser", [10, 20]), ("Island", [1, 2])]),
            "Table 4": _sheet("Deaths by HA", ["2025\xa0Apr", "2025 May"], [("Fraser", [3, 4])]),
        }
        frames = _coroners_frames_from_sheets(sheets)["Deaths by HA"]
        assert [f["grain"] for f in frames] == ["year", "month"]
        assert frames[0]["periods"] == ["2024", "2025"]
        assert frames[0]["rows"] == {"Fraser": [10, 20], "Island": [1, 2]}
        assert frames[1]["periods"] == ["2025-04", "2025-05"]
        assert frames[1]["rows"] == {"Fraser": [3, 4]}

    @pytest.mark.parametrize("bad_header", ["Total", "nan"])
    def test_partial_month_header_fails_loudly_naming_the_sheet(self, bad_header):
        # First header is a month but a later one drifts: a strict rebuild must not publish partial
        # data, and the error must be traceable to the sheet.
        sheets = {"Table 4": _sheet("Deaths by Sex", ["2025 Apr", "2025 May", bad_header], [("Fraser", [1, 2, 3])])}
        with pytest.raises(ValueError, match="Deaths by Sex") as excinfo:
            _coroners_frames_from_sheets(sheets)
        assert repr(bad_header) in str(excinfo.value)

    def test_category_header_resembling_a_month_prefix_is_other(self):
        sheets = {"T": _sheet("Outcome", ["2023 Marginal", "2023 Decreased"], [("Fraser", [1, 2])])}
        (frame,) = _coroners_frames_from_sheets(sheets)["Outcome"]
        assert frame["grain"] == "other"

    def test_non_period_sheet_is_not_tagged_month(self):
        sheets = {"T": _sheet("Route of use", ["Injection", "Oral"], [("2025", [1, 2])])}
        (frame,) = _coroners_frames_from_sheets(sheets)["Route of use"]
        assert frame["grain"] == "other"
        assert frame["periods"] == ["Injection", "Oral"]

    def test_short_or_untitled_sheets_skipped(self):
        short = pandas.DataFrame([[None, None, "2024"]], columns=["Title", "Unnamed: 1", "Unnamed: 2"])
        untitled = pandas.DataFrame([[None] * 3] * 3, columns=["Unnamed: 0", "Unnamed: 1", "Unnamed: 2"])
        assert _coroners_frames_from_sheets({"a": short, "b": untitled}) == {}

    def test_coroners_frame_lookup_still_works(self):
        from data_viz.generate_visuals import _coroners_frame
        sheets = {"Table 4": _sheet("T", ["2025 Apr"], [("Fraser", [3])]),
                  "Table 1": _sheet("T", ["2025"], [("Fraser", [9])])}
        workbook = {"frames": _coroners_frames_from_sheets(sheets)}
        assert _coroners_frame(workbook, "T")["periods"] == ["2025"]
        assert _coroners_frame(workbook, "T", "month")["periods"] == ["2025-04"]
        assert _coroners_frame(workbook, "missing") is None


class TestCoronersMonthSeries:
    FRAME = {"grain": "month", "periods": ["2025-04", "2025-05", "2025-06"],
             "rows": {"Fraser": [3, "\xa0", "5"], "Island": [0, 0, 0]}}

    def test_cleans_cells_and_keeps_gaps_as_none(self):
        assert _coroners_month_series(self.FRAME, "Fraser") == {
            "2025-04": 3, "2025-05": None, "2025-06": 5.0}

    def test_zero_is_reported(self):
        assert _coroners_month_series(self.FRAME, "Island") == {
            "2025-04": 0, "2025-05": 0, "2025-06": 0}

    def test_missing_frame_or_label_is_empty(self):
        assert _coroners_month_series(None, "Fraser") == {}
        assert _coroners_month_series(self.FRAME, "Northern") == {}


class TestEmitMonthAndQuarter:
    class _Stub:
        def __init__(self):
            self.calls = []

        def fact(self, geo, time_frame, value, **kw):
            self.calls.append((geo, time_frame, value, kw))

    def test_months_then_complete_quarters_with_grain_tags(self):
        stub = self._Stub()
        _emit_month_and_quarter(stub, "Fraser", _months(2025, 4, [1, 2, 3, 4, None, 6, 7]), dimension2="Male")
        grains = [(c[1], c[3]["time_frame_type"]) for c in stub.calls]
        assert grains == [
            ("2025-04", "month"), ("2025-05", "month"), ("2025-06", "month"), ("2025-07", "month"),
            ("2025-09", "month"), ("2025-10", "month"),   # 2025-08 is None -> no fact, never a 0
            ("2025-Q2", "quarter"),                       # Q3 has a gap, Q4 only has 1 of 3 months
        ]
        assert stub.calls[-1][2] == 6
        assert all(c[0] == "Fraser" and c[3]["dimension2"] == "Male" and c[3]["data_type"] == "counts"
                   for c in stub.calls)

    def test_data_type_passed_through(self):
        stub = self._Stub()
        _emit_month_and_quarter(stub, "BC", _months(2025, 1, [1, 2, 3]), data_type="counts")
        assert [c[3]["data_type"] for c in stub.calls] == ["counts"] * 4
        assert stub.calls[-1][1:3] == ("2025-Q1", 6)

    def test_quarter_rates_are_rounded_against_float_noise(self):
        stub = self._Stub()
        _emit_month_and_quarter(stub, "BC", _months(2025, 1, [0.1, 0.2, 0.3]), data_type="rates")
        assert [c[2] for c in stub.calls[:3]] == [0.1, 0.2, 0.3]   # months are emitted untouched
        assert stub.calls[-1][1] == "2025-Q1"
        assert stub.calls[-1][2] == 0.6   # raw sum is 0.6000000000000001


class TestCoronersCleanerMonthQuarter:
    """v1_coroners_export_clean driven by a stub writer over a tiny synthetic workbook: the heatmap and
    age visuals gain month + complete-quarter facts; the yearly facts are unchanged."""

    HEAT = "Unregulated Drug Deaths by Health Authority of Injury"
    AGE = "Unregulated Drug Deaths by Age Group"
    AGE_RATES = "Age-Specific Unregulated Drug Death Rates per 100,000"
    MONTHS = ["2025-04", "2025-05", "2025-06", "2025-07", "2025-08", "2025-09"]   # Q2 whole, Q3 has a gap

    class _Visual:
        def __init__(self):
            self.facts = []        # (geo, time_frame, value, time_frame_type, data_type, dimension2)
            self.additionals = []  # (geo, time_frame, label, value, time_frame_type)
            self.source = None

        def use_source(self, source):
            self.source = source

        def fact(self, geo, time_frame, value, *, data_type="counts", dimension=None, dimension2=None,
                 time_frame_type=None):
            self.facts.append((geo, time_frame, value, time_frame_type or "year", data_type, dimension2))

        def additional(self, geo, time_frame, label, value, *, time_frame_type=None):
            self.additionals.append((geo, time_frame, label, value, time_frame_type or "year"))

    class _Writer:
        def __init__(self, visuals):
            self.visuals = visuals

        def visual(self, province, visual_id):
            return self.visuals.get(visual_id)

    @classmethod
    def _workbook(cls):
        def frame(grain, periods, rows):
            return {"grain": grain, "periods": periods, "rows": rows}
        return {
            "date_updated": "May 29, 2026", "data_until": "April 01, 2026",
            "frames": {
                cls.HEAT: [
                    frame("year", ["2024"], {"Fraser": [10], "British Columbia": [40]}),
                    frame("month", cls.MONTHS, {"Fraser": [1, 2, 3, 4, None, 6],
                                                "British Columbia": [5, 5, 5, 6, 6, 6]}),
                ],
                cls.AGE: [
                    frame("year", ["2024"], {"20-29": [7], "Not available": [1], "Total": [8]}),
                    frame("month", cls.MONTHS, {"20-29": [1, 1, 1, 2, 2, 2], "Not available": [0, 0, 1, 0, 0, 0],
                                                "Total": [1, 1, 2, 2, 2, None]}),
                ],
                cls.AGE_RATES: [
                    frame("year", ["2024"], {"20-29": [30.5], "Not available": [0]}),
                    frame("month", cls.MONTHS, {"20-29": [0.5, 0.25, 0.25, 1, 1, 1],
                                                "Not available": [0, 0, 0, 0, 0, 0]}),
                ],
            },
        }

    @pytest.fixture
    def run(self, monkeypatch):
        import data_viz.generate_visuals as gv
        monkeypatch.setattr(gv, "_read_coroners_workbook", self._workbook)
        visuals = {"drug_death_heatmap": self._Visual(), "drug_toxicity_deaths_by_age": self._Visual()}
        gv.v1_coroners_export_clean(self._Writer(visuals), "british-columbia")
        return visuals

    @staticmethod
    def _grain(facts, grain):
        return [f for f in facts if f[3] == grain]

    def test_heatmap_months_quarters_and_unchanged_year(self, run):
        facts = run["drug_death_heatmap"].facts
        assert self._grain(facts, "year") == [
            ("Fraser", "2024", 10, "year", "counts", None),
            ("British Columbia", "2024", 40, "year", "counts", None)]
        fraser_months = [(f[1], f[2]) for f in self._grain(facts, "month") if f[0] == "Fraser"]
        # 2025-08 is None -> no fact (never a 0)
        assert fraser_months == [("2025-04", 1), ("2025-05", 2), ("2025-06", 3), ("2025-07", 4),
                                 ("2025-09", 6)]
        quarters = {(f[0], f[1]): f[2] for f in self._grain(facts, "quarter")}
        assert quarters == {("Fraser", "2025-Q2"): 6, ("British Columbia", "2025-Q2"): 15,
                            ("British Columbia", "2025-Q3"): 18}    # Fraser's Q3 has a gap -> none

    def test_age_counts_months_quarters_and_relabel(self, run):
        v = run["drug_toxicity_deaths_by_age"]
        counts = [f for f in v.facts if f[4] == "counts"]
        assert [(f[1], f[2]) for f in self._grain(counts, "month") if f[5] == "20-29"] == [
            ("2025-04", 1), ("2025-05", 1), ("2025-06", 1), ("2025-07", 2), ("2025-08", 2), ("2025-09", 2)]
        quarters = {(f[1], f[5]): f[2] for f in self._grain(counts, "quarter")}
        assert quarters == {("2025-Q2", "20-29"): 3, ("2025-Q3", "20-29"): 6,
                            ("2025-Q2", "Age Unavailable"): 1, ("2025-Q3", "Age Unavailable"): 0}
        assert not any(f[5] in ("Not available", "Total") for f in v.facts)
        assert self._grain(counts, "year")[0] == ("British Columbia", "2024", 7, "year", "counts", "20-29")

    def test_age_total_row_becomes_additional_at_both_grains(self, run):
        v = run["drug_toxicity_deaths_by_age"]
        assert v.additionals == [
            ("British Columbia", "2024", "Total Deaths", 8, "year"),
            ("British Columbia", "2025-04", "Total Deaths", 1, "month"),
            ("British Columbia", "2025-05", "Total Deaths", 1, "month"),
            ("British Columbia", "2025-06", "Total Deaths", 2, "month"),
            ("British Columbia", "2025-07", "Total Deaths", 2, "month"),
            ("British Columbia", "2025-08", "Total Deaths", 2, "month"),   # 2025-09 is None -> skipped
            ("British Columbia", "2025-Q2", "Total Deaths", 4, "quarter"),  # Q3 incomplete -> no quarter
        ]

    def test_age_rates_pass_through_monthly_and_sum_per_quarter(self, run):
        rates = [f for f in run["drug_toxicity_deaths_by_age"].facts if f[4] == "rates"]
        assert [(f[1], f[2]) for f in self._grain(rates, "month") if f[5] == "20-29"] == [
            ("2025-04", 0.5), ("2025-05", 0.25), ("2025-06", 0.25), ("2025-07", 1), ("2025-08", 1),
            ("2025-09", 1)]
        quarters = {(f[1], f[5]): f[2] for f in self._grain(rates, "quarter")}
        assert quarters[("2025-Q2", "20-29")] == 1.0      # 0.5 + 0.25 + 0.25
        assert quarters[("2025-Q3", "20-29")] == 3
        assert ("2025-Q2", "Age Unavailable") in quarters
        assert self._grain(rates, "year")[0][5:] == ("20-29",) and self._grain(rates, "year")[0][2] == 30.5


class TestBccsuPeriodKeys:
    DATES = pandas.Series(["2024-12-31", "2025-01-01", "2025-04-15", "not a date", "2025-09-30"])

    def test_year_quarter_month_keys(self):
        assert _period_keys(self.DATES, "year").tolist()[:3] == ["2024", "2025", "2025"]
        quarters = _period_keys(self.DATES, "quarter")
        assert quarters.tolist()[:3] == ["2024-Q4", "2025-Q1", "2025-Q2"]   # Dec -> Q4, Jan -> Q1
        assert quarters.iloc[4] == "2025-Q3"                                 # Sep is the last month of Q3
        assert _period_keys(self.DATES, "month").tolist()[:3] == ["2024-12", "2025-01", "2025-04"]

    def test_unparseable_date_is_missing(self):
        for grain in ("year", "quarter", "month"):
            assert pandas.isna(_period_keys(self.DATES, grain).iloc[3])

    def test_unknown_grain_raises(self):
        with pytest.raises(ValueError):
            _period_keys(self.DATES, "week")


class TestBccsuBuckets:
    DF = pandas.DataFrame({
        "Visit Date": ["2025-02-10", "2024-12-31", "2025-01-05", "bad", "2025-05-01"],
        "Category": ["Opioid", "Opioid", "Stimulant", "Opioid", "Opioid"],
    })

    def test_buckets_ordered_and_totals_preserved(self):
        for grain, expected in (("year", ["2024", "2025"]),
                                ("quarter", ["2024-Q4", "2025-Q1", "2025-Q2"]),
                                ("month", ["2024-12", "2025-01", "2025-02", "2025-05"])):
            buckets = _bccsu_buckets(self.DF, grain)
            assert list(buckets) == expected
            assert sum(len(b) for b in buckets.values()) == 4     # the unparseable row is dropped

    def test_bucket_contents(self):
        buckets = _bccsu_buckets(self.DF, "quarter")
        assert buckets["2025-Q1"]["Category"].tolist() == ["Opioid", "Stimulant"]


class TestBccsuCleanerGrains:
    """v1_BCCSU_export_clean driven by a stub writer over a tiny synthetic visit frame."""

    VISUALS = ("drug_supply_by_year", "fent_benz_by_year", "opioid_types_by_year")

    @pytest.fixture
    def run(self, monkeypatch):
        import data_viz.generate_visuals as gv
        frame = pandas.DataFrame({
            "Visit Date": ["2024-12-20", "2025-01-10", "2025-01-20", "2025-02-03", "2025-04-02", "garbage"],
            "Category": ["Opioid", "Opioid", "Stimulant", "Opioid", "Stimulant", "Opioid"],
            "Fentanyl Strip": ["Pos", "Pos", "Neg", "Neg", "Pos", "Pos"],
            "Benzo Strip": ["Neg", "Pos", "Neg", "Neg", "Neg", "Neg"],
            "Medetomidine Strip": ["Neg", "Neg", "Neg", "Pos", "Neg", "Neg"],
            "Spectrometer": ["Fentanyl; Heroin", "Fentanyl", None, "Morphine", None, "Fentanyl"],
        })
        monkeypatch.setattr(gv, "pull_data", lambda names: {
            "bcDrugSense": {"dataframe": frame, "date_updated": "x", "data_until": "y"}})
        make = TestCoronersCleanerMonthQuarter._Visual
        visuals = {name: make() for name in self.VISUALS}
        gv.v1_BCCSU_export_clean(TestCoronersCleanerMonthQuarter._Writer(visuals), "british-columbia")
        return visuals

    @staticmethod
    def _by(facts, grain, period):
        return [f for f in facts if f[3] == grain and f[1] == period]

    def test_all_three_grains_emitted_per_visual(self, run):
        for name in self.VISUALS:
            grains = {f[3] for f in run[name].facts}
            assert grains == {"year", "quarter", "month"}, name

    def test_drug_supply_counts_and_rates_per_period(self, run):
        facts = run["drug_supply_by_year"].facts
        year = {(f[4], f[5]): f[2] for f in self._by(facts, "year", "2025")}
        assert year == {("counts", "Opioid"): 2, ("rates", "Opioid"): 50.0,
                        ("counts", "Stimulant"): 2, ("rates", "Stimulant"): 50.0}
        quarter = {(f[4], f[5]): f[2] for f in self._by(facts, "quarter", "2025-Q1")}
        assert quarter[("counts", "Opioid")] == 2 and quarter[("counts", "Stimulant")] == 1
        assert quarter[("rates", "Stimulant")] == 33.33
        month = {(f[4], f[5]): f[2] for f in self._by(facts, "month", "2025-04")}
        assert month[("counts", "Opioid")] == 0 and month[("rates", "Stimulant")] == 100.0

    def test_drug_supply_rates_sum_to_100_per_grain_period(self, run):
        facts = run["drug_supply_by_year"].facts
        periods = {(f[3], f[1]) for f in facts}
        assert len(periods) == 2 + 3 + 4      # 2 years, 3 quarters, 4 months
        for grain, period in periods:
            total = sum(f[2] for f in self._by(facts, grain, period) if f[4] == "rates")
            assert total == pytest.approx(100, abs=0.02), (grain, period)

    def test_strips_use_period_total_as_denominator(self, run):
        facts = run["fent_benz_by_year"].facts
        q1 = {(f[4], f[5]): f[2] for f in self._by(facts, "quarter", "2025-Q1")}
        assert q1[("counts", "Fentanyl")] == 1 and q1[("rates", "Fentanyl")] == 33.33
        assert q1[("counts", "Benzodiazepines")] == 1 and q1[("counts", "Medetomidine")] == 1

    def test_opioid_types_denominator_is_opioid_samples_of_the_period(self, run):
        v = run["opioid_types_by_year"]
        jan = {(f[4], f[5]): f[2] for f in self._by(v.facts, "month", "2025-01")}
        assert jan[("counts", "Fentanyl")] == 1 and jan[("rates", "Fentanyl")] == 100.0
        year = {(f[4], f[5]): f[2] for f in self._by(v.facts, "year", "2025")}
        assert year[("counts", "Fentanyl")] == 1 and year[("rates", "Fentanyl")] == 50.0
        # a period with no opioid samples: zero counts and rates, no divide-by-zero
        apr = {(f[4], f[5]): f[2] for f in self._by(v.facts, "month", "2025-04")}
        assert apr[("rates", "Fentanyl")] == 0

    def test_additional_totals_at_every_grain(self, run):
        adds = run["opioid_types_by_year"].additionals
        assert ("British Columbia", "2025", "Total Opioid Samples", 2, "year") in adds
        assert ("British Columbia", "2025-Q1", "Total Samples", 3, "quarter") in adds
        assert ("British Columbia", "2025-04", "Total Samples", 1, "month") in adds
        assert ("British Columbia", "2024", "Total Samples", 1, "year") in adds
        drug_adds = run["drug_supply_by_year"].additionals
        assert ("British Columbia", "2025-02", "Total Samples", 1, "month") in drug_adds

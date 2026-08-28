"""Cell/frame-level ingest helpers - pure pandas, no files, no DB."""
from datetime import date

import pandas
import pytest

from data_viz.das_ingest import (
    _chunked,
    _date,
    _flag,
    _float,
    _norm,
    _promote_header,
    _resolve_columns,
    _sample_number,
    _text,
)


class TestNorm:
    def test_collapses_embedded_newlines_and_runs(self):
        assert _norm("Date\nSample\n Received") == "Date Sample Received"

    def test_strips(self):
        assert _norm("  Sample Number  ") == "Sample Number"

    def test_non_string_coerced(self):
        assert _norm(123) == "123"


class TestText:
    def test_strips_and_returns(self):
        assert _text("  hello ") == "hello"

    def test_excel_float_id_becomes_digit_string(self):
        assert _text(3716630.0) == "3716630"
        assert _text("3716630.0") == "3716630"

    def test_real_decimal_not_mangled(self):
        assert _text("3.5") == "3.5"

    @pytest.mark.parametrize("value", [None, float("nan"), "", "   "])
    def test_blankish_is_none(self, value):
        assert _text(value) is None


class TestSampleNumber:
    def test_plausible_id(self):
        assert _sample_number("A123-45") == "A123-45"

    def test_na_star_is_valid(self):
        # Uncertified NPS findings share the literal id "N/A*".
        assert _sample_number("N/A*") == "N/A*"

    def test_footnote_prose_rejected(self):
        assert _sample_number("Note: these results are preliminary and bilingual") is None

    def test_over_length_rejected(self):
        assert _sample_number("x" * 51) is None

    def test_blank_rejected(self):
        assert _sample_number(None) is None
        assert _sample_number("  ") is None


class TestFlag:
    @pytest.mark.parametrize("raw,expected", [
        ("Y", True), ("y", True), ("N", False), ("n", False),
        ("maybe", None), (None, None), ("", None),
    ])
    def test_flag(self, raw, expected):
        assert _flag(raw) is expected


class TestDate:
    def test_datetime_like(self):
        assert _date("2026-06-30") == date(2026, 6, 30)

    def test_pandas_timestamp(self):
        assert _date(pandas.Timestamp("2026-06-30")) == date(2026, 6, 30)

    @pytest.mark.parametrize("raw", [None, float("nan"), "not a date"])
    def test_unparseable_is_none(self, raw):
        assert _date(raw) is None


class TestFloat:
    def test_numeric(self):
        assert _float("2.5") == 2.5
        assert _float(3) == 3.0

    @pytest.mark.parametrize("raw", [None, "n/a", "", float("nan")])
    def test_non_numeric_is_none(self, raw):
        assert _float(raw) is None


class TestChunked:
    def test_chunks(self):
        assert list(_chunked(range(5), size=2)) == [[0, 1], [2, 3], [4]]

    def test_empty(self):
        assert list(_chunked([], size=2)) == []

    def test_accepts_any_iterable(self):
        assert list(_chunked((x for x in "abc"), size=2)) == [["a", "b"], ["c"]]


class TestPromoteHeader:
    def _grid(self):
        # Bilingual banner rows above the real header, as the workbooks ship.
        return pandas.DataFrame([
            ["Drug Analysis Service / Service d'analyse des drogues", None],
            ["Données mensuelles", None],
            ["Sample Number", "Province"],
            ["S-1", "ON"],
            ["S-2", "BC"],
        ])

    def test_finds_header_and_returns_data_below(self):
        frame = _promote_header(self._grid(), "Sample Number", "DAS ID All")
        assert list(frame.columns) == ["Sample Number", "Province"]
        assert frame.iloc[0, 0] == "S-1"
        assert len(frame) == 2

    def test_missing_header_raises_with_sheet_name(self):
        with pytest.raises(ValueError, match="DAS ID All"):
            _promote_header(self._grid(), "Nonexistent Prefix", "DAS ID All")

    def test_header_with_newlines_normalized(self):
        grid = pandas.DataFrame([["Sample\nNumber", "Province"], ["S-1", "ON"]])
        frame = _promote_header(grid, "Sample Number", "x")
        assert list(frame.columns) == ["Sample Number", "Province"]


class TestResolveColumns:
    def test_prefix_match(self):
        frame = pandas.DataFrame(columns=["Sample Number (English)", "Province / Territoire"])
        resolved = _resolve_columns(
            frame, {"sample_number": "Sample Number", "province": "Province"}, "sheet")
        assert resolved == {"sample_number": "Sample Number (English)",
                            "province": "Province / Territoire"}

    def test_missing_column_raises_with_field_and_sheet(self):
        frame = pandas.DataFrame(columns=["Sample Number"])
        with pytest.raises(ValueError, match="missing a column starting 'Province'"):
            _resolve_columns(frame, {"province": "Province"}, "DAS QUANT")

    def test_first_match_wins(self):
        frame = pandas.DataFrame(columns=["Date Returned A", "Date Returned B"])
        resolved = _resolve_columns(frame, {"date_returned": "Date Returned"}, "sheet")
        assert resolved["date_returned"] == "Date Returned A"

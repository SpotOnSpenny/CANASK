"""Request-parsing helpers for the DAS Explorer APIs - pure given a MultiDict."""
from datetime import date

import pytest
from sqlalchemy.dialects import postgresql
from werkzeug.datastructures import MultiDict

from data_viz.das_explorer import DATASETS, _quarter, _serialize, parse_filters
from data_viz.database.models import DasSamples


class TestParseFilters:
    def test_strips_f_prefix_and_whitelists(self):
        args = MultiDict([("f_city", "Toronto"), ("f_unknown_field", "x"), ("page", "2")])
        assert parse_filters(args, "id_all") == {"city": "Toronto"}

    def test_select_kind_collects_repeats(self):
        args = MultiDict([("f_province", "ON"), ("f_province", "BC")])
        assert parse_filters(args, "id_all") == {"province": ["ON", "BC"]}

    def test_text_kind_keeps_first_value_only(self):
        args = MultiDict([("f_city", "Toronto"), ("f_city", "Ottawa")])
        assert parse_filters(args, "id_all") == {"city": "Toronto"}

    def test_blank_values_dropped(self):
        args = MultiDict([("f_city", "   "), ("f_province", "")])
        assert parse_filters(args, "id_all") == {}

    def test_values_stripped(self):
        args = MultiDict([("f_city", "  Toronto  ")])
        assert parse_filters(args, "id_all") == {"city": "Toronto"}

    def test_per_dataset_whitelist(self):
        # `units` exists on quant but not id_all
        args = MultiDict([("f_units", "mg")])
        assert parse_filters(args, "id_all") == {}
        assert parse_filters(args, "quant") == {"units": ["mg"]}

    def test_empty_args(self):
        assert parse_filters(MultiDict(), "id_all") == {}


class TestSerialize:
    def test_none(self):
        assert _serialize(None) is None

    def test_date_isoformat(self):
        assert _serialize(date(2026, 6, 30)) == "2026-06-30"

    def test_passthrough(self):
        assert _serialize("ON") == "ON"
        assert _serialize(3.5) == 3.5


class TestQuarterPivotDims:
    """The quarter roll-up mirrors the month/year date dims on every dataset."""

    @pytest.mark.parametrize("dataset, dim", [
        ("id_all", "quarter_returned"), ("id_all", "quarter_received"),
        ("quant", "quarter_returned"), ("nps", "quarter_found"),
    ])
    def test_registered_as_date_kind(self, dataset, dim):
        spec = DATASETS[dataset]["pivot_dims"][dim]
        assert spec["kind"] == "date"
        assert spec["label"].startswith("Quarter ")

    def test_expression_renders_yyyy_qn(self):
        sql = str(_quarter(DasSamples.date_received).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "to_char(das_samples.date_received, 'YYYY')" in sql
        assert "'-Q'" in sql
        assert "to_char(das_samples.date_received, 'Q')" in sql

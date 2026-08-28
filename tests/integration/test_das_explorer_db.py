"""DAS Explorer query layer on Postgres - including the paths that only exist there
(to_char month/year dims, the * wildcard's unnest/string_to_array EXISTS)."""
from datetime import date

import pytest

from data_viz.das_explorer import query_pivot, query_rows

from tests.factories import make_das_drug, make_das_nps, make_das_quant, make_das_sample


@pytest.fixture()
def samples(db_session):
    cocaine = make_das_drug(code="COC", display_name="Cocaine")
    fentanyl = make_das_drug(code="FEN", display_name="Fentanyl")
    only_cocaine = make_das_sample(sample_number="S-1", province="ON", city="Toronto",
                                   drugs=[cocaine], date_returned=date(2026, 5, 10))
    mixed = make_das_sample(sample_number="S-2", province="ON", city="Ottawa",
                            drugs=[cocaine, fentanyl], date_returned=date(2026, 6, 2))
    bc = make_das_sample(sample_number="S-3", province="BC", city="Vancouver",
                         drugs=[fentanyl], date_returned=date(2026, 6, 20))
    return {"cocaine": cocaine, "fentanyl": fentanyl,
            "only_cocaine": only_cocaine, "mixed": mixed, "bc": bc}


class TestQueryRows:
    def test_basic_page_shape(self, samples):
        result = query_rows("id_all", page=1, size=50, sort=[], filters={})
        assert result["last_row"] == 3
        assert result["last_page"] == 1
        assert {row["sample_number"] for row in result["data"]} == {"S-1", "S-2", "S-3"}

    def test_pagination_clamps_and_pages(self, samples):
        result = query_rows("id_all", page=2, size=1, sort=["sample_number.asc"], filters={})
        assert result["last_page"] == 3
        assert [row["sample_number"] for row in result["data"]] == ["S-2"]

    def test_size_clamped_to_max(self, samples):
        result = query_rows("id_all", page=1, size=99999, sort=[], filters={})
        assert result["last_row"] == 3  # no crash; MAX_PAGE_SIZE cap applied internally

    def test_unknown_sort_field_silently_dropped(self, samples):
        result = query_rows("id_all", page=1, size=10, sort=["evil_field.desc"], filters={})
        assert result["last_row"] == 3

    def test_select_filter_multi_value(self, samples):
        result = query_rows("id_all", page=1, size=10, sort=[],
                            filters={"province": ["ON"]})
        assert {row["sample_number"] for row in result["data"]} == {"S-1", "S-2"}

    def test_text_filter_expression(self, samples):
        result = query_rows("id_all", page=1, size=10, sort=[],
                            filters={"drugs_identified": "cocaine AND fentanyl"})
        assert [row["sample_number"] for row in result["data"]] == ["S-2"]

    def test_star_wildcard_only_cocaine(self, samples):
        """`cocaine NOT *` = samples containing cocaine and nothing else - the
        Postgres-only unnest/string_to_array EXISTS path."""
        result = query_rows("id_all", page=1, size=10, sort=[],
                            filters={"drugs_identified": "cocaine NOT *"})
        assert [row["sample_number"] for row in result["data"]] == ["S-1"]

    def test_dates_serialized_isoformat(self, samples):
        result = query_rows("id_all", page=1, size=10, sort=["sample_number.asc"], filters={})
        assert result["data"][0]["date_returned"] == "2026-05-10"


class TestQueryPivot:
    def test_rows_only_pivot(self, samples):
        result = query_pivot("id_all", "province", None, {}, "samples")
        as_map = dict(zip(result["rows"], (row[0] for row in result["cells"])))
        assert as_map == {"ON": 2.0, "BC": 1.0}
        assert result["truncated"] is False

    def test_month_columns_use_to_char(self, samples):
        """The func.to_char month grain - Postgres-only."""
        result = query_pivot("id_all", "province", "month_returned", {}, "samples")
        assert result["cols"] == ["2026-05", "2026-06"]  # chronological, not by total
        on_row = result["cells"][result["rows"].index("ON")]
        assert on_row == [1.0, 1.0]

    def test_drug_dimension_counts_sample_once_per_drug_group(self, samples):
        result = query_pivot("id_all", "drug", None, {}, "samples")
        as_map = dict(zip(result["rows"], (row[0] for row in result["cells"])))
        # S-2 contains both, so it contributes to both groups (distinct per group).
        assert as_map == {"Cocaine": 2.0, "Fentanyl": 2.0}

    def test_null_dimension_lands_in_unknown(self, db_session):
        make_das_sample(sample_number="S-9", province=None, city=None)
        result = query_pivot("id_all", "province", None, {}, "samples")
        assert result["rows"] == ["Unknown"]

    def test_rows_cap_truncates_by_total(self, db_session):
        for i in range(5):
            make_das_sample(sample_number=f"S-c{i}", city=f"City{i}", province="ON")
        make_das_sample(sample_number="S-c5", city="City0", province="ON")  # City0 leads
        result = query_pivot("id_all", "city", None, {}, "samples", rows_cap=2)
        assert result["truncated"] is True
        assert len(result["rows"]) == 2
        assert result["rows"][0] == "City0, ON"

    def test_city_keys_province_qualified(self, samples):
        result = query_pivot("id_all", "city", None, {}, "samples")
        assert "Toronto, ON" in result["rows"]


class TestOtherDatasets:
    def test_quant_avg_measure(self, db_session):
        drug = make_das_drug(code="MET", display_name="Methamphetamine")
        make_das_quant(drug=drug, quantity=10.0, province="ON")
        make_das_quant(drug=drug, quantity=20.0, province="ON")
        result = query_pivot("quant", "drug", None, {}, "avg_quantity")
        assert result["rows"] == ["Methamphetamine"]
        assert result["cells"][0][0] == 15.0

    def test_nps_rows(self, db_session):
        make_das_nps(substance_name="New Substance A", province="QC")
        result = query_rows("nps", page=1, size=10, sort=[], filters={})
        assert result["last_row"] == 1
        assert result["data"][0]["sample_number"] == "N/A*"

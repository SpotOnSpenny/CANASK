"""Tiny source files + a fake SourceSpec for scrape-pipeline tests (no real scrapes, no network)."""
import datetime

import pandas

from data_scraping.contracts import FileContract, SheetContract, RevisionConfig
from data_scraping.registry import SourceSpec

FAKE_CONTRACT = FileContract(ext="csv", sheets={None: SheetContract(
    required={"Region": "text", "Year": "text", "Value": "number"},
    known_values={"Region": frozenset({"Alberta", "Ontario"})},
    markers=frozenset({"Suppr."}),
    revision=RevisionConfig(period_column="Year", value_column="Value", key_columns=("Region",), recent_periods=1),
)})


def fake_spec(**overrides):
    base = dict(key="fakeSource", label="Fake Source", kind="v1", data_source_name="Fake Source",
                expected_cycle_days=30, scrape="tests.scrape_fixtures:fake_scrape", contract=FAKE_CONTRACT)
    base.update(overrides)
    return SourceSpec(**base)


def write_csv(path, rows):
    pandas.DataFrame(rows, columns=list(rows[0].keys())).to_csv(path, index=False)
    return str(path)


def good_rows(extra_year=None):
    rows = [{"Region": r, "Year": y, "Value": v}
            for r in ("Alberta", "Ontario") for y, v in (("2023", 1.0), ("2024", 2.0))]
    if extra_year:
        rows += [{"Region": r, "Year": extra_year, "Value": 3.0} for r in ("Alberta", "Ontario")]
    return rows


FAKE_SCRAPE_RESULT = {}


def fake_scrape(ctx):
    """Returns whatever the test put in FAKE_SCRAPE_RESULT['result'] (or raises FAKE_SCRAPE_RESULT['raise'])."""
    if "raise" in FAKE_SCRAPE_RESULT:
        raise FAKE_SCRAPE_RESULT["raise"]
    return FAKE_SCRAPE_RESULT["result"](ctx)

# The static description of every data source the pipeline knows about. Mutable switches (scheduled
# scraping, auto-publish, pause state) live in the SourceSettings table instead. Scrape callables are
# dotted-path strings resolved lazily so the web image (no Selenium) can import this module.

import importlib
from dataclasses import dataclass, field

from data_scraping.contracts import FileContract, RevisionConfig, SheetContract


@dataclass(frozen=True)
class Thresholds:
    row_drop_pct: float = 10.0
    revision_warn_pct: float = 5.0


@dataclass(frozen=True)
class SourceSpec:
    key: str
    label: str
    kind: str                       # "v1" -> export_data_to_db; "das" -> DAS ingest/replay
    data_source_name: str           # DataSources.name (Data Owner scoping)
    expected_cycle_days: int        # staleness threshold in the nightly report
    scrape: str | None = None       # "package.module:function"; None = upload-only
    contract: FileContract | None = None   # None = registered but not onboarded yet
    time_limit: int = 900           # seconds (soft limit; hard = +60)
    thresholds: Thresholds = field(default_factory=Thresholds)


def _drugcheck_normalize(frame):
    from data_viz.generate_visuals import normalize_drugcheck_headers
    return normalize_drugcheck_headers(frame)


def _das_validate(path):
    from data_viz.das_ingest import validate_das_workbook
    validate_das_workbook(path)


# drugChecking's harmonized export: headers vary by cycle (see DRUGCHECK_COLUMN_RENAMES in
# generate_visuals.py), so the contract normalizes through the same renames the cleaner uses before
# checking columns. "Visit Date" is "any" because it's mixed strings/datetimes by design (see
# _drugcheck_months). Verified against output/20260908_20260813_drugChecking.xlsx (sheet "Sheet1").
DRUGCHECK_CONTRACT = FileContract(ext="xlsx", normalize=_drugcheck_normalize, sheets={
    "Sheet1": SheetContract(required={
        "Visit Date": "any", "Province": "text", "Site/Organization": "text",
        "Expected Drug (1)": "any", "Expected Drug Category (1)": "any", "Expected Drug (2)": "any",
        "FTIR (1)": "any", "FTIR (2)": "any", "FTIR (3)": "any", "FTIR (4)": "any", "FTIR (5)": "any",
        "Fentanyl test strip": "any", "Benzodiazepine test strip": "any", "Nitazene test strip": "any",
        "Xylazine test strip": "any", "MDMA Test strip": "any", "Medetomidine test strip": "any",
    }, min_rows=50),
})


# Health Infobase's quarterly export: verified against output/20260611_20260101_nationalHealthInfobase.csv.
# known_values is the set the cleaner maps *today*; "Source" is deliberately not listed -- the cleaner
# reads only Deaths/Hospitalizations, and the other sources are already present, so listing it adds no signal.
INFOBASE_CONTRACT = FileContract(ext="csv", sheets={None: SheetContract(
    required={"Substance": "text", "Source": "text", "Specific_Measure": "text", "Region": "text",
              "PRUID": "number", "Time_Period": "text", "Year_Quarter": "text", "Aggregator": "any",
              "Disaggregator": "any", "Unit": "text", "Value": "number"},
    markers=frozenset({"Suppr."}),
    known_values={
        "Substance": frozenset({"Opioids", "Stimulants"}),
        "Region": frozenset({"Alberta", "British Columbia", "Canada", "Manitoba", "New Brunswick",
                             "Newfoundland and Labrador", "Northern and rural Manitoba", "Northwest Territories",
                             "Nova Scotia", "Nunavut", "Ontario", "Prince Edward Island", "Quebec", "Saskatchewan",
                             "Territories", "Whitehorse, Yukon", "Winnipeg, Manitoba",
                             "Yellowknife, Northwest Territories", "Yukon"}),
        "Specific_Measure": frozenset({"Age group", "Intent", "Involving opioids", "Involving other psychoactive substances",
                                       "Involving stimulants", "Manner of death", "Origin of opioid(s)", "Overall numbers",
                                       "Sex", "Sex and age group", "Type of opioids", "Type of stimulants"}),
        "Unit": frozenset({"Crude rate", "Number", "Percent"}),
    },
    min_rows=10000,
    revision=RevisionConfig(period_column="Year_Quarter", value_column="Value",
                            key_columns=("Substance", "Source", "Specific_Measure", "Region", "Time_Period",
                                         "Aggregator", "Disaggregator", "Unit"),
                            recent_periods=8),
)})


# Contracts for the not-yet-onboarded sources are added in their rollout task (see the plan's
# per-source checklist); until then contract=None and the page shows "Not onboarded".
SOURCES = {spec.key: spec for spec in (
    SourceSpec("nationalHealthInfobase", "Health Infobase (national)", "v1",
               "Health Infobase - Health data in Canada", 92,
               scrape="data_scraping.sources.nationalHealthInfobase:scrape",
               contract=INFOBASE_CONTRACT),
    # No cleaner writes a dedicated DataSources row for population estimates (it's read straight
    # into rate calculations), so there's no real name to scope a Data Owner grant to. Site admins
    # still see it regardless.
    SourceSpec("nationalPopulationData", "StatCan population estimates", "v1",
               "Statistics Canada population estimates", 366),
    SourceSpec("nsRatesFatalities", "Nova Scotia fatalities", "v1",
               "Nova Scotia Numbers and Rates of Substance-Related Fatalities", 92),
    SourceSpec("onODPRN", "Ontario ODPRN", "v1", "Ontario Drug Policy Research Network (ODPRN)", 31),
    SourceSpec("bcDrugSense", "BC DrugSense", "v1", "British Columbia Centre for Substance Use (BCCSU)", 14),
    SourceSpec("skPubCentre", "Saskatchewan Coroners", "v1", "Saskatchewan Coroners Service", 92),
    SourceSpec("bcCoronersReport", "BC Coroners", "v1", "BC Coroners Service", 31, time_limit=1800),
    SourceSpec("drugChecking", "Drug checking (harmonized)", "v1",
               "Pan-Canadian Drug Checking Data Harmonization", 92, contract=DRUGCHECK_CONTRACT),
    SourceSpec("nationalDAS", "Health Canada DAS", "das",
               "Health Canada Drug Analysis Service", 31, time_limit=1800,
               contract=FileContract(ext="xlsx", custom=_das_validate)),
)}


def get_source(key):
    return SOURCES[key]


def onboarded(spec):
    return spec.contract is not None


def automated_sources():
    return [s for s in SOURCES.values() if s.scrape and onboarded(s)]


def resolve_scrape(spec):
    module, _, func = spec.scrape.partition(":")
    return getattr(importlib.import_module(module), func)


def rebuild_targets(spec):
    from data_viz.generate_visuals import targets_for_source
    return targets_for_source(spec.key)

"""ingest_das end-to-end against generated workbooks: parse -> replace-by-sample-number /
replace-by-source-month semantics -> FK cascade cleanup. Workbooks are built in-test
(openpyxl) with the bilingual banner + header shapes _promote_header expects."""
from datetime import date

import pandas
import pytest

from data_viz.das_ingest import DAS_SOURCE_NAME, ingest_das
from data_viz.database.models import (
    DasDrugCodes,
    DasNps,
    DasQuant,
    DasSampleDrugs,
    DasSamples,
    DataSources,
)

BANNER = ["Drug Analysis Service / Service d'analyse des drogues"]

ID_ALL_HEADER = ["Sample #", "Public Health Sample", "Contains NPS",
                 "Date Returned to Client", "Received Date", "Customer City",
                 "Prov/Terr", "Description", "Drug ID 1", "Drug ID 2"]
QUANT_HEADER = ["Sample #", "Public Health Sample", "Date Returned to Client",
                "Received Date", "Customer City", "Prov/Terr", "Description",
                "Drug Code", "Numeric Quantity", "Units"]
NPS_HEADER = ["Sample #", "Drug Code", "Substance name", "Other name", "Prov/Terr",
              "Finding Date", "Description"]
DRUG_ID_HEADER = ["Drug Code", "English legal drug name", "French legal drug name",
                  "English Name", "French Name", "English Pharmacological Class",
                  "English sub-class", "CAS Number", "Act", "Schedule", "Item"]


def sample_row(number, drugs=("COC", None), city="Toronto", province="ON",
               returned="2026-06-10"):
    d1, d2 = (list(drugs) + [None, None])[:2]
    return [number, "Y", "N", returned, "2026-06-01", city, province, "powder", d1, d2]


def quant_row(number, code="COC", quantity=42.5, units="%"):
    return [number, "N", "2026-06-10", "2026-06-01", "Toronto", "ON", "powder",
            code, quantity, units]


def nps_row(number="N/A*", code="NPS1", name="Novel One"):
    return [number, code, name, "aka-one", "QC", "2026-06-05", "crystal"]


def drug_row(code, english="Cocaine"):
    return [code, f"{english} (legal en)", f"{english} (legal fr)", english,
            f"{english} (fr)", "Stimulant", "Sub", "50-36-2", "CDSA", "I", "1"]


def build_workbook(path, id_all, quant, nps, drugs):
    """Each sheet: one bilingual banner row, the real header, then data rows."""
    sheets = {
        "DAS ID All": [BANNER + [None] * 9, ID_ALL_HEADER, *id_all],
        "DAS QUANT": [BANNER + [None] * 9, QUANT_HEADER, *quant],
        "NPS": [BANNER + [None] * 6, NPS_HEADER, *nps],
        "Drug Id": [BANNER + [None] * 10, DRUG_ID_HEADER, *drugs],
    }
    with pandas.ExcelWriter(path, engine="openpyxl") as writer:
        for name, rows in sheets.items():
            width = max(len(r) for r in rows)
            padded = [list(r) + [None] * (width - len(r)) for r in rows]
            pandas.DataFrame(padded).to_excel(writer, sheet_name=name,
                                              header=False, index=False)
    return str(path)


@pytest.fixture()
def june_file(tmp_path):
    return build_workbook(
        tmp_path / "20260715_20260630_nationalDAS.xlsx",
        id_all=[sample_row("S-1", drugs=("COC", "FEN")), sample_row("S-2", drugs=("COC",))],
        quant=[quant_row("S-1"), quant_row("S-1")],   # exact duplicate collapses in-file
        nps=[nps_row()],
        drugs=[drug_row("COC", "Cocaine"), drug_row("FEN", "Fentanyl")],
    )


class TestIngest:
    def test_first_ingest_populates_all_tables(self, db_session, june_file):
        ingest_das(file=june_file)
        assert DasSamples.query.count() == 2
        assert DasSampleDrugs.query.count() == 3
        assert DasQuant.query.count() == 1      # exact-duplicate quant row collapsed
        assert DasNps.query.count() == 1
        assert DasDrugCodes.query.count() == 3  # COC, FEN + placeholder for NPS1

    def test_display_name_and_denormalized_list(self, db_session, june_file):
        ingest_das(file=june_file)
        s1 = DasSamples.query.get("S-1")
        assert s1.drugs_identified == "Cocaine; Fentanyl"
        assert s1.province == "ON"
        assert s1.date_returned == date(2026, 6, 10)
        assert s1.source_month == date(2026, 6, 1)   # data-until month, day clamped to 1

    def test_placeholder_drug_code_for_unknown_reference(self, db_session, june_file):
        ingest_das(file=june_file)
        placeholder = DasDrugCodes.query.get("NPS1")
        assert placeholder is not None
        assert placeholder.english_name == "NPS1"

    def test_source_row_upserted(self, db_session, june_file):
        ingest_das(file=june_file)
        assert DataSources.query.filter_by(name=DAS_SOURCE_NAME).count() == 1

    def test_reingest_same_file_is_idempotent(self, db_session, june_file):
        ingest_das(file=june_file)
        ingest_das(file=june_file)
        assert DasSamples.query.count() == 2
        assert DasSampleDrugs.query.count() == 3
        assert DasQuant.query.count() == 1
        assert DasNps.query.count() == 1

    def test_later_month_replaces_sample_and_cascades_drug_rows(self, db_session,
                                                                june_file, tmp_path):
        ingest_das(file=june_file)
        july = build_workbook(
            tmp_path / "20260815_20260731_nationalDAS.xlsx",
            # S-1 reappears with only ONE drug now; S-3 is new.
            id_all=[sample_row("S-1", drugs=("COC", None), city="Hamilton"),
                    sample_row("S-3", drugs=("FEN", None))],
            quant=[], nps=[],
            drugs=[drug_row("COC", "Cocaine"), drug_row("FEN", "Fentanyl")],
        )
        ingest_das(file=july)
        assert DasSamples.query.count() == 3            # S-1 replaced, S-2 kept, S-3 new
        assert DasSamples.query.get("S-1").city == "Hamilton"
        # The old (S-1, FEN) join row must be gone - removed by ON DELETE CASCADE when
        # the sample row was replaced, not by any explicit delete.
        s1_drugs = DasSampleDrugs.query.filter_by(sample_number="S-1").all()
        assert [d.drug_code for d in s1_drugs] == ["COC"]
        # June's quant/NPS rows own their month and are untouched by July's file.
        assert DasQuant.query.count() == 1
        assert DasNps.query.count() == 1

    def test_footnote_prose_rows_skipped(self, db_session, tmp_path):
        workbook = build_workbook(
            tmp_path / "20260715_20260630_nationalDAS.xlsx",
            id_all=[sample_row("S-1"),
                    ["Note: these bilingual footnote rows are not samples",
                     None, None, None, None, None, None, None, None, None]],
            quant=[], nps=[], drugs=[drug_row("COC")],
        )
        ingest_das(file=workbook)
        assert DasSamples.query.count() == 1

    def test_bad_filename_rejected(self, db_session, tmp_path):
        with pytest.raises(ValueError, match="nationalDAS"):
            ingest_das(file=str(tmp_path / "wrong-name.xlsx"))

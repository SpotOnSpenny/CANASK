# Python Standard Library Dependencies
import os
import re
import datetime

# External Dependency Imports
import pandas

# Ingests the monthly Health Canada Drug Analysis Service (DAS) workbook from output/ into the
# dedicated das_* row-level tables (NOT the DataPoints star schema -- the DAS Explorer serves raw
# sample rows through its own paginated API). Runs via `flask ingest-das`; re-running the same file
# is a net no-op, and a new month's file replaces any overlapping samples, so history accumulates
# across monthly files keyed by sample number.
#
# This deliberately does not go through generate_visuals.pull_data(): its generic xlsx branch
# promotes row 0 to the header and dropna()s every row with any blank cell, which would delete
# almost the entire ID All sheet (Drug ID 1..20 are sparse by design).

DAS_SOURCE_NAME = "Health Canada Drug Analysis Service"
DAS_SOURCE_LINK = ("https://www.canada.ca/en/health-canada/services/health-concerns/"
                   "controlled-substances-precursor-chemicals/drug-analysis-service.html")
DAS_ABOUT = (
    "Health Canada's Drug Analysis Service (DAS) operates laboratories across Canada that analyze "
    "suspected illegal drugs seized by Canadian law enforcement agencies. Noncontrolled substances "
    "(such as cutting agents) are not systematically reported by DAS if present in combination with "
    "a controlled substance, so statistics based on DAS samples may not be completely representative "
    "of drug seizures in Canada, nor of substances circulating on the market. DAS data should be "
    "used with caution when determining trends or drawing conclusions about the illicit market. "
    "Each row represents the result(s) for a single sample received by DAS."
)

# The file is currently hand-placed; fullmatch (not substring) so the stray Windows
# "....xlsx:Zone.Identifier" sibling never matches.
_FILENAME_RE = re.compile(r"(\d{8})_(\d{8})_nationalDAS\.xlsx")

# Canonical field -> the English prefix of the (bilingual, whitespace-mangled) source header.
# Matching is prefix-based on whitespace-collapsed headers; anything required that doesn't match
# raises, so upstream format drift fails loudly instead of ingesting garbage.
_ID_ALL_FIELDS = {
    "sample_number": "Sample #",
    "public_health": "Public Health Sample",
    "contains_nps": "Contains NPS",
    "date_returned": "Date Returned to Client",
    "date_received": "Received Date",
    "city": "Customer City",
    "province": "Prov/Terr",
    "description": "Description",
}
_QUANT_FIELDS = {
    "sample_number": "Sample #",
    "public_health": "Public Health Sample",
    "date_returned": "Date Returned to Client",
    "date_received": "Received Date",
    "city": "Customer City",
    "province": "Prov/Terr",
    "description": "Description",
    "drug_code": "Drug Code",
    "quantity": "Numeric Quantity",
    "units": "Units",
}
_NPS_FIELDS = {
    "sample_number": "Sample #",
    "drug_code": "Drug Code",
    "substance_name": "Substance name",
    "other_name": "Other name",
    "province": "Prov/Terr",
    "finding_date": "Finding Date",
    "description": "Description",
}
_DRUG_ID_FIELDS = {
    "code": "Drug Code",
    "english_legal_name": "English legal drug name",
    "french_legal_name": "French legal drug name",
    "english_name": "English Name",
    "french_name": "French Name",
    "pharm_class": "English Pharmacological Class",
    "pharm_subclass": "English sub-class",
    "cas": "CAS Number",
    "act": "Act",
    "schedule": "Schedule",
    "item": "Item",
}

_DRUG_POSITION_RE = re.compile(r"^Drug ID (\d+)")   # tolerates the source's "Drug ID 14de la drogue" typo


def find_das_files(explicit=None):
    """Locate nationalDAS workbook(s) in output/ and parse their filename dates.

    Returns a list of (path, scraped_date, data_until_date), oldest data-month first, so a fresh
    database rebuilds its full history from whatever monthly files have accumulated. `explicit`
    narrows to one file (a name in output/, or an absolute path)."""
    output_dir = os.path.join(os.path.abspath(os.path.dirname(os.path.dirname(__file__))), "output")
    if explicit:
        paths = [explicit if os.path.isabs(explicit) else os.path.join(output_dir, explicit)]
        if _FILENAME_RE.fullmatch(os.path.basename(paths[0])) is None:
            raise ValueError(f"{os.path.basename(paths[0])} does not match <scraped>_<data-until>_nationalDAS.xlsx")
    else:
        names = sorted((f for f in os.listdir(output_dir) if _FILENAME_RE.fullmatch(f)),
                       key=lambda f: _FILENAME_RE.fullmatch(f).group(2))
        if not names:
            raise FileNotFoundError("No <scraped>_<data-until>_nationalDAS.xlsx file in the output directory!")
        paths = [os.path.join(output_dir, f) for f in names]
    found = []
    for path in paths:
        match = _FILENAME_RE.fullmatch(os.path.basename(path))
        found.append((path,
                      datetime.datetime.strptime(match.group(1), "%Y%m%d").date(),
                      datetime.datetime.strptime(match.group(2), "%Y%m%d").date()))
    return found


def _norm(header):
    """Collapse all whitespace (the source headers embed newlines) into single spaces."""
    return re.sub(r"\s+", " ", str(header)).strip()


def _promote_header(grid, first_cell_prefix, sheet):
    """The sheets carry bilingual banner rows above the real header; find the header row by its
    first cell's English prefix and return a DataFrame of the rows below it, with normalized
    column names."""
    for i in range(min(len(grid), 15)):
        if _norm(grid.iat[i, 0]).startswith(first_cell_prefix):
            frame = grid.iloc[i + 1:].copy()
            frame.columns = [_norm(c) for c in grid.iloc[i]]
            return frame.reset_index(drop=True)
    raise ValueError(f"Could not find the header row (first cell starting '{first_cell_prefix}') "
                     f"in sheet '{sheet}' -- has the workbook format changed?")


def _resolve_columns(frame, fields, sheet):
    """Map canonical field names to actual column labels by English prefix; raise on any miss."""
    resolved = {}
    for field, prefix in fields.items():
        matches = [c for c in frame.columns if str(c).startswith(prefix)]
        if not matches:
            raise ValueError(f"Sheet '{sheet}' is missing a column starting '{prefix}' "
                             f"(for field '{field}') -- has the workbook format changed?")
        resolved[field] = matches[0]
    return resolved


def _text(value):
    if value is None or (isinstance(value, float) and pandas.isna(value)) or pandas.isna(value):
        return None
    text = str(value).strip()
    # Excel round-trips numeric ids like 3716630 as floats; keep them as clean digit strings.
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text or None


# Loose on purpose: uncertified NPS findings use the literal id "N/A*". Whitespace or absurd
# length is what marks a non-sample.
_SAMPLE_NUMBER_RE = re.compile(r"\S{1,50}")


def _sample_number(value):
    """A plausible sample id, or None. The sheets carry footnote rows below the data whose first
    cell is long bilingual prose -- anything with whitespace (or absurd length) is not a sample."""
    text = _text(value)
    if text is None or not _SAMPLE_NUMBER_RE.fullmatch(text):
        return None
    return text


def _flag(value):
    text = _text(value)
    if text is None:
        return None
    return {"Y": True, "N": False}.get(text.upper())


def _date(value):
    if value is None or pandas.isna(value):
        return None
    stamp = pandas.to_datetime(value, errors="coerce")
    return None if pandas.isna(stamp) else stamp.date()


def _float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pandas.isna(number) else number


def _chunked(values, size=1000):
    values = list(values)
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _parse_workbook(path):
    """Read the four sheets we ingest and return canonical row dicts (plus per-sample drug hits)."""
    grids = pandas.read_excel(path, engine="calamine", header=None,
                              sheet_name=["DAS ID All", "DAS QUANT", "NPS", "Drug Id"])

    # Drug Id lookup -----------------------------------------------------------------------------
    frame = _promote_header(grids["Drug Id"], "Drug Code", "Drug Id")
    cols = _resolve_columns(frame, _DRUG_ID_FIELDS, "Drug Id")
    drug_codes = {}
    for _, row in frame.iterrows():
        code = _text(row[cols["code"]])
        if code is None:
            continue
        drug_codes[code] = {field: _text(row[cols[field]]) for field in _DRUG_ID_FIELDS}
        drug_codes[code]["code"] = code

    # DAS ID All ---------------------------------------------------------------------------------
    frame = _promote_header(grids["DAS ID All"], "Sample #", "DAS ID All")
    cols = _resolve_columns(frame, _ID_ALL_FIELDS, "DAS ID All")
    drug_columns = []   # (position, column label)
    for column in frame.columns:
        match = _DRUG_POSITION_RE.match(str(column))
        if match:
            drug_columns.append((int(match.group(1)), column))
    if not drug_columns:
        raise ValueError("Sheet 'DAS ID All' has no 'Drug ID N' columns -- has the workbook format changed?")

    samples, sample_drugs = {}, {}   # keyed by sample_number; in-file duplicates keep the last row
    for _, row in frame.iterrows():
        sample_number = _sample_number(row[cols["sample_number"]])
        if sample_number is None:
            continue
        samples[sample_number] = {
            "sample_number": sample_number,
            "public_health": _flag(row[cols["public_health"]]),
            "contains_nps": _flag(row[cols["contains_nps"]]),
            "date_returned": _date(row[cols["date_returned"]]),
            "date_received": _date(row[cols["date_received"]]),
            "city": _text(row[cols["city"]]),
            "province": _text(row[cols["province"]]),
            "description": _text(row[cols["description"]]),
        }
        hits = []
        for position, column in drug_columns:
            code = _text(row[column])
            if code is not None:
                hits.append({"sample_number": sample_number, "position": position, "drug_code": code})
        sample_drugs[sample_number] = hits

    # DAS QUANT ----------------------------------------------------------------------------------
    frame = _promote_header(grids["DAS QUANT"], "Sample #", "DAS QUANT")
    cols = _resolve_columns(frame, _QUANT_FIELDS, "DAS QUANT")
    quant = []
    for _, row in frame.iterrows():
        sample_number = _sample_number(row[cols["sample_number"]])
        if sample_number is None:
            continue
        entry = {
            "sample_number": sample_number,
            "public_health": _flag(row[cols["public_health"]]),
            "date_returned": _date(row[cols["date_returned"]]),
            "date_received": _date(row[cols["date_received"]]),
            "city": _text(row[cols["city"]]),
            "province": _text(row[cols["province"]]),
            "description": _text(row[cols["description"]]),
            "drug_code": _text(row[cols["drug_code"]]),
            "quantity": _float(row[cols["quantity"]]),
            "units": _text(row[cols["units"]]),
        }
        if entry not in quant:   # exact-duplicate rows only; same sample+drug can recur (e.g. two units)
            quant.append(entry)

    # NPS ----------------------------------------------------------------------------------------
    frame = _promote_header(grids["NPS"], "Sample #", "NPS")
    cols = _resolve_columns(frame, _NPS_FIELDS, "NPS")
    nps = []
    for _, row in frame.iterrows():
        sample_number = _sample_number(row[cols["sample_number"]])
        if sample_number is None:
            continue
        entry = {
            "sample_number": sample_number,
            "drug_code": _text(row[cols["drug_code"]]),
            "substance_name": _text(row[cols["substance_name"]]),
            "other_name": _text(row[cols["other_name"]]),
            "province": _text(row[cols["province"]]),
            "finding_date": _date(row[cols["finding_date"]]),
            "description": _text(row[cols["description"]]),
        }
        if entry not in nps:   # uncertified findings share sample "N/A*", so only exact dups collapse
            nps.append(entry)

    return drug_codes, samples, sample_drugs, quant, nps


def _upsert_das_source(db, DataSources, scraped, data_until):
    """Fetch/create the DAS DataSources row (same name the nationalDAS.json manifest uses, so
    define-visuals and ingest hit one row) and refresh its about/link/scrape-date fields."""
    source = DataSources.query.filter_by(name=DAS_SOURCE_NAME).first() or DataSources(name=DAS_SOURCE_NAME)
    db.session.add(source)
    source.link = DAS_SOURCE_LINK
    source.about = DAS_ABOUT
    source.last_updated = datetime.datetime.combine(scraped, datetime.time())
    source.data_until = datetime.datetime.combine(data_until, datetime.time())
    source.last_updated_str = scraped.strftime("%B %d, %Y")
    source.data_until_str = data_until.strftime("%B %d, %Y")


def ingest_das(file=None):
    """Parse the DAS workbook(s) in output/ and rewrite their rows into the das_* tables, one
    transaction per file, oldest first.

    Idempotent per file: incoming sample numbers are deleted then re-inserted (das_sample_drugs
    follows das_samples via FK cascade) and quant/NPS rows are rewritten by source month, so
    overlapping months replace rather than duplicate."""
    for path, scraped, data_until in find_das_files(file):
        _ingest_file(path, scraped, data_until)


def _ingest_file(path, scraped, data_until):
    from data_viz import db
    from data_viz.database.models import (DasDrugCodes, DasSamples, DasSampleDrugs, DasQuant,
                                          DasNps, DataSources)

    source_month = data_until.replace(day=1)
    print(f"Ingesting {os.path.basename(path)} (data until {data_until.isoformat()})...")

    drug_codes, samples, sample_drugs, quant, nps = _parse_workbook(path)

    # Any code referenced by a sample sheet but absent from the lookup still needs a row for the FK;
    # a placeholder named after the code keeps the explorer readable until the lookup catches up.
    referenced = ({hit["drug_code"] for hits in sample_drugs.values() for hit in hits}
                  | {row["drug_code"] for row in quant if row["drug_code"]}
                  | {row["drug_code"] for row in nps if row["drug_code"]})
    for code in referenced - set(drug_codes):
        drug_codes[code] = {"code": code, "english_name": code}

    # The primary segment of the (possibly synonym-listing) english name is what users see.
    for code, entry in drug_codes.items():
        entry["display_name"] = (entry.get("english_name") or code).split(";")[0].strip()[:255] or code

    lookup = {code: entry["display_name"] for code, entry in drug_codes.items()}
    for sample_number, hits in sample_drugs.items():
        samples[sample_number]["drugs_identified"] = (
            "; ".join(lookup[hit["drug_code"]] for hit in hits) or None)
    for row in list(samples.values()) + quant + nps:
        row["source_month"] = source_month

    try:
        # Drug code lookup: insert new codes, refresh existing ones in place (no deletes -- old
        # months' rows may reference codes that drop out of a later lookup sheet).
        existing_codes = {row.code for row in db.session.query(DasDrugCodes.code).all()}
        db.session.bulk_insert_mappings(
            DasDrugCodes, [e for c, e in drug_codes.items() if c not in existing_codes])
        db.session.bulk_update_mappings(
            DasDrugCodes, [e for c, e in drug_codes.items() if c in existing_codes])

        # Replace incoming samples (das_sample_drugs rows follow via the FK's ON DELETE CASCADE).
        for chunk in _chunked(samples.keys()):
            DasSamples.query.filter(DasSamples.sample_number.in_(chunk)).delete(synchronize_session=False)
        db.session.bulk_insert_mappings(DasSamples, list(samples.values()))
        db.session.bulk_insert_mappings(DasSampleDrugs,
                                        [hit for hits in sample_drugs.values() for hit in hits])

        # Quant/NPS have no reliable per-row key (uncertified NPS findings all share "N/A*"), so
        # each monthly file wholly owns its month: drop the month, insert the file's rows.
        DasQuant.query.filter(DasQuant.source_month == source_month).delete(synchronize_session=False)
        db.session.bulk_insert_mappings(DasQuant, quant)

        DasNps.query.filter(DasNps.source_month == source_month).delete(synchronize_session=False)
        db.session.bulk_insert_mappings(DasNps, nps)

        _upsert_das_source(db, DataSources, scraped, data_until)
        db.session.commit()
    except Exception:
        # Fail loudly and leave the tables as they were -- a partial ingest would silently
        # misrepresent the month.
        db.session.rollback()
        raise

    print(f"  {len(samples)} samples ({sum(len(h) for h in sample_drugs.values())} drug hits), "
          f"{len(quant)} quantitation rows, {len(nps)} NPS rows, {len(drug_codes)} drug codes.")

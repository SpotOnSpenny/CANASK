# Python Standard Library Dependencies
import os
import json
import datetime
import logging
import re

# External Dependency Imports
import pandas

logger = logging.getLogger(__name__)

#######################################################################################
#                                        Notes:                                       #
# For now, these functions include the cleaning of the dataframes required to create  #
# the visualization. In the future, a big #TODO will be to remove this step and       #
# include it in either separate scripts that pass the data to a database after        #
# scraping, or directly in the scraping scripts themselves.                           #
#######################################################################################

# Helper function to pull data from the specified excel/csv file
def pull_data(data_source: list):
    sheets = {}
    for source in data_source:
        output_dir = os.path.join(os.path.abspath(os.path.dirname(os.path.dirname(__file__))), "output")
        if any(source in file for file in os.listdir(output_dir)):
            file = [file for file in os.listdir(output_dir) if source in file][0]
            match file.split(".")[-1]:
                case "csv":
                    sheets[source] = {
                        "date_updated": datetime.datetime.strptime(file.split("_")[0], "%Y%m%d").strftime("%B %d, %Y"),
                        "data_until": datetime.datetime.strptime(file.split("_")[1], "%Y%m%d").strftime("%B %d, %Y") if len(file.split("_")) > 1 else None,
                        "dataframe": pandas.read_csv(os.path.join(output_dir, file))
                        }
                case "xlsx":
                    # Specific handling for ontario data (match the source name anywhere in the
                    # filename -- the convention is <scraped>_<data-until>_onODPRN.xlsx, so the
                    # source token isn't at a fixed split position).
                    if "onODPRN" in file:
                        dataframes = pandas.read_excel(os.path.join(output_dir, file), engine="calamine", sheet_name=None)
                        for name, dataframe in dataframes.items():
                            dataframe.set_flags(allows_duplicate_labels=False)
                            dataframe.dropna(axis=0, inplace=True)
                            sheets[name] = {
                                "date_updated": datetime.datetime.strptime(file.split("_")[0], "%Y%m%d").strftime("%B %d, %Y"),
                                "dataframe": dataframe
                                }
                    # Handling for other xlsx files
                    else:
                        dataframes = pandas.read_excel(os.path.join(output_dir, file), engine='calamine', sheet_name=None).values()
                        for dataframe in dataframes:
                            name = list(filter(lambda value: True if "Unnamed" not in value and value != "NaN" else False, dataframe.columns))[0]
                            dataframe.set_flags(allows_duplicate_labels=False)
                            dataframe.columns = dataframe.iloc[0]
                            dataframe.dropna(axis=0, inplace=True)
                            dataframe = dataframe.drop(dataframe.columns[[0]], axis=1).reset_index(drop=True)
                            if file.split("_")[1].isdigit():
                                try: # Try the full date format
                                    data_until = datetime.datetime.strptime(file.split("_")[1], "%Y%m%d").strftime("%B %d, %Y")
                                except ValueError: # If it fails, try the year only format
                                    data_until = datetime.datetime.strptime(file.split("_")[1], "%Y%m").strftime("%B, %Y")
                            else:
                                data_until = file.split("_")[0]
                            sheets[name] = {
                                "date_updated": datetime.datetime.strptime(file.split("_")[0], "%Y%m%d").strftime("%B %d, %Y"),
                                "data_until": data_until,
                                "dataframe": dataframe
                                }
        else:
            raise FileNotFoundError(f"Data source {source} not found in the output directory!")
    return sheets

# Helper function to pull the data from the provided source into a dataframe
# Use exact_match to determine if the seach should be looking for the exact title (ie, return a single, exact dataframe for each term)
# or if it should be looking for any dataframe that contains the term (ie, return all dataframes that contain the term)
def filter_data(data: dict, find_these: list, exact_match: bool = False):
    dataframes = []
    match exact_match:
        case True:
            for key in data.keys():
                if any(find_this.split(",")[0].lower().replace(" ", "") == key.split(",")[0].lower().replace(" ", "") for find_this in find_these):
                    dataframes.append(data[key])
        case False:
            for key in data.keys():
                if any(find_this.split(",")[0].lower().replace(" ", "") in key.split(",")[0].lower().replace(" ", "") for find_this in find_these):
                    data[key]["Name"] = key
                    dataframes.append(data[key])
    return dataframes


# The drug-checking feed is hand-entered across sites in English and French, and lost its accents
# upstream (é/ï arrive as the U+FFFD replacement char "�" in drug names, a literal "?" in site
# names), so one drug/category shows up under several spellings. These maps fold every observed
# variant onto a single canonical label before grouping, so the treemap doesn't split a drug across
# near-duplicate nodes; anything unmapped falls through as its trimmed original (a new value surfaces
# rather than silently vanishing). Extend the maps as new spellings appear.
_DRUGCHECK_CATEGORY_CANON = {
    "dissociatifs": "Dissociatives",
    "substance inconnue": "Unknown drug",
}
# Categories carrying no substance signal ("sans objet" == not applicable) -- drop these rows.
_DRUGCHECK_DROP_CATEGORIES = {"sans objet"}
_DRUGCHECK_DRUG_CANON = {
    "Coca�ne": "Cocaine",
    "Cocaine HCL": "Cocaine",
    "Cocaine Base (crack)": "Crack",
    "Crack (coca�ne base)": "Crack",
    "K�tamine": "Ketamine",
    "Benzodiaz�pine": "Benzodiazepine",
    "Dextroamph�tamine": "Dextroamphetamine",
    "Magn�sium": "Magnesium",
    "MDMA - (3,4-Methylenedioxymethamphetamine)": "MDMA",
    "MDA - (3,4-Methylenedioxyamphetamine)": "MDA",
    "Probiotique": "Probiotic",
    "Substance inconnue": "Unknown substance",
    "Unknown Stimulant": "Unknown stimulant",
    "speed": "Speed",
}
_DRUGCHECK_SITE_CANON = {
    "N?wo Y?tina Friendship Centre": "Nïwo Yëtina Friendship Centre",
}


def v1_drugchecking_export_clean(writer, province):
    # Pan-Canadian drug-checking harmonized data: one row per checked sample, spanning provinces.
    # New-style cleaner -- emits a category_treemap (Category -> Expected Drug, Province + Site as
    # geo levels) straight to the writer, no intermediate block dict. `province` is the target scope
    # key ("canada"); the per-sample Province lives in the geo composite.
    pulled = pull_data(["drugChecking"])["drugChecking"]
    df = pulled["dataframe"].copy()
    # The raw headers carry stray leading/trailing and double spaces (e.g. "Visit Date ",
    # "Expected Drug Category  (1)"); normalize whitespace so the column refs below are clean.
    df.columns = df.columns.str.strip().str.replace(r"\s+", " ", regex=True)
    # Province arrives both abbreviated and spelled out; canonicalize known abbreviations so the
    # Province dropdown groups them as one (extend this map as new provinces appear).
    df["Province"] = df["Province"].replace({"Sask": "Saskatchewan"})

    v = writer.visual(province, "checked_samples_by_expected_drug")
    if v is None:
        return   # no definition yet (run `define-visuals`); writer already warned
    v.use_source({
        "name": "Pan-Canadian Drug Checking Data Harmonization",
        "about": """
This data is collected by individual organizations across Canada, and provided to the CCSA working towards the goal of data harmonization across drug checking sites.

To find out more about drug checking, and the CCSA's Drug Checking working group, please visit the link below:
        """,
        "link": "https://www.ccsa.ca/en/data-trends/drug-checking",
        "last_updated": pulled["date_updated"],
        "data_until": pulled["data_until"],
    })

    # Drop rows missing any grouping key first (so the string ops below never hit NaN), then trim and
    # canonicalize the three label columns onto their stable forms (see the maps above). The
    # "sans objet" rows are dropped outright -- they carry no substance to place in the tree.
    df = df.dropna(
        subset=["Site/Organization", "Province", "Expected Drug Category (1)", "Expected Drug (1)"]).copy()
    df["Site/Organization"] = df["Site/Organization"].str.strip().replace(_DRUGCHECK_SITE_CANON)
    df["Expected Drug Category (1)"] = df["Expected Drug Category (1)"].str.strip()
    df = df[~df["Expected Drug Category (1)"].isin(_DRUGCHECK_DROP_CATEGORIES)]
    df["Expected Drug Category (1)"] = df["Expected Drug Category (1)"].replace(_DRUGCHECK_CATEGORY_CANON)
    df["Expected Drug (1)"] = df["Expected Drug (1)"].str.strip().replace(_DRUGCHECK_DRUG_CANON)

    # Parse "Visit Date" (M/D/YYYY) to a "YYYY-MM" month key -- the same month grain the BC treemap
    # uses (client derives year/seasonal/all-time).
    df["_month"] = pandas.to_datetime(
        df["Visit Date"], format="%m/%d/%Y", errors="coerce").dt.strftime("%Y-%m")
    df = df[df["_month"].notna()]
    # Geo levels ordered broad -> narrow ("Province||Site") so the client's cascade is Province then
    # Site; matches manifest geo_levels=["Province", "Site/Organization"]. Types (metric/geo_type/
    # dimension*) come from the Visuals row via the writer -- cleaning only supplies values.
    for (prov, site), site_df in df.groupby(["Province", "Site/Organization"]):
        geo = f"{prov}||{site}"
        counts = site_df.groupby(["_month", "Expected Drug Category (1)", "Expected Drug (1)"]).size()
        for (month, category, drug), n in counts.items():
            v.fact(geo, month, int(n), dimension=category, dimension2=drug, time_frame_type="month")


def v1_BCCSU_export_clean(writer, province):
    # BC Centre for Substance Use drug-checking data: one row per voluntarily submitted sample.
    # New-style cleaner -- emits the by-year line charts straight to the writer, no intermediate
    # block dict. The re-scraped feed dropped its Health Authority / Site columns, so the former
    # geographic map / pie / regional drill chain and the site treemap are gone (pruned from the
    # manifest); only these province-level series remain. Types (metric/dimension*) come from each
    # Visuals row via the writer -- cleaning supplies values only.
    pulled = pull_data(["bcDrugSense"])["bcDrugSense"]
    df = pulled["dataframe"].copy()
    # Parse "Visit Date" (YYYY-MM-DD) to a year; drop rows we can't date, then iterate the years
    # actually present (avoids a divide-by-zero on a year with no samples).
    df["_year"] = pandas.to_datetime(df["Visit Date"], errors="coerce").dt.year
    df = df[df["_year"].notna()]
    years = [str(int(year)) for year in sorted(df["_year"].unique())]
    by_year = {year: df[df["_year"] == int(year)] for year in years}

    source = {
        "name": "British Columbia Centre for Substance Use (BCCSU)",
        "about": """
This data is collected from the British Columbia Centre on Substance Use (BCCSU) and is based on voluntary drug testing results.The data is collected from samples provided by individuals and organizations in British Columbia.The data is collected to help inform the public about the drug supply in British Columbia and to help inform harm reduction strategies.Please note that this data is not representative of the entire illicit drug supply in British Columbia,but rather provides a snapshot of the drug supply based on voluntary submissions.

For more information visit the BCCSU's Drug Sense website by clicking the button below:
        """,
        "link": "https://drugsense.bccsu.ubc.ca/",
        "last_updated": pulled["date_updated"],
        "data_until": pulled["data_until"],
    }
    geo = PROVINCE_DISPLAY[province]

    # ----- Drug Supply by Year: sample counts/rates per drug Category -----
    v = writer.visual(province, "drug_supply_by_year")
    if v is not None:
        v.use_source(source)
        categories = df["Category"].dropna().unique()
        for year in years:
            year_df = by_year[year]
            total = len(year_df)
            for category in categories:
                count = int((year_df["Category"] == category).sum())
                v.fact(geo, year, count, dimension2=category)
                v.fact(geo, year, round(count / total * 100, 2) if total else 0,
                       data_type="rates", dimension2=category)
            v.additional(geo, year, "Total Samples", total)

    # ----- Presence of Fentanyl, Benzodiazepines and Medetomidine by Year (test strips) -----
    v = writer.visual(province, "fent_benz_by_year")
    if v is not None:
        v.use_source(source)
        strips = {
            "Fentanyl": "Fentanyl Strip",
            "Benzodiazepines": "Benzo Strip",
            "Medetomidine": "Medetomidine Strip",
        }
        for year in years:
            year_df = by_year[year]
            total = len(year_df)
            for label, column in strips.items():
                count = int((year_df[column] == "Pos").sum())
                v.fact(geo, year, count, dimension2=label)
                v.fact(geo, year, round(count / total * 100, 2) if total else 0,
                       data_type="rates", dimension2=label)
            v.additional(geo, year, "Total Samples", total)

    # ----- Presence of Opioid Types by Year (parsed from the Spectrometer column) -----
    v = writer.visual(province, "opioid_types_by_year")
    if v is not None:
        v.use_source(source)
        opioid_categories = ["Codeine", "Fentanyl", "Heroin", "Hydrocodone", "Hydromorphone",
                             "Methadone", "Morphine", "Oxycodone", "Buprenorphine"]
        for year in years:
            year_df = by_year[year]
            opioid_df = year_df[year_df["Category"] == "Opioid"].fillna("No Data")
            opioid_total = len(opioid_df)
            for opioid in opioid_categories:
                count = int(opioid_df["Spectrometer"].str.contains(opioid, case=False).sum())
                v.fact(geo, year, count, dimension2=opioid)
                v.fact(geo, year, round(count / opioid_total * 100, 2) if opioid_total else 0,
                       data_type="rates", dimension2=opioid)
            v.additional(geo, year, "Total Opioid Samples", opioid_total)
            v.additional(geo, year, "Total Samples", len(year_df))



# --------------------------------------------------------------------------------------- #
# BC Coroners Service -- direct-write cleaner (yearly grain).
# --------------------------------------------------------------------------------------- #

# Health authorities in heatmap/menu order, as they appear in the HA_Name column / sheet titles.
_BC_HEALTH_AUTHORITIES = ["Interior", "Fraser", "Vancouver Coastal", "Island", "Northern", "British Columbia"]

_BCCS_ABOUT = """
This data has been collected by the British Columbia Coroners Service (BCCS),and is based on toxicology reports from individuals who have died in British Columbia where the cause of death was determined to be unregulated drugs and/or drugs sold illicitly,and does not include deaths related to an individuals prescribed drugs,or intentional deaths due to toxicity.The data is updated monthly by the BCCS.

For more information,visit the BCCS website by clicking the button below:
        """
_BCCS_LINK = "https://app.powerbi.com/view?r=eyJrIjoiNjhiYjgxYzUtYjIyOC00ZGQ2LThhMzEtOWU5Y2Q4YWI0OTc5IiwidCI6IjZmZGI1MjAwLTNkMGQtNGE4YS1iMDM2LWQzNjg1ZTM1OWFkYyJ9"


def _coroners_clean_cell(value):
    """A coroners cell -> a reported number (including a genuine 0), or None for a blank / non-numeric
    cell (not reported). The coroners workbook has no suppression marker, so blanks are treated as
    'not reported' (a gap) rather than fabricated as 0."""
    if isinstance(value, str):
        text = value.replace("\xa0", "").replace("%", "").strip()
        if text == "":
            return None
        try:
            return float(text)
        except ValueError:
            return None
    if value is None or (isinstance(value, float) and pandas.isna(value)):
        return None
    return value


def _emit_fact(visual, geo, time_frame, value, **kw):
    """Write a fact unless `value` is None (not reported -> a true gap / no row). Reported numbers
    (incl 0) and the SUPPRESSED sentinel pass through. Shared by the direct-write cleaners."""
    if value is not None:
        visual.fact(geo, time_frame, value, **kw)


def _read_coroners_workbook():
    """Read the BC Coroners workbook, preserving duplicate-titled sheets that pull_data() collapses
    (the file ships both a yearly and a last-13-months version of the heatmap / age tables under one
    title; pull_data keys by title so only the last survives). Returns {date_updated, data_until,
    frames} where frames maps each title -> [{grain: 'year'|'month', periods: [...], rows: {label: [values]}}]."""
    output_dir = os.path.join(os.path.abspath(os.path.dirname(os.path.dirname(__file__))), "output")
    match = next((f for f in os.listdir(output_dir) if "bcCoronersReport" in f), None)
    if match is None:
        raise FileNotFoundError("Data source bcCoronersReport not found in the output directory!")
    date_updated = datetime.datetime.strptime(match.split("_")[0], "%Y%m%d").strftime("%B %d, %Y")
    data_until = datetime.datetime.strptime(match.split("_")[1], "%Y%m%d").strftime("%B %d, %Y")
    sheets = pandas.read_excel(os.path.join(output_dir, match), engine="calamine", sheet_name=None)
    frames = {}
    for sheet in sheets.values():
        titled = [c for c in sheet.columns if "Unnamed" not in str(c) and str(c) != "NaN"]
        if not titled or sheet.shape[0] < 3:
            continue
        title = titled[0]
        # The real header is row 0 (col 0 is a junk index, col 1 the row-label column, col 2+ the time
        # periods); row 1 is a blank spacer; data begins at row 2.
        header = [str(cell).replace("\xa0", "").strip() for cell in sheet.iloc[0].tolist()]
        periods = header[2:]
        grain = "year" if periods and re.fullmatch(r"\d{4}", periods[0]) else "month"
        rows = {}
        for _, row in sheet.iloc[2:].iterrows():
            label = row.iloc[1]
            if not isinstance(label, str):
                continue
            label = label.replace("\xa0", "").strip()
            if label:
                rows[label] = row.iloc[2:].tolist()
        frames.setdefault(title, []).append({"grain": grain, "periods": periods, "rows": rows})
    return {"date_updated": date_updated, "data_until": data_until, "frames": frames}


def _coroners_frame(workbook, title, grain="year"):
    """The frame for `title` at the requested grain, or None if the workbook lacks it."""
    return next((f for f in workbook["frames"].get(title, []) if f["grain"] == grain), None)


def _bc_population_by_year():
    """{year: BC population} from the national population scrape, or {} if that scrape is absent
    (only the drug-type death *rates* need it -- everything else degrades gracefully)."""
    try:
        population = pull_data(["nationalPopulationData"])
    except FileNotFoundError:
        logger.warning("nationalPopulationData scrape absent -- skipping BC drug-type death rates.")
        return {}
    frame = filter_data(population, ["nationalPopulationData"])[0]["dataframe"]
    frame = frame.loc[frame["GEO"] == "British Columbia"]
    return {str(int(ref)): float(val) for ref, val in zip(frame["REF_DATE"], frame["VALUE"])}


def v1_coroners_export_clean(writer, province):
    # BC Coroners Service deaths -> facts, straight to the writer (no block dict). Uses the YEARLY
    # tables: the workbook also ships a last-13-months version of the heatmap/age tables under an
    # identical title, so we read the workbook ourselves (pull_data shadows the yearly one).
    workbook = _read_coroners_workbook()
    source = {
        "name": "BC Coroners Service",
        "about": _BCCS_ABOUT,
        "link": _BCCS_LINK,
        "last_updated": workbook["date_updated"],
        "data_until": workbook["data_until"],
    }
    geo_province = PROVINCE_DISPLAY[province]

    # ----- Heatmap: unregulated drug deaths by health authority, per year (counts only) -----
    bc_year_totals = {}   # year -> BC-wide deaths, reused by the drug-type counts derivation below
    heat = _coroners_frame(workbook, "Unregulated Drug Deaths by Health Authority of Injury", "year")
    if heat is not None:
        v = writer.visual(province, "drug_death_heatmap")
        if v is not None:
            v.use_source(source)
        for ha, values in heat["rows"].items():
            for period, value in zip(heat["periods"], values):
                count = _coroners_clean_cell(value)
                if ha == "British Columbia":
                    bc_year_totals[period] = count
                if v is not None:
                    _emit_fact(v, ha, period, count)

    # ----- Deaths by sex, per health authority, per year (drill from the heatmap) -----
    v = writer.visual(province, "deaths_by_sex_line")
    if v is not None:
        v.use_source(source)
        for ha in _BC_HEALTH_AUTHORITIES:
            counts = _coroners_frame(workbook, f"{ha} Health Authority: Unregulated Drug Deaths by Sex")
            rates = _coroners_frame(
                workbook, f"{ha} Health Authority: Sex-Specific Unregulated Drug Death Rates per 100,000")
            if counts is not None:
                for sex_label, values in counts["rows"].items():        # Female, Male, Total
                    for period, value in zip(counts["periods"], values):
                        _emit_fact(v, ha, period, _coroners_clean_cell(value), dimension2=sex_label.lower())
            if rates is not None:
                for sex_label, values in rates["rows"].items():         # Female, Male
                    for period, value in zip(rates["periods"], values):
                        _emit_fact(v, ha, period, _coroners_clean_cell(value),
                                   data_type="rates", dimension2=sex_label.lower())

    # ----- Drug toxicity deaths by drug type, BC-wide, per year -----
    # The frame holds the PERCENT of deaths each drug was relevant to; counts are derived from the
    # BC-wide yearly totals (heatmap) and rates per 100,000 from population data (skipped if absent).
    drugs = _coroners_frame(workbook, "Unregulated Drug Deaths by Drug Types Relevant to Death")
    v = writer.visual(province, "toxicity_deaths_per_drug_by_year")
    if v is not None and drugs is not None:
        v.use_source(source)
        bc_population = _bc_population_by_year()
        for drug, values in drugs["rows"].items():
            for period, value in zip(drugs["periods"], values):
                percent = _coroners_clean_cell(value)
                _emit_fact(v, geo_province, period, percent, data_type="percentages", dimension2=drug)
                count = _derived_count(percent, bc_year_totals.get(period))
                _emit_fact(v, geo_province, period, count, dimension2=drug)
                _emit_fact(v, geo_province, period, _derived_rate(count, bc_population.get(period)),
                           data_type="rates", dimension2=drug)

    # ----- Unregulated drug toxicity deaths by age group, BC-wide, per year -----
    age_counts = _coroners_frame(workbook, "Unregulated Drug Deaths by Age Group", "year")
    age_rates = _coroners_frame(workbook, "Age-Specific Unregulated Drug Death Rates per 100,000", "year")
    v = writer.visual(province, "drug_toxicity_deaths_by_age")
    if v is not None and age_counts is not None:
        v.use_source(source)
        for label, values in age_counts["rows"].items():
            for period, value in zip(age_counts["periods"], values):
                number = _coroners_clean_cell(value)
                if label == "Total":
                    if number is not None:
                        v.additional(geo_province, period, "Total Deaths", number)
                else:
                    age_group = "Age Unavailable" if label == "Not available" else label
                    _emit_fact(v, geo_province, period, number, dimension2=age_group)
        if age_rates is not None:
            for label, values in age_rates["rows"].items():
                age_group = "Age Unavailable" if label == "Not available" else label
                for period, value in zip(age_rates["periods"], values):
                    _emit_fact(v, geo_province, period, _coroners_clean_cell(value),
                               data_type="rates", dimension2=age_group)


def v1_british_columbia_export_clean(writer, province):
    """BC direct-write entry: BCCSU drug-checking + BC Coroners deaths + national Health Infobase,
    all emitting to the writer under different data_source_ids. BC's by-age/sex/drug-type/manner
    deaths come from the richer BC Coroners data, so it is excluded from NATIONAL_PROVINCES to avoid
    duplicating those. The infobase cleaner still runs here for the infobase-ONLY visuals (harms
    spectrum, polysubstance, origin of opioids) -- BC authors manifest entries for just those, so the
    overlapping demographic blocks self-skip (writer.visual(...) returns None)."""
    v1_BCCSU_export_clean(writer, province)
    v1_coroners_export_clean(writer, province)
    v1_national_export_clean(writer, province)


# --------------------------------------------------------------------------------------- #
# National Health Infobase -- direct-write cleaner (one CSV, filtered per province).
# --------------------------------------------------------------------------------------- #

_INFOBASE_ABOUT = """
This data was collected from Canada's Health Infobase Opioid- and Stimulant-related Harms in Canada dataset, a report published quarterly on providing information on opioid and stimulant-related deaths and overdoses in Canada in collaboration with Chief Coroners, Chief Medical Examiners, Public Health agencies, and Emergency Medical Services from individual provinces and territories.

For more information visit the report directly by clicking the below:
        """
_INFOBASE_LINK = "https://health-infobase.canada.ca/substance-related-harms/opioids-stimulants/"


# Sentinel carried in a fact's value (round-trips through data_value_text) to mark a SUPPRESSED cell:
# the source confirms data exists but hides the small count for privacy. The frontend renders these as
# a dashed "bridge" between the surrounding reported points; a genuine 0 stays 0 and a not-reported
# cell (None below) emits no fact at all (a true gap). Do NOT collapse these three into 0.
SUPPRESSED = "Suppr."


def _infobase_cell(value):
    """Classify a Health Infobase Value cell into one of three states:
      * a float  -> reported (including a genuine 0),
      * SUPPRESSED ("Suppr.") -> suppressed (small count hidden for privacy; data exists),
      * None     -> not reported (blank / NaN / unparseable; no data)."""
    if value is None or (isinstance(value, float) and pandas.isna(value)):
        return None
    text = str(value).replace("\xa0", "").replace("%", "").strip()
    if text == "":
        return None
    if text == SUPPRESSED:
        return SUPPRESSED
    try:
        return float(text)
    except ValueError:
        return None


def _derived_count(percent, total):
    """Count = percent% x Overall total, propagating the 3-state through the derivation.
    Returns a float (incl 0), SUPPRESSED, or None (not reported -> skip)."""
    if percent == SUPPRESSED or total == SUPPRESSED:
        return SUPPRESSED
    if percent is None or total is None:
        return None
    return round(percent / 100 * total)


def _derived_rate(count, population):
    """Rate = count / total population x 100k, propagating the 3-state. None population -> skip."""
    if count == SUPPRESSED:
        return SUPPRESSED
    if count is None or not population:
        return None
    return round(count / population * 100000, 2)


def v1_national_export_clean(writer, province):
    # National Health Infobase opioid/stimulant deaths -> facts, straight to the writer. One CSV,
    # filtered by Region; runs per national province (see NATIONAL_PROVINCES). The per-measure rate
    # math below is deliberate and was audited against the source -- DO NOT "simplify" it:
    #   * sex rates  = pass-through of the infobase "Crude rate" rows, which are already SEX-STRATIFIED
    #                  by the source (male deaths / male population, not / total). We only have a
    #                  TOTAL-population file, so recomputing them would wrongly divide by the total.
    #   * drug-type / manner rates = derived_count / TOTAL provincial population * 100k. Correct:
    #                  these aren't population strata, so a crude total-population rate is the right
    #                  measure (the source ships no crude rate for them, only Percent).
    #   * age = NO rate (the source has only percentages; no age-banded population exists).
    # The infobase publishes no per-stratum counts, so counts are derived = percent x the substance's
    # "Overall numbers" total. (The legacy cleaner used the *opioid* total even for stimulant sex/manner
    # counts -- a latent bug; this version uses the substance-matched total.)
    df = pull_data(["nationalHealthInfobase"])["nationalHealthInfobase"]
    frame = df["dataframe"]
    region = PROVINCE_DISPLAY[province]
    geo = region
    source = {
        "name": "Health Infobase - Health data in Canada",
        "about": _INFOBASE_ABOUT,
        "link": _INFOBASE_LINK,
        "last_updated": df["date_updated"],
        "data_until": df["data_until"],
    }

    def rows_for(substance, measure, unit=None, source_name="Deaths"):
        sel = frame[(frame["Region"] == region) & (frame["Substance"] == substance)
                    & (frame["Specific_Measure"] == measure) & (frame["Time_Period"] == "By year")
                    & (frame["Source"] == source_name)]
        return sel if unit is None else sel[sel["Unit"] == unit]

    def year_of(year_quarter):
        return str(year_quarter).replace("\xa0", "").strip().split(" ")[0]

    def emit(visual, year, value, **kw):
        # A not-reported cell (None) emits nothing -> a true gap. Reported numbers (incl 0) and the
        # SUPPRESSED sentinel are written through as-is.
        _emit_fact(visual, geo, year, value, **kw)

    def overall_totals(substance):
        return {year_of(r["Year_Quarter"]): _infobase_cell(r["Value"])
                for _, r in rows_for(substance, "Overall numbers", "Number").iterrows()}

    opioid_totals = overall_totals("Opioids")
    stimulant_totals = overall_totals("Stimulants")
    totals_for = {"Opioids": opioid_totals, "Stimulants": stimulant_totals}

    # Total provincial population by year -> the denominator for the computed drug-type / manner rates.
    try:
        pop_frames = pull_data(["nationalPopulationData"])
        pop_df = filter_data(pop_frames, ["nationalPopulationData"])[0]["dataframe"]
        pop_df = pop_df.loc[pop_df["GEO"] == region].set_index("REF_DATE")["VALUE"].to_dict()
        population = {str(int(year)): float(val) for year, val in pop_df.items()}
    except (FileNotFoundError, KeyError, IndexError):
        logger.warning("nationalPopulationData unavailable for %s -- drug-type/manner rates skipped.", region)
        population = {}

    def has_reported(rows):
        # A substance is present only if it carries at least one *reported* number (incl 0). All-blank
        # (e.g. Alberta stimulants) or all-suppressed substances are skipped entirely.
        return any(isinstance(_infobase_cell(r["Value"]), float) for _, r in rows.iterrows())

    # ----- Opioid deaths by age group (derived counts + source percentages; NO rates) -----
    v = writer.visual(province, "opioid_deaths_by_age")
    if v is not None:
        v.use_source(source)
        v.options({
            "counts-title": f"Opioid Deaths in {region} by Age Group",
            "percentages-title": f"Percent of Total Opioid Deaths in {region} belonging to each Age Group",
            "table-title": f"Opioid Deaths in {region} by Age Group",
            "counts-y-axis-title": "Number of Opioid Deaths",
            "percentages-y-axis-title": "Percent of Total Opioid Deaths",
            "table-percentages-row": "Percent of Total Opioid Deaths for those aged replace_me",
            "table-counts-row": "Number of Opioid Deaths for those aged replace_me",
        })
        for _, row in rows_for("Opioids", "Age group", "Percent").iterrows():
            year = year_of(row["Year_Quarter"]); pct = _infobase_cell(row["Value"]); age = row["Disaggregator"]
            emit(v, year, pct, data_type="percentages", dimension="opioids", dimension2=age)
            emit(v, year, _derived_count(pct, opioid_totals.get(year)), dimension="opioids", dimension2=age)

    # ----- Deaths by drug type (opioid + stimulant types; counts + computed rates + percentages) -----
    v = writer.visual(province, "deaths_by_drug_type")
    if v is not None:
        v.use_source(source)
        v.options({
            "counts-title": f"Deaths in {region} Attributed to Unregulated Drugs by Drug Type",
            "percentages-title": f"Percent of Total Unregulated Drug Deaths in {region} by Drug Type",
            "rates-title": f"Unregulated Drug Deaths per 100,000 Population in {region} by Drug Type",
            "table-title": f"Unregulated Drug Deaths in {region} by Drug Type",
            "counts-y-axis-title": "Number of Unregulated Drug Deaths",
            "percentages-y-axis-title": "Percent of Total Unregulated Drug Deaths",
            "rates-y-axis-title": "Unregulated Drug Deaths per 100,000 Population",
            "table-percentages-row": "Percent of Total Unregulated Drug Deaths Attributed to replace_me",
            "table-counts-row": "Unregulated Drug Deaths Attributed to replace_me",
            "table-rates-row": "Unregulated Drug Deaths Attributed to replace_me/100,000 Population",
            "hover-type": "x unified",
            "hover-info": "default",
        })
        for substance, measure in [("Opioids", "Type of opioids"), ("Stimulants", "Type of stimulants")]:
            subst = "opioids" if substance == "Opioids" else "stimulants"
            rows = rows_for(substance, measure, "Percent")
            if not has_reported(rows):
                continue   # province carries no data for this substance (e.g. Alberta has no stimulants)
            for _, row in rows.iterrows():
                year = year_of(row["Year_Quarter"]); pct = _infobase_cell(row["Value"]); drug = row["Disaggregator"]
                emit(v, year, pct, data_type="percentages", dimension=subst, dimension2=drug)
                count = _derived_count(pct, totals_for[substance].get(year))
                emit(v, year, count, dimension=subst, dimension2=drug)
                emit(v, year, _derived_rate(count, population.get(year)),
                     data_type="rates", dimension=subst, dimension2=drug)

    # ----- Deaths by sex (derived counts + SOURCE crude rates [sex-stratified] + percentages) -----
    v = writer.visual(province, "deaths_by_sex")
    if v is not None:
        v.use_source(source)
        v.options({
            "counts-title": f"Unregulated Drug Toxicity Deaths in {region} by Sex",
            "rates-title": f"Unregulated Drug Toxicity Deaths in {region} per 100,000 Population by Sex",
            "percentages-title": f"Percent of Total Unregulated Drug Toxicity Deaths in {region} by Sex",
            "table-title": f"Unregulated Drug Toxicity Deaths in {region} by Sex",
            "counts-y-axis-title": "Number of Unregulated Drug Toxicity Deaths",
            "rates-y-axis-title": "Unregulated Drug Deaths/100,000 Population",
            "percentages-y-axis-title": "Percent of Total Unregulated Drug Toxicity Deaths",
            "table-percentages-row": "Percent of Total Unregulated Drug Toxicity Deaths that were replace_me Deaths",
            "table-rates-row": "Unregulated Drug Toxicity Deaths/100,000 Population that were replace_me Deaths",
            "table-counts-row": "Unregulated Drug Toxicity Deaths that were replace_me Deaths",
        })
        for substance in ["Opioids", "Stimulants"]:
            subst = "opioids" if substance == "Opioids" else "stimulants"
            pct_rows = rows_for(substance, "Sex", "Percent")
            if not has_reported(pct_rows):
                continue
            for _, row in pct_rows.iterrows():
                year = year_of(row["Year_Quarter"]); pct = _infobase_cell(row["Value"]); sex = row["Disaggregator"]
                emit(v, year, pct, data_type="percentages", dimension=subst, dimension2=sex)
                emit(v, year, _derived_count(pct, totals_for[substance].get(year)),
                     dimension=subst, dimension2=sex)
            for _, row in rows_for(substance, "Sex", "Crude rate").iterrows():   # source-stratified rate
                year = year_of(row["Year_Quarter"]); sex = row["Disaggregator"]
                emit(v, year, _infobase_cell(row["Value"]), data_type="rates",
                     dimension=subst, dimension2=sex)

    # ----- Deaths by manner of death (derived counts + computed rates + percentages) -----
    v = writer.visual(province, "deaths_by_manner")
    if v is not None:
        v.use_source(source)
        v.options({
            "counts-title": f"Unregulated Drug Toxicity Deaths in {region} by Manner of Death",
            "rates-title": f"Unregulated Drug Toxicity Deaths in {region} per 100,000 Population by Manner of Death",
            "percentages-title": f"Percent of Total Unregulated Drug Toxicity Deaths in {region} by Manner of Death",
            "table-title": f"Unregulated Drug Toxicity Deaths in {region} by Manner of Death",
            "counts-y-axis-title": "Number of Unregulated Drug Toxicity Deaths",
            "rates-y-axis-title": "Unregulated Drug Deaths/100,000 Population",
            "percentages-y-axis-title": "Percent of Total Unregulated Drug Toxicity Deaths",
            "table-percentages-row": "Percent of Total Unregulated Drug Toxicity Deaths that were replace_me",
            "table-rates-row": "Unregulated Drug Toxicity Deaths/100,000 Population that were replace_me",
            "table-counts-row": "Unregulated Drug Toxicity Deaths that were replace_me",
        })
        for substance in ["Opioids", "Stimulants"]:
            subst = "opioids" if substance == "Opioids" else "stimulants"
            rows = rows_for(substance, "Manner of death", "Percent")
            if not has_reported(rows):
                continue
            for _, row in rows.iterrows():
                year = year_of(row["Year_Quarter"]); pct = _infobase_cell(row["Value"]); manner = row["Disaggregator"]
                emit(v, year, pct, data_type="percentages", dimension=subst, dimension2=manner)
                count = _derived_count(pct, totals_for[substance].get(year))
                emit(v, year, count, dimension=subst, dimension2=manner)
                emit(v, year, _derived_rate(count, population.get(year)),
                     data_type="rates", dimension=subst, dimension2=manner)

    # ----- Opioid deaths by origin of opioid (derived counts + computed rates + percentages) -----
    # Pharmaceutical vs non-pharmaceutical vs both vs undetermined; mirrors the by-manner block.
    v = writer.visual(province, "opioid_deaths_by_origin")
    if v is not None:
        v.use_source(source)
        v.options({
            "counts-title": f"Opioid Toxicity Deaths in {region} by Origin of Opioid",
            "rates-title": f"Opioid Toxicity Deaths in {region} per 100,000 Population by Origin of Opioid",
            "percentages-title": f"Percent of Total Opioid Toxicity Deaths in {region} by Origin of Opioid",
            "table-title": f"Opioid Toxicity Deaths in {region} by Origin of Opioid",
            "counts-y-axis-title": "Number of Opioid Toxicity Deaths",
            "rates-y-axis-title": "Opioid Toxicity Deaths/100,000 Population",
            "percentages-y-axis-title": "Percent of Total Opioid Toxicity Deaths",
            "table-percentages-row": "Percent of Total Opioid Toxicity Deaths from replace_me opioids",
            "table-rates-row": "Opioid Toxicity Deaths/100,000 Population from replace_me opioids",
            "table-counts-row": "Opioid Toxicity Deaths from replace_me opioids",
        })
        for _, row in rows_for("Opioids", "Origin of opioid(s)", "Percent").iterrows():
            year = year_of(row["Year_Quarter"]); pct = _infobase_cell(row["Value"]); origin = row["Disaggregator"]
            emit(v, year, pct, data_type="percentages", dimension="opioids", dimension2=origin)
            count = _derived_count(pct, opioid_totals.get(year))
            emit(v, year, count, dimension="opioids", dimension2=origin)
            emit(v, year, _derived_rate(count, population.get(year)),
                 data_type="rates", dimension="opioids", dimension2=origin)

    # ----- Polysubstance involvement (derived counts + source percentages; NO rates) -----
    # Source quirk: each "Involving X" measure is the share of the OTHER substance's deaths that also
    # involved X, so it is filed under the host substance. The Disaggregator is blank -> one row per
    # year; the series identity is the measure, carried in a descriptive dimension2 label.
    v = writer.visual(province, "deaths_polysubstance")
    if v is not None:
        v.use_source(source)
        v.options({
            "counts-title": f"Polysubstance Involvement in Toxicity Deaths in {region}",
            "percentages-title": f"Percent of Toxicity Deaths in {region} Involving Co-occurring Substances",
            "table-title": f"Polysubstance Involvement in Toxicity Deaths in {region}",
            "counts-y-axis-title": "Number of Toxicity Deaths",
            "percentages-y-axis-title": "Percent of Toxicity Deaths",
            "table-percentages-row": "Percent: replace_me",
            "table-counts-row": "Number: replace_me",
        })
        poly_series = [
            ("Opioids", "opioids", "Involving stimulants", "Opioid deaths also involving stimulants"),
            ("Stimulants", "stimulants", "Involving opioids", "Stimulant deaths also involving opioids"),
            ("Opioids", "opioids", "Involving other psychoactive substances",
             "Opioid deaths involving other psychoactive substances"),
            ("Stimulants", "stimulants", "Involving other psychoactive substances",
             "Stimulant deaths involving other psychoactive substances"),
        ]
        for substance, subst, measure, label in poly_series:
            rows = rows_for(substance, measure, "Percent")
            if not has_reported(rows):
                continue
            for _, row in rows.iterrows():
                year = year_of(row["Year_Quarter"]); pct = _infobase_cell(row["Value"])
                emit(v, year, pct, data_type="percentages", dimension=subst, dimension2=label)
                emit(v, year, _derived_count(pct, totals_for[substance].get(year)),
                     dimension=subst, dimension2=label)

    # ----- Spectrum of harm: overall opioid/stimulant harms by type (SOURCE counts only; no derivation) -----
    # Deaths vs Hospitalizations vs ED visits vs EMS responses over time. These carry real reported
    # "Number" totals, so counts pass through verbatim. Source coverage varies by province -> each series
    # self-omits where unreported (has_reported guard).
    harm_sources = [
        ("Deaths", "Deaths"),
        ("Hospitalizations", "Hospitalizations"),
        ("Emergency Department (ED) Visits", "Emergency Department Visits"),
        ("Emergency Medical Services (EMS)", "Emergency Medical Services Responses"),
    ]
    for substance, subst, visual_id in [("Opioids", "opioids", "opioid_harms_by_type"),
                                        ("Stimulants", "stimulants", "stimulant_harms_by_type")]:
        v = writer.visual(province, visual_id)
        if v is None:
            continue
        label = "Opioid" if substance == "Opioids" else "Stimulant"
        v.use_source(source)
        v.options({
            "counts-title": f"{label}-Related Harms in {region} by Type",
            "table-title": f"{label}-Related Harms in {region} by Type",
            "counts-y-axis-title": f"Number of {label}-Related Harms",
            "table-counts-row": "Number of replace_me",
            "hover-type": "x unified",
            "hover-info": "default",
        })
        for source_name, harm_label in harm_sources:
            rows = rows_for(substance, "Overall numbers", "Number", source_name=source_name)
            if not has_reported(rows):
                continue
            for _, row in rows.iterrows():
                year = year_of(row["Year_Quarter"])
                emit(v, year, _infobase_cell(row["Value"]), dimension=subst, dimension2=harm_label)

    # ----- Hospitalizations by age & sex (real source cross-tab counts; substance filter + year control) -----
    # Self-contained grouped-bar visual: age bands on the x-axis, grouped by sex, with a client-side
    # substance filter and a year selector. The "Sex and age group" measure ships real "Number" counts,
    # so values pass through (no derivation). Three stratifiers (age, sex, substance) but a fact has only
    # two generic dimension slots, so substance rides in a "<substance>|<sex>" composite dimension2 that
    # the client splits (mirrors the treemap's "||"-joined composite geo).
    v = writer.visual(province, "hospitalizations_by_age_sex")
    if v is not None:
        v.use_source(source)
        v.options({
            "title": f"replace_substance Hospitalizations in {region} by Age and Sex (replace_year)",
            "table_title": f"replace_substance Hospitalizations in {region} by Age and Sex (replace_year)",
            "x_axis_title": "Age Group",
            "y_axis_title": "Number of Hospitalizations",
            "filter_label": "Substance",
            "time_label": "Year",
        })
        for substance, subst in [("Opioids", "opioids"), ("Stimulants", "stimulants")]:
            rows = rows_for(substance, "Sex and age group", "Number", source_name="Hospitalizations")
            if not has_reported(rows):
                continue
            for _, row in rows.iterrows():
                year = year_of(row["Year_Quarter"]); age = row["Aggregator"]; sex = row["Disaggregator"]
                emit(v, year, _infobase_cell(row["Value"]), dimension=age, dimension2=f"{subst}|{sex}")


_SK_ABOUT = """
This data has been collected by the Saskatchewan Coroners Service (SKCS), and is based on toxicology reports from individuals who have died in Saskatchewan where the cause of death was confirmed, or suspected to be,drug toxicity.The data is updated monthly by the SKCS

For more information,visit the SKCS website to view the PDF report by clicking the button below:
            """
_SK_LINK = "https://publications.saskatchewan.ca/#/products/90505"


def _sk_source(table):
    """The Saskatchewan Coroners DataSources metadata, dated from one scraped skPubCentre table."""
    return {"name": "Saskatchewan Coroners Service", "about": _SK_ABOUT, "link": _SK_LINK,
            "last_updated": table["date_updated"], "data_until": table["data_until"]}


def v1_SK_export_clean(writer, province):
    """Saskatchewan Coroners (skPubCentre) visuals -> facts, straight to the writer. Two visuals:
    `drug_death_heatmap` (geo_series, counts by health authority) and `deaths_by_opioid_type`
    (flat_series, counts/rates/percentages by drug type). The national-infobase SK visuals come
    from v1_national_export_clean; this covers only the skPubCentre PDF tables. Static titles/axis
    labels live in the saskatchewan-coroners-service.json manifest (not set here)."""
    # ----- Pull and Filter the SK Data -----
    to_filter = pull_data(["skPubCentre"])
    sk_pub_centre = filter_data(to_filter, ["Confirmed&SuspectedDrugToxicityDeathsbyMannerofDeath","BreakdownofOpioidDrugsIdentifiedinConfirmedDrugToxicityDeathsbyMannerofDeath", "ConfirmedDrugToxicityDeathsbyPlaceofDeath"])

    # ----- Pull the total CONFIRMED deaths to use in other calculations (percentage denominators) -----
    data = sk_pub_centre[0]["dataframe"]
    # Drop the total and suspected rows
    data = data[(data["Year"] != "Total") & (data["Year"] != "Suspected")]
    # Convert all columns except Year to numeric, forcing errors to NaN then 0
    data = data.replace("--", 0)
    for col in data.columns:
        if col != "Year":
            data[col] = pandas.to_numeric(data[col], errors="coerce").fillna(0).astype(int)
    # sum each year column -> per-year total of confirmed deaths, keyed by year. Sheet[0] lays years
    # out as columns; sheet[1] (below) lays them out as rows, so key by the actual year rather than
    # trusting the two sheets' year orders to line up positionally.
    total_by_year = {int(year): total for year, total in data.sum(numeric_only=True).to_dict().items()}

    # ----- Deaths by Place of Death (drug_death_heatmap) -----
    place = writer.visual(province, "drug_death_heatmap")
    if place is not None:
        place.use_source(_sk_source(sk_pub_centre[2]))
        data = sk_pub_centre[2]["dataframe"]
        data = data.replace("-", 0)
        years = data.columns[1:].to_list()
        # Load the key mapping each location to its health authority
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static/js/SK_HA_key.json")
        with open(filepath, "r") as file:
            ha_key = json.load(file)
        # Instantiate a dict of health authorities with empty lists to hold deaths in each
        health_authorities = {ha: [0] * len(years) for ha in ha_key.keys()}
        def get_key_from_value(dict, value):
            for key, values in dict.items():
                if value in values:
                    return key
            return None
        for index, row in data.iterrows():
            location = row["Location"]
            health_authority = get_key_from_value(ha_key, location)
            if health_authority:
                for index, year in enumerate(years):
                    health_authorities[health_authority][index] += int(row[year])
            elif location.lower() == "total":
                #Add this to a total row eventually
                health_authorities["Saskatchewan"] = [int(row[year]) for year in years]
            else:
                if "Unknown" not in health_authorities:
                    health_authorities["Unknown"] = [0] * len(years)
                for index, year in enumerate(years):
                    health_authorities["Unknown"][index] += int(row[year])
        for health_authority, counts in health_authorities.items():
            for index, year in enumerate(years):
                place.fact(health_authority, year, counts[index])

    # ----- Deaths by Opioid Type (deaths_by_opioid_type) -----
    by_type = writer.visual(province, "deaths_by_opioid_type")
    if by_type is not None:
        by_type.use_source(_sk_source(sk_pub_centre[1]))
        data = sk_pub_centre[1]["dataframe"]
        # Because of the way these show up in the PDF we have to do a little extra cleaning
        # Remove the manner of death column
        data = data.drop(columns=["MannerOfDeath"])
        drug_types = data.columns[1:].to_list()
        for index, drug in enumerate(drug_types):
            if drug == "FuranylFentanyl":
                drug_types[index] = "Furanyl Fentanyl"
            elif drug == "FuranylUF-17":
                drug_types[index] = "Furanyl UF-17"
            elif drug == "Opioid(Unknown)":
                drug_types[index] = "Opioid (Unknown)"
        # Reset the columns to their new names
        data.columns = ["Year"] + drug_types
        # Replace all the "--" with 0 values
        data = data.replace("--", 0)
        for col in data.columns:
            if col != "Year":
                data[col] = pandas.to_numeric(data[col], errors="coerce").fillna(0).astype(int)
        data = data.groupby("Year", as_index=False).sum(numeric_only=True)

        years = data["Year"].tolist()
        population_data = pull_data(["nationalPopulationData"])
        population_data = filter_data(population_data, ["nationalPopulationData"])[0]["dataframe"]
        population_data = population_data.loc[population_data["GEO"] == "Saskatchewan"].set_index("REF_DATE")["VALUE"].to_dict()
        for drug in drug_types:
            counts = data[drug].tolist()
            # counts is aligned to `years`; look the denominators (total deaths, population) up by the
            # actual year so a year missing from either source can't shift counts onto the wrong year.
            for year, count in zip(years, counts):
                by_type.fact(PROVINCE_DISPLAY[province], year, count, dimension2=drug)
                total = total_by_year.get(int(year))
                percentage = round((count / total) * 100, 2) if total else 0
                by_type.fact(PROVINCE_DISPLAY[province], year, percentage,
                             data_type="percentages", dimension2=drug)
                # Only emit a rate for years with a population figure (e.g. the latest year often lacks one).
                population = population_data.get(int(year))
                if population is not None:
                    rate = round((count / population) * 100000, 2) if population != 0 else 0
                    by_type.fact(PROVINCE_DISPLAY[province], year, rate, data_type="rates", dimension2=drug)



###########################################################################################
#                          DB persistence (the data layer)                                #
# Each v1_*_export_clean(writer, province) cleaner emits its facts straight into the        #
# FactWriter, which writes them as normalized rows: DataSources (about + scrape dates),     #
# DataPoints (facts), and VisualQuery (the predicates that select a visual's facts). The    #
# Visuals rows that *describe* each visual (shape/metric/dimensions/key encoding/menu        #
# config) are authored separately from JSON manifests by data_viz/visual_definitions.py     #
# (`flask define-visuals`); this layer reads each Visuals row to learn how to map a          #
# cleaner's values into facts -- there is no hard-coded VISUAL_SPECS / VISUAL_MENU registry. #
###########################################################################################

# --------------------------------------------------------------------------------------- #
# Write-side constants + helpers for mapping cleaned values into normalized facts.
# --------------------------------------------------------------------------------------- #

# URL-friendly province -> the display/Region name used as the geo for province-level facts
PROVINCE_DISPLAY = {
    "british-columbia": "British Columbia",
    "alberta": "Alberta",
    "saskatchewan": "Saskatchewan",
    "manitoba": "Manitoba",
    "new-brunswick": "New Brunswick",
    "ontario": "Ontario",
    "nova-scotia": "Nova Scotia",
    "quebec": "Quebec",
    "prince-edward-island": "Prince Edward Island",
    "newfoundland-and-labrador": "Newfoundland and Labrador",
    "yukon": "Yukon",
    "northwest-territories": "Northwest Territories",
    "nunavut": "Nunavut",
}

# Provinces/territories whose national Health Infobase visuals are produced by
# v1_national_export_clean (registered into V1_DIRECT below). BC is intentionally excluded -- it
# uses its richer BC Coroners data and would otherwise get duplicate by-sex/age/drug-type visuals.
NATIONAL_PROVINCES = [
    "alberta", "saskatchewan", "manitoba", "new-brunswick", "ontario", "nova-scotia",
    "quebec", "prince-edward-island", "newfoundland-and-labrador",
    "yukon", "northwest-territories", "nunavut",
]

TIME_FRAME_TYPE = "year"
ADDITIONAL_DIM_TYPE = "additional_label"   # tags a table-only total row in dimension2


def additional_metric(label):
    """Stable metric name for a table-only total row, derived from its display label."""
    return "total_" + label.replace("Total ", "").strip().replace(" ", "_").lower()


def _split_value(value):
    """Return (numeric_or_None, text_or_None). Strings round-trip verbatim via the text column;
    numbers go in the float column (queryable)."""
    if isinstance(value, str):
        try:
            return float(value), value
        except ValueError:
            return None, value
    if value is None:
        return None, None
    return float(value), None


class FactWriter:
    """Collects normalized facts during a regeneration run, then drops & rewrites ONLY the rows the
    run reproduces -- the ``(data_source_id, geo)`` territory it emitted + the touched visuals'
    predicates -- in a single transaction, so untouched sources keep their rows.

    Cleaners emit through :meth:`visual` / :class:`VisualWriter` (which call :meth:`point` /
    :meth:`predicate` under the hood); all share this buffer + scoped :meth:`finish`."""

    def __init__(self, db, models):
        self.db = db
        self.DataSources, self.DataPoints, self.Visuals, self.VisualQuery = models
        self._sources = {}        # name -> DataSources row (upserted immediately for its id)
        self._points = {}         # natural-key -> buffered DataPoints (dedups within the run)
        self._preds = {}          # (visual_id, type, value) -> buffered VisualQuery kwargs (dedup)
        self._territory = set()   # (data_source_id, geo) pairs this run reproduces -> delete scope
        self._visual_ids = set()  # visuals whose predicates this run reproduces -> delete scope

    def upsert_source(self, data_source):
        """Fetch/create the DataSources row by name, refresh its about/scrape-date strings, return id."""
        name = data_source["name"]
        source = self._sources.get(name)
        if source is None:
            source = self.DataSources.query.filter_by(name=name).first() or self.DataSources(name=name)
            self.db.session.add(source)
            self._sources[name] = source
        source.link = data_source.get("link", source.link)
        source.about = data_source.get("about")
        source.last_updated_str = data_source.get("last_updated")
        source.data_until_str = data_source.get("data_until")
        self.db.session.flush()   # need source.id
        return source.id

    def point(self, source_id, geo_type, geo, time_frame, metric, data_type,
              dim_type=None, dim_val=None, dim2_type=None, dim2_val=None, value=None,
              time_frame_type=TIME_FRAME_TYPE):
        """Buffer one DataPoints row (dedup by natural key) and record its (source, geo) territory."""
        key = (source_id, geo_type, geo, str(time_frame), metric, data_type,
               dim_type, dim_val, dim2_type, dim2_val)
        if key in self._points:
            return
        num, text = _split_value(value)
        self._points[key] = self.DataPoints(
            data_source_id=source_id, geo_type=geo_type, geo=geo,
            time_frame_type=time_frame_type, time_frame=str(time_frame),
            data_metric=metric, data_type=data_type,
            dimension_type=dim_type, dimension_value=dim_val,
            dimension2_type=dim2_type, dimension2_value=dim2_val,
            data_value=num, data_value_text=text,
        )
        if source_id is not None:
            self._territory.add((source_id, geo))

    def predicate(self, visual_id, filter_type, filter_value):
        """Buffer a VisualQuery predicate (dedup) and mark the visual for a scoped predicate refresh."""
        self._visual_ids.add(visual_id)
        self._preds[(visual_id, filter_type, str(filter_value))] = dict(
            for_visual_id=visual_id, filter_type=filter_type, filter_value=str(filter_value))

    def visual(self, province, visual_id):
        """A VisualWriter bound to this visual's Visuals row, or None (with a warning) if undefined."""
        row = self.Visuals.query.filter_by(province=province, name=visual_id).first()
        if row is None:
            print(f"  ! No definition for {province}/{visual_id} -- "
                  f"run `flask define-visuals` first. Skipping its data.")
            return None
        return VisualWriter(self, row)

    def finish(self):
        """One transaction: drop only the reproduced (source, geo) territory + touched predicates,
        then insert the buffered rows. Other sources/provinces are left untouched."""
        try:
            for source_id, geo in self._territory:
                self.DataPoints.query.filter_by(data_source_id=source_id, geo=geo).delete()
            for visual_id in self._visual_ids:
                self.VisualQuery.query.filter_by(for_visual_id=visual_id).delete()
            self.db.session.flush()
            for point in self._points.values():
                self.db.session.add(point)
            for kwargs in self._preds.values():
                self.db.session.add(self.VisualQuery(**kwargs))
            self.db.session.commit()
        except Exception:
            # A failed delete/insert leaves the session with the dropped rows flushed but not committed;
            # roll back so a retry/next caller doesn't inherit partial state, then re-raise to fail loudly.
            self.db.session.rollback()
            raise


class VisualWriter:
    """Bound to one Visuals row: cleaning passes VALUES (geo/time/dim values/count); the metric and
    dimension TYPES come from the row, so the manifest stays the single source of truth."""

    def __init__(self, writer, visual):
        self.writer = writer
        self.visual = visual
        self.source_id = visual.data_source_id

    def use_source(self, data_source):
        """Refresh this run's DataSources metadata from the freshly scraped block."""
        self.source_id = self.writer.upsert_source(data_source)
        return self

    def options(self, opts):
        """Set this visual's presentation options (titles / axis / table labels) at gen-visuals
        time, overwriting the Visuals row's visual_options. Used when titles are province-
        parameterized (so they can't live statically in the manifest); static options should be
        declared in the manifest instead. Committed by FactWriter.finish() with the rest of the run."""
        self.visual.visual_options = opts
        return self

    def _dim_type(self, dimension):
        # regional/treemap author dimension_type in the manifest; the flat/geo substance slot is
        # untyped there, so default it to "substance" when a substance value is supplied.
        return self.visual.dimension_type or ("substance" if dimension is not None else None)

    def fact(self, geo, time_frame, value, *, data_type="counts",
             dimension=None, dimension2=None, time_frame_type=None):
        v = self.visual
        self.writer.point(self.source_id, v.geo_type, geo, time_frame, v.metric, data_type,
                          self._dim_type(dimension), dimension, v.dimension2_type, dimension2, value,
                          time_frame_type=(time_frame_type or TIME_FRAME_TYPE))
        if v.geo_type == "province":
            self.writer.predicate(v.id, "geo", geo)   # province-shared facts scoped to this geo

    def additional(self, geo, time_frame, label, value, *, time_frame_type=None):
        """A table-only total row: one additional_rows fact + its additional_metric predicate."""
        metric = additional_metric(label)
        self.writer.point(self.source_id, self.visual.geo_type, geo, time_frame, metric,
                          "additional_rows", ADDITIONAL_DIM_TYPE, label, None, None, value,
                          time_frame_type=(time_frame_type or TIME_FRAME_TYPE))
        self.writer.predicate(self.visual.id, "additional_metric", metric)


# --------------------------------------------------------------------------------------- #
# Ontario -- national Health Infobase + ODPRN Public Health Unit deaths (two sources).
# --------------------------------------------------------------------------------------- #

_ODPRN_LINK = "https://odprn.ca/occ-opioid-and-suspect-drug-related-death-data/"
_ODPRN_ABOUT = """
This data was collected from the Ontario Drug Policy Research Network (ODPRN), which publishes monthly counts of confirmed and probable opioid- and suspected drug-related deaths recorded by the Office of the Chief Coroner of Ontario, broken down by Public Health Unit.

For more information visit the data source directly by clicking the below:
        """


def v1_ontario_odprn_export_clean(writer, province):
    """ODPRN confirmed & probable opioid-toxicity deaths by Public Health Unit -> facts, straight to
    the writer. The "PHU Confirmed & Probable" sheet is monthly (a YYYYMM `date` column); we sum each
    PHU's months into annual totals. The PHU column names already match the ontario.geojson `ENGNAME`
    values, so they're emitted as the geo verbatim (the heatmap renderer keys the choropleth by
    ENGNAME). The Ontario-wide "All" column is not a health authority and is skipped."""
    data = pull_data(["onODPRN"])
    phu = filter_data(data, ["PHU Confirmed & Probable"])[0]
    df = phu["dataframe"]
    latest = int(df["date"].max())   # YYYYMM of the most recent month present -> data_until
    source = {
        "name": "Ontario Drug Policy Research Network (ODPRN)",
        "about": _ODPRN_ABOUT,
        "link": _ODPRN_LINK,
        "last_updated": phu["date_updated"],
        "data_until": datetime.datetime.strptime(str(latest), "%Y%m").strftime("%B, %Y"),
    }

    v = writer.visual(province, "deaths_by_health_unit")
    if v is None:
        return
    v.use_source(source)

    years = (df["date"] // 100).astype(int)
    for phu_name in df.columns:
        if phu_name in ("date", "All"):
            continue
        annual = pandas.to_numeric(df[phu_name], errors="coerce").groupby(years).sum()
        for year, total in annual.items():
            v.fact(phu_name, str(int(year)), int(total))


def v1_ontario_export_clean(writer, province):
    """Ontario direct-write entry: national Health Infobase deaths + ODPRN PHU deaths, both emitting
    to the writer (their facts live under different data_source_ids, so they don't collide)."""
    v1_national_export_clean(writer, province)   # preserve Ontario's existing infobase visuals
    v1_ontario_odprn_export_clean(writer, province)


# --------------------------------------------------------------------------------------- #
# Nova Scotia -- national Health Infobase + NS Substance-Related Fatalities (two sources).
# --------------------------------------------------------------------------------------- #

_NS_RF_LINK = "https://data.novascotia.ca/Health-and-Wellness/Numbers-and-rates-of-substance-related-fatalities-/iu6y-z4n3/about_data"
_NS_RF_ABOUT = """
This data was collected from the Nova Scotia Numbers and Rates of Substance-Related Fatalities dashboard, published on the provincial open-data portal by the Nova Scotia Medical Examiner Service. It reports substance-related deaths broken down by health zone, drug type, manner of death, and sex, with annual counts and population-adjusted rates.

For more information visit the data source directly by clicking the below:
        """

_NS_RATE_COL = "rate_per_100_000_population_annualized_for_quarterly_data"
_NS_ZONES = ["Central", "Eastern", "Northern", "Western"]


def v1_ns_rates_fatalities_export_clean(writer, province):
    """Nova Scotia substance-related fatalities -> facts, straight to the writer. The dashboard ships
    one long CSV at several grains; the `quarter == "All"` & `sex == "Total"` rows are the annual
    roll-up carrying both a count (`frequency`) and an annualized rate, so all three visuals read from
    them. The four health-zone names already match nova-scotia.geojson `ENGNAME`. Counts coerce to int,
    rates stay float, and NaN cells are skipped (a true gap, not a 0)."""
    data = pull_data(["nsRatesFatalities"])["nsRatesFatalities"]
    frame = data["dataframe"]
    annual = frame[(frame["quarter"] == "All") & (frame["sex"] == "Total")]
    source = {
        "name": "Nova Scotia Numbers and Rates of Substance-Related Fatalities",
        "about": _NS_RF_ABOUT,
        "link": _NS_RF_LINK,
        "last_updated": data["date_updated"],
        "data_until": data["data_until"],
    }

    def count_of(row):
        n = pandas.to_numeric(row["frequency"], errors="coerce")
        return None if pandas.isna(n) else int(n)

    def rate_of(row):
        r = pandas.to_numeric(row[_NS_RATE_COL], errors="coerce")
        return None if pandas.isna(r) else float(r)

    # ----- Deaths by health zone (annual all-substance counts per zone; heatmap) -----
    v = writer.visual(province, "deaths_by_health_zone")
    if v is not None:
        v.use_source(source)
        rows = annual[(annual["drug_type"] == "Total - all substances")
                      & (annual["manner_of_death"] == "All manners")
                      & (annual["health_zone_of_residence"].isin(_NS_ZONES))]
        for _, row in rows.iterrows():
            _emit_fact(v, row["health_zone_of_residence"], str(int(row["year"])), count_of(row))

    province_geo = PROVINCE_DISPLAY[province]
    ns = annual[annual["health_zone_of_residence"] == province_geo]

    # ----- Deaths by drug type (every substance except the grand total; counts + rates) -----
    v = writer.visual(province, "deaths_by_substance")
    if v is not None:
        v.use_source(source)
        rows = ns[(ns["manner_of_death"] == "All manners")
                  & (ns["drug_type"] != "Total - all substances")]
        for _, row in rows.iterrows():
            year = str(int(row["year"])); drug = row["drug_type"]
            _emit_fact(v, province_geo, year, count_of(row), dimension2=drug)
            _emit_fact(v, province_geo, year, rate_of(row), data_type="rates", dimension2=drug)

    # ----- Deaths by manner of death (all substances; counts + rates) -----
    v = writer.visual(province, "deaths_by_manner_all_substances")
    if v is not None:
        v.use_source(source)
        rows = ns[ns["drug_type"] == "Total - all substances"]
        for _, row in rows.iterrows():
            year = str(int(row["year"])); manner = row["manner_of_death"]
            _emit_fact(v, province_geo, year, count_of(row), dimension2=manner)
            _emit_fact(v, province_geo, year, rate_of(row), data_type="rates", dimension2=manner)


def v1_nova_scotia_export_clean(writer, province):
    """Nova Scotia direct-write entry: national Health Infobase deaths + NS substance-related
    fatalities, both emitting to the writer under different data_source_ids so they don't collide."""
    v1_national_export_clean(writer, province)   # preserve Nova Scotia's existing infobase visuals
    v1_ns_rates_fatalities_export_clean(writer, province)


def v1_saskatchewan_export_clean(writer, province):
    """Saskatchewan direct-write entry: national Health Infobase deaths + SK Coroners (skPubCentre)
    visuals, both emitting to the writer under different data_source_ids so they don't collide."""
    v1_national_export_clean(writer, province)   # preserve Saskatchewan's existing infobase visuals
    v1_SK_export_clean(writer, province)


# URL-friendly target key -> new-style cleaner that emits straight to the writer: builder(writer, key)
V1_DIRECT = {
    "canada": v1_drugchecking_export_clean,
    "british-columbia": v1_british_columbia_export_clean,
    "ontario": v1_ontario_export_clean,   # composes national infobase + ODPRN PHU deaths
    "nova-scotia": v1_nova_scotia_export_clean,   # composes national infobase + NS fatalities dashboard
    "saskatchewan": v1_saskatchewan_export_clean,   # composes national infobase + SK Coroners PDF tables
}
# Every national-infobase province shares one cleaner (filters the single CSV by Region). `ontario`
# is set explicitly above (it also needs ODPRN), so setdefault leaves it alone.
for _national_province in NATIONAL_PROVINCES:
    V1_DIRECT.setdefault(_national_province, v1_national_export_clean)


def export_data_to_db(only=None):
    """Regenerate V1 facts into DataPoints + VisualQuery.

    `only`: iterable of target/province keys (e.g. ["canada"]) to regenerate; None = all targets.
    Only the rows the selected run reproduces -- its (data_source_id, geo) territory + the touched
    visuals' predicates -- are dropped and rewritten, in one transaction, so untouched sources keep
    their rows and a target whose scrape is missing is simply skipped.

    Visual *definitions* (the Visuals rows) are authored separately by `flask define-visuals`; this
    layer reads each row to learn how to map cleaned data into facts.
    """
    from data_viz.database import db
    from data_viz.database.models import DataSources, DataPoints, Visuals, VisualQuery

    writer = FactWriter(db, (DataSources, DataPoints, Visuals, VisualQuery))
    targets = set(only) if only else None

    # Each cleaner emits straight into the writer's buffer (builder(writer, province)).
    for province, builder in V1_DIRECT.items():
        if targets and province not in targets:
            continue
        try:
            builder(writer, province)
        except FileNotFoundError as exc:
            # Only a *missing scrape* is skipped (the province keeps its existing rows). Any other
            # error (e.g. a cleaner referencing a column the source dropped) is a real defect and is
            # left to propagate rather than silently dropping the province's data.
            logger.warning("Skipping %s: missing scrape (%s)", province, exc)

    writer.finish()


# Test code below
if __name__ == '__main__':
    # The cleaners write straight to the DB now and need an app context + a FactWriter, so run a
    # regeneration via the CLI instead, e.g.:  flask gen-visuals --only canada
    pass
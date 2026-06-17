###########################################################################################
#                                  Visual data specs                                      #
# Shared, pure-Python registry that bridges the normalized DataPoints rows and the legacy #
# JSON shape the frontend expects. Both the write side (generateVisuals.export_data_to_db)#
# and the read side (visual_query.build_province_payload) drive off this single source of #
# truth so the encode/decode can't drift.                                                 #
#                                                                                          #
# A DataPoints row is a star-schema fact:                                                 #
#   data_metric : the EVENT being measured  -> "deaths", "samples", "spectrometer_positive"#
#   data_type   : the unit -> counts | rates | percentages | additional_rows              #
#   geo         : where (province or health authority)                                    #
#   time_frame  : when (year)                                                             #
#   dimension   / dimension2 : up to two generic disaggregators (substance, sex, age,     #
#                 drug_type, manner, drug_category, spectrometer result, ...)              #
#                                                                                          #
# The frontend still keys its series by legacy strings ("Male Opioid_y", "20-29_y",       #
# "Fentanyl", "y"). encode_series_key / decode_series_key convert between a legacy key and #
# the (dimension, dimension2) values for the flat_series / geo_series shapes. pie_nested   #
# and regional are nested and handled directly by the persistence / reconstruction code.  #
###########################################################################################

# URL-friendly province -> the display/Region name used as the geo for province-level facts
PROVINCE_DISPLAY = {
    "british-columbia": "British Columbia",
    "alberta": "Alberta",
    "saskatchewan": "Saskatchewan",
    "manitoba": "Manitoba",
    "new-brunswick": "New Brunswick",
}

PROVINCE_GEO_TYPE = "province"
TIME_FRAME_TYPE = "year"
ADDITIONAL_DIM_TYPE = "additional_label"   # tags a table-only total row in dimension2

# substance dimension value (clean) <-> the token used in the legacy series keys
SUBSTANCE_DISPLAY = {"opioids": "Opioid", "stimulants": "Stimulant"}
SUBSTANCE_FROM_KEY = {"Opioid": "opioids", "Stimulant": "stimulants"}

_MANNER_SUFFIX = " Deaths"


def additional_metric(label):
    """Stable metric name for a table-only total row, derived from its display label."""
    return "total_" + label.replace("Total ", "").strip().replace(" ", "_").lower()


# Each visual_id maps to how its series are encoded/decoded. visual_id is unique within a
# province; shared ids (national death visuals, drug_death_heatmap) reuse one spec.
#   shape          : geo_series | flat_series | pie_nested | regional | map_none
#   metric         : the event (data_metric)
#   geo_type       : geo_type for the nested geo (geo_series / pie_nested / regional)
#   dimension2_type: the primary disaggregator type carried in the series key
#   substance      : how to fill the substance dimension (slot 1):
#                      None        -> no substance dimension
#                      "opioids"   -> constant opioids (e.g. national age, opioid-only)
#                      "from_key"  -> parsed out of the series key (national sex / manner)
#                      "lookup"    -> looked up from a {disaggregator: substance} map
#   key            : constant | suffix_y | plain | sex_substance | manner_substance
#   key_constant   : the literal series key for `constant` (e.g. "y")
VISUAL_SPECS = {
    # ---- BC Coroners (unregulated drug toxicity deaths) ----
    "drug_death_heatmap": {
        "shape": "geo_series", "geo_type": "health_authority",
        "metric": "deaths", "dimension2_type": None, "substance": None,
        "key": "constant", "key_constant": "y",
    },
    "deaths_by_sex_line": {
        "shape": "geo_series", "geo_type": "health_authority",
        "metric": "deaths", "dimension2_type": "sex", "substance": None,
        "key": "suffix_y",
    },
    "toxicity_deaths_per_drug_by_year": {
        "shape": "flat_series",
        "metric": "deaths", "dimension2_type": "drug_type", "substance": None,
        "key": "suffix_y",
    },
    "drug_toxicity_deaths_by_age": {
        "shape": "flat_series",
        "metric": "deaths", "dimension2_type": "age_group", "substance": None,
        "key": "suffix_y",
    },
    # ---- BC Centre for Substance Use (drug supply / testing) ----
    "drug_supply_by_year": {
        "shape": "flat_series",
        "metric": "samples", "dimension2_type": "drug_category", "substance": None,
        "key": "suffix_y",
    },
    "fent_benz_by_year": {
        "shape": "flat_series",
        "metric": "strip_positive", "dimension2_type": "drug", "substance": None,
        "key": "plain",
    },
    "opioid_types_by_year": {
        "shape": "flat_series",
        "metric": "spectrometer_opioid_positive", "dimension2_type": "opioid_type", "substance": None,
        "key": "plain",
    },
    "drug_supply_geographically": {
        "shape": "map_none",
    },
    "geographical_drug_supply_pie": {
        "shape": "pie_nested", "geo_type": "health_authority",
        "metric": "samples", "dimension2_type": "drug_category",
    },
    "regional_drug_supply_breakdown": {
        "shape": "regional", "geo_type": "health_authority",
        "metric": "spectrometer_positive",
        "dimension_type": "drug_category", "dimension2_type": "result",
        "grid_from": "geographical_drug_supply_pie",   # (ha, year, drug) grid incl. empty {} cells
    },
    # ---- National (Health Infobase): shared by AB / SK / MB / NB ----
    "opioid_deaths_by_age": {
        "shape": "flat_series",
        "metric": "deaths", "dimension2_type": "age_group", "substance": "opioids",
        "key": "suffix_y",
    },
    "deaths_by_age": {  # Manitoba's id for the same national age visual
        "shape": "flat_series",
        "metric": "deaths", "dimension2_type": "age_group", "substance": "opioids",
        "key": "suffix_y",
    },
    "deaths_by_drug_type": {
        "shape": "flat_series",
        "metric": "deaths", "dimension2_type": "drug_type", "substance": "lookup",
        "key": "suffix_y",
    },
    "deaths_by_sex": {
        "shape": "flat_series",
        "metric": "deaths", "dimension2_type": "sex", "substance": "from_key",
        "key": "sex_substance",
    },
    "deaths_by_manner": {
        "shape": "flat_series",
        "metric": "deaths", "dimension2_type": "manner", "substance": "from_key",
        "key": "manner_substance",
    },
    # ---- SK Coroners only ----
    "deaths_by_opioid_type": {
        "shape": "flat_series",
        "metric": "deaths", "dimension2_type": "drug_type", "substance": None,
        "key": "suffix_y",
    },
}


# ----------------------------------------------------------------------------------------- #
# Menu / presentation config (formerly the static visuals.js). Keyed by visual_id, using the
# exact key names the frontend reads. The route returns this alongside the data so the menu is
# built from the DB response, not a static file. Province-specific drill links live in
# PROVINCE_NEXT_VIS_OVERRIDE; per-province landing visuals in DEFAULT_VISUALS.
# ----------------------------------------------------------------------------------------- #
VISUAL_MENU = {
    # BC Coroners
    "drug_death_heatmap": {"type": "heatmap", "data-types": ["counts"],
        "menu-parent": "Deaths and Demographics", "menu-name": "Drug Toxicity Deaths by Health Authority",
        "level": 1, "vis-parent": None, "next-vis": None},
    "deaths_by_sex_line": {"type": "line", "data-types": ["counts", "rates"],
        "menu-parent": None, "menu-name": None,
        "level": 2, "vis-parent": "drug_death_heatmap", "next-vis": None},
    "toxicity_deaths_per_drug_by_year": {"type": "bar", "data-types": ["counts", "rates", "percentages"],
        "menu-parent": "Deaths and Demographics", "menu-name": "Unregulated Drug Toxicity Deaths by Drug Type",
        "level": 1, "vis-parent": None, "next-vis": None},
    "drug_toxicity_deaths_by_age": {"type": "line", "data-types": ["counts", "rates"],
        "menu-parent": "Deaths and Demographics", "menu-name": "Unregulated Drug Toxicity Deaths by Age Group",
        "level": 1, "vis-parent": None, "next-vis": None},
    # BC Centre for Substance Use
    "drug_supply_by_year": {"type": "line", "data-types": ["counts", "rates"],
        "menu-parent": "Drug Supply", "menu-name": "Drugs by Category",
        "level": 1, "vis-parent": None, "next-vis": None},
    "fent_benz_by_year": {"type": "line", "data-types": ["counts", "rates"],
        "menu-parent": "Drug Supply", "menu-name": "Presence of Fentanly and Benzodiazepines",
        "level": 1, "vis-parent": None, "next-vis": None},
    "opioid_types_by_year": {"type": "line", "data-types": ["counts", "rates"],
        "menu-parent": "Drug Supply", "menu-name": "Presence of Opioid Types",
        "level": 1, "vis-parent": None, "next-vis": None},
    "drug_supply_geographically": {"type": "map", "data-types": None,
        "menu-parent": "Drug Supply", "menu-name": "Drug Supply by Health Authority",
        "level": 1, "vis-parent": None, "next-vis": None},
    "geographical_drug_supply_pie": {"type": "pie", "data-types": ["counts"],
        "menu-parent": None, "menu-name": None,
        "level": 2, "vis-parent": "drug_supply_geographically", "next-vis": "regional_drug_supply_breakdown"},
    "regional_drug_supply_breakdown": {"type": "bar", "data-types": ["counts"],
        "menu-parent": None, "menu-name": None,
        "level": 3, "vis-parent": "geographical_drug_supply_pie", "next-vis": None},
    # National (shared AB / SK / MB / NB)
    "opioid_deaths_by_age": {"type": "line", "data-types": ["counts", "percentages"],
        "menu-parent": "Deaths and Demographics", "menu-name": "Opioid Deaths by Age Group",
        "level": 1, "vis-parent": None, "next-vis": None},
    "deaths_by_age": {"type": "line", "data-types": ["counts", "percentages"],
        "menu-parent": "Deaths and Demographics", "menu-name": "Opioid Deaths by Age Group",
        "level": 1, "vis-parent": None, "next-vis": None},
    "deaths_by_drug_type": {"type": "bar", "data-types": ["counts", "rates", "percentages"],
        "menu-parent": "Deaths and Demographics", "menu-name": "Opioid Deaths by Drug Type",
        "level": 1, "vis-parent": None, "next-vis": None},
    "deaths_by_sex": {"type": "line", "data-types": ["counts", "rates", "percentages"],
        "menu-parent": "Deaths and Demographics", "menu-name": "Opioid Deaths by Sex",
        "level": 1, "vis-parent": None, "next-vis": None},
    "deaths_by_manner": {"type": "line", "data-types": ["counts", "rates", "percentages"],
        "menu-parent": "Deaths and Demographics", "menu-name": "Unregulated Drug Toxicity Deaths by Manner of Death",
        "level": 1, "vis-parent": None, "next-vis": None},
    # SK Coroners
    "deaths_by_opioid_type": {"type": "bar", "data-types": ["counts", "rates", "percentages"],
        "menu-parent": "Deaths and Demographics", "menu-name": "Opioid Deaths by Drug Type",
        "level": 1, "vis-parent": None, "next-vis": None},
}

# Province-specific drill link overrides (the same visual_id chains differently per province)
PROVINCE_NEXT_VIS_OVERRIDE = {
    ("british-columbia", "drug_death_heatmap"): "deaths_by_sex_line",
    ("british-columbia", "drug_supply_geographically"): "geographical_drug_supply_pie",
}

# Per-province landing visual
DEFAULT_VISUALS = {
    "british-columbia": "drug_death_heatmap",
    "alberta": "opioid_deaths_by_age",
    "saskatchewan": "opioid_deaths_by_age",
    "manitoba": "deaths_by_age",
    "new-brunswick": "opioid_deaths_by_age",
    "nova-scotia": "drug_supply_geographically",
}


def encode_series_key(spec, key, substance_map=None):
    """Legacy series key -> (dimension_value, dimension2_value) for the substance + disaggregator dims.

    dimension (slot 1) holds the substance; dimension2 (slot 2) holds the key's disaggregator.
    """
    kind = spec["key"]
    if kind == "constant":
        return None, None
    if kind in ("suffix_y", "plain"):
        disaggregator = key[:-2] if (kind == "suffix_y" and key.endswith("_y")) else key
        substance = _resolve_substance(spec, disaggregator, substance_map)
        return substance, disaggregator
    if kind == "sex_substance":
        base = key[:-2] if key.endswith("_y") else key
        sex, token = base.rsplit(" ", 1)
        return SUBSTANCE_FROM_KEY.get(token, token), sex
    if kind == "manner_substance":
        base = key[:-len(_MANNER_SUFFIX)] if key.endswith(_MANNER_SUFFIX) else key
        manner, token = base.rsplit(" ", 1)
        return SUBSTANCE_FROM_KEY.get(token, token), manner
    raise ValueError(f"Unknown key kind: {kind}")


def decode_series_key(spec, dimension_value, dimension2_value):
    """(dimension_value=substance, dimension2_value=disaggregator) -> legacy series key."""
    kind = spec["key"]
    if kind == "constant":
        return spec["key_constant"]
    if kind == "suffix_y":
        return f"{dimension2_value}_y"
    if kind == "plain":
        return dimension2_value
    if kind == "sex_substance":
        return f"{dimension2_value} {SUBSTANCE_DISPLAY.get(dimension_value, dimension_value)}_y"
    if kind == "manner_substance":
        return f"{dimension2_value} {SUBSTANCE_DISPLAY.get(dimension_value, dimension_value)}{_MANNER_SUFFIX}"
    raise ValueError(f"Unknown key kind: {kind}")


def _resolve_substance(spec, disaggregator, substance_map):
    mode = spec.get("substance")
    if mode == "opioids":
        return "opioids"
    if mode == "lookup" and substance_map:
        return substance_map.get(disaggregator)
    return None

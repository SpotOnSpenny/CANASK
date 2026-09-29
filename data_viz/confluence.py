###########################################################################################
#                       Confluence: overlay DAS seizures with a V1 visual                  #
# Every alignment rule between the two data worlds lives here: which V1 visuals can be     #
# paired, the shared time grain, DAS coverage / censoring, the substance crosswalk, and     #
# the payload the /api/v1/confluence/data route returns. The client only draws.            #
###########################################################################################

# Python Standard Library Dependencies
import json
import os
import re
from functools import lru_cache

# External Dependency Imports
from sqlalchemy import func, or_, select

# Internal Dependency Imports
from data_viz import db
from data_viz.database.models import DasDrugCodes, DasSampleDrugs, DasSamples, DataSources
from data_viz.das_explorer import PIVOT_MAX_COLS_GEO, PIVOT_MAX_ROWS_GEO, query_pivot
from data_viz.provinces import PROVINCE_CODES, PROVINCE_LABELS
from data_viz.visual_generic import visual_block, visual_dimension_values
from data_viz.visual_query import accessible_provinces, displayable_visuals, source_block

# Which V1 shapes can be overlaid, and how the DAS half is cut for each: a health-authority
# map gets DAS by city (bubbles on the same map), a flat chart gets DAS by period (a second axis).
SUPPORTED_SHAPES = {"geo_series": "cities", "flat_series": "series"}
LEVELS = ("group", "family")
BASES = ("received", "returned")
MAX_KEYS = 8            # series mode runs one pivot per key; bound the request's cost
ALL_KEY = "all"         # the "no substance selected" pseudo-key: every sample
ALL_LABEL = "All samples"
# DAS "returned to client" lags "received" by ~38 days (p95 57), so the last two received-months
# are still filling in when a monthly file lands. Periods inside this tail are flagged, never hidden.
CENSOR_MONTHS = 2
DAS_DATASET = "id_all"
DAS_MEASURE = "samples"

CROSSWALK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_config",
    "das_substance_groups.json")

_YEAR = re.compile(r"^\d{4}$")
_MONTH = re.compile(r"^\d{4}-\d{2}$")


# --------------------------------------------------------------------------------------- #
# Substance crosswalk
# --------------------------------------------------------------------------------------- #

def validate_substance_groups(data):
    """Fail loudly on a malformed crosswalk: every group names a real family, has something to
    match on, keys don't collide across the two levels, and every term points at a known key."""
    families, groups, terms = data.get("families", {}), data.get("groups", {}), data.get("terms", {})
    collisions = set(families) & set(groups)
    if collisions:
        raise ValueError(f"substance crosswalk: keys collide across families/groups: {sorted(collisions)}")
    for key, group in groups.items():
        if group.get("family") not in families:
            raise ValueError(f"substance crosswalk: group {key!r} names unknown family {group.get('family')!r}")
        if not (group.get("codes") or group.get("subclasses")):
            raise ValueError(f"substance crosswalk: group {key!r} has neither codes nor subclasses")
    known = set(families) | set(groups)
    for term, keys in terms.items():
        for key in keys:
            if key not in known:
                raise ValueError(f"substance crosswalk: term {term!r} names unknown key {key!r}")
    return data


@lru_cache(maxsize=1)
def load_substance_groups():
    with open(CROSSWALK_PATH, encoding="utf-8") as handle:
        return validate_substance_groups(json.load(handle))


def resolve_terms(values, data=None):
    """V1 dimension values -> the crosswalk keys they name (case/whitespace-insensitive;
    unknown values are simply not matched)."""
    terms = (data or load_substance_groups())["terms"]
    keys = set()
    for value in values:
        if value is None:
            continue
        keys.update(terms.get(str(value).strip().lower(), ()))
    return keys


def keys_at_level(keys, level, data=None):
    """Normalize a mix of family/group keys to one level: at "group" a family expands to its
    member groups; at "family" a group collapses to its family. Crosswalk order, no duplicates."""
    data = data or load_substance_groups()
    families, groups = data["families"], data["groups"]
    if level not in LEVELS:
        raise ValueError(f"unknown level {level!r}")
    keys = set(keys)
    for key in keys:
        if key not in families and key not in groups:
            raise ValueError(f"unknown substance key {key!r}")
    if level == "group":
        return [g for g, spec in groups.items() if g in keys or spec["family"] in keys]
    wanted = {groups[k]["family"] if k in groups else k for k in keys}
    return [f for f in families if f in wanted]


def key_label(key, data=None):
    data = data or load_substance_groups()
    if key == ALL_KEY:
        return ALL_LABEL
    return (data["groups"].get(key) or data["families"][key])["label"]


def _group_membership(spec):
    """`sample_number IN (SELECT ... )` for one group: any drug row whose code is listed or whose
    pharmacological subclass matches, minus the excluded codes."""
    matches = []
    if spec.get("codes"):
        matches.append(DasDrugCodes.code.in_(list(spec["codes"])))
    if spec.get("subclasses"):
        matches.append(DasDrugCodes.pharm_subclass.in_(list(spec["subclasses"])))
    inner = (select(DasSampleDrugs.sample_number)
             .join(DasDrugCodes, DasDrugCodes.code == DasSampleDrugs.drug_code)
             .where(or_(*matches)))
    if spec.get("exclude_codes"):
        inner = inner.where(DasDrugCodes.code.notin_(list(spec["exclude_codes"])))
    return DasSamples.sample_number.in_(inner)


def group_clause(keys, level, data=None):
    """One SQLAlchemy boolean clause selecting the samples that contain ANY of `keys` (a family
    is the union of its groups, so everything resolves to the group level). Fed to
    query_pivot's extra_where."""
    data = data or load_substance_groups()
    if level not in LEVELS:
        raise ValueError(f"unknown level {level!r}")
    groups = keys_at_level(keys, "group", data)
    if not groups:
        raise ValueError("no substance groups to match")
    return or_(*[_group_membership(data["groups"][g]) for g in groups])


# --------------------------------------------------------------------------------------- #
# Time alignment (pure)
# --------------------------------------------------------------------------------------- #

def detect_grain(facts):
    """"year" when every main fact's time frame is YYYY, "month" when every one is YYYY-MM.
    Anything else (mixed, quarterly, empty) is unsupported and raises."""
    frames = {f["t"] for f in facts if f.get("dt") != "additional_rows"}
    if frames and all(_YEAR.match(str(t)) for t in frames):
        return "year"
    if frames and all(_MONTH.match(str(t)) for t in frames):
        return "month"
    raise ValueError("this visual's time frames can't be aligned with DAS (expected YYYY or YYYY-MM)")


def visual_periods(facts):
    return sorted({str(f["t"]) for f in facts if f.get("dt") != "additional_rows"})


def shift_month(ym, delta):
    year, month = (int(part) for part in ym.split("-"))
    index = year * 12 + (month - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def period_bounds(key, grain):
    """(first_month, last_month) a period spans, as YYYY-MM strings (which compare lexically)."""
    if grain == "year":
        return f"{key}-01", f"{key}-12"
    return key, key


def period_complete(key, grain, coverage):
    """Whether DAS has finished reporting for this period: every month of it sits inside the
    complete window, from the first return month to `complete_through`."""
    if not coverage:
        return False
    first, last = period_bounds(key, grain)
    return first >= coverage["first_month"] and last <= coverage["complete_through"]


def overlapping_periods(periods, grain, coverage):
    """The visual's periods that touch DAS coverage at all (the overlay's shared time axis):
    from `overlap_from` (which opens CENSOR_MONTHS before the first return month on the received
    basis, since those months' samples were returned later) to the last return month."""
    if not coverage:
        return []
    kept = []
    for key in periods:
        first, last = period_bounds(key, grain)
        if last >= coverage["overlap_from"] and first <= coverage["last_month"]:
            kept.append(key)
    return kept


def das_dim(grain, basis):
    return f"{grain}_{basis}"


def series_from_pivot(pivot):
    """{period: value} from a rows-only pivot; null cells are omitted, never zeroed."""
    return {row: cells[0] for row, cells in zip(pivot["rows"], pivot["cells"]) if cells[0] is not None}


def cities_from_pivot(pivot):
    """{"City, PR": {period: value}} from a rows x cols pivot; null cells omitted."""
    out = {}
    for row, cells in zip(pivot["rows"], pivot["cells"]):
        values = {col: v for col, v in zip(pivot["cols"], cells) if v is not None}
        if values:
            out[row] = values
    return out


# --------------------------------------------------------------------------------------- #
# DB-backed
# --------------------------------------------------------------------------------------- #

def supported_visuals(user, province):
    """The province's displayable visuals Confluence can pair: top-level, data-bearing, and one
    of the supported shapes (drill children and structural maps are out)."""
    return [v for v in displayable_visuals(user, province)
            if v.metric and str(v.level) == "1" and v.data_shape in SUPPORTED_SHAPES]


def das_coverage(basis):
    """DAS coverage from the return dates in das_samples, or None when nothing has been ingested:
    `first_month`..`complete_through` is the window a period must sit inside to count as
    complete, `overlap_from`..`last_month` the wider window a period must touch to appear at
    all. Return dates bound both bases -- a sample only exists in the data once it has been
    returned -- so on the received basis the CENSOR_MONTHS at EACH end are present-but-partial:
    the tail is still growing, and the months before the first return month hold only the
    samples that were returned later. Both are shown flagged, never hidden or zeroed."""
    first, last = db.session.query(func.min(DasSamples.date_returned),
                                   func.max(DasSamples.date_returned)).one()
    if first is None or last is None:
        return None
    first_month, last_month = first.strftime("%Y-%m"), last.strftime("%Y-%m")
    censor = CENSOR_MONTHS if basis == "received" else 0
    return {"first_month": first_month,
            "overlap_from": shift_month(first_month, -censor),
            "last_month": last_month,
            "complete_through": shift_month(last_month, -censor)}


def das_source():
    """The DAS DataSources row (None until the first ingest)."""
    from data_viz.das_ingest import DAS_SOURCE_NAME
    return DataSources.query.filter_by(name=DAS_SOURCE_NAME).first()


def _das_filters(code, expr):
    filters = {"province": [code]}
    if expr:
        filters["drugs_identified"] = expr
    return filters


def das_series(code, keys, level, grain, basis, expr):
    """{key: {period: samples}} -- one pivot per key (or one "all" series when no key is chosen).
    rows_cap must be the geo cap: the date-kind ordering keeps only the NEWEST rows_cap periods,
    which for the bar-chart default would silently drop a month series' oldest months."""
    series, truncated = {}, False
    for key in (keys or [ALL_KEY]):
        extra = [] if key == ALL_KEY else [group_clause([key], level)]
        pivot = query_pivot(DAS_DATASET, das_dim(grain, basis), None, _das_filters(code, expr),
                            DAS_MEASURE, rows_cap=PIVOT_MAX_ROWS_GEO, extra_where=extra)
        values = series_from_pivot(pivot)
        values.pop("Unknown", None)   # samples with no date: no period to align to
        series[key] = values
        truncated = truncated or pivot["truncated"]
    return series, truncated


def das_cities(code, keys, level, grain, basis, expr):
    """{"City, PR": {period: samples}} for the union of the chosen keys."""
    extra = [] if not keys else [group_clause(keys, level)]
    pivot = query_pivot(DAS_DATASET, "city", das_dim(grain, basis), _das_filters(code, expr),
                        DAS_MEASURE, rows_cap=PIVOT_MAX_ROWS_GEO, cols_cap=PIVOT_MAX_COLS_GEO,
                        extra_where=extra)
    return cities_from_pivot(pivot), pivot["truncated"]


def _clip(values_by_period, periods):
    return {p: v for p, v in values_by_period.items() if p in periods}


def build_confluence_payload(province, visual, keys, level, basis, expr):
    """The whole pre-aligned overlay for one (province, visual) pair. Raises ValueError for a
    visual whose time frames can't be aligned and FilterSyntaxError for a bad `expr`."""
    block = visual_block(visual)
    grain = detect_grain(block["facts"])
    coverage = das_coverage(basis)
    periods = overlapping_periods(visual_periods(block["facts"]), grain, coverage)
    period_set = set(periods)
    code = PROVINCE_CODES[province]
    mode = SUPPORTED_SHAPES[visual.data_shape]

    source = das_source()
    das = {
        "source": source_block(source) if source else None,
        "basis": basis,
        "level": level,
        "keys": [{"key": k, "label": key_label(k)} for k in (keys or [ALL_KEY])],
        "mode": mode,
        "coverage": coverage,
    }
    if mode == "series":
        series, truncated = das_series(code, keys, level, grain, basis, expr)
        das["series"] = {k: _clip(v, period_set) for k, v in series.items()}
    else:
        cities, truncated = das_cities(code, keys, level, grain, basis, expr)
        das["cities"] = {c: clipped for c, v in cities.items() if (clipped := _clip(v, period_set))}
    das["truncated"] = truncated

    return {
        "province": {"slug": province, "label": PROVINCE_LABELS[province], "code": code},
        "visual": block,
        # Labels ride in the payload so the client never reads them off live controls the user
        # may have changed since the fetch (a theme redraw must not relabel the chart).
        "visual_label": visual.menu_name or visual.name,
        "visual_metric": visual.metric,
        "grain": grain,
        "periods": [{"key": p, "das_complete": period_complete(p, grain, coverage)} for p in periods],
        "das": das,
    }


def confluence_config(user):
    """Everything the confluence.jinja boot script needs: the viewer's provinces that have at
    least one pairable visual (with the substance keys each visual's dimension values resolve
    to), plus the crosswalk's groups and families for the chip controls."""
    data = load_substance_groups()
    accessible = accessible_provinces(user)
    provinces = {}
    for slug in PROVINCE_CODES:
        if slug not in accessible:
            continue
        visuals = [{
            "id": v.name,
            "menu_name": v.menu_name or v.name,
            "chart_type": v.chart_type,
            "shape": v.data_shape,
            "metric": v.metric,
            "terms_resolved": keys_at_level(resolve_terms(visual_dimension_values(v), data), "group", data),
        } for v in supported_visuals(user, slug)]
        if visuals:
            provinces[slug] = {"label": PROVINCE_LABELS[slug], "code": PROVINCE_CODES[slug],
                               "visuals": visuals}
    return {
        "provinces": provinces,
        "groups": {k: {"label": g["label"], "family": g["family"]} for k, g in data["groups"].items()},
        "families": {k: {"label": f["label"],
                         "members": [g for g, spec in data["groups"].items() if spec["family"] == k]}
                     for k, f in data["families"].items()},
        "levels": list(LEVELS),
        "bases": list(BASES),
        "maxKeys": MAX_KEYS,
    }

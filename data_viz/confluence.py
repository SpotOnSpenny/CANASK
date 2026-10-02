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
from data_viz.visual_generic import visual_block, visual_facets
from data_viz.visual_query import accessible_provinces, displayable_visuals, source_block

# Which V1 shapes can be overlaid, and how the DAS half is cut for each: a health-authority
# map gets DAS by city (bubbles on the same map), a flat chart gets DAS by period (a second axis).
SUPPORTED_SHAPES = {"geo_series": "cities", "flat_series": "series"}
LEVELS = ("group", "family")
BASES = ("received", "returned")
# The time grains a visual can be overlaid at, in display order. A manifest's
# visual_options.time_grains narrows them; undeclared means every grain the visual's facts carry.
GRAINS = ("year", "quarter", "month")
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
_QUARTER = re.compile(r"^\d{4}-Q[1-4]$")


class UnalignableVisualError(ValueError):
    """The chosen visual can't be put on a shared time axis with DAS -- a user-facing 400, unlike
    any other ValueError out of the payload build (which is a bug and should surface as a 500)."""


# --------------------------------------------------------------------------------------- #
# Substance crosswalk
# --------------------------------------------------------------------------------------- #

def _is_str_list(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_substance_groups(data):
    """Fail loudly on a malformed crosswalk: every key has a label, every group names a real
    family and has something to match on (lists of strings, never a bare string), keys don't
    collide across the two levels or with ALL_KEY, and every term points at a known key.
    Returns the crosswalk with member-less families dropped -- there's nothing to match or show
    for them -- so a term naming one fails here like any other unknown key."""
    families, groups, terms = data.get("families", {}), data.get("groups", {}), data.get("terms", {})
    collisions = (set(families) & set(groups)) | ({ALL_KEY} & (set(families) | set(groups)))
    if collisions:
        raise ValueError(f"substance crosswalk: keys collide across families/groups/{ALL_KEY!r}: "
                         f"{sorted(collisions)}")
    for kind, specs in (("family", families), ("group", groups)):
        for key, spec in specs.items():
            if not isinstance(spec.get("label"), str) or not spec["label"]:
                raise ValueError(f"substance crosswalk: {kind} {key!r} has no label")
    for key, group in groups.items():
        if group.get("family") not in families:
            raise ValueError(f"substance crosswalk: group {key!r} names unknown family {group.get('family')!r}")
        for field in ("codes", "subclasses", "exclude_codes"):
            if field in group and not _is_str_list(group[field]):
                raise ValueError(f"substance crosswalk: group {key!r} {field} must be a list of strings")
        if not (group.get("codes") or group.get("subclasses")):
            raise ValueError(f"substance crosswalk: group {key!r} has neither codes nor subclasses")
    populated = {g["family"] for g in groups.values()}
    families = {k: f for k, f in families.items() if k in populated}
    known = set(families) | set(groups)
    for term, keys in terms.items():
        if not _is_str_list(keys):
            raise ValueError(f"substance crosswalk: term {term!r} must map to a list of keys")
        for key in keys:
            if key not in known:
                raise ValueError(f"substance crosswalk: term {term!r} names unknown key {key!r}")
    return {**data, "families": families}


@lru_cache(maxsize=1)
def load_substance_groups():
    """The validated crosswalk, cached for the process -- shared by every caller, so treat it as
    read-only."""
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
    pharmacological subclass matches, ignoring drug rows whose code is in exclude_codes (a sample
    still matches if another of its drugs does)."""
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


def group_clause(keys, data=None):
    """One SQLAlchemy boolean clause selecting the samples that contain ANY of `keys` (group or
    family keys; a family is the union of its groups, so everything resolves to the group level).
    Fed to query_pivot's extra_where."""
    data = data or load_substance_groups()
    groups = keys_at_level(keys, "group", data)
    if not groups:
        raise ValueError("no substance groups to match")
    return or_(*[_group_membership(data["groups"][g]) for g in groups])


# --------------------------------------------------------------------------------------- #
# Time alignment (pure)
# --------------------------------------------------------------------------------------- #

def detect_grain(facts):
    """"year" when every main fact's time frame is YYYY, "quarter" when every one is YYYY-Qn,
    "month" when every one is YYYY-MM. Anything else (mixed, unknown, empty) is unsupported and
    raises UnalignableVisualError."""
    frames = {f["t"] for f in facts if f.get("dt") != "additional_rows"}
    if not frames:
        raise UnalignableVisualError("This visual has no data for this province.")
    for grain, pattern in (("year", _YEAR), ("quarter", _QUARTER), ("month", _MONTH)):
        if all(pattern.match(str(t)) for t in frames):
            return grain
    raise UnalignableVisualError(
        "This visual's time frames can't be aligned with DAS (expected years, quarters or months).")


def _fact_grain(fact):
    """A fact's time_frame_type; untagged facts predate the grain toggle and are yearly."""
    return fact.get("g") or "year"


def facts_at_grain(facts, grain):
    """The facts a visual shows at one grain: those tagged with that grain (untagged = year),
    additional rows included -- the same narrowing the province page applies (factsAtGrain)."""
    return [f for f in facts if _fact_grain(f) == grain]


def _drawn(visual, fact):
    """Whether a fact is drawn (and so can make a grain available): never an additional row, and only
    counts on a heatmap, which draws counts alone (the province page's availableGrains rule)."""
    if fact.get("dt") == "additional_rows":
        return False
    return visual.chart_type != "heatmap" or fact.get("dt") == "counts"


def available_grains(visual, facts):
    """The grains this visual can be overlaid at, in GRAINS order: every grain its drawn facts
    carry (_drawn), narrowed to the manifest's visual_options.time_grains when it declares them."""
    declared = (visual.visual_options or {}).get("time_grains")
    present = {_fact_grain(f) for f in facts if _drawn(visual, f)}
    return [g for g in GRAINS if g in present and (declared is None or g in declared)]


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
    if grain == "quarter":
        year, quarter = key[:4], int(key[-1])
        return f"{year}-{(quarter - 1) * 3 + 1:02d}", f"{year}-{quarter * 3:02d}"
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
    """{period: value} from a rows-only pivot; null cells are omitted here (build_confluence_payload
    zero-fills the periods DAS covers)."""
    return {row: cells[0] for row, cells in zip(pivot["rows"], pivot["cells"]) if cells[0] is not None}


def cities_from_pivot(pivot):
    """{"City, PR": {period: value}} from a rows x cols pivot; null cells omitted (an absent
    city/period means zero samples -- a map draws no bubble for it)."""
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


def das_series(code, keys, grain, basis, expr):
    """({key: {period: samples}}, {key: undated samples}, truncated) -- one pivot per key (or one
    "all" series when no key is chosen). rows_cap must be the geo cap: the date-kind ordering
    keeps only the NEWEST rows_cap periods, which for the bar-chart default would silently drop a
    month series' oldest months."""
    series, undated, truncated = {}, {}, False
    for key in (keys or [ALL_KEY]):
        extra = [] if key == ALL_KEY else [group_clause([key])]
        pivot = query_pivot(DAS_DATASET, das_dim(grain, basis), None, _das_filters(code, expr),
                            DAS_MEASURE, rows_cap=PIVOT_MAX_ROWS_GEO, extra_where=extra)
        values = series_from_pivot(pivot)
        # Samples with no date have no period to align to; they're counted, not plotted.
        undated[key] = values.pop("Unknown", 0)
        series[key] = values
        truncated = truncated or pivot["truncated"]
    return series, undated, truncated


def das_cities(code, keys, grain, basis, expr):
    """({"City, PR": {period: samples}}, undated samples, truncated) for the union of the keys."""
    extra = [] if not keys else [group_clause(keys)]
    pivot = query_pivot(DAS_DATASET, "city", das_dim(grain, basis), _das_filters(code, expr),
                        DAS_MEASURE, rows_cap=PIVOT_MAX_ROWS_GEO, cols_cap=PIVOT_MAX_COLS_GEO,
                        extra_where=extra)
    cities = cities_from_pivot(pivot)
    undated = sum(by_period.pop("Unknown", 0) for by_period in cities.values())
    return cities, undated, pivot["truncated"]


def _clip(values_by_period, periods):
    return {p: v for p, v in values_by_period.items() if p in periods}


def build_confluence_payload(province, visual, keys, level, basis, expr, grain=None):
    """The whole pre-aligned overlay for one (province, visual) pair at one time grain (default:
    the visual's first available grain). `keys` must already be normalized to `level`
    (keys_at_level). Raises UnalignableVisualError for a grain the visual doesn't offer or whose
    time frames can't be aligned, and FilterSyntaxError for a bad `expr`.

    The payload's `visual` key is the province API's block with its facts narrowed to the grain
    (facts_at_grain);
    `grains` lists every grain the visual offers, so the client can switch between them.

    `das` always carries both `series` and `cities`; `mode` says which one is populated:
    - "series" (flat visuals): series = {key: {period: samples}} over every shared period (a
      period DAS covers with no matching samples is 0), undated = {key: samples with no date};
    - "cities" (health-authority maps): cities = {"City, PR": {period: samples}}, sparse (absent
      = 0), undated = {ALL_KEY: samples with no date}."""
    block = visual_block(visual)
    grains = available_grains(visual, block["facts"])
    if not grains:
        raise UnalignableVisualError("This visual has no data to overlay for this province.")
    grain = grain or grains[0]
    if grain not in grains:
        raise UnalignableVisualError(f"This visual has no {grain} data to overlay.")
    block["facts"] = facts_at_grain(block["facts"], grain)
    # The keys must match their tag: a year-tagged "2025-06" would otherwise be binned as a year.
    if detect_grain(block["facts"]) != grain:
        raise UnalignableVisualError(
            f"This visual's {grain} time frames can't be aligned with DAS.")
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
    das["series"], das["cities"] = {}, {}
    if mode == "series":
        series, undated, truncated = das_series(code, keys, grain, basis, expr)
        # Every shared period touches DAS coverage, so a period with no matching samples is a
        # real zero, not missing data.
        das["series"] = {k: {p: v.get(p, 0) for p in periods} for k, v in series.items()}
    else:
        cities, undated_total, truncated = das_cities(code, keys, grain, basis, expr)
        das["cities"] = {c: clipped for c, v in cities.items() if (clipped := _clip(v, period_set))}
        undated = {ALL_KEY: undated_total}
    das["undated"] = undated
    das["truncated"] = truncated

    return {
        "province": {"slug": province, "label": PROVINCE_LABELS[province], "code": code},
        "visual": block,
        # Labels ride in the payload so the client never reads them off live controls the user
        # may have changed since the fetch (a theme redraw must not relabel the chart).
        "visual_label": visual.menu_name or visual.name,
        "visual_metric": visual.metric,
        "grain": grain,
        "grains": grains,
        "periods": [{"key": p, "das_complete": period_complete(p, grain, coverage)} for p in periods],
        "das": das,
    }


def confluence_config(user):
    """Everything the confluence.jinja boot script needs: the viewer's provinces that have at
    least one pairable visual (with the substance keys each visual's dimension values resolve
    to, and the time grains it can be overlaid at), plus the crosswalk's groups and families for
    the chip controls."""
    data = load_substance_groups()
    accessible = accessible_provinces(user)
    provinces = {}
    for slug in PROVINCE_CODES:
        if slug not in accessible:
            continue
        visuals = []
        for v in supported_visuals(user, slug):
            values, grain_types = visual_facets(v)
            visuals.append({
                "id": v.name,
                "menu_name": v.menu_name or v.name,
                "chart_type": v.chart_type,
                "shape": v.data_shape,
                "metric": v.metric,
                "terms_resolved": keys_at_level(resolve_terms(values, data), "group", data),
                "grains": available_grains(v, [{"g": g, "dt": dt} for g, dt in grain_types]),
            })
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

# External Dependency Imports
from sqlalchemy import func, cast, String, case

# Internal Dependency Imports
from data_viz import db
from data_viz.database.models import DasSamples, DasSampleDrugs, DasDrugCodes, DasQuant, DasNps
from data_viz.das_filter_expr import compile_expression

# Query/serialization layer for the DAS Explorer: the app's first row-level, server-side
# paginated/filtered/sorted API (the star-schema visuals ship every fact to the client; ~8.5k+
# samples a month can't). One registry per dataset drives everything -- the sort/filter whitelist
# (client field names are looked up here, never interpolated), the Tabulator column definitions,
# and the pivot builder's dimension/measure lists -- so the three stay in agreement by construction.

# The Visuals scope key the explorer's access control hangs off (see app_config/visuals/
# nationalDAS.json): one metric-less Visuals row under this scope inherits the standard
# visibility/GroupVisuals/GroupDataSources machinery and the accessible_provinces nav gating.
DAS_SCOPE = "canada-das"

MAX_PAGE_SIZE = 200
PIVOT_MAX_ROWS = 40    # top-N by measure; the rest is clipped and flagged `truncated`
PIVOT_MAX_COLS = 15
# Ceiling for the opt-up `rows_limit` param (map charts plot every city rather than a
# top-N bar race). The clip bounds payload size, not query cost -- the GROUP BY runs
# identically either way -- so a larger cap is safe on this rate-limited, RBAC-gated API.
PIVOT_MAX_ROWS_GEO = 1000


def das_access_allowed(user):
    """Whether this viewer may see the DAS Explorer (same per-visual visibility model as every
    other visual -- enforced here server-side on the API, not just by hiding the nav link)."""
    from data_viz.visual_query import allowed_visuals
    return len(allowed_visuals(user, DAS_SCOPE)) > 0


def _yes_no(column):
    return case((column.is_(True), "Yes"), (column.is_(False), "No"))


def _month(column):
    return func.to_char(column, "YYYY-MM")


def _year(column):
    return func.to_char(column, "YYYY")


# Per-field spec: `expr` is the SELECT/filter/sort expression, `kind` picks the filter semantics
# (text -> ilike substring, select/bool -> equality, date/number -> substring on the cast text,
# which lets a "2026-06" header filter match a date column), `joins` names join tags the
# expression needs (applied once per query), `filter_values` marks a field whose distinct values
# feed a dropdown header filter.
def _field(label, expr, kind="text", joins=(), filter_values=False, width=None):
    return {"label": label, "expr": expr, "kind": kind, "joins": tuple(joins),
            "filter_values": filter_values, "width": width}


_DRUG_NAME_QUANT = func.coalesce(DasDrugCodes.display_name, DasQuant.drug_code)
_DRUG_NAME_NPS = func.coalesce(DasDrugCodes.display_name, DasNps.drug_code)

DATASETS = {
    "id_all": {
        "label": "Substances Identified",
        "model": DasSamples,
        # join tag -> how to attach it to a query rooted at the dataset's model
        "joins": {
            "drugs": lambda q: (q.join(DasSampleDrugs, DasSampleDrugs.sample_number == DasSamples.sample_number)
                                 .join(DasDrugCodes, DasDrugCodes.code == DasSampleDrugs.drug_code)),
        },
        "fields": {
            "sample_number": _field("Sample #", DasSamples.sample_number, width=110),
            "date_returned": _field("Date Returned", DasSamples.date_returned, kind="date", width=130),
            "date_received": _field("Date Received", DasSamples.date_received, kind="date", width=130),
            "province": _field("Prov/Terr", DasSamples.province, kind="select", filter_values=True, width=100),
            "city": _field("City", DasSamples.city, width=130),
            "drugs_identified": _field("Drugs Identified", DasSamples.drugs_identified),
            "description": _field("Description", DasSamples.description, width=120),
            "public_health": _field("Public Health", _yes_no(DasSamples.public_health), kind="bool", width=110),
            "contains_nps": _field("Contains NPS", _yes_no(DasSamples.contains_nps), kind="bool", width=110),
        },
        "default_sort": [DasSamples.date_returned.desc(), DasSamples.sample_number],
        "pivot_dims": {
            "province": _field("Province/Territory", DasSamples.province, kind="select"),
            # "City, PR" -- same-named cities exist in different provinces (Woodstock ON/NB,
            # Richmond BC/QC), and the city map's gazetteer is keyed on this exact form.
            # NULL city stays NULL so those rows still land in the "Unknown" bucket.
            "city": _field("City", case((DasSamples.city.is_(None), None),
                                        else_=DasSamples.city + ", " + DasSamples.province)),
            "month_returned": _field("Month Returned", _month(DasSamples.date_returned), kind="date"),
            "month_received": _field("Month Received", _month(DasSamples.date_received), kind="date"),
            "year_returned": _field("Year Returned", _year(DasSamples.date_returned), kind="date"),
            "year_received": _field("Year Received", _year(DasSamples.date_received), kind="date"),
            "public_health": _field("Public Health Sample", _yes_no(DasSamples.public_health), kind="bool"),
            "contains_nps": _field("Contains NPS", _yes_no(DasSamples.contains_nps), kind="bool"),
            "drug": _field("Drug Identified", func.coalesce(DasDrugCodes.display_name, DasSampleDrugs.drug_code),
                           joins=("drugs",)),
            "pharm_class": _field("Pharmacological Class", DasDrugCodes.pharm_class, joins=("drugs",)),
        },
        # Each sample counts once per group it lands in -- so with a drug dimension, a sample
        # containing three drugs contributes to three groups (disclosed in the page copy).
        "measures": {
            "samples": {"label": "Number of samples",
                        "expr": func.count(func.distinct(DasSamples.sample_number))},
        },
    },
    "quant": {
        "label": "Quantitation (Purity)",
        "model": DasQuant,
        "joins": {
            "codes": lambda q: q.outerjoin(DasDrugCodes, DasDrugCodes.code == DasQuant.drug_code),
        },
        "fields": {
            "sample_number": _field("Sample #", DasQuant.sample_number, width=110),
            "date_returned": _field("Date Returned", DasQuant.date_returned, kind="date", width=130),
            "date_received": _field("Date Received", DasQuant.date_received, kind="date", width=130),
            "province": _field("Prov/Terr", DasQuant.province, kind="select", filter_values=True, width=100),
            "city": _field("City", DasQuant.city, width=130),
            "drug": _field("Drug", _DRUG_NAME_QUANT, joins=("codes",)),
            "quantity": _field("Quantity", DasQuant.quantity, kind="number", width=110),
            "units": _field("Units", DasQuant.units, kind="select", filter_values=True, width=90),
            "description": _field("Description", DasQuant.description, width=120),
            "public_health": _field("Public Health", _yes_no(DasQuant.public_health), kind="bool", width=110),
        },
        "default_sort": [DasQuant.date_returned.desc(), DasQuant.sample_number],
        "pivot_dims": {
            "province": _field("Province/Territory", DasQuant.province, kind="select"),
            "month_returned": _field("Month Returned", _month(DasQuant.date_returned), kind="date"),
            "year_returned": _field("Year Returned", _year(DasQuant.date_returned), kind="date"),
            "drug": _field("Drug", _DRUG_NAME_QUANT, joins=("codes",)),
            "pharm_class": _field("Pharmacological Class", DasDrugCodes.pharm_class, joins=("codes",)),
            "units": _field("Units", DasQuant.units, kind="select"),
        },
        "measures": {
            "results": {"label": "Number of results", "expr": func.count()},
            "avg_quantity": {"label": "Average quantity", "expr": func.avg(DasQuant.quantity)},
        },
    },
    "nps": {
        "label": "New Psychoactive Substances",
        "model": DasNps,
        "joins": {
            "codes": lambda q: q.outerjoin(DasDrugCodes, DasDrugCodes.code == DasNps.drug_code),
        },
        "fields": {
            "sample_number": _field("Sample #", DasNps.sample_number, width=110),
            "finding_date": _field("Finding Date", DasNps.finding_date, kind="date", width=130),
            "province": _field("Prov/Terr", DasNps.province, kind="select", filter_values=True, width=100),
            "substance_name": _field("Substance", DasNps.substance_name),
            "other_name": _field("Other Names", DasNps.other_name),
            "description": _field("Description", DasNps.description, width=120),
        },
        "default_sort": [DasNps.finding_date.desc(), DasNps.sample_number],
        "pivot_dims": {
            "province": _field("Province/Territory", DasNps.province, kind="select"),
            "month_found": _field("Month Found", _month(DasNps.finding_date), kind="date"),
            "year_found": _field("Year Found", _year(DasNps.finding_date), kind="date"),
            "substance_name": _field("Substance", DasNps.substance_name),
            "pharm_class": _field("Pharmacological Class", DasDrugCodes.pharm_class, joins=("codes",)),
        },
        "measures": {
            "findings": {"label": "Number of findings", "expr": func.count()},
        },
    },
}


def _base_query(dataset, entities, extra_joins=()):
    """A query over `entities` rooted at the dataset's model with the named join tags applied once."""
    query = db.session.query(*entities).select_from(dataset["model"])
    for tag in dict.fromkeys(extra_joins):   # dedup, order-preserving
        query = dataset["joins"][tag](query)
    return query


def _apply_filters(query, dataset, filters):
    """Filter the query by {field: value(s)} using each field's kind: select -> any-of (IN) over
    the list of picked values, text -> the AND/OR/NOT expression language (compile_expression;
    raises FilterSyntaxError -> 400 in the routes), bool -> equality, date/number -> substring on
    the cast text. Unknown fields were already dropped by the caller (the registry is the
    whitelist)."""
    for field, value in filters.items():
        spec = dataset["fields"][field]
        expr = spec["expr"]
        if spec["kind"] == "select":
            query = query.filter(expr.in_(value))
        elif spec["kind"] == "bool":
            query = query.filter(expr == value)
        elif spec["kind"] in ("date", "number"):
            query = query.filter(cast(expr, String).ilike(f"%{value}%"))
        else:
            query = query.filter(compile_expression(expr, value))
    return query


def parse_filters(args, dataset_key):
    """Pull f_<field> params, keeping only fields the dataset registry knows. Select-kind fields
    collect every repeated value (multi-select dropdowns -> OR'd in _apply_filters); other kinds
    keep the first value."""
    fields = DATASETS[dataset_key]["fields"]
    filters = {}
    for key, values in args.lists():
        if not key.startswith("f_"):
            continue
        field = key[2:]
        if field not in fields:
            continue
        values = [v.strip() for v in values if v.strip()]
        if not values:
            continue
        filters[field] = values if fields[field]["kind"] == "select" else values[0]
    return filters


def _serialize(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def query_rows(dataset_key, page, size, sort, filters):
    """One page of explorer rows: {"data": [...], "last_page": n, "last_row": total}.

    `sort` is a list of "field.dir" strings (whitelisted against the registry), `filters` the
    output of parse_filters. The response shape is Tabulator's remote-pagination contract."""
    dataset = DATASETS[dataset_key]
    fields = dataset["fields"]
    size = max(1, min(int(size), MAX_PAGE_SIZE))
    page = max(1, int(page))

    joins = [tag for spec in fields.values() for tag in spec["joins"]]
    query = _base_query(dataset, [spec["expr"] for spec in fields.values()], joins)
    query = _apply_filters(query, dataset, filters)

    order = []
    for item in sort:
        field, _, direction = item.partition(".")
        if field in fields:
            expr = fields[field]["expr"]
            order.append(expr.desc() if direction == "desc" else expr.asc())
    query = query.order_by(*(order or dataset["default_sort"]))

    total = query.order_by(None).count()
    rows = query.offset((page - 1) * size).limit(size).all()
    names = list(fields.keys())
    return {
        "data": [{name: _serialize(value) for name, value in zip(names, row)} for row in rows],
        "last_page": max(1, -(-total // size)),
        "last_row": total,
    }


def query_pivot(dataset_key, rows_field, cols_field, filters, measure_key, rows_cap=PIVOT_MAX_ROWS):
    """Pivot aggregation: GROUP BY the chosen dimension(s), aggregate the chosen measure.

    Returns {"rows": [...], "cols": [...], "cells": [[value|None per col] per row], "measure":
    label, "truncated": bool}. Categories beyond the top PIVOT_MAX_* by measure total are clipped
    (flagged) so a city- or drug-grained pivot can't ship thousands of traces. `rows_cap` lets the
    map charts opt up to PIVOT_MAX_ROWS_GEO (a map plots every place, not a top-N)."""
    dataset = DATASETS[dataset_key]
    dims = dataset["pivot_dims"]
    row_spec = dims[rows_field]
    col_spec = dims[cols_field] if cols_field else None
    measure = dataset["measures"][measure_key]

    entities = [row_spec["expr"].label("r")]
    joins = list(row_spec["joins"])
    if col_spec is not None:
        entities.append(col_spec["expr"].label("c"))
        joins += list(col_spec["joins"])
    entities.append(measure["expr"].label("v"))
    # Filters may reference joined fields (e.g. quant's resolved drug name), so bring their joins too.
    joins += [tag for field in filters for tag in dataset["fields"][field]["joins"]]
    query = _apply_filters(_base_query(dataset, entities, joins), dataset, filters)
    query = query.group_by("r", "c") if col_spec is not None else query.group_by("r")
    results = query.all()

    def key(value):
        return "Unknown" if value is None else str(value)

    row_totals, col_totals, cells = {}, {}, {}
    for result in results:
        r = key(result.r)
        c = key(result.c) if col_spec is not None else None
        value = float(result.v) if result.v is not None else 0
        row_totals[r] = row_totals.get(r, 0) + value
        if c is not None:
            col_totals[c] = col_totals.get(c, 0) + value
        cells[(r, c)] = value

    # Time-like dims read best in chronological order; everything else by measure, largest first.
    def ordered(totals, spec, cap):
        if spec["kind"] == "date":
            keys = sorted(totals)[-cap:]  # over the cap, favor the most recent periods
            return keys, len(totals) > cap
        keys = sorted(totals, key=totals.get, reverse=True)
        return keys[:cap], len(keys) > cap

    row_keys, rows_truncated = ordered(row_totals, row_spec, rows_cap)
    if col_spec is not None:
        col_keys, cols_truncated = ordered(col_totals, col_spec, PIVOT_MAX_COLS)
    else:
        col_keys, cols_truncated = [None], False

    return {
        "rows": row_keys,
        "cols": [] if col_spec is None else col_keys,
        "cells": [[cells.get((r, c)) for c in col_keys] for r in row_keys],
        "measure": measure["label"],
        "truncated": rows_truncated or cols_truncated,
    }


def explorer_config():
    """Everything the das_explorer.jinja boot script needs, serialized once at page render:
    Tabulator column defs, pivot dimension/measure lists, and dropdown filter values."""
    config = {"datasets": {}}
    for dataset_key, dataset in DATASETS.items():
        columns = []
        for field, spec in dataset["fields"].items():
            column = {"title": spec["label"], "field": field, "headerFilter": "input"}
            if spec["kind"] == "select" and spec["filter_values"]:
                # Multi-select checkbox dropdown; picked values are OR'd server-side (IN).
                # headerFilterLiveFilter must stay off: the live-filter keyup path would commit
                # the comma-joined display string instead of the array of values.
                values = [v[0] for v in _base_query(dataset, [spec["expr"]], spec["joins"])
                          .filter(spec["expr"].isnot(None)).distinct().order_by(spec["expr"]).all()]
                column["headerFilter"] = "list"
                column["headerFilterParams"] = {"values": values, "multiselect": True}
                column["headerFilterLiveFilter"] = False
            elif spec["kind"] == "bool":
                column["headerFilter"] = "list"
                column["headerFilterParams"] = {"values": ["", "Yes", "No"], "clearable": True}
            elif spec["kind"] == "text":
                column["headerFilterPlaceholder"] = "e.g. (a AND b) OR c"
            if spec["width"]:
                column["width"] = spec["width"]
            columns.append(column)
        config["datasets"][dataset_key] = {
            "label": dataset["label"],
            "columns": columns,
            # Per-field filter kinds, shipped separately from the column defs (unknown column-def
            # keys make Tabulator log warnings). The client uses these to know which inputs get
            # the expression validator and which lists are multiselect.
            "kinds": {field: spec["kind"] for field, spec in dataset["fields"].items()},
            # kind lets the client tell date dims (valid slider splits for map charts) from the rest.
            "pivotDims": [{"field": f, "label": s["label"], "kind": s["kind"]}
                          for f, s in dataset["pivot_dims"].items()],
            "measures": [{"field": f, "label": m["label"]} for f, m in dataset["measures"].items()],
        }
    return config

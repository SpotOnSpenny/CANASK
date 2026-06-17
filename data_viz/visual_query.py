###########################################################################################
#                          Reconstruction of the frontend JSON                            #
# build_province_payload(province) reads the normalized Visuals / VisualQuery / DataPoints #
# rows and rebuilds the exact per-visual JSON shapes the frontend expects -- i.e. the      #
# inverse of generateVisuals.export_data_to_db(). Both sides share data_viz.visual_specs   #
# so the encode/decode stay in lock-step.                                                  #
###########################################################################################

import re
from collections import defaultdict

from data_viz.database.models import (Visuals, VisualQuery, DataPoints, DataSources,
                                       UserGroups, GroupVisuals, GroupDataSources)
from data_viz.auth.role_hierarchy import ROLE_HIERARCHY
from data_viz import visual_specs as vs


def _viewer_context(user):
    """Precompute, once per request, what `user` is allowed to leverage for the per-visual visibility
    test below: whether they are signed in / a site admin, the data_source_ids they own (Data Owner+
    in a group with the source), and the visual ids granted to their groups (GroupVisuals)."""
    signed_in = user is not None and getattr(user, "is_authenticated", False)
    site_admin = signed_in and getattr(user, "site_admin", False)
    owned_sources, granted = set(), set()
    if signed_in and not site_admin:
        memberships = UserGroups.query.filter_by(user_id=user.id).all()
        group_ids = [m.group_id for m in memberships]
        if group_ids:
            granted = {gv.visual_id for gv in
                       GroupVisuals.query.filter(GroupVisuals.group_id.in_(group_ids)).all()}
        owner_level = ROLE_HIERARCHY["Data Owner"]
        owner_group_ids = [m.group_id for m in memberships
                           if ROLE_HIERARCHY.get(m.role, -1) >= owner_level]
        if owner_group_ids:
            owned_sources = {gds.data_source_id for gds in GroupDataSources.query.filter(
                GroupDataSources.group_id.in_(owner_group_ids)).all()}
    return signed_in, site_admin, owned_sources, granted


def _can_see(visual, signed_in, site_admin, owned_sources, granted):
    """Whether a single visual passes its own visibility level for this viewer (ignoring the drill
    hierarchy, which allowed_visuals enforces separately)."""
    if visual.visibility == "public":
        return True
    if not signed_in:
        return False
    if site_admin:
        return True
    # A Data Owner of the visual's source sees it at any level (private or group).
    if visual.data_source_id is not None and visual.data_source_id in owned_sources:
        return True
    if visual.visibility == "group":
        return visual.id in granted
    return False   # private, and the viewer is neither site admin nor an owner of the source


def allowed_visuals(user, province):
    """The Visuals in `province` a user may see, per each visual's visibility level (public / group /
    private). A drill-child is only reachable if its parent chain is also visible, so any visual whose
    in-province parent was filtered out is pruned (no orphaned children in the menu)."""
    visuals = Visuals.query.filter_by(province=province).all()
    signed_in, site_admin, owned_sources, granted = _viewer_context(user)
    visible = {v.id for v in visuals
               if _can_see(v, signed_in, site_admin, owned_sources, granted)}

    by_name = {v.name: v for v in visuals}
    changed = True
    while changed:
        changed = False
        for v in visuals:
            if v.id in visible and v.vis_parent_name:
                parent = by_name.get(v.vis_parent_name)
                if parent is None or parent.id not in visible:
                    visible.discard(v.id)
                    changed = True
    return [v for v in visuals if v.id in visible]


def accessible_provinces(user):
    """The set of provinces where the user can see at least one visual -- used to gate the province
    nav links so users only see provinces they have access to."""
    provinces = {row[0] for row in Visuals.query.with_entities(Visuals.province).distinct().all()}
    return {p for p in provinces if allowed_visuals(user, p)}


def build_province_payload(province, user=None):
    """Return {visual_id: block} for a province (filtered to what `user` may see), shaped identically
    to a slice of visual_data.json."""
    visuals = allowed_visuals(user, province)
    by_name = {v.name: v for v in visuals}
    payload = {}
    for visual in visuals:
        spec = vs.VISUAL_SPECS[visual.name]
        block = _base_block(visual)
        shape = visual.data_shape
        preds = _predicates(visual.id)
        if shape == "flat_series":
            _build_flat(block, visual, spec, preds)
        elif shape == "geo_series":
            _build_geo(block, visual, spec)
        elif shape == "pie_nested":
            _build_pie(block, visual, spec)
        elif shape == "regional":
            _build_regional(block, visual, spec, by_name)
        # map_none -> visual_options only (already in base block)
        payload[visual.name] = block
    return payload


def build_province_menu(province, user=None):
    """Return the menu/presentation config for a province plus its default visual, read from the
    Visuals table and filtered to what `user` may see. Replaces the static visuals.js."""
    allowed = allowed_visuals(user, province)
    config = {}
    for visual in allowed:
        config[visual.name] = {
            "type": visual.chart_type,
            "data-types": visual.data_types.split(",") if visual.data_types else None,
            "menu-parent": visual.menu_parent,
            "menu-name": visual.menu_name,
            "level": int(visual.level) if visual.level is not None else None,
            "vis-parent": visual.vis_parent_name,
            "next-vis": visual.next_vis_name,
        }
    # Landing visual: the flagged default if the user may see it, else the first allowed level-1 visual.
    default = next((v.name for v in allowed if v.is_default), None)
    if default is None:
        default = next((v.name for v in allowed if v.level == "1"),
                       allowed[0].name if allowed else None)
    return {"config": config, "default": default}


# --------------------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------------------- #

def _predicates(visual_id):
    preds = defaultdict(list)
    for row in VisualQuery.query.filter_by(for_visual_id=visual_id).all():
        preds[row.filter_type].append(row.filter_value)
    return preds


def _base_block(visual):
    block = {}
    # Maps carry a data_source_id only for ownership/RBAC (drill-heading maps inherit their chain's
    # source); their payload is visual_options-only, so don't emit a data_source block for them.
    if visual.data_source_id and visual.data_shape != "map_none":
        source = DataSources.query.get(visual.data_source_id)
        if source:
            block["data_source"] = {
                "name": source.name,
                "about": source.about,
                "link": source.link,
                "last_updated": source.last_updated_str,
                "data_until": source.data_until_str,
            }
    if visual.visual_options is not None:
        block["visual_options"] = visual.visual_options
    return block


def _value(point):
    """Reproduce the original JSON value type: text cell verbatim, else an int/float."""
    if point.data_value_text is not None:
        return point.data_value_text
    if point.data_value is None:
        return None
    if point.data_value.is_integer():
        return int(point.data_value)
    return point.data_value


def _year_key(year):
    match = re.match(r"\d+", str(year))
    return (int(match.group()), str(year)) if match else (10 ** 9, str(year))


def _sorted_years(years):
    return sorted(years, key=_year_key)


# --------------------------------------------------------------------------------------- #
# per-shape reconstruction
# --------------------------------------------------------------------------------------- #

def _build_flat(block, visual, spec, preds):
    dim2_type = spec.get("dimension2_type")
    query = DataPoints.query.filter_by(
        data_source_id=visual.data_source_id, geo=preds["geo"][0], data_metric=spec["metric"])
    query = query.filter(DataPoints.dimension2_type == dim2_type) if dim2_type \
        else query.filter(DataPoints.dimension2_type.is_(None))

    series = defaultdict(lambda: defaultdict(dict))   # dtype -> series_key -> {year: value}
    years = defaultdict(set)
    for point in query.all():
        key = vs.decode_series_key(spec, point.dimension_value, point.dimension2_value)
        series[point.data_type][key][point.time_frame] = _value(point)
        years[point.data_type].add(point.time_frame)

    data = {}
    for dtype, series_map in series.items():
        out = {"x": _sorted_years(years[dtype])}
        for key, year_vals in series_map.items():
            # Each series keeps its OWN span of years (the source leaves some ragged, e.g. a
            # stimulant series shorter than the year axis); don't pad to the union.
            out[key] = [year_vals[year] for year in _sorted_years(year_vals)]
        data[dtype] = out
    if data:
        block["data"] = data

    _build_additional(block, visual, preds)


def _build_geo(block, visual, spec):
    dim2_type = spec.get("dimension2_type")
    query = DataPoints.query.filter_by(
        data_source_id=visual.data_source_id, geo_type=spec["geo_type"], data_metric=spec["metric"])
    query = query.filter(DataPoints.dimension2_type == dim2_type) if dim2_type \
        else query.filter(DataPoints.dimension2_type.is_(None))

    # dtype -> geo -> series_key -> {year: value}
    series = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    years = defaultdict(lambda: defaultdict(set))
    for point in query.all():
        key = vs.decode_series_key(spec, point.dimension_value, point.dimension2_value)
        series[point.data_type][point.geo][key][point.time_frame] = _value(point)
        years[point.data_type][point.geo].add(point.time_frame)

    data = {}
    for dtype, geo_map in series.items():
        data[dtype] = {}
        for geo, series_map in geo_map.items():
            out = {"x": _sorted_years(years[dtype][geo])}
            for key, year_vals in series_map.items():
                out[key] = [year_vals[year] for year in _sorted_years(year_vals)]
            data[dtype][geo] = out
    block["data"] = data


def _build_additional(block, visual, preds):
    add_metrics = preds.get("additional_metric", [])
    if not add_metrics:
        return
    points = DataPoints.query.filter(
        DataPoints.data_source_id == visual.data_source_id,
        DataPoints.geo == preds["geo"][0],
        DataPoints.data_type == "additional_rows",
        DataPoints.data_metric.in_(add_metrics)).all()
    rows = defaultdict(dict)   # label -> {year: value}
    years = set()
    for point in points:
        rows[point.dimension_value][point.time_frame] = _value(point)
        years.add(point.time_frame)
    ordered_years = _sorted_years(years)
    additional = {label: [year_vals.get(year) for year in ordered_years] for label, year_vals in rows.items()}
    if additional:
        block["additional_rows"] = additional


def _build_pie(block, visual, spec):
    counts = defaultdict(lambda: defaultdict(dict))   # ha -> year -> {drug: value}
    for point in DataPoints.query.filter_by(
            data_source_id=visual.data_source_id, geo_type=spec["geo_type"],
            data_metric=spec["metric"], data_type="counts").all():
        counts[point.geo][point.time_frame][point.dimension2_value] = _value(point)

    # Total Samples (table-only) per ha/year
    totals = defaultdict(dict)
    for point in DataPoints.query.filter_by(
            data_source_id=visual.data_source_id, geo_type=spec["geo_type"],
            data_type="additional_rows").all():
        totals[point.geo][point.time_frame] = _value(point)

    drug_order = _first_seen_drugs(counts)
    counts_out, tabular = {}, {}
    for geo, year_map in counts.items():
        ordered_years = _sorted_years(year_map)
        counts_out[geo] = {year: dict(year_map[year]) for year in ordered_years}
        tab = {drug: [year_map[year].get(drug, 0) for year in ordered_years] for drug in drug_order}
        tab["Total Samples"] = [totals.get(geo, {}).get(year, 0) for year in ordered_years]
        tabular[geo] = tab

    block["data"] = {"counts": counts_out}
    block["tabular_data"] = tabular


def _build_regional(block, visual, spec, by_name):
    # spectrometer results -> ha -> year -> drug -> {f"{result}_y": [count]}
    results = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    for point in DataPoints.query.filter_by(
            data_source_id=visual.data_source_id, geo_type=spec["geo_type"],
            data_metric=spec["metric"], data_type="counts").all():
        results[point.geo][point.time_frame][point.dimension_value][f"{point.dimension2_value}_y"] = [_value(point)]

    grid = _pie_grid(by_name.get(spec.get("grid_from")))
    counts, rates = {}, {}
    for geo, year_map in grid.items():
        counts[geo], rates[geo] = {}, {}
        for year, drugs in year_map.items():
            counts[geo][year] = {drug: results.get(geo, {}).get(year, {}).get(drug, {}) for drug in drugs}
            rates[geo][year] = {}
    block["data"] = {"counts": counts, "rates": rates}
    block["tabular_data"] = {}   # regional carries an always-empty tabular_data block


def _pie_grid(pie_visual):
    """Reproduce the (ha -> year -> [drugs]) grid (incl. empty cells) from the pie visual's facts."""
    if pie_visual is None:
        return {}
    spec = vs.VISUAL_SPECS[pie_visual.name]
    counts = defaultdict(lambda: defaultdict(list))
    seen = defaultdict(set)
    for point in DataPoints.query.filter_by(
            data_source_id=pie_visual.data_source_id, geo_type=spec["geo_type"],
            data_metric=spec["metric"], data_type="counts").all():
        if point.dimension2_value not in seen[(point.geo, point.time_frame)]:
            counts[point.geo][point.time_frame].append(point.dimension2_value)
            seen[(point.geo, point.time_frame)].add(point.dimension2_value)
    grid = {}
    for geo, year_map in counts.items():
        grid[geo] = {year: year_map[year] for year in _sorted_years(year_map)}
    return grid


def _first_seen_drugs(counts):
    order = []
    seen = set()
    for year_map in counts.values():
        for drug_map in year_map.values():
            for drug in drug_map:
                if drug not in seen:
                    seen.add(drug)
                    order.append(drug)
    return order

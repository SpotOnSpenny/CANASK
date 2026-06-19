###########################################################################################
#                      Per-visual access (RBAC/visibility) + menu config                  #
# allowed_visuals() filters a province's Visuals by each visual's visibility level and the #
# drill hierarchy; build_province_menu() serves the menu config. The visual data itself is #
# served as normalized facts by visual_generic.build_province_generic().                  #
###########################################################################################

from collections import defaultdict

from data_viz.database.models import (Visuals, VisualQuery, DataPoints, DataSources,
                                       UserGroups, GroupVisuals, GroupDataSources)
from data_viz.auth.role_hierarchy import ROLE_HIERARCHY


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


def _prune_orphans(visuals, kept):
    """Remove from `kept` (a set of visual ids) any drill-child whose in-province parent chain is not
    also kept, so a child is never shown without its parent. Mutates and returns `kept`."""
    by_name = {v.name: v for v in visuals}
    changed = True
    while changed:
        changed = False
        for v in visuals:
            if v.id in kept and v.vis_parent_name:
                parent = by_name.get(v.vis_parent_name)
                if parent is None or parent.id not in kept:
                    kept.discard(v.id)
                    changed = True
    return kept


def _filter_visible(visuals, ctx):
    """Filter one province's Visuals to the visible set, given a precomputed viewer context `ctx`
    (from _viewer_context): apply each visual's visibility level, then prune any drill-child whose
    in-province parent chain was filtered out (no orphaned children in the menu)."""
    visible = {v.id for v in visuals if _can_see(v, *ctx)}
    _prune_orphans(visuals, visible)
    return [v for v in visuals if v.id in visible]


def allowed_visuals(user, province):
    """The Visuals in `province` a user may see, per each visual's visibility level (public / group /
    private). A drill-child is only reachable if its parent chain is also visible, so any visual whose
    in-province parent was filtered out is pruned (no orphaned children in the menu)."""
    visuals = Visuals.query.filter_by(province=province).all()
    return _filter_visible(visuals, _viewer_context(user))


def _visual_has_data(visual):
    """Whether a visual has anything to show. Structural visuals (no metric -- maps / drill headings /
    sourceless scaffolding) always count. A data-bearing visual counts only if it has at least one
    REPORTED, non-zero fact -- matching the frontend, which hides series that are entirely zero,
    suppressed, or not reported. So e.g. a territory whose every breakdown is suppressed is dropped
    from the menu instead of opening to an empty chart."""
    if not visual.metric:
        return True
    preds = _predicates(visual.id)
    if visual.geo_type == "province" and not preds.get("geo"):
        return False   # province-level visual not scoped to a geo -> no data of its own
    query = DataPoints.query.filter(
        DataPoints.data_source_id == visual.data_source_id,
        DataPoints.data_metric == visual.metric,
        DataPoints.data_type != "additional_rows",
        DataPoints.data_value.isnot(None),   # suppressed / not-reported have no numeric value
        DataPoints.data_value != 0,          # all-zero series are hidden, like the frontend does
    )
    if visual.geo_type:
        query = query.filter(DataPoints.geo_type == visual.geo_type)
    if visual.dimension2_type is not None:
        query = query.filter(DataPoints.dimension2_type == visual.dimension2_type)
    else:
        query = query.filter(DataPoints.dimension2_type.is_(None))
    if visual.geo_type == "province":
        query = query.filter(DataPoints.geo == preds["geo"][0])
    return query.first() is not None


def displayable_visuals(user, province):
    """`allowed_visuals` (visibility) further filtered to the visuals that actually have data to show
    (structural maps kept), with orphaned drill-children pruned. The single source the menu and the
    fact payload share, so they never disagree -- a visual the menu hides is never in the data."""
    allowed = allowed_visuals(user, province)
    kept = {v.id for v in allowed if _visual_has_data(v)}
    _prune_orphans(allowed, kept)
    return [v for v in allowed if v.id in kept]


def accessible_provinces(user):
    """The set of provinces where the user can see at least one visual that has data -- used to gate
    the province nav links so a province whose every visual is hidden (RBAC) or empty (all
    suppressed / not reported, e.g. a small territory) doesn't show a dead link. Runs on every request
    (nav context processor): the viewer context is derived once and all visuals are loaded in one
    query; the per-visual data check short-circuits on the first visual with data (and a structural
    map returns immediately without a query)."""
    ctx = _viewer_context(user)
    by_province = defaultdict(list)
    for visual in Visuals.query.all():
        by_province[visual.province].append(visual)
    return {province for province, visuals in by_province.items()
            if any(_visual_has_data(v) for v in _filter_visible(visuals, ctx))}


def _menu_level(visual):
    """Parse a visual's free-form `level` string into an int, raising a clear, visual-identifying
    error on a manifest typo (e.g. "1a") instead of a bare ValueError mid-request."""
    if visual.level is None:
        return None
    try:
        return int(visual.level)
    except (TypeError, ValueError):
        raise ValueError(
            f"Visual {visual.province}/{visual.name} has a non-numeric level '{visual.level}'; "
            f"fix the manifest entry.")


def build_province_menu(province, user=None):
    """Return the menu/presentation config for a province plus its default visual, read from the
    Visuals table and filtered to what `user` may see and to visuals that have data. Replaces the
    static visuals.js."""
    allowed = displayable_visuals(user, province)
    config = {}
    for visual in allowed:
        config[visual.name] = {
            "type": visual.chart_type,
            "slug": visual.slug,
            "data-types": visual.data_types.split(",") if visual.data_types else None,
            "menu-parent": visual.menu_parent,
            "menu-name": visual.menu_name,
            "level": _menu_level(visual),
            "vis-parent": visual.vis_parent_name,
            "next-vis": visual.next_vis_name,
        }
    # Landing visual: the flagged default if the user may see it, else the first allowed level-1 visual.
    default = next((v.name for v in allowed if v.is_default), None)
    if default is None:
        default = next((v.name for v in allowed if v.level == "1"),
                       allowed[0].name if allowed else None)
    # Top-level menu dropdowns, derived from the level-1 visuals' menu_parent (ordered by first
    # appearance) -- the frontend builds its menu categories from this instead of a hard-coded list.
    categories = []
    for visual in allowed:
        if visual.level == "1" and visual.menu_parent and visual.menu_parent not in categories:
            categories.append(visual.menu_parent)
    return {"config": config, "default": default, "categories": categories}


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



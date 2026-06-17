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



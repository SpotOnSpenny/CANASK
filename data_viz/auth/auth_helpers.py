# Standard Library Imports
import re

# External Imports
from bcrypt import hashpw, gensalt, checkpw
from flask import flash, has_request_context

# Internal Imports
from data_viz.database import db
from data_viz.database.models import User, Invites, UserGroups, Groups, UserActivity, DataSources, GroupDataSources, Visuals, GroupVisuals, RemovalPassword, VISUAL_VISIBILITY
from data_viz.auth.role_hierarchy import ROLE_HIERARCHY

def create_user(email, username, password, invited_by = None, status = User.STATUS_ACTIVE, site_admin = False):
    password_hash = hashpw(password.encode('utf-8'), gensalt()).decode('utf-8')
    user = User(
        email = email,
        username = username, 
        password_hash = password_hash,
        status = status, 
        invited_by = invited_by,
        site_admin = site_admin
    )

    db.session.add(user)
    db.session.commit()

    if invited_by:
        invite = Invites.query.filter_by(email = email, status = "pending").first()
        if invite:
            invite.status = "accepted"
            db.session.add(invite)
        inviter = User.query.get(invited_by)
        details = f"Account created for {username} with email {email} with invite from {inviter.username}."
    else:
        details = f"Account created for {username} with email {email} without invite."

    activity = UserActivity(
        user_id = user.id,
        activity_type = "creation",
        activity_target_type = "account",
        details = details
    )

    db.session.add(activity)
    db.session.commit()
    return user

def create_group(name, created_by, description = None):
    group = Groups(
        name = name,
        description = description,
        created_by = created_by
    )

    db.session.add(group)
    db.session.commit()

    created_by_user = User.query.get(created_by)
    activity = UserActivity(
        user_id = created_by,
        activity_type = "creation",
        activity_target_type = "group",
        activity_target_id = group.id,
        details = f"Group {name} created by {created_by_user.username}."
    )

    db.session.add(activity)
    db.session.commit()
    return group

def assign_group(user_id, group_id, role, assigned_by = None, remove = False):
    existing_membership = UserGroups.query.filter_by(user_id = user_id, group_id = group_id).first()
    user = User.query.get(user_id)
    group = Groups.query.get(group_id)
    assigner = User.query.get(assigned_by) if assigned_by else None

    try:
        if remove and not existing_membership:
            raise ValueError("User does not belong to the specified group. Unable to remove non-existent membership.")
        if remove and existing_membership:
            db.session.delete(existing_membership)
            db.session.commit()
            if assigner:
                details = f"User {user.username} removed from Group {group.name} by {assigner.username}." 
            else:
                details = f"User ID {user_id} removed from Group ID {group_id} without assigner."
            activity = UserActivity(
                user_id = user_id,
                activity_type = "group_removal",
                activity_target_type = "user", 
                details = details
            )
            
            db.session.add(activity)
            db.session.commit()
            return None
    except Exception as e:
        print(f"Error in assign_group: {e}")
        if has_request_context():
            flash(f"An error occured while removing group access: {str(e)}", "danger")
        raise e

    try:
        if not remove and existing_membership and existing_membership.role == role:
            raise ValueError("User already has the specified role in this group.")

        if existing_membership and not remove:
            existing_membership.role = role
            membership = existing_membership
        else:
            membership = UserGroups(
                user_id = user_id, 
                group_id = group_id,
                role = role, 
            )

        user = User.query.get(user_id)
        group = Groups.query.get(group_id)
        if assigned_by:
            assigner = User.query.get(assigned_by)
            details = f"User {user.username} assigned to Group {group.name} with role {role} by {assigner.username}." 
        else:
            details = f"User ID {user_id} assigned to Group ID {group_id} with role {role} without assigner."
        activity = UserActivity(
            user_id = user_id,
            activity_type = "group_assignment",
            activity_target_type = "user", 
            details = details
        )
    except Exception as e:
        print(f"Error in assign_group: {e}")
        if has_request_context():
            flash(f"An error occured while assigning group access: {str(e)}", "danger")
        raise e
    
    db.session.add(activity)
    db.session.add(membership)
    db.session.commit()
    return membership

def assign_site_admin(user_id, remove = False, assigned_by = None):
    user = User.query.get(user_id)
    if not user:
        raise ValueError("User not found. Unable to assign site admin role.")

    user.site_admin = not remove

    verb = "removed from" if remove else "granted to"
    if assigned_by:
        assigner = User.query.get(assigned_by)
        details = f"Site admin privileges {verb} user {user.username} by {assigner.username}."
    else:
        details = f"Site admin privileges {verb} user ID {user_id} without assigner."

    activity = UserActivity(
        user_id = user_id,
        activity_type = "site_admin_assignment",
        activity_target_type = "user", 
        details = details
    )

    db.session.add(activity)
    db.session.add(user)
    db.session.commit()
    return user

def active_site_admins():
    return User.query.filter_by(site_admin = True, status = User.STATUS_ACTIVE).all()

def is_last_active_site_admin(user_id):
    return not User.query.filter(
        User.site_admin == True,
        User.status == User.STATUS_ACTIVE,
        User.id != user_id
    ).count()

def removal_password_is_set():
    return RemovalPassword.query.get(1) is not None

def check_removal_password(candidate):
    row = RemovalPassword.query.get(1)
    if not row or not candidate:
        return False
    return checkpw(candidate.encode("utf-8"), row.password_hash.encode("utf-8"))

def set_removal_password(new_password, changed_by = None, ip_address = None):
    # Single-row upsert pinned to id=1, so two concurrent initial sets collide on the PK and
    # one fails loudly instead of silently leaving two rows with divergent hashes. Strength
    # validation is the callers' job (rotation route / CLI); verification here is bcrypt-compare only.
    row = RemovalPassword.query.get(1) or RemovalPassword(id = 1)
    row.password_hash = hashpw(new_password.encode("utf-8"), gensalt()).decode("utf-8")
    row.updated_at = db.func.current_timestamp()
    row.updated_by = changed_by

    if changed_by:
        rotator = User.query.get(changed_by)
        details = f"Removal password rotated by {rotator.username}."
    else:
        details = "Removal password rotated by the CLI (break-glass)."

    activity = UserActivity(
        user_id = changed_by,
        activity_type = "removal_password_rotated",
        activity_target_type = "removal_password",
        details = details,
        ip_address = ip_address
    )

    db.session.add(row)
    db.session.add(activity)
    db.session.commit()
    return row

def deactivate_user(user_id, deactivated_by = None, ip_address = None):
    user = User.query.get(user_id)
    if not user:
        raise ValueError("User not found. Unable to deactivate account.")

    user.status = User.STATUS_DEACTIVATED
    # There is no reactivation flow, so don't leave a dormant admin bit behind.
    user.site_admin = False

    if deactivated_by:
        actor = User.query.get(deactivated_by)
        details = f"Account for {user.username} deactivated by {actor.username}."
    else:
        details = f"Account for {user.username} deactivated without actor."

    activity = UserActivity(
        user_id = deactivated_by,
        activity_type = "account_deactivated",
        activity_target_type = "user",
        activity_target_id = user.id,
        details = details,
        ip_address = ip_address
    )

    db.session.add(activity)
    db.session.add(user)
    db.session.commit()
    return user

def get_user_groups(user):
    if user.site_admin:
        return Groups.query.all()
    else:
        memberships = UserGroups.query.filter(
            UserGroups.user_id == user.id
        ).all()

        group_ids = [membership.group_id for membership in memberships]
        return Groups.query.filter(Groups.id.in_(group_ids)).all()

def get_manageable_users(manager):
    """Users the manager can see/manage: everyone for a site admin, otherwise every
    user who shares at least one group the manager belongs to."""
    if manager.site_admin:
        return User.query.all()

    group_ids = [group.id for group in get_user_groups(manager)]
    if not group_ids:
        return []

    user_ids = db.session.query(UserGroups.user_id).filter(
        UserGroups.group_id.in_(group_ids)
    ).distinct()
    return User.query.filter(User.id.in_(user_ids)).all()

def get_manageable_groups(user):
    """Groups whose data-source access this user may edit: every group for a site admin,
    otherwise groups where the user is at least a Data Owner."""
    if user.site_admin:
        return Groups.query.order_by(Groups.name).all()

    owner_level = ROLE_HIERARCHY["Data Owner"]
    memberships = UserGroups.query.filter_by(user_id = user.id).all()
    group_ids = [m.group_id for m in memberships if ROLE_HIERARCHY.get(m.role, -1) >= owner_level]
    if not group_ids:
        return []
    return Groups.query.filter(Groups.id.in_(group_ids)).order_by(Groups.name).all()

def set_group_data_sources(group_id, source_ids, changed_by = None):
    """Reconcile a group's GroupDataSources rows to exactly `source_ids` (a collection of
    DataSources ids). Adds/removes the difference and logs a UserActivity row. Returns the
    list of changes made (empty if nothing changed)."""
    group = Groups.query.get(group_id)
    if not group:
        raise ValueError("Group not found. Unable to update data source access.")

    target_ids = {int(sid) for sid in source_ids}
    existing = GroupDataSources.query.filter_by(group_id = group_id).all()
    existing_ids = {gds.data_source_id for gds in existing}

    to_add = target_ids - existing_ids
    to_remove = existing_ids - target_ids

    for gds in existing:
        if gds.data_source_id in to_remove:
            db.session.delete(gds)
    for source_id in to_add:
        db.session.add(GroupDataSources(group_id = group_id, data_source_id = source_id))

    changes = []
    if to_add:
        added_names = [s.name for s in DataSources.query.filter(DataSources.id.in_(to_add)).all()]
        changes.extend(f"+{name}" for name in added_names)
    if to_remove:
        removed_names = [s.name for s in DataSources.query.filter(DataSources.id.in_(to_remove)).all()]
        changes.extend(f"-{name}" for name in removed_names)

    if changes:
        changer = User.query.get(changed_by) if changed_by else None
        changer_name = changer.username if changer else f"user ID {changed_by}"
        activity = UserActivity(
            user_id = changed_by,
            activity_type = "group_data_sources_updated",
            activity_target_type = "group",
            activity_target_id = group_id,
            details = f"Data source access for group {group.name} updated by {changer_name}: {', '.join(changes)}."
        )
        db.session.add(activity)

    db.session.commit()
    return changes

# --------------------------------------------------------------------------------------- #
# Data ownership: control which groups can see which visuals (GroupVisuals), scoped to the
# data sources a user owns. A user "owns" a source if they are a Data Owner (or higher) in a
# group that has access to it via GroupDataSources; site admins own everything.
# --------------------------------------------------------------------------------------- #

def _source_ids_with_visuals():
    return {v.data_source_id for v in
            Visuals.query.filter(Visuals.data_source_id.isnot(None)).all()}

def _owner_group_ids(user):
    owner_level = ROLE_HIERARCHY["Data Owner"]
    return [m.group_id for m in UserGroups.query.filter_by(user_id = user.id).all()
            if ROLE_HIERARCHY.get(m.role, -1) >= owner_level]

def owned_data_sources(user):
    """DataSources (that have visuals) the user may manage: all for a site admin, otherwise those
    accessible by groups where the user is at least a Data Owner."""
    with_visuals = _source_ids_with_visuals()
    if user.site_admin:
        ids = with_visuals
    else:
        owner_group_ids = _owner_group_ids(user)
        ids = ({gds.data_source_id for gds in
                GroupDataSources.query.filter(GroupDataSources.group_id.in_(owner_group_ids)).all()}
               & with_visuals) if owner_group_ids else set()
    return DataSources.query.filter(DataSources.id.in_(ids)).order_by(DataSources.name).all() if ids else []

def can_manage_source(user, source_id):
    """True if the user may manage visual grants for `source_id` (site admin, or Data Owner in a
    group that has the source)."""
    if user.site_admin:
        return True
    owner_group_ids = _owner_group_ids(user)
    if not owner_group_ids:
        return False
    return GroupDataSources.query.filter(GroupDataSources.group_id.in_(owner_group_ids),
                                         GroupDataSources.data_source_id == source_id).first() is not None

def teams_for_source(source_id):
    """Groups that have access to the data source (and may therefore be granted its visuals)."""
    group_ids = {gds.group_id for gds in GroupDataSources.query.filter_by(data_source_id = source_id).all()}
    return Groups.query.filter(Groups.id.in_(group_ids)).order_by(Groups.name).all() if group_ids else []

def visuals_for_source(source_id):
    """A source's visuals as drill-trees grouped by province:
    [{province, trees: [node]}] where node = {visual, children: [node]}. Level-1 visuals are roots;
    children follow next_vis_name (the drill chain)."""
    # Order by id so the rows stay put: without an explicit sort Postgres returns rows in an unstable
    # physical order that shifts after an UPDATE (e.g. a visibility change), reordering the table.
    visuals = Visuals.query.filter_by(data_source_id = source_id).order_by(Visuals.id).all()
    by_name = {(v.province, v.name): v for v in visuals}
    provinces = {}
    for v in visuals:
        provinces.setdefault(v.province, []).append(v)

    def build(v):
        node = {"visual": v, "children": []}
        if v.next_vis_name:
            child = by_name.get((v.province, v.next_vis_name))
            if child:
                node["children"].append(build(child))
        return node

    out = []
    for province in sorted(provinces):
        roots = [build(v) for v in provinces[province] if v.level == "1" or v.level is None]
        out.append({"province": province, "trees": roots})
    return out

def set_group_visuals(group_id, visual_ids, scope_visual_ids, changed_by = None):
    """Reconcile a group's GroupVisuals rows for the visuals in `scope_visual_ids` (a single data
    source's visuals) to exactly `visual_ids`. Grants outside the scope are untouched, the drill
    hierarchy is enforced (a visual can't be granted unless its parent is), and a UserActivity row is
    logged. Returns the list of changes (empty if nothing changed)."""
    group = Groups.query.get(group_id)
    if not group:
        raise ValueError("Group not found. Unable to update visual access.")

    scope = {int(v) for v in scope_visual_ids}
    target = {int(v) for v in visual_ids} & scope   # only manage visuals within this source's scope

    # Enforce the drill hierarchy: drop any granted visual whose in-scope parent isn't also granted.
    scope_visuals = Visuals.query.filter(Visuals.id.in_(scope)).all() if scope else []
    by_name = {(v.province, v.name): v for v in scope_visuals}
    parent_id = {}
    for v in scope_visuals:
        if v.vis_parent_name:
            parent = by_name.get((v.province, v.vis_parent_name))
            if parent:
                parent_id[v.id] = parent.id
    changed = True
    while changed:
        changed = False
        for vid in list(target):
            pid = parent_id.get(vid)
            if pid is not None and pid not in target:
                target.discard(vid)
                changed = True

    existing = (GroupVisuals.query.filter(GroupVisuals.group_id == group_id,
                                          GroupVisuals.visual_id.in_(scope)).all() if scope else [])
    existing_ids = {gv.visual_id for gv in existing}
    to_add = target - existing_ids
    to_remove = existing_ids - target

    for gv in existing:
        if gv.visual_id in to_remove:
            db.session.delete(gv)
    for vid in to_add:
        db.session.add(GroupVisuals(group_id = group_id, visual_id = vid))

    def _names(ids):
        return [v.menu_name or v.name for v in Visuals.query.filter(Visuals.id.in_(ids)).all()]
    changes = [f"+{n}" for n in _names(to_add)] + [f"-{n}" for n in _names(to_remove)]

    # Only log a UserActivity row when there's an actor to attribute it to. A request-driven change
    # always passes changed_by; a seed/CLI call (changed_by=None) would otherwise write an activity
    # row with a NULL user_id, leaving the audit trail with no actor.
    if changes and changed_by is not None:
        changer = User.query.get(changed_by)
        changer_name = changer.username if changer else f"user ID {changed_by}"
        db.session.add(UserActivity(
            user_id = changed_by,
            activity_type = "group_visuals_updated",
            activity_target_type = "group",
            activity_target_id = group_id,
            details = f"Visual access for group {group.name} updated by {changer_name}: {', '.join(changes)}."
        ))

    db.session.commit()
    return changes

# Visibility openness, most- to least-restrictive. A drill-child can never be MORE open than its
# ancestors (you must pass through the parent to reach it), so rank(child) <= rank(every ancestor).
VISIBILITY_RANK = {level: i for i, level in enumerate(VISUAL_VISIBILITY)}   # private<group<public

def _chain_ancestors(visual):
    """The drill-chain ancestors of a visual (nearest first), walked up via vis_parent_name within
    the same province. Chains are linear (single parent), so this is a simple walk."""
    out, seen = [], set()
    current = visual
    while current.vis_parent_name and current.vis_parent_name not in seen:
        seen.add(current.vis_parent_name)
        parent = Visuals.query.filter_by(province = current.province,
                                         name = current.vis_parent_name).first()
        if not parent:
            break
        out.append(parent)
        current = parent
    return out

def _chain_descendants(visual):
    """The drill-chain descendants of a visual (nearest first), walked down via next_vis_name."""
    out, seen = [], set()
    current = visual
    while current.next_vis_name and current.next_vis_name not in seen:
        seen.add(current.next_vis_name)
        child = Visuals.query.filter_by(province = current.province,
                                        name = current.next_vis_name).first()
        if not child:
            break
        out.append(child)
        current = child
    return out

def _label(visual):
    return visual.menu_name or visual.name

def set_visual_visibility(visual_id, visibility, changed_by = None):
    """Set a visual's access level (one of VISUAL_VISIBILITY: private/group/public), enforcing the
    drill hierarchy. The caller must already have verified the user may manage the visual's data
    source (can_manage_source).

    A visual can't be made more visible than its ancestors (it would be unreachable) -> raises
    ValueError. Lowering a visual cascades down: descendants more open than the new level are clamped
    to it so none dangle as visible-but-unreachable. Logs a UserActivity row per changed visual and
    returns the new visibility."""
    if visibility not in VISUAL_VISIBILITY:
        raise ValueError(f"Invalid visibility '{visibility}'.")
    visual = Visuals.query.get(visual_id)
    if not visual:
        raise ValueError("Visual not found.")
    new_rank = VISIBILITY_RANK[visibility]

    # Guard: a child can't be more open than any ancestor in its drill chain.
    for ancestor in _chain_ancestors(visual):
        if VISIBILITY_RANK[ancestor.visibility] < new_rank:
            raise ValueError(
                f'"{_label(visual)}" can\'t be more visible than its access point '
                f'"{_label(ancestor)}" ({ancestor.visibility}). Raise "{_label(ancestor)}" first.')

    changes = []   # (visual, old, new)
    if visual.visibility != visibility:
        changes.append((visual, visual.visibility, visibility))
        visual.visibility = visibility

    # Cascade down: clamp any descendant that is now more open than this visual.
    for descendant in _chain_descendants(visual):
        if VISIBILITY_RANK[descendant.visibility] > new_rank:
            changes.append((descendant, descendant.visibility, visibility))
            descendant.visibility = visibility

    if not changes:
        return visibility

    changer = User.query.get(changed_by) if changed_by else None
    changer_name = changer.username if changer else f"user ID {changed_by}"
    for changed_visual, old, new in changes:
        db.session.add(UserActivity(
            user_id = changed_by,
            activity_type = "visual_visibility_updated",
            activity_target_type = "visual",
            activity_target_id = changed_visual.id,
            details = (f"Visibility for visual {_label(changed_visual)} changed from "
                       f"{old} to {new} by {changer_name}.")
        ))
    db.session.commit()
    return visibility

def set_source_visibility(source_id, visibility, changed_by = None):
    """Set every visual of a data source to `visibility` in one transaction. The caller must already
    have verified the user may manage the source (can_manage_source).

    A uniform level trivially satisfies the drill-hierarchy rank invariant within the source; chain
    links that cross into another source are guarded (raise ValueError) rather than cascaded, since
    the caller's authorization covers only this source. Logs a single summary UserActivity row (details
    stays bounded regardless of how many visuals the source has). Returns the number of visuals that
    changed (0 for a no-op, which neither commits nor logs)."""
    if visibility not in VISUAL_VISIBILITY:
        raise ValueError(f"Invalid visibility '{visibility}'.")
    source = DataSources.query.get(source_id)
    if not source:
        raise ValueError("Data source not found.")

    visuals = Visuals.query.filter_by(data_source_id = source_id).all()
    changed = [v for v in visuals if v.visibility != visibility]
    if not changed:
        return 0

    # Guard: a uniform level satisfies the drill-hierarchy rank invariant *within* the source, but a
    # drill chain can cross sources. Refuse rather than break the invariant -- or silently mutate a
    # visual in a source this caller was never authorized to manage.
    new_rank = VISIBILITY_RANK[visibility]
    for visual in changed:
        for ancestor in _chain_ancestors(visual):
            if ancestor.data_source_id != source_id and VISIBILITY_RANK[ancestor.visibility] < new_rank:
                raise ValueError(
                    f'"{_label(visual)}" can\'t be more visible than its access point '
                    f'"{_label(ancestor)}" ({ancestor.visibility}), which belongs to another data '
                    f'source. Raise "{_label(ancestor)}" first.')
        for descendant in _chain_descendants(visual):
            if descendant.data_source_id != source_id and VISIBILITY_RANK[descendant.visibility] > new_rank:
                raise ValueError(
                    f'Setting "{_label(visual)}" to {visibility} would leave its drill-down '
                    f'"{_label(descendant)}" ({descendant.visibility}), which belongs to another '
                    f'data source, more visible than its access point. Lower '
                    f'"{_label(descendant)}" first.')

    for visual in changed:
        visual.visibility = visibility

    # Only log with an actor to attribute the change to (same convention as set_group_visuals).
    if changed_by is not None:
        changer = User.query.get(changed_by)
        changer_name = changer.username if changer else f"user ID {changed_by}"
        db.session.add(UserActivity(
            user_id = changed_by,
            activity_type = "source_visibility_updated",
            activity_target_type = "data_source",
            activity_target_id = source_id,
            details = (f"All visuals of data source {source.name} set to {visibility} by "
                       f"{changer_name} ({len(changed)} of {len(visuals)} changed).")
        ))
    db.session.commit()
    return len(changed)

def set_province_default_visual(visual_id, changed_by = None):
    """Set (or unset) the landing visual for a province page. The caller must already have verified
    the user may manage the visual's data source (can_manage_source).

    Only a root/level-1 visual may be the default -- the landing visual renders without drill-chain
    context, so a drill child would have no access point above it (raises ValueError). The default is
    a single per-province pointer: setting one clears is_default on every other visual in that
    province (across all sources). Clicking the current default again unsets it, reverting the page to
    the automatic level-1 fallback. Logs a UserActivity row and returns the new is_default state.

    INTENTIONAL cross-source clear: a province's visuals can live in several data sources, but the
    landing default is one-per-province by design (build_province_menu picks a single is_default
    visual). So setting a default necessarily clears whatever default another source held for the same
    province -- even one set by a different source's Data Owner. Scoping the clear to only the actor's
    manageable sources would let two visuals in the same province both stay flagged, which
    build_province_menu would then resolve nondeterministically (first by query order). The route
    (set_default_visual) re-renders every owned source card so the moved star stays visually in sync.
    The actor still needs can_manage_source on the visual they are *setting*; this shared-province
    coupling is accepted."""
    visual = Visuals.query.get(visual_id)
    if not visual:
        raise ValueError("Visual not found.")
    if visual.level not in (None, "1"):
        raise ValueError(f'"{_label(visual)}" is a drill-down and can\'t be the page default. '
                         "Only top-level visuals can be the landing visual.")

    new_state = not visual.is_default   # toggle
    # The landing visual is one per province: clear every other flagged visual in this province.
    for other in Visuals.query.filter(Visuals.province == visual.province,
                                      Visuals.id != visual.id, Visuals.is_default.is_(True)).all():
        other.is_default = False
    visual.is_default = new_state

    changer = User.query.get(changed_by) if changed_by else None
    changer_name = changer.username if changer else f"user ID {changed_by}"
    action = "set as" if new_state else "cleared as"
    db.session.add(UserActivity(
        user_id = changed_by,
        activity_type = "visual_default_updated",
        activity_target_type = "visual",
        activity_target_id = visual.id,
        details = (f"Visual {_label(visual)} {action} the default for "
                   f"{visual.province} by {changer_name}.")
    ))
    db.session.commit()
    return new_state

def visibility_rows_for_source(source_id):
    """Flatten a source's drill-trees into ordered display rows for the Data Ownership visibility
    table: [{visual, depth, is_root, allowed}] where allowed maps each level -> bool (False when that
    level would make the visual more open than an ancestor, i.e. unreachable). Roots (access points)
    are unconstrained. Built from visuals_for_source so the drill hierarchy is preserved."""
    rows = []

    def walk(node, depth, ancestor_min_rank):
        visual = node["visual"]
        allowed = {level: VISIBILITY_RANK[level] <= ancestor_min_rank for level in VISUAL_VISIBILITY}
        rows.append({"visual": visual, "depth": depth, "is_root": depth == 0, "allowed": allowed})
        child_min = min(ancestor_min_rank, VISIBILITY_RANK[visual.visibility])
        for child in node["children"]:
            walk(child, depth + 1, child_min)

    for province in visuals_for_source(source_id):
        for root in province["trees"]:
            walk(root, 0, VISIBILITY_RANK[VISUAL_VISIBILITY[-1]])   # roots: max openness (public)
    return rows

# Legacy/seed data-source names mapped to the canonical pipeline name (the name the scraped data
# actually carries, which generate_visuals.export_data_to_db uses and Visuals.data_source_id points
# at). reconcile_source_aliases() folds the legacy rows into the canonical ones so group grants and
# visuals reference the same DataSources rows.
SOURCE_ALIASES = {
    "BC Coroners Service": ["BC Coroners Service Report"],
    "British Columbia Centre for Substance Use (BCCSU)": ["BC DrugSense"],
    "Health Infobase - Health data in Canada": ["National Health Infobase"],
}

def reconcile_source_aliases(changed_by = None):
    """Merge legacy/seed-named DataSources into their canonical pipeline-named rows: move each
    alias's GroupDataSources onto the canonical row (dropping duplicates) and delete the alias row.
    Idempotent. Returns the list of "alias -> canonical" merges performed."""
    merges = []
    for canonical_name, alias_names in SOURCE_ALIASES.items():
        canonical = DataSources.query.filter_by(name = canonical_name).first()
        if not canonical:
            continue
        for alias_name in alias_names:
            alias = DataSources.query.filter_by(name = alias_name).first()
            if not alias or alias.id == canonical.id:
                continue
            groups_with_canonical = {gds.group_id for gds in
                                     GroupDataSources.query.filter_by(data_source_id = canonical.id).all()}
            for gds in GroupDataSources.query.filter_by(data_source_id = alias.id).all():
                if gds.group_id in groups_with_canonical:
                    db.session.delete(gds)          # group already has the canonical source
                else:
                    gds.data_source_id = canonical.id
            db.session.delete(alias)
            merges.append(f"{alias_name} -> {canonical_name}")

    if merges:
        db.session.add(UserActivity(
            user_id = changed_by,
            activity_type = "data_sources_reconciled",
            activity_target_type = "data_source",
            details = f"Merged duplicate data sources into their pipeline equivalents: {', '.join(merges)}."
        ))
    db.session.commit()
    return merges

def nav_permissions(user):
    """Capability flags for showing/hiding the account-management nav links, mirroring the
    @require_role gates on those routes: manage_users (invite/user/invite-management) needs Group
    Admin+ in some group; manage_data (group management, data ownership) needs Data Owner+. Site
    admins get everything."""
    if not getattr(user, "is_authenticated", False):
        return {"manage_users": False, "manage_data": False}
    if getattr(user, "site_admin", False):
        return {"manage_users": True, "manage_data": True}
    top = max((ROLE_HIERARCHY.get(m.role, -1)
               for m in UserGroups.query.filter_by(user_id = user.id).all()), default = -1)
    return {
        "manage_users": top >= ROLE_HIERARCHY["Group Admin"],
        "manage_data": top >= ROLE_HIERARCHY["Data Owner"],
    }

def get_user_memberships_in_groups(user_id, group_ids):
    """A user's UserGroups rows, optionally limited to a set of group ids.
    Pass group_ids=None (site admin) to return every membership."""
    query = UserGroups.query.filter_by(user_id = user_id)
    if group_ids is not None:
        query = query.filter(UserGroups.group_id.in_(group_ids))
    return query.all()

def get_assignable_roles(user, group_id):
    if user.site_admin:
        return ["Data Owner", "Group Admin", "Data Viewer"]

    membership = UserGroups.query.filter_by(user_id = user.id, group_id = group_id).first()
    if membership:
        return [role for role in ROLE_HIERARCHY.keys() if ROLE_HIERARCHY[role] < ROLE_HIERARCHY[membership.role]]
    else:
        return []

def validate_password(password):
    if not password:
        return False, "A password is required."
    if len(password) < 12:
        return False, "Password must be at least 12 characters long."
    # bcrypt silently truncates at 72 bytes, so anything past that is ignored (and a user who typed a
    # long passphrase would be authenticating on a silently shortened one). Reject it up front.
    if len(password.encode("utf-8")) > 72:
        return False, "Password must be at most 72 bytes long."
    if not any(char.isupper() for char in password):
        return False, "Password must contain at least one uppercase letter."
    if not any(char.islower() for char in password):
        return False, "Password must contain at least one lowercase letter."
    if not any(char.isdigit() for char in password):
        return False, "Password must contain at least one number."
    special_characters = re.findall(r'[^a-zA-Z0-9]', password)
    if not special_characters:
        return False, "Password must contain at least one special character."
    return True, "Password is valid."
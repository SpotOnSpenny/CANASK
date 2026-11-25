# Standard Library Imports
import re
from collections import defaultdict

# External Imports
from bcrypt import hashpw, gensalt, checkpw
from flask import flash, has_request_context, current_app
from flask_login import current_user

# Internal Imports
from data_viz.database import db
from data_viz.database.models import (User, Invites, UserGroups, Groups, UserActivity, DataSources, GroupDataSources, Visuals,
                                       GroupVisuals, GroupLandingVisuals, UserLandingChoices, UserStartingViews, VisualQuery, SiteAdminKey,
                                       VISUAL_VISIBILITY)
from data_viz.auth.role_hierarchy import GROUP_ROLES, ROLE_HIERARCHY
from data_viz.auth.restricted import (RestrictedSourceRefusal, admin_reaches_group,
                                      admin_reaches_source, blocked_group_ids,
                                      restricted_source_ids, uncovered_source_ids)
from data_viz.provinces import PROVINCE_LABELS
from data_viz.starting_views import PAGES as STARTING_VIEW_PAGES, clean_view_query
from data_viz.visual_query import _visual_has_data, accessible_provinces, displayable_visuals, landing_for

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

def _refuse_blocked_site_admin(group_id):
    """Backstop for the restricted-source rule (auth/restricted.py): whatever a route's own
    checks did or didn't do, a signed-in site admin cannot change the membership of a group that
    holds a restricted source they are not covered for. Only fires inside a request with a
    signed-in site admin -- the CLI, the seed and invite acceptance (logged out) are untouched."""
    if not has_request_context():
        return
    if not getattr(current_user, "is_authenticated", False) or not current_user.site_admin:
        return
    # int(): a str id (straight off a form) would miss the set of int ids and read as allowed.
    if int(group_id) in blocked_group_ids(current_user):
        raise RestrictedSourceRefusal(f"Site admin {current_user.username} may not change membership of "
                              f"group {group_id}: it holds a restricted data source.")

def _refuse_uncovered_source_link(group_id, source_ids):
    """The same backstop for the group<->source link, the one write that makes someone covered:
    a signed-in site admin may not change the sources of a group that is blocked for them, nor
    attach or detach a restricted source they are not covered for. (Without it, a route that
    forgot its own check would let an outside admin join any open group, attach the restricted
    source to it, and so put themselves on the agreement.) Same scope as above: requests with a
    signed-in site admin only -- `flask set-group-source` is the break-glass and is untouched."""
    if not has_request_context():
        return
    if not getattr(current_user, "is_authenticated", False) or not current_user.site_admin:
        return
    if int(group_id) in blocked_group_ids(current_user):
        raise RestrictedSourceRefusal(f"Site admin {current_user.username} may not change the data sources "
                              f"of group {group_id}: it holds a restricted data source.")
    uncovered = uncovered_source_ids(current_user) & {int(sid) for sid in source_ids}
    if uncovered:
        raise RestrictedSourceRefusal(f"Site admin {current_user.username} may not attach or detach "
                              f"restricted data source(s) {sorted(uncovered)} on group {group_id}.")

def assign_group(user_id, group_id, role, assigned_by = None, remove = False, actor_label = None):
    """`actor_label` names a non-user actor for the activity row (the break-glass CLI), so a
    membership change made from the server is not recorded as merely "without assigner"."""
    _refuse_blocked_site_admin(group_id)
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
            elif actor_label:
                details = f"User {user.username} removed from Group {group.name} by {actor_label}."
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
            flash(f"An error occurred while removing group access: {str(e)}", "danger")
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
        elif actor_label:
            details = f"User {user.username} assigned to Group {group.name} with role {role} by {actor_label}."
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
            flash(f"An error occurred while assigning group access: {str(e)}", "danger")
        raise e
    
    db.session.add(activity)
    db.session.add(membership)
    db.session.commit()
    return membership

def assign_site_admin(user_id, remove = False, assigned_by = None):
    user = User.query.get(user_id)
    if not user:
        raise ValueError("User not found. Unable to assign site admin role.")
    # Backstop (see _refuse_blocked_site_admin): site admin + membership of a restricted
    # source's group = full powers over that source, so GRANTING site admin to a member of a
    # group the signed-in site admin is blocked from is granting the restricted data. Removing
    # admin status only ever takes access away and is never refused.
    if not remove:
        for membership in UserGroups.query.filter_by(user_id = user_id).all():
            _refuse_blocked_site_admin(membership.group_id)

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

def site_admin_key_is_set():
    return SiteAdminKey.query.get(1) is not None

def check_site_admin_key(candidate):
    row = SiteAdminKey.query.get(1)
    if not row or not candidate:
        return False
    return checkpw(candidate.encode("utf-8"), row.password_hash.encode("utf-8"))

def set_site_admin_key(new_password, changed_by = None, ip_address = None):
    # Single-row upsert pinned to id=1, so two concurrent initial sets collide on the PK and
    # one fails loudly instead of silently leaving two rows with divergent hashes. Strength
    # validation is the caller's job (the rotate-site-admin-key CLI -- rotation is deliberately
    # CLI-only, so a lone admin who knows the secret can't swap it in-app and lock the others out);
    # verification here is bcrypt-compare only.
    row = SiteAdminKey.query.get(1) or SiteAdminKey(id = 1)
    row.password_hash = hashpw(new_password.encode("utf-8"), gensalt()).decode("utf-8")
    row.updated_at = db.func.current_timestamp()
    row.updated_by = changed_by

    if changed_by:
        rotator = User.query.get(changed_by)
        details = f"Site admin key rotated by {rotator.username}."
    else:
        details = "Site admin key rotated by the CLI (break-glass)."

    activity = UserActivity(
        user_id = changed_by,
        activity_type = "site_admin_key_rotated",
        activity_target_type = "site_admin_key",
        details = details,
        ip_address = ip_address
    )

    db.session.add(row)
    db.session.add(activity)
    db.session.commit()
    return row

def set_user_password(user, new_password, ip_address = None, details = None):
    # Strength validation is the caller's job (same convention as set_site_admin_key). `details`
    # words the activity row for a caller other than the emailed reset link (the account page's
    # "change password"); the activity TYPE stays "password reset" either way, because
    # auth.recent_login_failures treats that type as the boundary that clears earlier failures.
    user.password_hash = hashpw(new_password.encode("utf-8"), gensalt()).decode("utf-8")
    # Bump so every OTHER session for this account fails load_user's version check on its next
    # request -- the standard "reset my password to kick out whoever's in my account" expectation.
    user.session_version = (user.session_version or 0) + 1

    activity = UserActivity(
        user_id = user.id,
        activity_type = "password reset",
        activity_target_type = "account",
        activity_target_id = user.id,
        details = details or f"Password reset completed for {user.username}",
        ip_address = ip_address
    )

    db.session.add(user)
    db.session.add(activity)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error committing password reset for user {user.id}: {str(e)}")
        raise
    return user

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

def get_directory_users(exclude_ids = ()):
    """Every ACTIVE account not in `exclude_ids`, by email -- the "All users" directory a
    group manager browses to find someone to add. Callers must show these rows as email,
    username and status only: what groups an outsider belongs to (and whether they are a
    site admin) is not the manager's to see."""
    query = User.query.filter(User.status == User.STATUS_ACTIVE)
    if exclude_ids:
        query = query.filter(User.id.notin_(list(exclude_ids)))
    return query.order_by(User.email).all()

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

def set_group_data_sources(group_id, source_ids, changed_by = None, actor_label = None):
    """Reconcile a group's GroupDataSources rows to exactly `source_ids` (a collection of
    DataSources ids). Adds/removes the difference and logs a UserActivity row. Returns the
    list of changes made (empty if nothing changed); a detached source's cleared visual grants
    and starting charts are among them ("N visual grant(s) cleared", "N starting chart(s) cleared")
    -- callers should show the list, because re-attaching the source does not bring them back. `actor_label` names a non-user
    actor (the break-glass CLI) in the activity row."""
    group = Groups.query.get(group_id)
    if not group:
        raise ValueError("Group not found. Unable to update data source access.")

    target_ids = {int(sid) for sid in source_ids}
    existing = GroupDataSources.query.filter_by(group_id = group_id).all()
    existing_ids = {gds.data_source_id for gds in existing}

    to_add = target_ids - existing_ids
    to_remove = existing_ids - target_ids
    _refuse_uncovered_source_link(group_id, to_add | to_remove)

    for gds in existing:
        if gds.data_source_id in to_remove:
            db.session.delete(gds)
    # A group that loses a source loses its visual grants for that source: left behind they
    # would still be live access to an open source's Group-level visuals, with no way to revoke
    # them in the app (team grants are only editable for groups that hold the source).
    # The same goes for a starting chart (GroupLandingVisuals) that rested on one of those grants:
    # members can no longer see it, and left behind it would come back to life unasked if the
    # source were re-attached and re-granted. A pick on a Public visual stays -- still visible.
    grants_cleared = landing_cleared = 0
    if to_remove:
        detached = db.session.query(Visuals.id, Visuals.visibility).filter(
            Visuals.data_source_id.in_(to_remove)).all()
        detached_visual_ids = [visual_id for visual_id, _ in detached]
        if detached_visual_ids:
            grants_cleared = GroupVisuals.query.filter(
                GroupVisuals.group_id == group_id,
                GroupVisuals.visual_id.in_(detached_visual_ids)).delete(synchronize_session = False)
        no_longer_visible = [visual_id for visual_id, visibility in detached if visibility != "public"]
        if no_longer_visible:
            landing_cleared = GroupLandingVisuals.query.filter(
                GroupLandingVisuals.group_id == group_id,
                GroupLandingVisuals.visual_id.in_(no_longer_visible)).delete(synchronize_session = False)
    for source_id in to_add:
        db.session.add(GroupDataSources(group_id = group_id, data_source_id = source_id))

    changes = []
    if to_add:
        added_names = [s.name for s in DataSources.query.filter(DataSources.id.in_(to_add)).all()]
        changes.extend(f"+{name}" for name in added_names)
    if to_remove:
        removed_names = [s.name for s in DataSources.query.filter(DataSources.id.in_(to_remove)).all()]
        changes.extend(f"-{name}" for name in removed_names)
    if grants_cleared:
        changes.append(f"{grants_cleared} visual grant{'' if grants_cleared == 1 else 's'} cleared")
    if landing_cleared:
        changes.append(f"{landing_cleared} starting chart{'' if landing_cleared == 1 else 's'} cleared")

    if changes:
        changer = User.query.get(changed_by) if changed_by else None
        changer_name = changer.username if changer else (actor_label or f"user ID {changed_by}")
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
    """DataSources (that have visuals) the user may manage: all for a site admin -- except a
    restricted source whose agreement they are not on (auth/restricted.py) -- otherwise those
    accessible by groups where the user is at least a Data Owner."""
    with_visuals = _source_ids_with_visuals()
    if user.site_admin:
        ids = with_visuals - uncovered_source_ids(user)
    else:
        owner_group_ids = _owner_group_ids(user)
        ids = ({gds.data_source_id for gds in
                GroupDataSources.query.filter(GroupDataSources.group_id.in_(owner_group_ids)).all()}
               & with_visuals) if owner_group_ids else set()
    return DataSources.query.filter(DataSources.id.in_(ids)).order_by(DataSources.name).all() if ids else []

def can_manage_source(user, source_id):
    """True if the user may manage visual grants for `source_id`: a site admin whose status
    reaches the source (always, unless it is restricted and they are outside its agreement), or a
    Data Owner in a group that has the source."""
    if admin_reaches_source(user, source_id):
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
    """Set (or unset) the SITE-WIDE landing visual for a province page: what the page opens on for
    anyone whose own groups picked nothing they can see (see set_group_landing for the per-group
    starting charts, and visual_query.resolve_landing for the order). The caller must already have
    verified the actor is a site admin who may manage the visual's data source.

    Only a root/level-1 visual may be the default -- the landing visual renders without drill-chain
    context, so a drill child would have no access point above it (raises ValueError). The default is
    a single per-province pointer: setting one clears is_default on every other visual in that
    province, across all sources (two flagged visuals would be resolved by row order). Clicking the
    current default again unsets it, reverting the page to the automatic level-1 fallback. Logs a
    UserActivity row and returns the new is_default state.

    The cross-source clear is why this is a site-admin setting: a Data Owner sees only their own
    sources, and used to be able to move the page off another owner's visual. One case is still
    refused (ValueError, nothing changed): the province's current default sits on a restricted
    source the actor is not covered for -- they can neither see nor manage that visual, so they
    may not decide the page stops opening on it."""
    visual = Visuals.query.get(visual_id)
    if not visual:
        raise ValueError("Visual not found.")
    if visual.level not in (None, "1"):
        raise ValueError(f'"{_label(visual)}" is a drill-down and can\'t be the page default. '
                         "Only top-level visuals can be the landing visual.")

    changer = User.query.get(changed_by) if changed_by else None
    others = Visuals.query.filter(Visuals.province == visual.province,
                                  Visuals.id != visual.id, Visuals.is_default.is_(True)).all()
    if changer is not None and others:
        uncovered = uncovered_source_ids(changer)
        if any(other.data_source_id in uncovered for other in others):
            raise ValueError("You can't change this page's default. It is currently set to a visual "
                             "covered by a data sharing agreement you aren't part of.")

    new_state = not visual.is_default   # toggle
    # The landing visual is one per province: clear every other flagged visual in this province.
    for other in others:
        other.is_default = False
    visual.is_default = new_state

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

# --------------------------------------------------------------------------------------- #
# Starting charts: the visual a GROUP's members open a province page on (GroupLandingVisuals),
# and any user's own choice for a page (UserLandingChoices), which beats it. The read side --
# what a viewer actually lands on -- is visual_query.landing_for / resolve_landing.
# --------------------------------------------------------------------------------------- #

def landing_scope_label(province):
    """Readable name for a Visuals.province scope key: a province, or a national dashboard's
    title for a national scope such as "canada"."""
    if province in PROVINCE_LABELS:
        return PROVINCE_LABELS[province]
    from data_viz.main import NATIONAL_DASHBOARDS   # lazy: main imports the auth package
    for dashboard in NATIONAL_DASHBOARDS.values():
        if dashboard["scope"] == province:
            return dashboard["title"]
    return province.replace("-", " ").title()

def _landing_pool(group_id, province = None):
    """What a group may pick as a starting chart: ({province: [Visuals]}, owners_only_ids).

    Level-1 visuals with data that the group's members have access to:
      - Public ones, and Group-level ones granted to this group (for a restricted source only
        if the group also holds it: a stray grant on a non-holding group shows its members
        nothing, see visual_query._can_see) -- every member sees these;
      - any other visual of a data source the group HOLDS (Private, or Group-level but not
        shared with the group itself): the group's own data, which its Data Owners see at any
        level and its other members do not. Their ids come back in `owners_only_ids`, so the
        modal can say so.
    A pick is only ever served to a viewer who can see it (visual_query.resolve_landing), so an
    owners-only pick opens for the group's Data Owners and leaves everyone else on the site
    default. Ordered by visual id."""
    granted = {gv.visual_id for gv in GroupVisuals.query.filter_by(group_id = group_id).all()}
    held = {gds.data_source_id for gds in GroupDataSources.query.filter_by(group_id = group_id).all()}
    query = Visuals.query.filter(Visuals.level == "1", db.or_(
        Visuals.visibility == "public",
        Visuals.id.in_(granted),
        Visuals.data_source_id.in_(held)))
    if province is not None:
        query = query.filter(Visuals.province == province)
    visuals = query.order_by(Visuals.id).all()
    if not visuals:
        return {}, set()
    restricted = restricted_source_ids()
    # Batch the geo predicates the has-data check reads (one query, as accessible_provinces does).
    geo_preds = defaultdict(list)
    for row in VisualQuery.query.filter(VisualQuery.filter_type == "geo",
                                        VisualQuery.for_visual_id.in_([v.id for v in visuals])).all():
        geo_preds[row.for_visual_id].append(row.filter_value)

    pool, owners_only = {}, set()
    for visual in visuals:
        source_id = visual.data_source_id
        everyone = visual.visibility == "public" or (
            visual.visibility == "group" and visual.id in granted
            and (source_id not in restricted or source_id in held))
        if not everyone and source_id not in held:
            continue
        if not _visual_has_data(visual, geo_preds.get(visual.id, [])):
            continue
        if not everyone:
            owners_only.add(visual.id)
        pool.setdefault(visual.province, []).append(visual)
    return pool, owners_only

def landing_candidates(group_id, province):
    """The visuals `group_id` may pick as its starting chart for `province` (see _landing_pool)."""
    return _landing_pool(group_id, province)[0].get(province, [])

def landing_scopes_for(group_id):
    """Rows for a group's "Starting charts" modal: one per scope where there is something to choose
    (two or more candidates) or a pick is already stored --
    [{province, label, candidates, owners_only, current, stale}]. `owners_only` is the ids of
    the candidates only the group's Data Owners can see (see _landing_pool). `current` is the
    picked Visuals row (or None = the site default); `stale` flags a pick that is no longer a
    candidate (its grant or visibility changed), which members are not served. The modal must
    not NAME a stale pick: whoever opens it may no longer be allowed to see that visual (e.g. a
    restricted source). Scopes where the group has visuals beyond the Public ones come first,
    then alphabetical."""
    pool, owners_only = _landing_pool(group_id)
    current = {row.province: row for row in GroupLandingVisuals.query.filter_by(group_id = group_id).all()}
    scopes = []
    for province in set(pool) | set(current):
        candidates = pool.get(province, [])
        row = current.get(province)
        if len(candidates) < 2 and row is None:
            continue
        scopes.append({
            "province": province,
            "label": landing_scope_label(province),
            "candidates": candidates,
            "owners_only": {c.id for c in candidates} & owners_only,
            "current": row.visual if row is not None else None,
            "stale": row is not None and row.visual_id not in {c.id for c in candidates},
            "own": any(c.visibility != "public" for c in candidates),
        })
    scopes.sort(key = lambda scope: (not scope["own"], scope["label"]))
    return scopes

def set_group_landing(group_id, picks, changed_by = None):
    """Set a group's starting charts. `picks` maps a scope (Visuals.province) to a visual id, or
    to None to go back to the site default; scopes not in `picks` are untouched. The caller must
    already have verified the actor may manage the group.

    Every NEW pick must be one of landing_candidates for its scope, otherwise ValueError is raised
    before anything is written (an unchanged pick is kept as it is, even if it has gone stale).
    Logs one UserActivity row and returns the list of changes (empty, and nothing logged, for a
    no-op)."""
    group = db.session.get(Groups, group_id)
    if not group:
        raise ValueError("Group not found.")
    existing = {row.province: row for row in GroupLandingVisuals.query.filter_by(group_id = group_id).all()}

    wanted = {}   # province -> Visuals row (None = clear), only where something changes
    for province, visual_id in picks.items():
        row = existing.get(province)
        if visual_id is None:
            if row is not None:
                wanted[province] = None
            continue
        visual_id = int(visual_id)
        if row is not None and row.visual_id == visual_id:
            continue
        # Candidates are looked up only for a scope that actually changes (the has-data check
        # is a query per visual, so a one-scope save must not sweep every province).
        visual = next((c for c in landing_candidates(group_id, province) if c.id == visual_id), None)
        if visual is None:
            raise ValueError(f"That can't be {group.name}'s starting chart for "
                             f"{landing_scope_label(province)} because the group doesn't have access to it.")
        wanted[province] = visual

    changes = []
    for province, visual in wanted.items():
        row = existing.get(province)
        if visual is None:
            db.session.delete(row)
            changes.append(f"{landing_scope_label(province)}: site default")
            continue
        if row is None:
            db.session.add(GroupLandingVisuals(group_id = group_id, province = province,
                                               visual_id = visual.id, set_by = changed_by))
        else:
            row.visual_id = visual.id
            row.set_by = changed_by
        changes.append(f"{landing_scope_label(province)}: {_label(visual)}")
    if not changes:
        return []

    changer = db.session.get(User, changed_by) if changed_by else None
    changer_name = changer.username if changer else f"user ID {changed_by}"
    db.session.add(UserActivity(
        user_id = changed_by,
        activity_type = "group_landing_updated",
        activity_target_type = "group",
        activity_target_id = group_id,
        details = (f"Starting charts for group {group.name} updated by {changer_name}: "
                   f"{'; '.join(changes)}.")[:5000]
    ))
    db.session.commit()
    return changes

def _page_scopes():
    """The scope keys that have a page of their own: the active provinces plus the national
    dashboards' scopes (so not e.g. "canada-das", which backs the DAS Explorer)."""
    from data_viz.main import active_provinces, national_scopes   # lazy: main imports the auth package
    return set(active_provinces) | set(national_scopes)

def user_starting_charts(user, provinces = None):
    """Rows for the "Starting charts" section of a user's account page: one per page they can
    open (`provinces` defaults to their accessible page scopes), ordered by label --
    [{province, label, charts, by_category, chosen_id, default}]. `charts` is every top-level
    visual they can see there (`by_category` the same list grouped by menu category, in menu
    order), `chosen_id` their own choice (None if they made none, or can no longer see it) and
    `default` the Visuals row the page opens on WITHOUT their choice (their group's pick, else
    the site default...). A page with nothing they can see is left out."""
    if provinces is None:
        provinces = accessible_provinces(user) & _page_scopes()
    chosen = {row.province: row.visual_id
              for row in UserLandingChoices.query.filter_by(user_id = user.id).all()}
    rows = []
    for province in provinces:
        allowed = displayable_visuals(user, province)
        charts = [v for v in allowed if v.level == "1"]
        if not charts:
            continue
        default_name, _ = landing_for(user, province, allowed, personal = False)
        chosen_id = chosen.get(province)
        # The same grouping and order as the page's own menu (menu_parent, first appearance).
        by_category = {}
        for chart in charts:
            by_category.setdefault(chart.menu_parent or "Other", []).append(chart)
        rows.append({
            "province": province,
            "label": landing_scope_label(province),
            "charts": charts,
            "by_category": list(by_category.items()),
            "chosen_id": chosen_id if chosen_id in {c.id for c in charts} else None,
            "default": next((v for v in allowed if v.name == default_name), None),
        })
    rows.sort(key = lambda row: row["label"])
    return rows

def set_user_landing_choice(user, province, visual_id):
    """Set `user`'s own starting chart for `province` to `visual_id`, or clear it with None (the
    page goes back to their group's pick / the site default). The visual must be a top-level one
    they can see on that page, otherwise ValueError and nothing is written. Returns the name of
    the visual the page now opens on for them."""
    allowed = displayable_visuals(user, province)
    choice = UserLandingChoices.query.filter_by(user_id = user.id, province = province).first()

    if visual_id is None:
        if choice is None:
            return landing_for(user, province, allowed)[0]
        db.session.delete(choice)
        visual, details = None, (f"{user.username} went back to the default starting chart for "
                                 f"{landing_scope_label(province)}.")
    else:
        try:
            visual_id = int(visual_id)
        except (TypeError, ValueError):
            raise ValueError("That isn't a chart you can start this page on.")
        visual = next((v for v in allowed if v.id == visual_id and v.level == "1"), None)
        if visual is None:
            raise ValueError("That isn't a chart you can start this page on.")
        if choice is None:
            db.session.add(UserLandingChoices(user_id = user.id, province = province, visual_id = visual.id))
        elif choice.visual_id != visual.id:
            choice.visual_id = visual.id
        else:
            return visual.name
        details = (f"{user.username} set their starting chart for {landing_scope_label(province)} "
                   f"to {_label(visual)}.")

    db.session.add(UserActivity(
        user_id = user.id,
        activity_type = "landing_choice_updated",
        activity_target_type = "visual",
        activity_target_id = visual.id if visual is not None else None,
        details = details
    ))
    db.session.commit()
    return visual.name if visual is not None else landing_for(user, province, displayable_visuals(user, province))[0]

# Starting views: the same personal setting for the two pages that have no chart list. What they
# show is a set of settings in the address, so the user saves one such set per page
# (data_viz/starting_views.py knows the pages and cleans what is saved).

def can_open_view_page(user, page):
    """Whether `user` may open a starting-view page -- the same rules that show its nav link:
    the DAS gate visual for both, plus at least one province to overlay for Confluence."""
    if page not in STARTING_VIEW_PAGES:
        return False
    accessible = accessible_provinces(user)
    if "canada-das" not in accessible:
        return False
    return page != "confluence" or bool(accessible & set(PROVINCE_LABELS))

def saved_starting_view(user, page):
    """The query string `user` saved for `page`, or "" (also for an anonymous visitor)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return ""
    row = UserStartingViews.query.filter_by(user_id = user.id, page = page).first()
    return row.query_string if row is not None else ""

def user_starting_views(user):
    """Rows for the account page: one per starting-view page the user can open --
    [{page, label, path, query}] with `query` the saved query string or ""."""
    saved = {row.page: row.query_string for row in UserStartingViews.query.filter_by(user_id = user.id).all()}
    return [{"page": page, "label": spec["label"], "path": spec["path"], "query": saved.get(page, "")}
            for page, spec in STARTING_VIEW_PAGES.items() if can_open_view_page(user, page)]

def set_user_starting_view(user, page, raw):
    """Save `raw` (the page's query string, or a pasted link to it) as `user`'s starting view for
    `page`, or clear it with None. Raises ValueError (message fit to show) when it carries nothing
    the page recognises -- see clean_view_query. The caller checks can_open_view_page. Returns
    the stored query string ("" after a clear)."""
    row = UserStartingViews.query.filter_by(user_id = user.id, page = page).first()
    label = STARTING_VIEW_PAGES[page]["label"]
    if raw is None:
        if row is None:
            return ""
        db.session.delete(row)
        cleaned, details = "", f"{user.username} cleared their starting view for {label}."
    else:
        cleaned = clean_view_query(page, raw)
        if row is None:
            db.session.add(UserStartingViews(user_id = user.id, page = page, query_string = cleaned))
        elif row.query_string != cleaned:
            row.query_string = cleaned
        else:
            return cleaned
        details = f"{user.username} saved a starting view for {label}: {cleaned}"
    db.session.add(UserActivity(
        user_id = user.id,
        activity_type = "starting_view_updated",
        activity_target_type = "account",
        activity_target_id = user.id,
        details = details[:5000]
    ))
    db.session.commit()
    return cleaned

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
    Idempotent. Returns the list of "alias -> canonical" merges performed.

    Two exceptions for restricted sources (auth/restricted.py): a restricted CANONICAL source
    never inherits an alias's group links (they are dropped, and the merge note says so), and a
    restricted ALIAS row is refused outright -- deleting it would delete the restriction and move
    its holding groups onto an open source. A restricted source must never be listed as an alias."""
    merges = []
    for canonical_name, alias_names in SOURCE_ALIASES.items():
        canonical = DataSources.query.filter_by(name = canonical_name).first()
        if not canonical:
            continue
        for alias_name in alias_names:
            alias = DataSources.query.filter_by(name = alias_name).first()
            if not alias or alias.id == canonical.id:
                continue
            if alias.restricted:
                db.session.rollback()
                raise ValueError(
                    f"Data source {alias_name!r} is restricted but is listed in SOURCE_ALIASES as "
                    f"an alias of {canonical_name!r}. Merging would delete the restricted row and "
                    f"its restriction; remove it from SOURCE_ALIASES.")
            groups_with_canonical = {gds.group_id for gds in
                                     GroupDataSources.query.filter_by(data_source_id = canonical.id).all()}
            for gds in GroupDataSources.query.filter_by(data_source_id = alias.id).all():
                # A restricted canonical source (auth/restricted.py) never inherits group links:
                # the alias row is an open source any site admin could attach to a group of
                # their choosing, so carrying its links over would be a side door into the
                # agreement. They are dropped, and the merge note says so.
                if gds.group_id in groups_with_canonical or canonical.restricted:
                    db.session.delete(gds)          # already has the canonical source / not carried
                else:
                    gds.data_source_id = canonical.id
            db.session.delete(alias)
            merges.append(f"{alias_name} -> {canonical_name}"
                          + (" (group access NOT carried over: the source is restricted)"
                             if canonical.restricted else ""))

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
    Admin+ in some group; manage_data (group management, data ownership) needs Data Owner+;
    site_admin gates the site-admin-only pages (Feedback). Site admins get everything."""
    if not getattr(user, "is_authenticated", False):
        return {"manage_users": False, "manage_data": False, "site_admin": False}
    if getattr(user, "site_admin", False):
        return {"manage_users": True, "manage_data": True, "site_admin": True}
    top = max((ROLE_HIERARCHY.get(m.role, -1)
               for m in UserGroups.query.filter_by(user_id = user.id).all()), default = -1)
    return {
        "manage_users": top >= ROLE_HIERARCHY["Group Admin"],
        "manage_data": top >= ROLE_HIERARCHY["Data Owner"],
        "site_admin": False,
    }

def get_user_memberships_in_groups(user_id, group_ids):
    """A user's UserGroups rows, optionally limited to a set of group ids.
    Pass group_ids=None (site admin) to return every membership."""
    query = UserGroups.query.filter_by(user_id = user_id)
    if group_ids is not None:
        query = query.filter(UserGroups.group_id.in_(group_ids))
    return query.all()

def get_assignable_roles(user, group_id, blocked = None):
    """Roles `user` may grant in `group_id`. A site admin may grant any -- unless the group is
    blocked for them (it holds a restricted source they are not covered for), in which case their
    admin status grants nothing there and, not being a member, neither does a role. A caller
    rendering many groups passes `blocked` (= blocked_group_ids(user), computed once) so the
    check is not re-queried per group; a non-admin costs no restricted-source query at all."""
    if admin_reaches_group(user, group_id, blocked):
        return list(GROUP_ROLES)

    membership = UserGroups.query.filter_by(user_id = user.id, group_id = group_id).first()
    if membership:
        return [role for role in GROUP_ROLES if ROLE_HIERARCHY[role] < ROLE_HIERARCHY[membership.role]]
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
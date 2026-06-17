# Standard Library Imports
import re

# External Imports
from bcrypt import hashpw, gensalt
from flask import flash, has_request_context

# Internal Imports
from data_viz.database import db
from data_viz.database.models import User, Invites, UserGroups, Groups, UserActivity, DataSources, GroupDataSources, Visuals, GroupVisuals
from data_viz.auth.role_hierarchy import ROLE_HIERARCHY

def create_user(email, username, password, invited_by = None, status = "active",site_admin = False):
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

    if assigned_by:
        assigner = User.query.get(assigned_by)
        details = f"User {user.username} was granted site admin privileges by {assigner.username}."
    else:
        details = f"User ID {user_id} was granted site admin privileges without assigner."

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
    visuals = Visuals.query.filter_by(data_source_id = source_id).all()
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

    if changes:
        changer = User.query.get(changed_by) if changed_by else None
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
    if len(password) < 12:
        return False, "Password must be at least 12 characters long."
    if not any(char.isupper() for char in password):
        return False, "Password must contain at least one uppercase letter."
    special_characters = re.findall(r'[^a-zA-Z0-9]', password)
    if not special_characters:
        return False, "Password must contain at least one special character."
    return True, "Password is valid."
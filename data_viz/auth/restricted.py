###########################################################################################
#                Restricted data sources (data sharing agreements)                        #
# A DataSources row flagged `restricted` is covered by an agreement that not every site    #
# admin has signed. For such a source, site-admin status counts ONLY when the admin is     #
# also a member (any role) of a group that holds the source (a GroupDataSources row).      #
# Everything else about site admins is unchanged, and nothing here has any effect while    #
# no source is restricted.                                                                 #
#                                                                                          #
# Vocabulary used across the codebase:                                                     #
#   covered   (user, source): the user belongs to a group that holds the source            #
#   protected group:          a group holding at least one restricted source               #
#   blocked   (user, group):  a protected group holding a source the user isn't covered    #
#                             for -- a site admin may not change its membership, invites   #
#                             or sources                                                   #
#                                                                                          #
# This module imports only the models (and the dependency-free role constants) so both     #
# visual_query (viewing) and auth_helpers (managing) can use it. Nothing is memoized: a    #
# POST changes memberships and then renders,                                               #
# and a stale answer here would be an access-control bug. The common case (no restricted   #
# source at all) costs one indexed query.                                                  #
###########################################################################################

from data_viz.auth.role_hierarchy import ROLE_HIERARCHY
from data_viz.database import db
from data_viz.database.models import (DataSources, GroupDataSources, Groups, GroupVisuals,
                                       InviteGroups, Invites, User, UserGroups, Visuals)


def restricted_source_ids():
    """Ids of every DataSources row flagged restricted."""
    return {source_id for (source_id,) in
            db.session.query(DataSources.id).filter(DataSources.restricted.is_(True))}


def uncovered_source_ids(user):
    """The restricted sources `user` is NOT covered for (every restricted source for an
    anonymous viewer). Empty when the user is on every agreement, or none exists."""
    restricted = restricted_source_ids()
    if not restricted:
        return set()
    if user is None or not getattr(user, "is_authenticated", False):
        return restricted
    my_group_ids = {group_id for (group_id,) in
                    db.session.query(UserGroups.group_id).filter_by(user_id = user.id)}
    if not my_group_ids:
        return restricted
    covered = {source_id for (source_id,) in
               db.session.query(GroupDataSources.data_source_id).filter(
                   GroupDataSources.data_source_id.in_(restricted),
                   GroupDataSources.group_id.in_(my_group_ids))}
    return restricted - covered


def blocked_group_ids(user):
    """The protected groups that hold a restricted source `user` is not covered for. A site
    admin has no powers over these: not their membership, their invites, or their sources."""
    uncovered = uncovered_source_ids(user)
    if not uncovered:
        return set()
    return {group_id for (group_id,) in
            db.session.query(GroupDataSources.group_id).filter(
                GroupDataSources.data_source_id.in_(uncovered))}


def protected_group_ids():
    """Every group holding at least one restricted source (for labelling in the UI)."""
    restricted = restricted_source_ids()
    if not restricted:
        return set()
    return {group_id for (group_id,) in
            db.session.query(GroupDataSources.group_id).filter(
                GroupDataSources.data_source_id.in_(restricted))}


def admin_reaches_source(user, source_id):
    """Whether `user`'s SITE-ADMIN status applies to this source. False for non-admins (their
    access comes from group roles, checked by the callers) and for an admin outside the
    agreement of a restricted source."""
    if not getattr(user, "site_admin", False):
        return False
    return source_id not in uncovered_source_ids(user)


def admin_reaches_group(user, group_id):
    """Whether `user`'s SITE-ADMIN status applies to this group (see admin_reaches_source)."""
    if not getattr(user, "site_admin", False):
        return False
    return group_id not in blocked_group_ids(user)


def source_access_report(source):
    """Everything that decides who can see `source` (a DataSources row) right now, for the
    `flask source-access` command -- the list an owner checks against the agreement:

      restricted              the flag
      groups                  [{group, members: [{user, role}]}] for every group holding it
      site_admins_covered     active site admins whose admin status reaches the source
      site_admins_not_covered active site admins shut out by the restriction
      stray_grants            [(group, [visual])] GroupVisuals on groups that do NOT hold the
                              source (dead while it is restricted; live access if it is not)
      public_visuals          its visuals anyone can see, signed in or not
      pending_invites         [{invite, group, role}] not-yet-accepted invites into its groups
    """
    holding_ids = {group_id for (group_id,) in db.session.query(GroupDataSources.group_id)
                   .filter_by(data_source_id = source.id)}
    groups = []
    for group in (Groups.query.filter(Groups.id.in_(holding_ids)).order_by(Groups.name).all()
                  if holding_ids else []):
        memberships = UserGroups.query.filter_by(group_id = group.id).all()
        members = sorted(({"user": m.user, "role": m.role} for m in memberships),
                         key = lambda m: (-ROLE_HIERARCHY.get(m["role"], 0), m["user"].email))
        groups.append({"group": group, "members": members})

    covered, not_covered = [], []
    for admin in (User.query.filter_by(site_admin = True, status = User.STATUS_ACTIVE)
                  .order_by(User.email).all()):
        (covered if admin_reaches_source(admin, source.id) else not_covered).append(admin)

    visuals = Visuals.query.filter_by(data_source_id = source.id).order_by(Visuals.id).all()
    by_id = {v.id: v for v in visuals}
    stray = {}
    if by_id:
        for grant in GroupVisuals.query.filter(GroupVisuals.visual_id.in_(by_id)).all():
            if grant.group_id not in holding_ids:
                stray.setdefault(grant.group_id, []).append(by_id[grant.visual_id])
    stray_grants = [(Groups.query.get(group_id), granted)
                    for group_id, granted in sorted(stray.items())]

    pending_invites = []
    if holding_ids:
        rows = (db.session.query(InviteGroups, Invites)
                .join(Invites, Invites.id == InviteGroups.invite_id)
                .filter(InviteGroups.group_id.in_(holding_ids), Invites.status == "pending")
                .order_by(Invites.id).all())
        group_by_id = {entry["group"].id: entry["group"] for entry in groups}
        pending_invites = [{"invite": invite, "group": group_by_id[row.group_id], "role": row.role}
                           for row, invite in rows]

    return {
        "source": source,
        "restricted": bool(source.restricted),
        "groups": groups,
        "site_admins_covered": covered,
        "site_admins_not_covered": not_covered,
        "stray_grants": stray_grants,
        "public_visuals": [v for v in visuals if v.visibility == "public"],
        "pending_invites": pending_invites,
    }

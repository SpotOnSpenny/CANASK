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
# POST changes memberships and then renders, and a stale answer here would be an           #
# access-control bug. With no restricted source each helper is one small query on          #
# data_sources; with one, up to three (uncovered_source_ids) or four (blocked_group_ids)   #
# per call -- callers that loop compute the set once and pass it on.                       #
###########################################################################################

from data_viz.auth.role_hierarchy import ROLE_HIERARCHY
from data_viz.database import db
from data_viz.database.models import (DataSources, GroupDataSources, Groups, GroupVisuals,
                                       InviteGroups, Invites, User, UserGroups, Visuals)


class RestrictedSourceRefusal(PermissionError):
    """Raised by the backstops (auth_helpers.assign_group / assign_site_admin /
    set_group_data_sources, auth._add_invite_group) when a signed-in site admin tries to grant
    through a group or source they are not covered for. The routes are meant to refuse first,
    with their own message; this is what a route that forgot -- or a state that changed between
    its check and its write -- runs into. The app turns it into a visible refusal
    (data_viz/__init__.py) rather than a bare 500. A PermissionError subclass so that handler
    cannot swallow an unrelated OS-level "permission denied"."""


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
    admin may not grant anything through these: not membership, invites or sources. (Revoking
    an invite into one stays allowed -- it only removes access.)"""
    uncovered = uncovered_source_ids(user)
    if not uncovered:
        return set()
    return {group_id for (group_id,) in
            db.session.query(GroupDataSources.group_id).filter(
                GroupDataSources.data_source_id.in_(uncovered))}


def admin_reaches_source(user, source_id):
    """Whether `user`'s SITE-ADMIN status applies to this source. False for non-admins (their
    access comes from group roles, checked by the callers) and for an admin outside the
    agreement of a restricted source.

    `source_id` must be a real DataSources id: it is coerced with int() so that None, or a key
    of some other kind, raises instead of quietly missing the deny-set and reading as reachable."""
    if not getattr(user, "site_admin", False):
        return False
    return int(source_id) not in uncovered_source_ids(user)


def admin_reaches_group(user, group_id, blocked = None):
    """Whether `user`'s SITE-ADMIN status applies to this group (see admin_reaches_source;
    `group_id` is coerced the same way). `blocked` lets a caller that loops over groups pass
    the set it already computed for `user` instead of re-querying per group."""
    if not getattr(user, "site_admin", False):
        return False
    return int(group_id) not in (blocked_group_ids(user) if blocked is None else blocked)


def source_access_report(source):
    """Everything that decides who can see `source` (a DataSources row) right now, for the
    `flask source-access` command -- the list an owner checks against the agreement:

      restricted              the flag
      groups                  [{group, members: [{user, role}]}] for every group holding it
      site_admins_covered     active site admins whose admin status reaches the source
      site_admins_not_covered active site admins shut out by the restriction
      stray_grants            [{group, visuals}] GroupVisuals on groups that do NOT hold the
                              source. While it is restricted they give nothing to anyone
                              outside the holding groups (a holding-group member who is also
                              in the granted group still gets them); while it is open they
                              are live for the visuals set to Group visibility
      public_visuals          its visuals anyone can see, signed in or not
      pending_invites         [{invite, group, role, site_admin_invite}] unexpired invites
                              into its groups
      expired_invites         the same shape for EXPIRED invites, which a manager covered by
                              the agreement can renew back to pending
    On a site-admin invite the group rows are dormant (acceptance grants the flag, not the
    groups), hence the `site_admin_invite` marker.
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
    stray_grants = [{"group": Groups.query.get(group_id), "visuals": granted}
                    for group_id, granted in sorted(stray.items())]

    invites = {"pending": [], "expired": []}
    if holding_ids:
        rows = (db.session.query(InviteGroups, Invites)
                .join(Invites, Invites.id == InviteGroups.invite_id)
                .filter(InviteGroups.group_id.in_(holding_ids),
                        Invites.status.in_(list(invites)))
                .order_by(Invites.id).all())
        group_by_id = {entry["group"].id: entry["group"] for entry in groups}
        for row, invite in rows:
            invites[invite.status].append({
                "invite": invite, "group": group_by_id[row.group_id], "role": row.role,
                "site_admin_invite": bool(invite.site_admin_invite)})

    return {
        "restricted": bool(source.restricted),
        "groups": groups,
        "site_admins_covered": covered,
        "site_admins_not_covered": not_covered,
        "stray_grants": stray_grants,
        "public_visuals": [v for v in visuals if v.visibility == "public"],
        "pending_invites": invites["pending"],
        "expired_invites": invites["expired"],
    }

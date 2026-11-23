"""Restricted data sources (data sharing agreements): for a restricted source, site-admin
status counts only when the admin is also a member of a group that holds the source.

Three recurring actors: a non-member site admin (the person the restriction is for), a
member site admin (keeps full powers) and a member Data Owner (the agreement's own lead)."""
import pytest

from data_viz.auth.auth_helpers import can_manage_source, get_assignable_roles, owned_data_sources
from data_viz.auth.role_hierarchy import GROUP_ROLES
from data_viz.auth.restricted import (
    admin_reaches_group,
    admin_reaches_source,
    blocked_group_ids,
    restricted_source_ids,
    uncovered_source_ids,
)
from data_viz.visual_query import accessible_provinces, allowed_visuals

from tests.factories import (
    grant_data_source,
    grant_visual,
    make_data_source,
    make_group,
    make_restricted_source,
    make_user,
    make_visual,
    unique,
)


def names(visuals):
    return sorted(v.name for v in visuals)


class TestTheRule:
    def test_no_restricted_source_means_nothing_is_uncovered_or_blocked(self, db_session):
        source = make_data_source()
        group = make_group()
        grant_data_source(group, source)
        admin = make_user(site_admin=True)
        assert source.id not in restricted_source_ids()
        assert source.id not in uncovered_source_ids(admin)
        assert group.id not in blocked_group_ids(admin)
        assert admin_reaches_source(admin, source.id) is True
        assert admin_reaches_group(admin, group.id) is True

    def test_non_member_is_uncovered_and_the_group_is_blocked(self, db_session):
        source, group = make_restricted_source()
        admin = make_user(site_admin=True)
        assert source.id in restricted_source_ids()
        assert source.id in uncovered_source_ids(admin)
        assert group.id in blocked_group_ids(admin)
        assert admin_reaches_source(admin, source.id) is False
        assert admin_reaches_group(admin, group.id) is False

    def test_any_membership_in_a_holding_group_covers_the_user(self, db_session):
        source, group = make_restricted_source()
        admin = make_user(site_admin=True, group=group, role="Data Viewer")
        assert source.id not in uncovered_source_ids(admin)
        assert group.id not in blocked_group_ids(admin)
        assert admin_reaches_source(admin, source.id) is True
        assert admin_reaches_group(admin, group.id) is True

    def test_membership_in_an_unrelated_group_does_not_cover(self, db_session):
        source, _ = make_restricted_source()
        admin = make_user(site_admin=True, group=make_group(), role="Data Owner")
        assert source.id in uncovered_source_ids(admin)

    def test_covered_through_one_group_reaches_every_group_holding_the_source(self, db_session):
        # Two groups hold the same restricted source; being in one puts you on the agreement,
        # so the other is not blocked either.
        source, group = make_restricted_source()
        second = make_group()
        grant_data_source(second, source)
        admin = make_user(site_admin=True, group=group)
        assert blocked_group_ids(admin) & {group.id, second.id} == set()

    def test_group_holding_two_restricted_sources_needs_both_covered(self, db_session):
        source_a, group_a = make_restricted_source()
        source_b, group_b = make_restricted_source()
        grant_data_source(group_b, source_a)   # group_b now holds both
        admin = make_user(site_admin=True, group=group_a)   # covered for A only
        assert group_a.id not in blocked_group_ids(admin)
        assert group_b.id in blocked_group_ids(admin)

    def test_reaches_helpers_are_false_for_non_admins(self, db_session):
        source = make_data_source()
        group = make_group()
        user = make_user(group=group, role="Data Owner")
        assert admin_reaches_source(user, source.id) is False
        assert admin_reaches_group(user, group.id) is False

    def test_ids_are_coerced_so_a_wrong_kind_of_key_cannot_read_as_reachable(self, db_session):
        # Both helpers test membership of a set of int ids: None or a str would simply miss it.
        source, group = make_restricted_source()
        admin = make_user(site_admin=True)
        with pytest.raises(TypeError):
            admin_reaches_source(admin, None)
        assert admin_reaches_source(admin, str(source.id)) is False
        assert admin_reaches_group(admin, str(group.id)) is False

    def test_anonymous_viewer_context_fails_closed(self, db_session):
        # The 5th element is a DENY set: for an anonymous viewer it is every restricted source,
        # so a caller that skips _can_see's signed-in test still cannot open one.
        from data_viz.visual_query import _viewer_context
        assert _viewer_context(None)[4] == set()   # nothing restricted yet
        source, _ = make_restricted_source()
        make_data_source()
        assert _viewer_context(None)[4] == {source.id}


class TestViewing:
    def test_non_member_site_admin_sees_no_restricted_visuals(self, db_session):
        province = unique("prov")
        source, group = make_restricted_source()
        make_visual(province=province, name="priv", visibility="private", data_source=source)
        granted = make_visual(province=province, name="grp", visibility="group",
                              data_source=source)
        grant_visual(group, granted)
        admin = make_user(site_admin=True)
        assert names(allowed_visuals(admin, province)) == []

    def test_member_site_admin_sees_everything_in_it(self, db_session):
        province = unique("prov")
        source, group = make_restricted_source()
        make_visual(province=province, name="priv", visibility="private", data_source=source)
        make_visual(province=province, name="grp", visibility="group", data_source=source)
        admin = make_user(site_admin=True, group=group, role="Data Viewer")
        assert names(allowed_visuals(admin, province)) == ["grp", "priv"]

    def test_member_roles_keep_their_normal_rules(self, db_session):
        province = unique("prov")
        source, group = make_restricted_source()
        make_visual(province=province, name="priv", visibility="private", data_source=source)
        granted = make_visual(province=province, name="grp", visibility="group",
                              data_source=source)
        grant_visual(group, granted)
        owner = make_user(group=group, role="Data Owner")
        viewer = make_user(group=group, role="Data Viewer")
        assert names(allowed_visuals(owner, province)) == ["grp", "priv"]
        assert names(allowed_visuals(viewer, province)) == ["grp"]

    def test_public_visual_of_a_restricted_source_is_public(self, db_session):
        province = unique("prov")
        source, _ = make_restricted_source()
        make_visual(province=province, name="pub", visibility="public", data_source=source)
        assert names(allowed_visuals(None, province)) == ["pub"]
        assert names(allowed_visuals(make_user(site_admin=True), province)) == ["pub"]

    def test_grant_on_a_group_that_does_not_hold_the_source_reveals_nothing(self, db_session):
        # A stale or stray GroupVisuals row must not leak a restricted visual.
        province = unique("prov")
        source, _ = make_restricted_source()
        visual = make_visual(province=province, name="grp", visibility="group",
                             data_source=source)
        other = make_group()
        grant_visual(other, visual)
        outsider = make_user(group=other, role="Data Viewer")
        assert names(allowed_visuals(outsider, province)) == []

    def test_non_member_site_admin_still_sees_other_sources(self, db_session):
        province = unique("prov")
        make_restricted_source()
        make_visual(province=province, name="other", visibility="private",
                    data_source=make_data_source())
        make_visual(province=province, name="sourceless", visibility="private")
        admin = make_user(site_admin=True)
        assert names(allowed_visuals(admin, province)) == ["other", "sourceless"]

    def test_province_holding_only_restricted_visuals_leaves_the_nav(self, db_session):
        province = unique("prov")
        source, group = make_restricted_source()
        make_visual(province=province, name="map", visibility="private", data_source=source,
                    metric=None)
        assert province not in accessible_provinces(make_user(site_admin=True))
        assert province in accessible_provinces(make_user(site_admin=True, group=group))


class TestManagingTheSource:
    def test_non_member_site_admin_cannot_manage_it(self, db_session):
        source, _ = make_restricted_source()
        make_visual(data_source=source)
        other = make_data_source()
        make_visual(data_source=other)
        admin = make_user(site_admin=True)
        assert can_manage_source(admin, source.id) is False
        assert can_manage_source(admin, other.id) is True
        owned = {s.id for s in owned_data_sources(admin)}
        assert source.id not in owned and other.id in owned

    def test_member_site_admin_manages_it_at_any_role(self, db_session):
        source, group = make_restricted_source()
        make_visual(data_source=source)
        admin = make_user(site_admin=True, group=group, role="Data Viewer")
        assert can_manage_source(admin, source.id) is True
        assert source.id in {s.id for s in owned_data_sources(admin)}

    def test_member_data_owner_manages_it_and_viewer_does_not(self, db_session):
        source, group = make_restricted_source()
        make_visual(data_source=source)
        assert can_manage_source(make_user(group=group, role="Data Owner"), source.id) is True
        assert can_manage_source(make_user(group=group, role="Data Viewer"), source.id) is False


class TestAssignableRoles:
    def test_non_member_site_admin_can_assign_nothing_in_a_blocked_group(self, db_session):
        _, group = make_restricted_source()
        admin = make_user(site_admin=True)
        assert get_assignable_roles(admin, group.id) == []
        assert get_assignable_roles(admin, make_group().id) != []

    def test_member_site_admin_can_assign_every_role(self, db_session):
        _, group = make_restricted_source()
        admin = make_user(site_admin=True, group=group, role="Data Viewer")
        assert sorted(get_assignable_roles(admin, group.id)) == [
            "Data Owner", "Data Viewer", "Group Admin"]

    def test_precomputed_blocked_set_gives_the_same_answers(self, db_session):
        _, group = make_restricted_source()
        admin = make_user(site_admin=True)
        blocked = blocked_group_ids(admin)
        assert get_assignable_roles(admin, group.id, blocked=blocked) == []
        assert get_assignable_roles(admin, make_group().id, blocked=blocked) == list(GROUP_ROLES)

    def test_blocked_parameter_is_trusted_as_given(self, db_session):
        """Documents the contract: `blocked` is the caller's responsibility. Passing an empty set
        for a group that IS blocked returns every role -- the helper does not re-check, so a
        caller must pass blocked_group_ids(user) for that same user, or nothing."""
        _, group = make_restricted_source()
        admin = make_user(site_admin=True)
        assert get_assignable_roles(admin, group.id, blocked=set()) == list(GROUP_ROLES)
        assert get_assignable_roles(admin, group.id) == []


class TestSourceAliasReconcile:
    """reconcile_source_aliases folds a legacy-named DataSources row into its canonical one and
    moves the alias's group links across. An alias row is unrestricted and attachable by any
    site admin, so moving its links onto a RESTRICTED canonical source would be a side door
    into the agreement."""

    def test_links_are_not_carried_onto_a_restricted_canonical_source(self, db_session,
                                                                      monkeypatch):
        from data_viz.auth import auth_helpers
        from data_viz.database.models import DataSources, GroupDataSources
        canonical, holders = make_restricted_source()
        alias = make_data_source()
        sneaky = make_group()
        grant_data_source(sneaky, alias)
        monkeypatch.setattr(auth_helpers, "SOURCE_ALIASES", {canonical.name: [alias.name]})
        merges = auth_helpers.reconcile_source_aliases()
        assert len(merges) == 1 and "restricted" in merges[0]
        assert DataSources.query.filter_by(name=alias.name).count() == 0
        held_by = {g.group_id for g in
                   GroupDataSources.query.filter_by(data_source_id=canonical.id)}
        assert held_by == {holders.id}

    def test_links_still_move_onto_an_open_canonical_source(self, db_session, monkeypatch):
        from data_viz.auth import auth_helpers
        from data_viz.database.models import GroupDataSources
        canonical, alias, group = make_data_source(), make_data_source(), make_group()
        grant_data_source(group, alias)
        monkeypatch.setattr(auth_helpers, "SOURCE_ALIASES", {canonical.name: [alias.name]})
        auth_helpers.reconcile_source_aliases()
        assert GroupDataSources.query.filter_by(group_id=group.id,
                                                data_source_id=canonical.id).count() == 1

    def test_a_restricted_alias_row_is_refused_not_merged(self, db_session, monkeypatch):
        # Merging deletes the alias row -- and with it the restriction -- and moves its holding
        # group onto the open canonical source.
        from data_viz.auth import auth_helpers
        from data_viz.database import db
        from data_viz.database.models import DataSources, GroupDataSources
        canonical = make_data_source()
        alias, holders = make_restricted_source()
        # The refusal rolls back: land it on a savepoint, not on the fixture's transaction.
        db.session.commit()
        for connection in db.engines.values():
            connection.begin_nested()
        monkeypatch.setattr(auth_helpers, "SOURCE_ALIASES", {canonical.name: [alias.name]})
        with pytest.raises(ValueError, match="SOURCE_ALIASES"):
            auth_helpers.reconcile_source_aliases()
        row = DataSources.query.filter_by(name=alias.name).one()
        assert row.restricted is True
        assert {g.group_id for g in
                GroupDataSources.query.filter_by(data_source_id=row.id)} == {holders.id}
        assert GroupDataSources.query.filter_by(data_source_id=canonical.id).count() == 0


class TestDetachingASourceClearsItsGrants:
    """A group that loses a source loses its visual grants for that source too -- left behind
    they are live access for an open source, and nothing in the app can revoke them (team
    grants are only editable for groups that hold the source)."""

    def test_detach_removes_the_groups_grants_for_that_source_only(self, db_session):
        from data_viz.auth.auth_helpers import set_group_data_sources
        from data_viz.database.models import GroupVisuals
        source, other, group = make_data_source(), make_data_source(), make_group()
        grant_data_source(group, source)
        grant_data_source(group, other)
        gone = make_visual(visibility="group", data_source=source)
        kept = make_visual(visibility="group", data_source=other)
        grant_visual(group, gone)
        grant_visual(group, kept)
        set_group_data_sources(group.id, [other.id])
        assert {g.visual_id for g in GroupVisuals.query.filter_by(group_id=group.id)} == {kept.id}

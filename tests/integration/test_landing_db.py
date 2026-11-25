"""What a province page opens on: the viewer's own starting chart if they set one (any chart
they can see), else a group's pick for its own members, then the site-wide default. Structural visuals (metric=None) always
count as having data, so these tests are about the resolution order only."""
import pytest

from data_viz.auth.auth_helpers import (
    landing_candidates,
    landing_scope_label,
    landing_scopes_for,
    set_group_data_sources,
    set_group_landing,
    set_province_default_visual,
    set_user_landing_choice,
    user_starting_charts,
)
from data_viz.database.models import GroupLandingVisuals, UserActivity, UserLandingChoices
from data_viz.visual_query import build_province_menu

from tests.factories import (
    add_membership,
    choose_landing,
    grant_data_source,
    grant_visual,
    make_data_source,
    make_group,
    make_restricted_source,
    make_user,
    make_visual,
    set_landing,
    unique,
)


def names(visuals):
    return [v.name for v in visuals]


def _province(*names, default=None):
    """A province with public level-1 visuals named `names`; `default` carries the site default.
    Returns (province, {name: visual})."""
    province = unique("prov")
    visuals = {name: make_visual(province=province, name=name, visibility="public", level="1",
                                 menu_name=name.title(), is_default=(name == default))
               for name in names}
    return province, visuals


class TestSingleGroupPick:
    def test_member_opens_on_their_groups_pick(self, db_session):
        province, v = _province("site", "ours", default="site")
        group = make_group()
        member = make_user(group=group)
        set_landing(group, v["ours"])
        assert build_province_menu(province, member)["default"] == "ours"

    def test_one_pick_offers_no_switch(self, db_session):
        province, v = _province("site", "ours", default="site")
        group = make_group()
        member = make_user(group=group)
        set_landing(group, v["ours"])
        assert build_province_menu(province, member)["landing"] is None

    def test_non_member_keeps_the_site_default(self, db_session):
        province, v = _province("site", "ours", default="site")
        set_landing(make_group(), v["ours"])
        assert build_province_menu(province, make_user())["default"] == "site"

    def test_anonymous_keeps_the_site_default(self, db_session):
        province, v = _province("site", "ours", default="site")
        set_landing(make_group(), v["ours"])
        menu = build_province_menu(province, None)
        assert menu["default"] == "site"
        assert menu["landing"] is None

    def test_pick_in_another_province_does_not_apply(self, db_session):
        province, _ = _province("site", "other", default="site")
        _, elsewhere = _province("theirs")
        group = make_group()
        member = make_user(group=group)
        set_landing(group, elsewhere["theirs"])
        assert build_province_menu(province, member)["default"] == "site"

    def test_pick_the_member_cannot_see_is_skipped(self, db_session):
        """A group-level visual whose grant was removed after it was picked."""
        province, _ = _province("site", default="site")
        hidden = make_visual(province=province, name="hidden", visibility="group", level="1")
        group = make_group()
        member = make_user(group=group)
        set_landing(group, hidden)
        assert build_province_menu(province, member)["default"] == "site"

    def test_granted_group_level_pick_applies(self, db_session):
        province, _ = _province("site", default="site")
        shared = make_visual(province=province, name="shared", visibility="group", level="1")
        group = make_group()
        member = make_user(group=group)
        grant_visual(group, shared)
        set_landing(group, shared)
        assert build_province_menu(province, member)["default"] == "shared"

    def test_drill_child_pick_is_ignored(self, db_session):
        province, _ = _province("site", default="site")
        make_visual(province=province, name="parent", visibility="public", level="1",
                    next_vis_name="child")
        child = make_visual(province=province, name="child", visibility="public", level="2",
                            vis_parent_name="parent")
        group = make_group()
        member = make_user(group=group)
        set_landing(group, child)
        assert build_province_menu(province, member)["default"] == "site"


class TestOwnersOnlyPick:
    """A pick on the group's own Private visual: its Data Owners open on it, its other
    members -- who can't see it -- keep the site default."""

    def _private_pick(self):
        province, _ = _province("site", default="site")
        source = make_data_source()
        group = make_group()
        grant_data_source(group, source)
        priv = make_visual(province=province, name="priv", visibility="private", level="1",
                           data_source=source)
        set_landing(group, priv)
        return province, group

    def test_data_owner_opens_on_it(self, db_session):
        province, group = self._private_pick()
        owner = make_user(group=group, role="Data Owner")
        assert build_province_menu(province, owner)["default"] == "priv"

    def test_data_viewer_keeps_the_site_default(self, db_session):
        province, group = self._private_pick()
        viewer = make_user(group=group, role="Data Viewer")
        assert build_province_menu(province, viewer)["default"] == "site"


class TestPersonalChoice:
    """A signed-in user may start a page on ANY top-level chart they can see. It is their own
    setting: it beats their groups' picks and does not depend on being in a group at all."""

    def test_beats_the_site_default_without_any_group(self, db_session):
        province, v = _province("site", "mine", default="site")
        user = make_user()
        choose_landing(user, v["mine"])
        assert build_province_menu(province, user)["default"] == "mine"

    def test_beats_their_groups_pick(self, db_session):
        province, v = _province("site", "groups", "mine", default="site")
        group = make_group()
        user = make_user(group=group, role="Data Owner")
        set_landing(group, v["groups"])
        choose_landing(user, v["mine"])
        assert build_province_menu(province, user)["default"] == "mine"

    def test_is_theirs_alone(self, db_session):
        province, v = _province("site", "mine", default="site")
        choose_landing(make_user(), v["mine"])
        assert build_province_menu(province, make_user())["default"] == "site"
        assert build_province_menu(province, None)["default"] == "site"

    def test_chart_they_can_no_longer_see_falls_back(self, db_session):
        province, v = _province("site", "groups", default="site")
        hidden = make_visual(province=province, name="hidden", visibility="private", level="1")
        group = make_group()
        user = make_user(group=group)
        set_landing(group, v["groups"])
        choose_landing(user, hidden)
        assert build_province_menu(province, user)["default"] == "groups"

    def test_drill_down_choice_is_ignored(self, db_session):
        province, _ = _province("site", default="site")
        make_visual(province=province, name="parent", visibility="public", level="1",
                    next_vis_name="child")
        child = make_visual(province=province, name="child", visibility="public", level="2",
                            vis_parent_name="parent")
        user = make_user()
        choose_landing(user, child)
        assert build_province_menu(province, user)["default"] == "site"

    def test_choice_for_another_page_does_not_apply(self, db_session):
        province, _ = _province("site", "other", default="site")
        _, elsewhere = _province("theirs")
        user = make_user()
        choose_landing(user, elsewhere["theirs"])
        assert build_province_menu(province, user)["default"] == "site"


class TestTwoGroupsDisagree:
    """No personal choice, and the user's groups picked different charts: rank decides, and the
    page is told so it can ask (the one-time notice)."""

    def _two_groups(self, first_role="Data Viewer", second_role="Data Viewer"):
        province, v = _province("site", "alpha", "beta", default="site")
        first, second = make_group(name=unique("First")), make_group(name=unique("Second"))
        user = make_user()
        add_membership(user, first, first_role)
        add_membership(user, second, second_role)
        set_landing(first, v["alpha"])
        set_landing(second, v["beta"])
        return province, user, v

    def test_higher_role_wins(self, db_session):
        province, user, _ = self._two_groups("Data Viewer", "Data Owner")
        assert build_province_menu(province, user)["default"] == "beta"

    def test_equal_roles_older_membership_wins(self, db_session):
        province, user, _ = self._two_groups()
        assert build_province_menu(province, user)["default"] == "alpha"

    def test_the_notice_lists_both_picks(self, db_session):
        province, user, v = self._two_groups()
        landing = build_province_menu(province, user)["landing"]
        assert landing["active"] == "alpha"
        assert [(o["visual"], o["visual_id"], o["label"]) for o in landing["options"]] == [
            ("alpha", v["alpha"].id, "Alpha"), ("beta", v["beta"].id, "Beta")]
        assert all(o["group"] for o in landing["options"])

    def test_no_notice_once_they_have_their_own_choice(self, db_session):
        province, user, v = self._two_groups()
        choose_landing(user, v["beta"])
        menu = build_province_menu(province, user)
        assert menu["default"] == "beta"
        assert menu["landing"] is None

    def test_choosing_some_third_chart_also_ends_the_notice(self, db_session):
        province, user, v = self._two_groups()
        choose_landing(user, v["site"])
        menu = build_province_menu(province, user)
        assert menu["default"] == "site"
        assert menu["landing"] is None

    def test_choice_they_can_no_longer_see_brings_the_notice_back(self, db_session):
        province, user, _ = self._two_groups()
        choose_landing(user, make_visual(province=province, name="gone", visibility="private",
                                         level="1"))
        menu = build_province_menu(province, user)
        assert menu["default"] == "alpha"
        assert menu["landing"] is not None

    def test_an_unseen_pick_leaves_no_notice(self, db_session):
        """Only one of the two picks is visible to the user: nothing to choose between."""
        province, v = _province("site", "alpha", default="site")
        hidden = make_visual(province=province, name="hidden", visibility="group", level="1")
        first, second = make_group(), make_group()
        user = make_user()
        add_membership(user, first, "Data Owner")
        add_membership(user, second)
        set_landing(first, hidden)
        set_landing(second, v["alpha"])
        menu = build_province_menu(province, user)
        assert menu["default"] == "alpha"
        assert menu["landing"] is None


class TestTwoGroupsAgree:
    def test_same_visual_twice_needs_no_notice(self, db_session):
        province, v = _province("site", "alpha", default="site")
        user = make_user()
        for group in (make_group(), make_group()):
            add_membership(user, group)
            set_landing(group, v["alpha"])
        menu = build_province_menu(province, user)
        assert menu["default"] == "alpha"
        assert menu["landing"] is None


class TestSiteDefaultStillApplies:
    def test_menu_without_any_pick_reports_no_landing(self, db_session):
        province, _ = _province("first", "flagged", default="flagged")
        menu = build_province_menu(province, make_user())
        assert menu["default"] == "flagged"
        assert menu["landing"] is None


class TestLandingCandidates:
    """What a group may pick: level-1 visuals every one of its members can see."""

    def test_public_visuals_are_offered(self, db_session):
        province, _ = _province("one", "two")
        assert names(landing_candidates(make_group().id, province)) == ["one", "two"]

    def test_group_level_visual_needs_a_grant_to_this_group(self, db_session):
        province = unique("prov")
        shared = make_visual(province=province, name="shared", visibility="group", level="1")
        mine, other = make_group(), make_group()
        grant_visual(mine, shared)
        assert names(landing_candidates(mine.id, province)) == ["shared"]
        assert landing_candidates(other.id, province) == []

    def test_private_visual_of_a_source_the_group_holds_is_offered(self, db_session):
        """The group's own data: its Data Owners see it at any level, so it can be their
        starting chart even before anything is shared more widely."""
        province = unique("prov")
        source = make_data_source()
        group = make_group()
        grant_data_source(group, source)
        make_visual(province=province, name="priv", visibility="private", level="1",
                    data_source=source)
        assert names(landing_candidates(group.id, province)) == ["priv"]

    def test_ungranted_group_level_visual_of_a_held_source_is_offered(self, db_session):
        province = unique("prov")
        source = make_data_source()
        group = make_group()
        grant_data_source(group, source)
        make_visual(province=province, name="grp", visibility="group", level="1",
                    data_source=source)
        assert names(landing_candidates(group.id, province)) == ["grp"]

    def test_private_visual_of_a_source_the_group_does_not_hold_is_not_offered(self, db_session):
        province = unique("prov")
        make_visual(province=province, name="priv", visibility="private", level="1",
                    data_source=make_data_source())
        assert landing_candidates(make_group().id, province) == []

    def test_drill_child_is_not_offered(self, db_session):
        province = unique("prov")
        make_visual(province=province, name="parent", visibility="public", level="1",
                    next_vis_name="child")
        make_visual(province=province, name="child", visibility="public", level="2",
                    vis_parent_name="parent")
        assert names(landing_candidates(make_group().id, province)) == ["parent"]

    def test_visual_without_data_is_not_offered(self, db_session):
        province = unique("prov")
        make_visual(province=province, name="empty", visibility="public", level="1",
                    data_source=make_data_source(), metric="deaths", geo_type="province")
        assert landing_candidates(make_group().id, province) == []

    def test_restricted_group_level_visual_only_for_a_holding_group(self, db_session):
        """A stray grant on a group that does not hold the restricted source shows nothing
        to its members (visual_query._can_see), so it is not a pick either."""
        province = unique("prov")
        source, holder = make_restricted_source()
        visual = make_visual(province=province, name="agreed", visibility="group", level="1",
                             data_source=source)
        stray = make_group()
        grant_visual(holder, visual)
        grant_visual(stray, visual)
        assert names(landing_candidates(holder.id, province)) == ["agreed"]
        assert landing_candidates(stray.id, province) == []


class TestLandingScopes:
    def _scope(self, group, province):
        return next((s for s in landing_scopes_for(group.id) if s["province"] == province), None)

    def test_province_with_a_choice_to_make_is_listed(self, db_session):
        province, v = _province("one", "two")
        group = make_group()
        scope = self._scope(group, province)
        assert names(scope["candidates"]) == ["one", "two"]
        assert scope["current"] is None
        assert scope["stale"] is False

    def test_province_with_a_single_candidate_is_not_listed(self, db_session):
        province, _ = _province("only")
        assert self._scope(make_group(), province) is None

    def test_current_pick_is_reported(self, db_session):
        province, v = _province("one", "two")
        group = make_group()
        set_landing(group, v["two"])
        scope = self._scope(group, province)
        assert scope["current"].name == "two"
        assert scope["stale"] is False

    def test_pick_that_is_no_longer_a_candidate_is_flagged_stale(self, db_session):
        province, v = _province("one", "two")
        gone = make_visual(province=province, name="gone", visibility="group", level="1")
        group = make_group()
        set_landing(group, gone)
        scope = self._scope(group, province)
        assert scope["current"].name == "gone"
        assert scope["stale"] is True

    def test_picks_only_the_groups_data_owners_see_are_flagged(self, db_session):
        """Public and shared visuals reach every member; the group's own Private (or not yet
        shared) visuals reach only its Data Owners -- the modal says which is which."""
        province = unique("prov")
        source = make_data_source()
        group = make_group()
        grant_data_source(group, source)
        pub = make_visual(province=province, name="pub", visibility="public", level="1")
        shared = make_visual(province=province, name="shared", visibility="group", level="1",
                             data_source=source)
        grant_visual(group, shared)
        priv = make_visual(province=province, name="priv", visibility="private", level="1",
                           data_source=source)
        unshared = make_visual(province=province, name="unshared", visibility="group",
                               level="1", data_source=source)
        scope = self._scope(group, province)
        assert names(scope["candidates"]) == ["pub", "shared", "priv", "unshared"]
        assert scope["owners_only"] == {priv.id, unshared.id}

    def test_label_is_the_readable_province_name(self, db_session):
        make_visual(province="nova-scotia", name=unique("a"), visibility="public", level="1")
        make_visual(province="nova-scotia", name=unique("b"), visibility="public", level="1")
        assert self._scope(make_group(), "nova-scotia")["label"] == "Nova Scotia"

    def test_national_scope_is_labelled_by_its_dashboard(self, db_session):
        assert landing_scope_label("canada") == "Drug Checking"

    def test_scopes_with_the_groups_own_shared_visuals_come_first(self, db_session):
        plain, _ = _province("one", "two")
        shared_province = unique("zzprov")
        group = make_group()
        for name in ("s1", "s2"):
            grant_visual(group, make_visual(province=shared_province, name=name,
                                            visibility="group", level="1"))
        order = [s["province"] for s in landing_scopes_for(group.id)]
        assert order.index(shared_province) < order.index(plain)


class TestSetGroupLanding:
    def test_sets_the_pick(self, db_session):
        province, v = _province("one", "two")
        group, owner = make_group(), make_user()
        changes = set_group_landing(group.id, {province: v["two"].id}, changed_by=owner.id)
        row = GroupLandingVisuals.query.filter_by(group_id=group.id, province=province).one()
        assert row.visual_id == v["two"].id
        assert row.set_by == owner.id
        assert len(changes) == 1

    def test_logs_one_activity_row(self, db_session):
        province, v = _province("one", "two")
        group, owner = make_group(), make_user()
        set_group_landing(group.id, {province: v["two"].id}, changed_by=owner.id)
        log = UserActivity.query.filter_by(activity_type="group_landing_updated",
                                           activity_target_id=group.id).one()
        assert log.user_id == owner.id
        assert log.activity_target_type == "group"
        assert group.name in log.details

    def test_changing_the_pick_replaces_it(self, db_session):
        province, v = _province("one", "two")
        group = make_group()
        set_landing(group, v["one"])
        set_group_landing(group.id, {province: v["two"].id}, changed_by=make_user().id)
        row = GroupLandingVisuals.query.filter_by(group_id=group.id, province=province).one()
        assert row.visual_id == v["two"].id

    def test_none_clears_the_pick(self, db_session):
        province, v = _province("one", "two")
        group = make_group()
        set_landing(group, v["one"])
        set_group_landing(group.id, {province: None}, changed_by=make_user().id)
        assert GroupLandingVisuals.query.filter_by(group_id=group.id).count() == 0

    def test_provinces_not_submitted_are_untouched(self, db_session):
        province, v = _province("one", "two")
        other_province, w = _province("three", "four")
        group = make_group()
        set_landing(group, v["one"])
        set_group_landing(group.id, {other_province: w["four"].id}, changed_by=make_user().id)
        assert GroupLandingVisuals.query.filter_by(group_id=group.id).count() == 2

    def test_non_candidate_is_refused_and_nothing_is_saved(self, db_session):
        province, v = _province("one", "two")
        hidden = make_visual(province=province, name="hidden", visibility="private", level="1")
        group = make_group()
        with pytest.raises(ValueError):
            set_group_landing(group.id, {province: v["one"].id, unique("prov"): hidden.id},
                              changed_by=make_user().id)
        assert GroupLandingVisuals.query.filter_by(group_id=group.id).count() == 0

    def test_visual_from_another_province_is_refused(self, db_session):
        province, _ = _province("one", "two")
        _, elsewhere = _province("three")
        with pytest.raises(ValueError):
            set_group_landing(make_group().id, {province: elsewhere["three"].id},
                              changed_by=make_user().id)

    def test_no_change_logs_nothing(self, db_session):
        province, v = _province("one", "two")
        group = make_group()
        set_landing(group, v["one"])
        assert set_group_landing(group.id, {province: v["one"].id},
                                 changed_by=make_user().id) == []
        assert UserActivity.query.filter_by(activity_type="group_landing_updated",
                                            activity_target_id=group.id).count() == 0


class TestSetUserLandingChoice:
    def test_choice_is_stored_and_followed(self, db_session):
        province, v = _province("site", "mine", default="site")
        user = make_user()
        assert set_user_landing_choice(user, province, v["mine"].id) == "mine"
        assert build_province_menu(province, user)["default"] == "mine"

    def test_choosing_again_keeps_one_row(self, db_session):
        province, v = _province("site", "one", "two", default="site")
        user = make_user()
        set_user_landing_choice(user, province, v["one"].id)
        set_user_landing_choice(user, province, v["two"].id)
        row = UserLandingChoices.query.filter_by(user_id=user.id, province=province).one()
        assert row.visual_id == v["two"].id

    def test_none_goes_back_to_the_default(self, db_session):
        province, v = _province("site", "mine", default="site")
        user = make_user()
        choose_landing(user, v["mine"])
        assert set_user_landing_choice(user, province, None) == "site"
        assert UserLandingChoices.query.filter_by(user_id=user.id).count() == 0

    def test_back_to_default_means_the_groups_pick_when_there_is_one(self, db_session):
        province, v = _province("site", "groups", "mine", default="site")
        group = make_group()
        user = make_user(group=group)
        set_landing(group, v["groups"])
        choose_landing(user, v["mine"])
        assert set_user_landing_choice(user, province, None) == "groups"

    def test_chart_they_cannot_see_is_refused(self, db_session):
        province, _ = _province("site", default="site")
        hidden = make_visual(province=province, name="hidden", visibility="private", level="1")
        user = make_user()
        with pytest.raises(ValueError):
            set_user_landing_choice(user, province, hidden.id)
        assert UserLandingChoices.query.filter_by(user_id=user.id).count() == 0

    def test_drill_down_is_refused(self, db_session):
        province, _ = _province("site", default="site")
        make_visual(province=province, name="parent", visibility="public", level="1",
                    next_vis_name="child")
        child = make_visual(province=province, name="child", visibility="public", level="2",
                            vis_parent_name="parent")
        with pytest.raises(ValueError):
            set_user_landing_choice(make_user(), province, child.id)

    def test_chart_from_another_page_is_refused(self, db_session):
        province, _ = _province("site", default="site")
        _, elsewhere = _province("theirs")
        with pytest.raises(ValueError):
            set_user_landing_choice(make_user(), province, elsewhere["theirs"].id)

    def test_garbage_is_refused(self, db_session):
        province, _ = _province("site", default="site")
        with pytest.raises(ValueError):
            set_user_landing_choice(make_user(), province, "banana")

    def test_a_change_is_logged(self, db_session):
        province, v = _province("site", "mine", default="site")
        user = make_user()
        set_user_landing_choice(user, province, v["mine"].id)
        log = UserActivity.query.filter_by(activity_type="landing_choice_updated",
                                           user_id=user.id).one()
        assert log.activity_target_id == v["mine"].id


class TestSiteDefaultGuard:
    def test_default_on_a_source_the_admin_is_outside_of_cannot_be_moved(self, db_session):
        """A site admin outside a data sharing agreement must not be able to change which
        visual a page opens on by un-starring a visual they can neither see nor manage."""
        province = unique("prov")
        source, _ = make_restricted_source()
        held = make_visual(province=province, name="held", visibility="private", level="1",
                           data_source=source, is_default=True)
        open_visual = make_visual(province=province, name="open", visibility="public",
                                  level="1", data_source=make_data_source())
        outsider = make_user(site_admin=True)
        with pytest.raises(ValueError):
            set_province_default_visual(open_visual.id, changed_by=outsider.id)
        assert held.is_default is True
        assert not open_visual.is_default

    def test_covered_admin_can_move_it(self, db_session):
        province = unique("prov")
        source, group = make_restricted_source()
        held = make_visual(province=province, name="held", visibility="private", level="1",
                           data_source=source, is_default=True)
        open_visual = make_visual(province=province, name="open", visibility="public",
                                  level="1", data_source=make_data_source())
        insider = make_user(site_admin=True, group=group)
        assert set_province_default_visual(open_visual.id, changed_by=insider.id) is True
        assert held.is_default is False


class TestDetachingASourceClearsItsPicks:
    """A group that loses a data source loses its visual grants for it (set_group_data_sources);
    a starting chart resting on one of those grants goes with them, so re-attaching the source
    later cannot quietly bring an old pick back to life."""

    def _holding_group(self):
        province = unique("prov")
        source = make_data_source()
        group = make_group()
        grant_data_source(group, source)
        return province, source, group

    def test_pick_on_a_group_level_visual_of_the_source_is_cleared(self, db_session):
        province, source, group = self._holding_group()
        shared = make_visual(province=province, name="shared", visibility="group", level="1",
                             data_source=source)
        grant_visual(group, shared)
        set_landing(group, shared)
        changes = set_group_data_sources(group.id, [], changed_by=make_user().id)
        assert GroupLandingVisuals.query.filter_by(group_id=group.id).count() == 0
        assert "1 starting chart cleared" in changes

    def test_pick_on_a_public_visual_of_the_source_is_kept(self, db_session):
        """Members can still see a Public visual, whoever holds its source."""
        province, source, group = self._holding_group()
        public = make_visual(province=province, name="pub", visibility="public", level="1",
                             data_source=source)
        set_landing(group, public)
        set_group_data_sources(group.id, [], changed_by=make_user().id)
        assert GroupLandingVisuals.query.filter_by(group_id=group.id).count() == 1

    def test_pick_on_another_sources_visual_is_kept(self, db_session):
        province, source, group = self._holding_group()
        other = make_data_source()
        grant_data_source(group, other)
        shared = make_visual(province=province, name="shared", visibility="group", level="1",
                             data_source=other)
        grant_visual(group, shared)
        set_landing(group, shared)
        set_group_data_sources(group.id, [other.id], changed_by=make_user().id)
        assert GroupLandingVisuals.query.filter_by(group_id=group.id).count() == 1

    def test_another_groups_pick_is_untouched(self, db_session):
        province, source, group = self._holding_group()
        shared = make_visual(province=province, name="shared", visibility="group", level="1",
                             data_source=source)
        other_group = make_group()
        grant_data_source(other_group, source)
        grant_visual(other_group, shared)
        set_landing(other_group, shared)
        set_group_data_sources(group.id, [], changed_by=make_user().id)
        assert GroupLandingVisuals.query.filter_by(group_id=other_group.id).count() == 1


class TestUserStartingCharts:
    """What the account page lists: one row per page the user can open, with every top-level
    chart they can see there, their own choice, and what the page opens on otherwise."""

    def _row(self, user, province):
        return next((r for r in user_starting_charts(user, [province])), None)

    def test_page_lists_every_chart_they_can_see(self, db_session):
        province, _ = _province("one", "two", "three")
        make_visual(province=province, name="hidden", visibility="private", level="1")
        row = self._row(make_user(), province)
        assert names(row["charts"]) == ["one", "two", "three"]

    def test_drill_downs_are_not_listed(self, db_session):
        province = unique("prov")
        make_visual(province=province, name="parent", visibility="public", level="1",
                    next_vis_name="child")
        make_visual(province=province, name="child", visibility="public", level="2",
                    vis_parent_name="parent")
        assert names(self._row(make_user(), province)["charts"]) == ["parent"]

    def test_no_choice_reports_the_default_it_opens_on(self, db_session):
        province, _ = _province("site", "other", default="site")
        row = self._row(make_user(), province)
        assert row["chosen_id"] is None
        assert row["default"].name == "site"

    def test_default_is_the_groups_pick_for_a_member(self, db_session):
        province, v = _province("site", "groups", default="site")
        group = make_group()
        user = make_user(group=group)
        set_landing(group, v["groups"])
        assert self._row(user, province)["default"].name == "groups"

    def test_their_choice_is_reported_and_the_default_is_still_the_fallback(self, db_session):
        province, v = _province("site", "mine", default="site")
        user = make_user()
        choose_landing(user, v["mine"])
        row = self._row(user, province)
        assert row["chosen_id"] == v["mine"].id
        assert row["default"].name == "site"

    def test_choice_they_can_no_longer_see_reads_as_no_choice(self, db_session):
        province, _ = _province("site", default="site")
        user = make_user()
        choose_landing(user, make_visual(province=province, name="gone", visibility="private",
                                         level="1"))
        assert self._row(user, province)["chosen_id"] is None

    def test_page_with_nothing_they_can_see_is_left_out(self, db_session):
        province = unique("prov")
        make_visual(province=province, name="hidden", visibility="private", level="1")
        assert self._row(make_user(), province) is None

    def test_label_is_the_readable_page_name(self, db_session):
        province, _ = _province("one")
        assert self._row(make_user(), province)["label"] == landing_scope_label(province)

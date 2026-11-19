"""The server-side commands that go with restricted data sources: `flask source-access`
(who can see a source right now -- the list to check against the agreement) and
`flask set-group-role` (lockout recovery when no member can manage the group in the app)."""
from data_viz.auth.restricted import source_access_report
from data_viz.database.models import User, UserActivity, UserGroups

from tests.factories import (
    grant_visual,
    make_data_source,
    make_group,
    make_invite,
    make_restricted_source,
    make_user,
    make_visual,
    unique,
)


def emails(users):
    return sorted(u.email for u in users)


class TestSourceAccessReport:
    def test_lists_holding_groups_and_their_members_by_role(self, db_session):
        source, group = make_restricted_source()
        owner = make_user(group=group, role="Data Owner")
        viewer = make_user(group=group, role="Data Viewer")
        report = source_access_report(source)
        assert report["restricted"] is True
        (entry,) = report["groups"]
        assert entry["group"].id == group.id
        assert sorted((m["user"].email, m["role"]) for m in entry["members"]) == sorted(
            [(owner.email, "Data Owner"), (viewer.email, "Data Viewer")])

    def test_splits_active_site_admins_into_covered_and_not(self, db_session):
        source, group = make_restricted_source()
        insider = make_user(site_admin=True, group=group)
        outsider = make_user(site_admin=True)
        make_user(site_admin=True, status=User.STATUS_DEACTIVATED)   # not listed either way
        report = source_access_report(source)
        assert insider.email in emails(report["site_admins_covered"])
        assert outsider.email in emails(report["site_admins_not_covered"])
        assert outsider.email not in emails(report["site_admins_covered"])
        listed = report["site_admins_covered"] + report["site_admins_not_covered"]
        assert all(u.status == User.STATUS_ACTIVE for u in listed)

    def test_every_site_admin_is_covered_for_an_unrestricted_source(self, db_session):
        source = make_data_source()
        admin = make_user(site_admin=True)
        report = source_access_report(source)
        assert report["restricted"] is False
        assert admin.email in emails(report["site_admins_covered"])
        assert report["site_admins_not_covered"] == []

    def test_flags_grants_on_groups_that_do_not_hold_the_source(self, db_session):
        source, group = make_restricted_source()
        visual = make_visual(visibility="group", data_source=source)
        grant_visual(group, visual)            # a normal grant: not flagged
        stranger = make_group()
        grant_visual(stranger, visual)         # a stray one
        report = source_access_report(source)
        assert [(g.id, [v.id for v in visuals]) for g, visuals in report["stray_grants"]] == [
            (stranger.id, [visual.id])]

    def test_lists_public_visuals(self, db_session):
        source, _ = make_restricted_source()
        public = make_visual(visibility="public", data_source=source)
        make_visual(visibility="private", data_source=source)
        assert [v.id for v in source_access_report(source)["public_visuals"]] == [public.id]

    def test_lists_pending_invites_into_holding_groups(self, db_session):
        source, group = make_restricted_source()
        pending = make_invite(groups=[(group, "Data Viewer")])
        make_invite(status="revoked", groups=[(group, "Data Viewer")])
        make_invite(groups=[(make_group(), "Data Viewer")])
        report = source_access_report(source)
        assert [(i["invite"].id, i["group"].id, i["role"]) for i in report["pending_invites"]] == [
            (pending.id, group.id, "Data Viewer")]


class TestSourceAccessCli:
    def _run(self, app, *args):
        return app.test_cli_runner().invoke(args=["source-access", *args])

    def test_prints_the_people_to_check_against_the_agreement(self, app, db_session):
        source, group = make_restricted_source()
        owner = make_user(group=group, role="Data Owner")
        outsider = make_user(site_admin=True)
        result = self._run(app, source.name)
        assert result.exit_code == 0, result.output
        assert "RESTRICTED" in result.output
        assert group.name in result.output
        assert owner.email in result.output and "Data Owner" in result.output
        assert outsider.email in result.output

    def test_no_argument_lists_every_source_with_its_flag(self, app, db_session):
        source, _ = make_restricted_source()
        plain = make_data_source()
        result = self._run(app)
        assert result.exit_code == 0, result.output
        lines = {line.strip() for line in result.output.splitlines()}
        assert f"[restricted] {source.name}" in lines
        assert f"[open]       {plain.name}" in lines

    def test_unknown_source_exits_nonzero(self, app, db_session):
        result = self._run(app, unique("no-such-source"))
        assert result.exit_code == 1
        assert "No data source named" in result.output


class TestSetGroupRoleCli:
    def _run(self, app, *args):
        return app.test_cli_runner().invoke(args=["set-group-role", *args])

    def _roles(self, user, group):
        return [m.role for m in UserGroups.query.filter_by(user_id=user.id, group_id=group.id)]

    def test_adds_a_member_to_a_protected_group(self, app, db_session):
        _, group = make_restricted_source()
        user = make_user()
        result = self._run(app, user.email, group.name, "Data Owner")
        assert result.exit_code == 0, result.output
        assert self._roles(user, group) == ["Data Owner"]
        assert UserActivity.query.filter_by(user_id=user.id,
                                            activity_type="group_assignment").count() == 1

    def test_changes_an_existing_role(self, app, db_session):
        group = make_group()
        user = make_user(group=group, role="Data Viewer")
        result = self._run(app, user.email, group.name, "Group Admin")
        assert result.exit_code == 0, result.output
        assert self._roles(user, group) == ["Group Admin"]

    def test_remove_flag_drops_the_membership(self, app, db_session):
        group = make_group()
        user = make_user(group=group, role="Data Viewer")
        result = self._run(app, user.email, group.name, "--remove")
        assert result.exit_code == 0, result.output
        assert self._roles(user, group) == []

    def test_site_admin_is_not_a_group_role(self, app, db_session):
        group = make_group()
        user = make_user()
        result = self._run(app, user.email, group.name, "Site Admin")
        assert result.exit_code == 1
        assert self._roles(user, group) == []

    def test_unknown_user_or_group_exits_nonzero(self, app, db_session):
        group = make_group()
        user = make_user()
        assert self._run(app, "nobody@example.org", group.name, "Data Viewer").exit_code == 1
        assert self._run(app, user.email, unique("no-such-group"), "Data Viewer").exit_code == 1

    def test_role_is_required_unless_removing(self, app, db_session):
        group = make_group()
        user = make_user()
        assert self._run(app, user.email, group.name).exit_code == 1
        assert self._roles(user, group) == []


class TestSetGroupSourceCli:
    """Break-glass for a restricted source no group holds (or holds wrongly): in the app only a
    covered user can attach it, so with no holder nobody can."""

    def _run(self, app, *args):
        return app.test_cli_runner().invoke(args=["set-group-source", *args])

    def _holds(self, group, source):
        from data_viz.database.models import GroupDataSources
        return GroupDataSources.query.filter_by(group_id=group.id,
                                                data_source_id=source.id).count() == 1

    def test_attaches_a_restricted_source_nobody_holds(self, app, db_session):
        source = make_data_source(restricted=True)
        group = make_group()
        result = self._run(app, group.name, source.name)
        assert result.exit_code == 0, result.output
        assert self._holds(group, source)
        activity = UserActivity.query.filter_by(activity_type="group_data_sources_updated",
                                                activity_target_id=group.id).one()
        assert source.name in activity.details

    def test_attaching_twice_is_a_noop(self, app, db_session):
        source, group = make_restricted_source()
        result = self._run(app, group.name, source.name)
        assert result.exit_code == 0, result.output
        assert self._holds(group, source)

    def test_other_sources_on_the_group_are_kept(self, app, db_session):
        from tests.factories import grant_data_source
        source = make_data_source(restricted=True)
        other = make_data_source()
        group = make_group()
        grant_data_source(group, other)
        self._run(app, group.name, source.name)
        assert self._holds(group, source) and self._holds(group, other)

    def test_remove_flag_detaches(self, app, db_session):
        source, group = make_restricted_source()
        result = self._run(app, group.name, source.name, "--remove")
        assert result.exit_code == 0, result.output
        assert not self._holds(group, source)

    def test_unknown_group_or_source_exits_nonzero(self, app, db_session):
        source, group = make_restricted_source()
        assert self._run(app, unique("no-such-group"), source.name).exit_code == 1
        assert self._run(app, group.name, unique("no-such-source")).exit_code == 1

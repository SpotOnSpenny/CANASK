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


# How the break-glass commands sign their UserActivity rows (data_viz/cli.py).
CLI_ACTOR = "the server CLI (break-glass)"


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
        assert [(entry["group"].id, [v.id for v in entry["visuals"]])
                for entry in report["stray_grants"]] == [(stranger.id, [visual.id])]

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

    def test_expired_invites_are_listed_apart_from_pending_ones(self, db_session):
        # An expired invite is not access yet, but a covered manager can renew it back to pending.
        source, group = make_restricted_source()
        expired = make_invite(status="expired", groups=[(group, "Group Admin")])
        pending = make_invite(groups=[(group, "Data Viewer")])
        make_invite(status="expired", groups=[(make_group(), "Data Viewer")])
        report = source_access_report(source)
        assert [(i["invite"].id, i["group"].id, i["role"]) for i in report["expired_invites"]] == [
            (expired.id, group.id, "Group Admin")]
        assert [i["invite"].id for i in report["pending_invites"]] == [pending.id]

    def test_invite_items_say_whether_the_invite_is_a_site_admin_invite(self, db_session):
        # On a site-admin invite the group row is dormant: acceptance grants the flag, not the group.
        source, group = make_restricted_source()
        plain = make_invite(groups=[(group, "Data Viewer")])
        flagged = make_invite(site_admin_invite=True, groups=[(group, "Data Owner")])
        lapsed = make_invite(status="expired", site_admin_invite=True,
                             groups=[(group, "Data Owner")])
        report = source_access_report(source)
        assert {i["invite"].id: i["site_admin_invite"] for i in report["pending_invites"]} == {
            plain.id: False, flagged.id: True}
        assert {i["invite"].id: i["site_admin_invite"] for i in report["expired_invites"]} == {
            lapsed.id: True}


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

    def test_each_person_is_printed_on_the_right_side_of_the_can_not_see_header(self, app,
                                                                               db_session):
        # The output is read by a person checking it against the agreement: an email in the
        # wrong section is the failure, not an email that is missing.
        source, group = make_restricted_source()
        insider = make_user(site_admin=True, group=group, role="Data Viewer")
        outsider = make_user(site_admin=True)
        result = self._run(app, source.name)
        assert result.exit_code == 0, result.output
        can_see, cannot_see = result.output.split("Site admins who can NOT see it (1):")
        cannot_see = cannot_see.split("\n\n")[0]   # that section only
        assert "Site admins who can see and manage it (1):" in can_see
        assert insider.email in can_see and insider.email not in cannot_see
        assert outsider.email in cannot_see and outsider.email not in can_see

    def test_stray_grants_and_invites_are_printed_under_their_headings(self, app, db_session):
        source, group = make_restricted_source()
        visual = make_visual(visibility="group", data_source=source)
        stranger = make_group()
        grant_visual(stranger, visual)
        pending = make_invite(groups=[(group, "Data Viewer")])
        expired = make_invite(status="expired", groups=[(group, "Group Admin")])
        flagged = make_invite(site_admin_invite=True, groups=[(group, "Data Owner")])
        result = self._run(app, source.name)
        assert result.exit_code == 0, result.output
        sections = {block.splitlines()[0]: block.splitlines()[1:]
                    for block in result.output.split("\n\n")}

        def section(prefix):
            (heading,) = [h for h in sections if h.startswith(prefix)]
            return heading, "\n".join(sections[heading])

        heading, body = section("Visual grants on groups that do not hold this source")
        assert "give nothing to anyone outside the groups above" in heading
        assert body.strip() == f"{stranger.name}: {visual.name}"

        heading, body = section("Pending invites into these groups")
        assert heading.endswith("(2):")
        assert f"{pending.email} -> {group.name} (Data Viewer), expires" in body
        assert expired.email not in body
        (flagged_line,) = [line for line in body.splitlines() if flagged.email in line]
        assert "[site-admin invite" in flagged_line
        (plain_line,) = [line for line in body.splitlines() if pending.email in line]
        assert "site-admin invite" not in plain_line

        heading, body = section("Expired invites into these groups")
        assert heading.endswith("(1):")
        assert f"{expired.email} -> {group.name} (Group Admin), expired" in body
        assert pending.email not in body

    def test_two_sources_of_one_name_exit_nonzero(self, app, db_session):
        # data_sources.name is not unique; reporting on whichever row came back first could
        # describe the unrestricted twin.
        source, _ = make_restricted_source()
        make_data_source(name=source.name)
        result = self._run(app, source.name)
        assert result.exit_code == 1
        assert "2 data sources are named" in result.output
        assert "Status:" not in result.output

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

    def test_deactivated_account_is_refused(self, app, db_session):
        # The command exists to give a group a manager who can act; this one cannot sign in.
        _, group = make_restricted_source()
        user = make_user(status=User.STATUS_DEACTIVATED)
        result = self._run(app, user.email, group.name, "Data Owner")
        assert result.exit_code == 1
        assert "deactivated" in result.output
        assert self._roles(user, group) == []

    def test_deactivated_account_can_still_be_removed(self, app, db_session):
        group = make_group()
        user = make_user(status=User.STATUS_DEACTIVATED, group=group, role="Data Owner")
        result = self._run(app, user.email, group.name, "--remove")
        assert result.exit_code == 0, result.output
        assert self._roles(user, group) == []

    def test_role_together_with_remove_is_refused(self, app, db_session):
        group = make_group()
        user = make_user(group=group, role="Data Viewer")
        result = self._run(app, user.email, group.name, "Data Owner", "--remove")
        assert result.exit_code == 1
        assert "--remove" in result.output
        assert self._roles(user, group) == ["Data Viewer"]

    def test_unknown_user_and_group_are_both_named(self, app, db_session):
        email, group_name = f"{unique('nobody')}@example.org", unique("no-such-group")
        result = self._run(app, email, group_name, "Data Viewer")
        assert result.exit_code == 1
        assert f"user with email {email!r}" in result.output
        assert f"group named {group_name!r}" in result.output

    def test_invalid_role_message_lists_the_group_roles(self, app, db_session):
        from data_viz.auth.role_hierarchy import GROUP_ROLES
        group = make_group()
        user = make_user()
        for role in ("Site Admin", "Overlord"):
            result = self._run(app, user.email, group.name, role)
            assert result.exit_code == 1
            assert f"Use one of: {', '.join(GROUP_ROLES)}." in result.output
        assert self._roles(user, group) == []

    def test_activity_rows_name_the_cli_as_the_actor(self, app, db_session):
        # Nobody is signed in on the server; "without assigner" would hide where it came from.
        group = make_group()
        user = make_user()
        assert self._run(app, user.email, group.name, "Data Owner").exit_code == 0
        assert self._run(app, user.email, group.name, "--remove").exit_code == 0
        (assigned,) = UserActivity.query.filter_by(user_id=user.id,
                                                   activity_type="group_assignment").all()
        (removed,) = UserActivity.query.filter_by(user_id=user.id,
                                                  activity_type="group_removal").all()
        assert CLI_ACTOR in assigned.details and group.name in assigned.details
        assert CLI_ACTOR in removed.details and group.name in removed.details


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

    def test_two_sources_of_one_name_attach_nothing(self, app, db_session):
        # Refuses to guess: the first row back could be the unrestricted twin.
        source = make_data_source(restricted=True)
        twin = make_data_source(name=source.name)
        group = make_group()
        result = self._run(app, group.name, source.name)
        assert result.exit_code == 1
        assert "2 data sources are named" in result.output
        assert not self._holds(group, source) and not self._holds(group, twin)

    def test_remove_from_a_group_that_does_not_hold_it_exits_nonzero(self, app, db_session):
        source, holders = make_restricted_source()
        bystander = make_group()
        result = self._run(app, bystander.name, source.name, "--remove")
        assert result.exit_code == 1
        assert "nothing to remove" in result.output
        assert self._holds(holders, source)

    def test_detach_prints_how_many_visual_grants_it_cleared(self, app, db_session):
        from data_viz.database.models import GroupVisuals
        source, group = make_restricted_source()
        for _ in range(2):
            grant_visual(group, make_visual(visibility="group", data_source=source))
        result = self._run(app, group.name, source.name, "--remove")
        assert result.exit_code == 0, result.output
        assert "2 visual grants cleared" in result.output
        assert GroupVisuals.query.filter_by(group_id=group.id).count() == 0

    def test_detach_without_grants_prints_no_cleared_line(self, app, db_session):
        source, group = make_restricted_source()
        result = self._run(app, group.name, source.name, "--remove")
        assert result.exit_code == 0, result.output
        assert "cleared" not in result.output

    def test_attaching_a_restricted_source_points_at_source_access(self, app, db_session):
        # Attaching covers every member of the group at once: say so, and say how to check.
        source = make_data_source(restricted=True)
        group = make_group()
        make_user(group=group, role="Data Viewer")
        make_user(site_admin=True, group=group, role="Data Viewer")
        result = self._run(app, group.name, source.name)
        assert result.exit_code == 0, result.output
        assert "all 2 member(s)" in result.output
        assert f'flask source-access "{source.name}"' in result.output

    def test_attaching_an_open_source_prints_no_pointer(self, app, db_session):
        source, group = make_data_source(), make_group()
        result = self._run(app, group.name, source.name)
        assert result.exit_code == 0, result.output
        assert "source-access" not in result.output

    def test_activity_row_names_the_cli_as_the_actor(self, app, db_session):
        source = make_data_source(restricted=True)
        group = make_group()
        assert self._run(app, group.name, source.name).exit_code == 0
        activity = UserActivity.query.filter_by(activity_type="group_data_sources_updated",
                                                activity_target_id=group.id).one()
        assert activity.user_id is None
        assert CLI_ACTOR in activity.details

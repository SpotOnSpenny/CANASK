"""Restricted data sources, through the routes: a site admin who is not a member of a group
holding the source can neither see its data nor grant access to it (to themselves, a second
account, or anyone else); a site admin who IS a member keeps every power.

`outsider` = non-member site admin, `insider` = member site admin (Data Viewer role, to prove
the role doesn't matter for an admin), `owner` = member Data Owner without site admin."""
from types import SimpleNamespace

import pytest
from flask_login import login_user

from data_viz.auth.auth_helpers import assign_group
from data_viz.database.models import (
    GroupDataSources,
    GroupVisuals,
    InviteGroups,
    Invites,
    UserActivity,
    UserGroups,
)

from tests.factories import (
    grant_data_source,
    grant_visual,
    make_data_source,
    make_group,
    make_invite,
    make_restricted_source,
    make_user,
    make_visual,
    unique,
)


@pytest.fixture()
def agreement(db_session):
    """A restricted source, the group that holds it, and one private structural visual."""
    source, group = make_restricted_source()
    province = "canada"   # the real national scope: the province API only serves known keys
    visual = make_visual(province=province, name=unique("restricted_vis"),
                         visibility="private", data_source=source, metric=None, level="1")
    return SimpleNamespace(source=source, group=group, visual=visual, province=province)


@pytest.fixture()
def outsider(db_session):
    return make_user(site_admin=True)


@pytest.fixture()
def insider(db_session, agreement):
    return make_user(site_admin=True, group=agreement.group, role="Data Viewer")


@pytest.fixture()
def owner(db_session, agreement):
    return make_user(group=agreement.group, role="Data Owner")


def memberships(user, group):
    return UserGroups.query.filter_by(user_id=user.id, group_id=group.id).all()


def holds(group, source):
    return GroupDataSources.query.filter_by(group_id=group.id,
                                            data_source_id=source.id).count() == 1


class TestViewingThroughTheApi:
    def test_outsider_admin_gets_no_restricted_visual(self, client, login_as, agreement,
                                                      outsider):
        login_as(outsider)
        payload = client.get(f"/api/v1/province/{agreement.province}/data").get_json()
        assert agreement.visual.name not in payload["data"]
        assert agreement.visual.name not in payload["config"]

    def test_insider_admin_gets_it(self, client, login_as, agreement, insider):
        login_as(insider)
        payload = client.get(f"/api/v1/province/{agreement.province}/data").get_json()
        assert agreement.visual.name in payload["config"]


class TestManagingTheSource:
    def test_outsider_cannot_change_visibility(self, client, login_as, agreement, outsider):
        login_as(outsider)
        client.post(f"/v1/visuals/{agreement.visual.id}/visibility",
                    data={"visibility": "public"})
        assert agreement.visual.visibility == "private"

    def test_outsider_cannot_bulk_change_visibility(self, client, login_as, agreement,
                                                    outsider):
        login_as(outsider)
        client.post(f"/v1/sources/{agreement.source.id}/visibility",
                    data={"visibility": "public"})
        assert agreement.visual.visibility == "private"

    def test_outsider_cannot_set_default_visual(self, client, login_as, agreement, outsider):
        login_as(outsider)
        client.post(f"/v1/visuals/{agreement.visual.id}/default")
        assert not agreement.visual.is_default

    def test_outsider_cannot_grant_visuals_to_a_team(self, client, login_as, agreement,
                                                     outsider):
        login_as(outsider)
        client.post(f"/v1/groups/{agreement.group.id}/sources/{agreement.source.id}/visuals",
                    data={"visual_ids": [str(agreement.visual.id)]})
        assert GroupVisuals.query.filter_by(visual_id=agreement.visual.id).count() == 0

    def test_source_absent_from_outsiders_data_ownership_page(self, client, login_as,
                                                              agreement, outsider):
        other = make_data_source()
        make_visual(data_source=other)
        login_as(outsider)
        body = client.get("/v1/data-ownership").get_data(as_text=True)
        assert agreement.source.name not in body
        assert other.name in body

    def test_insider_admin_can_publish(self, client, login_as, agreement, insider):
        login_as(insider)
        client.post(f"/v1/visuals/{agreement.visual.id}/visibility",
                    data={"visibility": "public"})
        assert agreement.visual.visibility == "public"

    def test_owner_can_publish(self, client, login_as, agreement, owner):
        login_as(owner)
        client.post(f"/v1/visuals/{agreement.visual.id}/visibility",
                    data={"visibility": "public"})
        assert agreement.visual.visibility == "public"

    def test_insider_sees_source_on_data_ownership_page(self, client, login_as, agreement,
                                                        insider):
        login_as(insider)
        assert agreement.source.name in client.get("/v1/data-ownership").get_data(as_text=True)


class TestTeamGrantsNeedAHoldingGroup:
    """Visual grants only go to groups that hold the source (true for every source, not just
    restricted ones): otherwise a Data Owner could grant to any group id."""

    def test_grant_to_group_without_the_source_refused(self, client, login_as, db_session):
        source = make_data_source()
        owners = make_group()
        grant_data_source(owners, source)
        visual = make_visual(visibility="group", data_source=source)
        stranger_group = make_group()
        login_as(make_user(group=owners, role="Data Owner"))
        client.post(f"/v1/groups/{stranger_group.id}/sources/{source.id}/visuals",
                    data={"visual_ids": [str(visual.id)]})
        assert GroupVisuals.query.filter_by(group_id=stranger_group.id).count() == 0

    def test_grant_to_holding_group_still_works(self, client, login_as, db_session):
        source = make_data_source()
        owners = make_group()
        grant_data_source(owners, source)
        visual = make_visual(visibility="group", data_source=source)
        login_as(make_user(group=owners, role="Data Owner"))
        response = client.post(f"/v1/groups/{owners.id}/sources/{source.id}/visuals",
                               data={"visual_ids": [str(visual.id)]})
        assert response.status_code == 200
        assert GroupVisuals.query.filter_by(group_id=owners.id,
                                            visual_id=visual.id).count() == 1


class TestAttachingSources:
    def test_outsider_cannot_attach_restricted_source_to_another_group(
            self, client, login_as, agreement, outsider):
        other_group = make_group()
        login_as(outsider)
        client.post(f"/v1/groups/{other_group.id}/data-sources",
                    data={"source_ids": [str(agreement.source.id)]})
        assert not holds(other_group, agreement.source)

    def test_outsider_can_still_attach_ordinary_sources(self, client, login_as, agreement,
                                                        outsider):
        other_group, ordinary = make_group(), make_data_source()
        login_as(outsider)
        client.post(f"/v1/groups/{other_group.id}/data-sources",
                    data={"source_ids": [str(ordinary.id), str(agreement.source.id)]})
        assert holds(other_group, ordinary)
        assert not holds(other_group, agreement.source)

    def test_outsider_cannot_change_the_protected_groups_sources(self, client, login_as,
                                                                 agreement, outsider):
        ordinary = make_data_source()
        login_as(outsider)
        # Neither detach the restricted source (empty submit) nor attach anything else.
        client.post(f"/v1/groups/{agreement.group.id}/data-sources", data={})
        client.post(f"/v1/groups/{agreement.group.id}/data-sources",
                    data={"source_ids": [str(ordinary.id)]})
        assert holds(agreement.group, agreement.source)
        assert not holds(agreement.group, ordinary)

    def test_outsider_modal_shows_restricted_source_locked(self, client, login_as, agreement,
                                                           outsider):
        other_group = make_group()
        login_as(outsider)
        body = client.get(f"/v1/groups/{other_group.id}/data-sources").get_data(as_text=True)
        assert agreement.source.name in body
        assert f'id="source_{agreement.source.id}"' in body
        locked = body.split(f'id="source_{agreement.source.id}"')[1].split(">")[0]
        assert "disabled" in locked

    def test_insider_can_attach_it_to_a_second_group(self, client, login_as, agreement,
                                                     insider):
        second = make_group()
        login_as(insider)
        client.post(f"/v1/groups/{second.id}/data-sources",
                    data={"source_ids": [str(agreement.source.id)]})
        assert holds(second, agreement.source)

    def test_owner_detach_then_reattach_round_trips(self, client, login_as, db_session):
        # Baseline coverage for the route itself (unrestricted source).
        source = make_data_source()
        make_visual(data_source=source)
        group = make_group()
        grant_data_source(group, source)
        second = make_group()
        login_as(make_user(site_admin=True))
        client.post(f"/v1/groups/{second.id}/data-sources",
                    data={"source_ids": [str(source.id)]})
        assert holds(second, source)
        client.post(f"/v1/groups/{second.id}/data-sources", data={})
        assert not holds(second, source)

    def test_data_owner_cannot_attach_a_source_they_do_not_own(self, client, login_as,
                                                               db_session):
        foreign = make_data_source()
        make_visual(data_source=foreign)
        group = make_group()
        login_as(make_user(group=group, role="Data Owner"))
        client.post(f"/v1/groups/{group.id}/data-sources",
                    data={"source_ids": [str(foreign.id)]})
        assert not holds(group, foreign)


class TestMembershipOfAProtectedGroup:
    def test_outsider_cannot_add_an_existing_user(self, client, login_as, agreement, outsider,
                                                  celery_stub):
        target = make_user()
        login_as(outsider)
        client.post("/v1/add-user", data={
            "email": target.email,
            "group_assignment_1": f"{agreement.group.name}__Data Owner"})
        assert memberships(target, agreement.group) == []

    def test_outsider_cannot_invite_a_new_email_via_add_user(self, client, login_as, agreement,
                                                             outsider, ses_outbox,
                                                             celery_stub):
        email = f"{unique('add')}@example.org"
        login_as(outsider)
        client.post("/v1/add-user", data={
            "email": email, "group_assignment_1": f"{agreement.group.name}__Data Viewer"})
        assert Invites.query.filter_by(email=email).count() == 0
        assert ses_outbox == []

    def test_outsider_cannot_invite(self, client, login_as, agreement, outsider, ses_outbox,
                                    celery_stub):
        email = f"{unique('inv')}@example.org"
        login_as(outsider)
        client.post("/v1/invite-user", data={
            "email": email, "group_assignment_1": f"{agreement.group.name}__Data Viewer"})
        assert Invites.query.filter_by(email=email).count() == 0
        assert ses_outbox == []

    def test_outsider_cannot_add_the_group_to_an_existing_invite(self, client, login_as,
                                                                 agreement, outsider,
                                                                 celery_stub):
        ordinary = make_group()
        invite = make_invite(groups=[(ordinary, "Data Viewer")])
        login_as(outsider)
        client.post("/v1/invite-user", data={
            "email": invite.email,
            "group_assignment_1": f"{agreement.group.name}__Data Viewer"})
        assert [ig.group_id for ig in invite.invite_groups] == [ordinary.id]

    @pytest.mark.parametrize("path", ["/v1/add-user", "/v1/invite-user"])
    def test_pickers_leave_the_group_out_for_the_outsider(self, client, login_as, agreement,
                                                          outsider, path):
        ordinary = make_group()
        login_as(outsider)
        body = client.get(path, headers={"HX-Request": "true"}).get_data(as_text=True)
        assert agreement.group.name not in body
        assert ordinary.name in body

    @pytest.mark.parametrize("path", ["/v1/add-user", "/v1/invite-user"])
    def test_pickers_offer_the_group_to_the_insider(self, client, login_as, agreement,
                                                    insider, path):
        login_as(insider)
        body = client.get(path, headers={"HX-Request": "true"}).get_data(as_text=True)
        assert agreement.group.name in body

    def test_outsider_adjust_cannot_add_a_user_to_the_group(self, client, login_as, agreement,
                                                            outsider):
        target = make_user()
        login_as(outsider)
        client.post(f"/v1/users/{target.id}/adjust-permissions",
                    data={f"role_{agreement.group.id}": "Data Owner"},
                    headers={"HX-Request": "true"})
        assert memberships(target, agreement.group) == []

    def test_outsider_adjust_never_drops_a_protected_membership(self, client, login_as,
                                                                agreement, outsider):
        # An omitted group means "revoke" in this form; the protected group is outside the
        # outsider's scope, so omitting it must not remove the member.
        ordinary = make_group()
        member = make_user(group=agreement.group, role="Data Owner")
        login_as(outsider)
        response = client.post(f"/v1/users/{member.id}/adjust-permissions",
                               data={f"role_{ordinary.id}": "Data Viewer"},
                               headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert [m.role for m in memberships(member, agreement.group)] == ["Data Owner"]
        assert [m.role for m in memberships(member, ordinary)] == ["Data Viewer"]

    def test_outsider_adjust_cannot_change_a_members_role(self, client, login_as, agreement,
                                                          outsider):
        member = make_user(group=agreement.group, role="Data Owner")
        login_as(outsider)
        client.post(f"/v1/users/{member.id}/adjust-permissions",
                    data={f"role_{agreement.group.id}": "Data Viewer"},
                    headers={"HX-Request": "true"})
        assert [m.role for m in memberships(member, agreement.group)] == ["Data Owner"]

    def test_outsider_adjust_modal_leaves_the_group_out(self, client, login_as, agreement,
                                                        outsider):
        ordinary = make_group()
        member = make_user(group=agreement.group, role="Data Viewer")
        login_as(outsider)
        body = client.get(f"/v1/users/{member.id}/adjust-permissions").get_data(as_text=True)
        assert f"role_{agreement.group.id}" not in body
        assert f"role_{ordinary.id}" in body

    def test_insider_admin_can_add_a_data_owner(self, client, login_as, agreement, insider,
                                                celery_stub):
        target = make_user()
        login_as(insider)
        client.post("/v1/add-user", data={
            "email": target.email,
            "group_assignment_1": f"{agreement.group.name}__Data Owner"})
        assert [m.role for m in memberships(target, agreement.group)] == ["Data Owner"]

    def test_insider_admin_can_invite(self, client, login_as, agreement, insider, ses_outbox,
                                      celery_stub):
        email = f"{unique('inv')}@example.org"
        login_as(insider)
        client.post("/v1/invite-user", data={
            "email": email, "group_assignment_1": f"{agreement.group.name}__Data Viewer"})
        invite = Invites.query.filter_by(email=email).one()
        assert [ig.group_id for ig in invite.invite_groups] == [agreement.group.id]

    def test_owner_manages_members_by_the_ordinary_rules(self, client, login_as, agreement,
                                                         owner, celery_stub):
        target = make_user()
        login_as(owner)
        client.post("/v1/add-user", data={
            "email": target.email,
            "group_assignment_1": f"{agreement.group.name}__Data Viewer"})
        assert [m.role for m in memberships(target, agreement.group)] == ["Data Viewer"]

    def test_unrestricted_groups_are_untouched_for_the_outsider(self, client, login_as,
                                                                agreement, outsider,
                                                                celery_stub):
        ordinary, target = make_group(), make_user()
        login_as(outsider)
        client.post("/v1/add-user", data={
            "email": target.email, "group_assignment_1": f"{ordinary.name}__Data Owner"})
        assert [m.role for m in memberships(target, ordinary)] == ["Data Owner"]


class TestInvitesIntoAProtectedGroup:
    def test_outsider_adjust_cannot_add_the_group(self, client, login_as, agreement, outsider,
                                                  celery_stub):
        ordinary = make_group()
        invite = make_invite(groups=[(ordinary, "Data Viewer")])
        login_as(outsider)
        client.post(f"/v1/invites/{invite.id}/adjust-permissions",
                    data={f"role_{ordinary.id}": "Data Viewer",
                          f"role_{agreement.group.id}": "Data Owner"},
                    headers={"HX-Request": "true"})
        assert {ig.group_id for ig in invite.invite_groups} == {ordinary.id}

    def test_outsider_cannot_adjust_an_invite_into_the_group_at_all(self, client, login_as,
                                                                   agreement, outsider,
                                                                   celery_stub):
        # "May revoke, may not adjust": not even the invite's other rows.
        ordinary = make_group()
        invite = make_invite(groups=[(ordinary, "Data Viewer"),
                                     (agreement.group, "Data Viewer")])
        login_as(outsider)
        client.post(f"/v1/invites/{invite.id}/adjust-permissions",
                    data={f"role_{ordinary.id}": "Group Admin"},
                    headers={"HX-Request": "true"})
        assert {(ig.group_id, ig.role) for ig in invite.invite_groups} == {
            (ordinary.id, "Data Viewer"), (agreement.group.id, "Data Viewer")}
        body = client.get(f"/v1/invites/{invite.id}/adjust-permissions",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "adjustPermissionsModal" not in body and "data sharing agreement" in body

    def test_outsider_cannot_wake_a_dormant_row_by_demoting_a_site_admin_invite(
            self, client, login_as, agreement, outsider, celery_stub):
        # A flagged invite ignores its group rows at acceptance. Demoting it (no key needed)
        # would make a row in the protected group live.
        ordinary = make_group()
        invite = make_invite(site_admin_invite=True, groups=[(agreement.group, "Data Owner")])
        login_as(outsider)
        client.post(f"/v1/invites/{invite.id}/adjust-permissions",
                    data={f"role_{ordinary.id}": "Data Viewer"},
                    headers={"HX-Request": "true"})
        assert invite.site_admin_invite is True
        assert [ig.group_id for ig in invite.invite_groups] == [agreement.group.id]

    def test_outsider_cannot_add_groups_to_an_invite_into_the_group(self, client, login_as,
                                                                   agreement, outsider,
                                                                   celery_stub):
        ordinary = make_group()
        invite = make_invite(groups=[(agreement.group, "Data Viewer")])
        login_as(outsider)
        client.post("/v1/invite-user", data={
            "email": invite.email, "group_assignment_1": f"{ordinary.name}__Data Viewer"})
        assert [ig.group_id for ig in invite.invite_groups] == [agreement.group.id]

    def test_outsider_cannot_upgrade_an_invite_into_the_group_to_site_admin(
            self, client, login_as, agreement, outsider, celery_stub):
        from tests.factories import SITE_ADMIN_KEY_SECRET, TEST_PASSWORD, seed_site_admin_key
        seed_site_admin_key()
        invite = make_invite(groups=[(agreement.group, "Data Viewer")])
        login_as(outsider)
        client.post("/v1/invite-user", data={
            "email": invite.email, "group_assignment_1": "Site Wide__Site Admin",
            "own_password": TEST_PASSWORD, "site_admin_key": SITE_ADMIN_KEY_SECRET})
        assert invite.site_admin_invite is False
        assert [ig.group_id for ig in invite.invite_groups] == [agreement.group.id]

    def test_insider_can_still_adjust_it(self, client, login_as, agreement, insider,
                                         celery_stub):
        invite = make_invite(groups=[(agreement.group, "Data Viewer")])
        login_as(insider)
        client.post(f"/v1/invites/{invite.id}/adjust-permissions",
                    data={f"role_{agreement.group.id}": "Group Admin"},
                    headers={"HX-Request": "true"})
        assert [ig.role for ig in invite.invite_groups] == ["Group Admin"]

    def test_outsider_adjust_modal_leaves_the_group_out(self, client, login_as, agreement,
                                                        outsider, celery_stub):
        ordinary = make_group()
        invite = make_invite(groups=[(ordinary, "Data Viewer")])
        login_as(outsider)
        body = client.get(f"/v1/invites/{invite.id}/adjust-permissions").get_data(as_text=True)
        assert agreement.group.name not in body
        assert ordinary.name in body

    def test_outsider_cannot_renew(self, client, login_as, agreement, outsider, ses_outbox,
                                   celery_stub):
        invite = make_invite(status="expired", groups=[(agreement.group, "Data Viewer")])
        login_as(outsider)
        client.post(f"/v1/invites/{invite.id}/renew")
        assert invite.status == "expired"
        assert ses_outbox == []
        assert celery_stub.scheduled == []

    def test_outsider_cannot_resend(self, client, login_as, agreement, outsider, ses_outbox,
                                    celery_stub):
        invite = make_invite(groups=[(agreement.group, "Data Viewer")])
        login_as(outsider)
        client.post(f"/v1/invites/{invite.id}/resend")
        assert ses_outbox == []

    def test_outsider_can_still_revoke(self, client, login_as, agreement, outsider,
                                       celery_stub):
        invite = make_invite(groups=[(agreement.group, "Data Viewer")])
        login_as(outsider)
        client.post(f"/v1/invites/{invite.id}/revoke")
        assert invite.status == "revoked"

    def test_insider_can_renew(self, client, login_as, agreement, insider, ses_outbox,
                               celery_stub):
        invite = make_invite(status="expired", groups=[(agreement.group, "Data Viewer")])
        login_as(insider)
        client.post(f"/v1/invites/{invite.id}/renew")
        assert invite.status == "pending"
        assert len(ses_outbox) == 1


class TestHelperBackstop:
    """assign_group refuses on its own, so a route that forgets the check still cannot grant."""

    def test_assign_group_refuses_a_blocked_site_admin(self, app, db_session, agreement,
                                                       outsider):
        target = make_user()
        with app.test_request_context():
            login_user(outsider)
            with pytest.raises(PermissionError):
                assign_group(target.id, agreement.group.id, "Data Viewer",
                             assigned_by=outsider.id)
        assert memberships(target, agreement.group) == []

    def test_assign_group_allows_an_insider(self, app, db_session, agreement, insider):
        target = make_user()
        with app.test_request_context():
            login_user(insider)
            assign_group(target.id, agreement.group.id, "Data Viewer", assigned_by=insider.id)
        assert len(memberships(target, agreement.group)) == 1

    def test_assign_group_outside_a_request_is_unaffected(self, db_session, agreement):
        # CLI / seed / invite acceptance (logged out) have no signed-in actor to refuse.
        target = make_user()
        assign_group(target.id, agreement.group.id, "Data Viewer")
        assert len(memberships(target, agreement.group)) == 1


class TestAddGroupsToExistingInvite:
    """Regression: adding several groups to an existing pending invite saved only the first
    (the commit + return sat inside the loop) while the flash named all of them."""

    def test_every_submitted_group_is_added(self, client, login_as, db_session, celery_stub):
        first, second, third = make_group(), make_group(), make_group()
        invite = make_invite(groups=[(first, "Data Viewer")])
        login_as(make_user(site_admin=True))
        before = UserActivity.query.filter_by(activity_type="user_invite",
                                              activity_target_id=invite.id).count()
        response = client.post("/v1/invite-user", data={
            "email": invite.email,
            "group_assignment_1": f"{second.name}__Data Viewer",
            "group_assignment_2": f"{third.name}__Group Admin"})
        assert response.status_code == 302
        rows = {(ig.group_id, ig.role)
                for ig in InviteGroups.query.filter_by(invite_id=invite.id)}
        assert rows == {(first.id, "Data Viewer"), (second.id, "Data Viewer"),
                        (third.id, "Group Admin")}
        after = UserActivity.query.filter_by(activity_type="user_invite",
                                             activity_target_id=invite.id).count()
        assert after - before == 1


class TestDrugCheckingNavLink:
    """The Drug Checking links (rail, mobile menu, home tile) follow access to the "canada"
    scope, like every other dashboard link -- they used to show to any signed-in user."""

    LINK = 'href="/v1/national/drug-checking"'

    def test_hidden_from_the_outsider_admin(self, client, login_as, agreement, outsider):
        login_as(outsider)
        assert self.LINK not in client.get("/").get_data(as_text=True)

    def test_shown_to_the_insider_admin_in_all_three_places(self, client, login_as, agreement,
                                                            insider):
        login_as(insider)
        assert client.get("/").get_data(as_text=True).count(self.LINK) == 3

    def test_hidden_from_a_signed_in_user_without_access(self, client, login_as, agreement):
        login_as(make_user(group=make_group(), role="Data Viewer"))
        assert self.LINK not in client.get("/").get_data(as_text=True)


class TestUiMarkers:
    def test_blocked_group_row_has_no_manage_action_for_the_outsider(self, client, login_as,
                                                                     agreement, outsider):
        ordinary = make_group()
        login_as(outsider)
        body = client.get("/v1/group-management").get_data(as_text=True)
        assert f'hx-get="/v1/groups/{ordinary.id}/data-sources"' in body
        assert f'hx-get="/v1/groups/{agreement.group.id}/data-sources"' not in body
        # The group and its restricted source are still listed (metadata, not data).
        assert agreement.group.name in body and agreement.source.name in body

    def test_insider_keeps_the_manage_action(self, client, login_as, agreement, insider):
        login_as(insider)
        body = client.get("/v1/group-management").get_data(as_text=True)
        assert f'hx-get="/v1/groups/{agreement.group.id}/data-sources"' in body

    def test_data_ownership_card_marks_a_restricted_source(self, client, login_as, agreement,
                                                           insider):
        login_as(insider)
        body = client.get("/v1/data-ownership").get_data(as_text=True)
        card = body.split(f'id="source-body-{agreement.source.id}"')
        assert "Restricted" in card[0].rsplit('class="card ', 1)[1]
        # "Private" no longer means "you and site admins" for this source.
        assert "who are members of a group that has this source" in card[1].split("</p>")[0]

    def test_data_ownership_card_wording_unchanged_for_an_open_source(self, client, login_as,
                                                                      db_session):
        source = make_data_source()
        make_visual(data_source=source)
        login_as(make_user(site_admin=True))
        body = client.get("/v1/data-ownership").get_data(as_text=True)
        card = body.split(f'id="source-body-{source.id}"')
        assert "Restricted" not in card[0].rsplit('class="card ', 1)[1]
        assert "only you and site admins" in card[1].split("</p>")[0]


class TestSiteAdminElevationOfAMember:
    """A member who is also a site admin has full powers over the restricted source, so only
    someone already covered may turn a member INTO a site admin -- otherwise an outsider with the
    shared key could hand a viewer the private visuals and the keys to the group."""

    def _elevate(self, client, target):
        from tests.factories import SITE_ADMIN_KEY_SECRET, TEST_PASSWORD
        return client.post(f"/v1/users/{target.id}/make-admin",
                           data={"own_password": TEST_PASSWORD,
                                 "site_admin_key": SITE_ADMIN_KEY_SECRET},
                           headers={"HX-Request": "true"})

    def test_outsider_cannot_make_a_member_site_admin(self, client, login_as, agreement,
                                                      outsider):
        from tests.factories import seed_site_admin_key
        seed_site_admin_key()
        member = make_user(group=agreement.group, role="Data Viewer")
        login_as(outsider)
        modal = client.get(f"/v1/users/{member.id}/make-admin", headers={"HX-Request": "true"})
        response = self._elevate(client, member)
        assert member.site_admin is False
        assert "data sharing agreement" in response.get_data(as_text=True)
        assert "data sharing agreement" in modal.get_data(as_text=True)

    def test_outsider_can_still_make_a_non_member_site_admin(self, client, login_as, agreement,
                                                             outsider):
        from tests.factories import seed_site_admin_key
        seed_site_admin_key()
        target = make_user(group=make_group(), role="Data Viewer")
        login_as(outsider)
        self._elevate(client, target)
        assert target.site_admin is True

    def test_insider_can_make_a_member_site_admin(self, client, login_as, agreement, insider):
        from tests.factories import seed_site_admin_key
        seed_site_admin_key()
        member = make_user(group=agreement.group, role="Data Viewer")
        login_as(insider)
        self._elevate(client, member)
        assert member.site_admin is True

    def test_helper_backstop_refuses_too(self, app, db_session, agreement, outsider):
        from data_viz.auth.auth_helpers import assign_site_admin
        member = make_user(group=agreement.group, role="Data Viewer")
        with app.test_request_context():
            login_user(outsider)
            with pytest.raises(PermissionError):
                assign_site_admin(member.id, assigned_by=outsider.id)
            # Removing admin status only ever takes access away, so it is not refused.
            admin_member = make_user(site_admin=True, group=agreement.group)
            assign_site_admin(admin_member.id, remove=True, assigned_by=outsider.id)
        assert member.site_admin is False
        assert admin_member.site_admin is False


class TestOtherPathsStayClosed:
    def test_deep_link_bounces_the_outsider(self, client, login_as, agreement, outsider):
        agreement.visual.slug = "restricted-entry"
        login_as(outsider)
        response = client.get("/v1/national/drug-checking/restricted-entry")
        assert response.status_code == 302

    def test_setting_another_default_never_renders_the_restricted_section(
            self, client, login_as, agreement, outsider):
        other = make_data_source()
        open_visual = make_visual(province="canada", name=unique("open_vis"),
                                  visibility="private", data_source=other, metric=None,
                                  level="1")
        login_as(outsider)
        body = client.post(f"/v1/visuals/{open_visual.id}/default").get_data(as_text=True)
        assert f'id="vis-section-{other.id}"' in body
        assert f'id="vis-section-{agreement.source.id}"' not in body
        assert agreement.source.name not in body

    def test_public_restricted_visual_shows_the_nav_link_to_anonymous(self, client,
                                                                      agreement):
        # Publishing is the owners' call; a Public visual is public, link included.
        agreement.visual.visibility = "public"
        body = client.get("/").get_data(as_text=True)
        assert 'href="/v1/national/drug-checking"' in body


class TestSiteAdminOwnMembership:
    """Coverage for a restricted source comes from group membership, so a site admin has to be
    able to put themselves into a group -- before the restriction is switched on, or (as a
    member) into a second group later. Site admins outrank every group role, so this is not an
    escalation; the "cannot change your own access" rule stays for everyone else."""

    def test_site_admin_adds_themselves_through_adjust(self, client, login_as, db_session):
        group = make_group()
        admin = make_user(site_admin=True)
        login_as(admin)
        client.post(f"/v1/users/{admin.id}/adjust-permissions",
                    data={f"role_{group.id}": "Data Owner"}, headers={"HX-Request": "true"})
        assert [m.role for m in memberships(admin, group)] == ["Data Owner"]

    def test_adjust_modal_opens_on_their_own_row(self, client, login_as, db_session):
        group = make_group()
        admin = make_user(site_admin=True)
        login_as(admin)
        body = client.get(f"/v1/users/{admin.id}/adjust-permissions",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert f'name="role_{group.id}"' in body

    def test_site_admin_adds_themselves_through_add_user(self, client, login_as, db_session,
                                                         celery_stub):
        group = make_group()
        admin = make_user(site_admin=True)
        login_as(admin)
        client.post("/v1/add-user", data={
            "email": admin.email, "group_assignment_1": f"{group.name}__Data Viewer"})
        assert [m.role for m in memberships(admin, group)] == ["Data Viewer"]

    def test_own_row_offers_adjust_to_a_site_admin_only(self, client, login_as, db_session):
        group = make_group()
        admin = make_user(site_admin=True)
        owner = make_user(group=group, role="Data Owner")
        login_as(admin)
        body = client.get("/v1/user-management").get_data(as_text=True)
        assert f'hx-get="/v1/users/{admin.id}/adjust-permissions"' in body
        # Removing your own site admin flag is still refused, so it is not offered either.
        assert f'hx-get="/v1/users/{admin.id}/remove-admin"' not in body
        login_as(owner)
        body = client.get("/v1/user-management").get_data(as_text=True)
        assert f'hx-get="/v1/users/{owner.id}/adjust-permissions"' not in body

    def test_joining_the_holding_group_before_the_flag_is_on_covers_them(self, client,
                                                                         login_as, db_session):
        # The rollout order: group holds the source, the admin joins, THEN the flag turns on.
        source, group = make_data_source(), make_group()
        grant_data_source(group, source)
        admin = make_user(site_admin=True)
        login_as(admin)
        client.post(f"/v1/users/{admin.id}/adjust-permissions",
                    data={f"role_{group.id}": "Data Owner"}, headers={"HX-Request": "true"})
        source.restricted = True
        db_session.flush()
        from data_viz.auth.restricted import uncovered_source_ids
        assert source.id not in uncovered_source_ids(admin)

    def test_outsider_cannot_add_themselves_through_adjust(self, client, login_as, agreement,
                                                           outsider):
        login_as(outsider)
        client.post(f"/v1/users/{outsider.id}/adjust-permissions",
                    data={f"role_{agreement.group.id}": "Data Owner"},
                    headers={"HX-Request": "true"})
        assert memberships(outsider, agreement.group) == []

    def test_outsider_cannot_add_themselves_through_add_user(self, client, login_as, agreement,
                                                             outsider, celery_stub):
        login_as(outsider)
        client.post("/v1/add-user", data={
            "email": outsider.email,
            "group_assignment_1": f"{agreement.group.name}__Data Owner"})
        assert memberships(outsider, agreement.group) == []

    def test_outsider_is_not_offered_the_group_on_their_own_row(self, client, login_as,
                                                                agreement, outsider):
        login_as(outsider)
        body = client.get(f"/v1/users/{outsider.id}/adjust-permissions",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert f'name="role_{agreement.group.id}"' not in body

    def test_non_admin_manager_still_cannot_change_their_own_role(self, client, login_as,
                                                                  db_session, celery_stub):
        group = make_group()
        manager = make_user(group=group, role="Group Admin")
        login_as(manager)
        client.post(f"/v1/users/{manager.id}/adjust-permissions",
                    data={f"role_{group.id}": "Data Viewer"}, headers={"HX-Request": "true"})
        client.post("/v1/add-user", data={
            "email": manager.email, "group_assignment_1": f"{group.name}__Data Viewer"})
        assert [m.role for m in memberships(manager, group)] == ["Group Admin"]

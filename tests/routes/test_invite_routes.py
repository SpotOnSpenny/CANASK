"""Invite lifecycle: create -> revoke/renew -> accept. SES and Celery are the autouse
stubs from conftest; assertions read their capture records."""
from datetime import timedelta

import jwt
import pytest

from data_viz.database.models import Invites, InviteGroups, User, UserActivity, UserGroups

from tests.factories import (
    SITE_ADMIN_KEY_SECRET,
    TEST_PASSWORD,
    make_group,
    make_invite,
    make_user,
    seed_site_admin_key,
    unique,
)


@pytest.fixture()
def admin(db_session, login_as):
    return login_as(make_user(site_admin=True))


class TestCreateInvite:
    def test_invite_created_scheduled_and_emailed(self, client, admin, ses_outbox,
                                                  celery_stub):
        group = make_group()
        email = f"{unique('inv')}@example.org"
        response = client.post("/v1/invite-user", data={
            "email": email, "group_assignment_1": f"{group.name}__Data Viewer"})
        assert response.status_code == 302

        invite = Invites.query.filter_by(email=email).one()
        assert invite.status == "pending"
        assert invite.expiry_task_id == "fake-task-1"
        assert [ig.role for ig in invite.invite_groups] == ["Data Viewer"]
        # Expiry task scheduled for the invite's expiry moment.
        (scheduled,) = celery_stub.scheduled
        assert scheduled.args == [invite.id]
        assert abs((scheduled.eta - scheduled.eta.__class__.fromtimestamp(
            invite.expires_at.timestamp(), tz=scheduled.eta.tzinfo)).total_seconds()) < 2
        # The token is a bearer credential: it goes in the email, and only there.
        (mail,) = ses_outbox
        assert mail.to == [email]
        assert invite.token in mail.html

    def test_debug_logs_link_instead_of_emailing(self, client, admin, ses_outbox,
                                                 celery_stub, app, monkeypatch, caplog):
        # Dev-only escape hatch: under DEBUG the accept link goes to the server log, not SES,
        # so the invite flow is testable in dev where SES creds are deliberately invalid.
        monkeypatch.setitem(app.config, "DEBUG", True)
        group = make_group()
        email = f"{unique('inv')}@example.org"
        with caplog.at_level("INFO"):
            client.post("/v1/invite-user", data={
                "email": email, "group_assignment_1": f"{group.name}__Data Viewer"})
        invite = Invites.query.filter_by(email=email).one()
        assert len(ses_outbox) == 0
        logged = [r.getMessage() for r in caplog.records if "DEV invite link" in r.getMessage()]
        assert len(logged) == 1
        assert invite.token in logged[0]

    def test_invalid_email_creates_nothing(self, client, admin, celery_stub):
        client.post("/v1/invite-user", data={
            "email": "not-an-email", "group_assignment_1": "whatever__Data Viewer"})
        assert celery_stub.scheduled == []

    def test_no_valid_assignments_creates_nothing(self, client, admin, celery_stub):
        email = f"{unique('inv')}@example.org"
        client.post("/v1/invite-user", data={"email": email})
        assert Invites.query.filter_by(email=email).count() == 0

    def test_existing_user_email_refused(self, client, admin, celery_stub):
        existing = make_user()
        group = make_group()
        client.post("/v1/invite-user", data={
            "email": existing.email, "group_assignment_1": f"{group.name}__Data Viewer"})
        assert Invites.query.filter_by(email=existing.email).count() == 0

    def test_non_site_admin_cannot_mint_site_admin(self, client, db_session, login_as,
                                                   celery_stub):
        group = make_group()
        login_as(make_user(group=group, role="Group Admin"))
        email = f"{unique('inv')}@example.org"
        client.post("/v1/invite-user", data={
            "email": email, "group_assignment_1": "Site Wide__Site Admin"})
        assert Invites.query.filter_by(email=email).count() == 0

    def test_upgrade_to_site_admin_deletes_group_rows(self, client, admin, celery_stub):
        seed_site_admin_key()
        group = make_group()
        invite = make_invite(groups=[(group, "Data Viewer")])
        response = client.post("/v1/invite-user", data={
            "email": invite.email,
            "group_assignment_1": "Site Wide__Site Admin",
            "own_password": TEST_PASSWORD,
            "site_admin_key": SITE_ADMIN_KEY_SECRET})
        assert response.status_code == 302
        assert invite.site_admin_invite is True
        assert InviteGroups.query.filter_by(invite_id=invite.id).count() == 0
        # Upgrading an existing invite is an elevation -- audited under the same type as the
        # adjust-modal path, so one query finds every elevation.
        assert UserActivity.query.filter_by(
            activity_type="site_admin_elevation").count() == 1


class TestRevokeRenew:
    def test_site_admin_revoke(self, client, admin, celery_stub):
        invite = make_invite(expiry_task_id="task-abc")
        response = client.post(f"/v1/invites/{invite.id}/revoke")
        assert response.status_code == 200
        assert invite.status == "revoked"
        assert celery_stub.revoked == ["task-abc"]

    def test_renew_revokes_old_task_and_mints_new_token(self, client, admin, ses_outbox,
                                                        celery_stub, app):
        invite = make_invite(status="expired", expiry_task_id="task-old")
        old_token = invite.generate_jwt(app.config["INVITE_JWT_SECRET"])
        response = client.post(f"/v1/invites/{invite.id}/renew")
        assert response.status_code == 200
        assert invite.status == "pending"
        assert invite.token != old_token
        assert celery_stub.revoked == ["task-old"]
        assert len(celery_stub.scheduled) == 1
        assert len(ses_outbox) == 1

    def test_renew_refused_for_revoked_invite(self, client, admin, celery_stub):
        invite = make_invite(status="revoked", expiry_task_id="task-x")
        client.post(f"/v1/invites/{invite.id}/renew")
        assert invite.status == "revoked"
        assert celery_stub.scheduled == []

    def test_renew_refused_for_pending_invite(self, client, admin, celery_stub):
        # A pending invite's action is /resend; renew is for expired ones only (the UI never
        # offered pending-renew, and the route now matches it).
        invite = make_invite(status="pending", expiry_task_id="task-p")
        client.post(f"/v1/invites/{invite.id}/renew")
        assert invite.status == "pending"
        assert celery_stub.scheduled == []
        assert celery_stub.revoked == []


class TestResendInvite:
    def test_resend_emails_and_toasts(self, client, admin, ses_outbox, celery_stub,
                                      app):
        invite = make_invite()
        invite.generate_jwt(app.config["INVITE_JWT_SECRET"])
        response = client.post(f"/v1/invites/{invite.id}/resend",
                               headers={"HX-Request": "true"})
        assert response.status_code == 200
        (mail,) = ses_outbox
        assert mail.to == [invite.email]
        assert invite.token in mail.html
        assert f"Invite resent to {invite.email}" in response.get_data(as_text=True)
        assert UserActivity.query.filter_by(activity_type="invite_resent").count() == 1

    def test_resend_debug_logs_link_instead_of_emailing(self, client, admin, ses_outbox,
                                                        celery_stub, app, monkeypatch,
                                                        caplog):
        # Mirrors TestCreateInvite.test_debug_logs_link_instead_of_emailing: under DEBUG
        # the accept link goes to the server log, not SES (dev SES creds are invalid).
        monkeypatch.setitem(app.config, "DEBUG", True)
        invite = make_invite()
        invite.generate_jwt(app.config["INVITE_JWT_SECRET"])
        with caplog.at_level("INFO"):
            client.post(f"/v1/invites/{invite.id}/resend",
                        headers={"HX-Request": "true"})
        assert len(ses_outbox) == 0
        logged = [r.getMessage() for r in caplog.records
                  if "DEV invite link" in r.getMessage()]
        assert len(logged) == 1
        assert invite.token in logged[0]

    def test_resend_refused_for_non_pending_invite(self, client, admin, ses_outbox,
                                                   celery_stub):
        invite = make_invite(status="revoked")
        response = client.post(f"/v1/invites/{invite.id}/resend",
                               headers={"HX-Request": "true"})
        assert len(ses_outbox) == 0
        assert "Only pending invites" in response.get_data(as_text=True)

    def test_resend_unknown_invite_flashes_for_site_admin(self, client, admin,
                                                          celery_stub):
        # Not a 204: the OOB flash hook skips 204s, so the error would silently vanish.
        response = client.post("/v1/invites/999999/resend",
                               headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert response.headers.get("HX-Reswap") == "none"
        assert "Invite not found." in response.get_data(as_text=True)

    def test_resend_email_failure_warns_and_logs_no_activity(self, client, admin,
                                                             ses_outbox, celery_stub,
                                                             app, monkeypatch):
        # send_invite_email returns False when it can't build the link; the resend must
        # surface the warning and NOT record an invite_resent row for a send that never went.
        monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", None)
        invite = make_invite()
        response = client.post(f"/v1/invites/{invite.id}/resend",
                               headers={"HX-Request": "true"})
        assert "could not be sent" in response.get_data(as_text=True)
        assert len(ses_outbox) == 0
        assert UserActivity.query.filter_by(activity_type="invite_resent").count() == 0


class TestAcceptInvite:
    def _pending_invite(self, app, groups=None):
        group = make_group()
        invite = make_invite(groups=[(group, groups or "Data Viewer")]
                             if not isinstance(groups, list) else groups)
        invite.generate_jwt(app.config["INVITE_JWT_SECRET"])
        return invite, group

    def test_happy_path_creates_user_with_membership(self, client, db_session, app,
                                                     celery_stub):
        invite, group = self._pending_invite(app)
        # URL-token hop stashes the token in the session.
        response = client.get(f"/v1/accept-invite/{invite.token}")
        assert response.status_code == 302
        assert "/v1/accept-invite" in response.headers["Location"]
        assert client.get("/v1/accept-invite").status_code == 200

        username = unique("newbie")
        response = client.post("/v1/accept-invite", data={
            "username": username, "password": TEST_PASSWORD,
            "confirm_password": TEST_PASSWORD})
        assert response.status_code == 302
        assert "/v1/login" in response.headers["Location"]

        db_session.expire_all()
        user = User.query.filter_by(username=username).one()
        assert user.email == invite.email
        assert invite.status == "accepted"
        assert celery_stub.revoked == [invite.expiry_task_id]
        memberships = {(m.group_id, m.role)
                       for m in UserGroups.query.filter_by(user_id=user.id)}
        assert memberships == {(group.id, "Data Viewer")}

    def test_expired_token_rejected(self, client, db_session, app):
        invite = make_invite(expires_in=timedelta(seconds=-60))
        token = invite.generate_jwt(app.config["INVITE_JWT_SECRET"])
        response = client.get(f"/v1/accept-invite/{token}", follow_redirects=True)
        assert "expired" in response.get_data(as_text=True)

    def test_tampered_token_rejected(self, client, db_session, app):
        invite = make_invite()
        token = invite.generate_jwt(app.config["INVITE_JWT_SECRET"])
        response = client.get(f"/v1/accept-invite/{token}x", follow_redirects=True)
        assert "invalid" in response.get_data(as_text=True)

    def test_superseded_token_rejected_after_renewal(self, client, db_session, app):
        invite = make_invite()
        old_token = invite.generate_jwt(app.config["INVITE_JWT_SECRET"])
        # A renewal mints a fresh token; the old one is valid JWT but no longer bound.
        invite.expires_at = invite.expires_at + timedelta(hours=1)
        invite.generate_jwt(app.config["INVITE_JWT_SECRET"])
        assert old_token != invite.token
        response = client.get(f"/v1/accept-invite/{old_token}", follow_redirects=True)
        assert "superseded" in response.get_data(as_text=True)

    def test_non_pending_invite_rejected(self, client, db_session, app):
        invite = make_invite(status="revoked")
        token = invite.generate_jwt(app.config["INVITE_JWT_SECRET"])
        response = client.get(f"/v1/accept-invite/{token}", follow_redirects=True)
        assert "no longer valid" in response.get_data(as_text=True)

    def test_email_collision_refused(self, client, db_session, app):
        invite, _ = self._pending_invite(app)
        make_user(email=invite.email)   # account created since the invite was issued
        response = client.get(f"/v1/accept-invite/{invite.token}", follow_redirects=True)
        assert "already exists" in response.get_data(as_text=True)

    def test_weak_password_refused(self, client, db_session, app):
        invite, _ = self._pending_invite(app)
        client.get(f"/v1/accept-invite/{invite.token}")
        response = client.post("/v1/accept-invite", data={
            "username": unique("u"), "password": "weak", "confirm_password": "weak"},
            follow_redirects=True)
        assert "at least 12 characters" in response.get_data(as_text=True)
        assert invite.status == "pending"

    def test_logged_in_user_bounced(self, client, db_session, app, login_as):
        login_as(make_user())
        invite, _ = self._pending_invite(app)
        response = client.get(f"/v1/accept-invite/{invite.token}")
        assert response.status_code == 302
        assert response.headers["Location"] in ("/", "http://localhost/")


class TestSiteAdminRenew:
    """Renewing a site-admin invite is one-click for site admins -- the credential gate was
    deliberately removed (the gate already approved the invite at creation, and any site
    admin could mint a fresh site-admin invite outright). Non-site-admins are still refused,
    even on hybrid flag+groups invites that pass the role decorator."""

    def _renew(self, client, invite_id):
        return client.post(f"/v1/invites/{invite_id}/renew",
                           headers={"HX-Request": "true"})

    def test_one_click_renew_for_site_admin(self, client, admin, ses_outbox,
                                            celery_stub, app):
        invite = make_invite(status="expired", site_admin_invite=True,
                             expiry_task_id="task-old-sa")
        old_token = invite.generate_jwt(app.config["INVITE_JWT_SECRET"])
        response = self._renew(client, invite.id)
        assert response.status_code == 200
        assert invite.status == "pending"
        assert invite.token != old_token
        assert celery_stub.revoked == ["task-old-sa"]
        assert len(celery_stub.scheduled) == 1
        assert len(ses_outbox) == 1
        assert invite.token in ses_outbox[0].html

    def test_hybrid_invite_not_renewable_by_group_admin(self, client, db_session,
                                                        login_as, celery_stub):
        # A site-admin invite that also carries group rows would pass the role decorator for a
        # Group Admin sharing the group -- the explicit site-admin check must still refuse.
        group = make_group()
        invite = make_invite(status="expired", site_admin_invite=True,
                             groups=[(group, "Data Viewer")])
        login_as(make_user(group=group, role="Group Admin"))
        response = self._renew(client, invite.id)
        assert "Only site admins" in response.get_data(as_text=True)
        assert invite.status == "expired"
        assert celery_stub.scheduled == []

    def test_plain_invite_renewal_needs_no_credentials(self, client, admin, ses_outbox,
                                                       celery_stub):
        invite = make_invite(status="expired")
        response = client.post(f"/v1/invites/{invite.id}/renew")
        assert response.status_code == 200
        assert invite.status == "pending"


class TestAdjustInvite:
    """Baseline adjust behavior for plain group invites."""

    def test_unknown_invite_flashes_for_site_admin(self, client, admin, celery_stub):
        # Not a 204: the OOB flash hook skips 204s, so the error would silently vanish.
        response = client.post("/v1/invites/999999/adjust-permissions", data={},
                               headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert response.headers.get("HX-Reswap") == "none"
        assert "Invite not found." in response.get_data(as_text=True)

    def test_adjust_still_works_for_group_invites(self, client, admin, celery_stub):
        group = make_group()
        invite = make_invite(groups=[(group, "Data Viewer")])
        response = client.post(f"/v1/invites/{invite.id}/adjust-permissions",
                               data={f"role_{group.id}": "Group Admin"},
                               headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert [ig.role for ig in invite.invite_groups] == ["Group Admin"]
        # The success flash reports exactly what was applied.
        body = response.get_data(as_text=True)
        assert "Invite permissions adjusted for" in body
        assert group.name in body

    def test_adjust_refused_for_revoked_invite(self, client, admin, celery_stub):
        # Mirrors renew/resend: revoked (and accepted) invites stay dead. Expired ones stay
        # adjustable -- adjust-then-renew is a legitimate flow.
        group = make_group()
        invite = make_invite(status="revoked", groups=[(group, "Data Viewer")])
        response = client.post(f"/v1/invites/{invite.id}/adjust-permissions",
                               data={f"role_{group.id}": "Group Admin"},
                               headers={"HX-Request": "true"})
        assert "can have their permissions adjusted" in response.get_data(as_text=True)
        assert [ig.role for ig in invite.invite_groups] == ["Data Viewer"]

    def test_group_admin_adjust_leaves_other_groups_rows_alone(self, client, db_session,
                                                               login_as, celery_stub):
        # The delete/re-add rewrite is scoped by managed_group_ids: a Group Admin of A must
        # not be able to clobber (or even touch) the invite's rows in group B.
        group_a, group_b = make_group(), make_group()
        invite = make_invite(groups=[(group_a, "Data Viewer"), (group_b, "Data Viewer")])
        login_as(make_user(group=group_a, role="Group Admin"))
        response = client.post(f"/v1/invites/{invite.id}/adjust-permissions",
                               data={f"role_{group_a.id}": "Data Viewer"},
                               headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert {(ig.group_id, ig.role) for ig in invite.invite_groups} == \
            {(group_a.id, "Data Viewer"), (group_b.id, "Data Viewer")}
        assert "Some changes were not applied" not in response.get_data(as_text=True)

    def test_modal_checkbox_and_select_ids_align(self, client, admin, celery_stub):
        # The change-detection JS derives its key from the checkbox id and looks up
        # select_<same key>; both must therefore be keyed by group_id. (Regression:
        # keying the checkbox by the InviteGroups row id made role-only changes
        # undetectable, blocking the submit with a "no changes" toast.)
        group = make_group()
        invite = make_invite(groups=[(group, "Data Viewer")])
        body = client.get(f"/v1/invites/{invite.id}/adjust-permissions",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert f'id="include_{group.id}"' in body
        assert f'id="select_{group.id}"' in body
        assert 'id="include_None"' not in body


class TestAdjustSiteAdminTransitions:
    """Site admins may open the adjust modal on a site-admin invite (to demote it) and
    may elevate a group invite by spending the site admin key. Nobody else may touch
    a site-admin invite at all."""

    def _adjust(self, client, invite_id, data=None):
        return client.post(f"/v1/invites/{invite_id}/adjust-permissions",
                           data=data or {}, headers={"HX-Request": "true"})

    def test_get_opens_modal_on_site_admin_invite_for_site_admin(self, client, admin,
                                                                 celery_stub):
        invite = make_invite(site_admin_invite=True)
        body = client.get(f"/v1/invites/{invite.id}/adjust-permissions",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "cannot be adjusted" not in body
        assert f"/v1/invites/{invite.id}/adjust-permissions" in body

    def test_get_still_refused_for_group_admin_on_hybrid_invite(self, client, db_session,
                                                                login_as, celery_stub):
        # A site-admin invite that also carries group rows passes the role decorator for
        # a Group Admin sharing the group -- the explicit site-admin check must refuse.
        group = make_group()
        invite = make_invite(site_admin_invite=True, groups=[(group, "Data Viewer")])
        login_as(make_user(group=group, role="Group Admin"))
        body = client.get(f"/v1/invites/{invite.id}/adjust-permissions",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "cannot be adjusted" in body

    def test_post_still_refused_for_group_admin_on_hybrid_invite(self, client, db_session,
                                                                 login_as, celery_stub):
        # The POST half of the same guard: without it, a shared-group admin's role fields
        # would fall into the demote branch and flip site_admin_invite off with no gate.
        group = make_group()
        invite = make_invite(site_admin_invite=True, groups=[(group, "Data Viewer")])
        login_as(make_user(group=group, role="Group Admin"))
        response = self._adjust(client, invite.id, {f"role_{group.id}": "Data Viewer"})
        assert "cannot be adjusted" in response.get_data(as_text=True)
        assert invite.site_admin_invite is True
        assert [ig.role for ig in invite.invite_groups] == ["Data Viewer"]

    def test_elevate_refused_without_credentials(self, client, admin, celery_stub):
        seed_site_admin_key()
        group = make_group()
        invite = make_invite(groups=[(group, "Data Viewer")])
        response = self._adjust(client, invite.id, {"site_admin_invite": "true"})
        assert response.headers.get("HX-Retarget") == "#modal-container"
        assert invite.site_admin_invite is False
        assert [ig.role for ig in invite.invite_groups] == ["Data Viewer"]

    def test_elevate_wrong_key_logged_and_refused(self, client, admin, celery_stub):
        seed_site_admin_key()
        invite = make_invite(groups=[(make_group(), "Data Viewer")])
        response = self._adjust(client, invite.id, {
            "site_admin_invite": "true", "own_password": TEST_PASSWORD,
            "site_admin_key": "Wrong-key-2!"})
        assert response.headers.get("HX-Retarget") == "#modal-container"
        assert invite.site_admin_invite is False
        assert UserActivity.query.filter_by(
            activity_type="site_admin_key_failure").count() == 1

    def test_elevate_wrong_password_and_wrong_key_give_identical_message(self, client, admin,
                                                                         celery_stub):
        # The response text must not reveal which of the two fields was wrong -- otherwise
        # anyone holding the actor's session (but not both secrets) could narrow down which
        # one to keep guessing.
        seed_site_admin_key()
        wrong_password_invite = make_invite(groups=[(make_group(), "Data Viewer")])
        wrong_password_body = self._adjust(client, wrong_password_invite.id, {
            "site_admin_invite": "true", "own_password": "Wrong-password-1!",
            "site_admin_key": SITE_ADMIN_KEY_SECRET}).get_data(as_text=True)
        wrong_key_invite = make_invite(groups=[(make_group(), "Data Viewer")])
        wrong_key_body = self._adjust(client, wrong_key_invite.id, {
            "site_admin_invite": "true", "own_password": TEST_PASSWORD,
            "site_admin_key": "Wrong-key-2!"}).get_data(as_text=True)
        assert "password or the site admin key was incorrect" in wrong_password_body
        assert "password or the site admin key was incorrect" in wrong_key_body

    def test_elevate_lockout_after_threshold_failures(self, client, admin, celery_stub,
                                                      app, monkeypatch):
        # The adjust modal spends the same shared secret as every other gated flow, so it
        # must share the lockout too -- otherwise it becomes the unthrottled brute-force
        # surface. Blocked attempts log "Blocked..." rows that don't count as failures.
        monkeypatch.setitem(app.config, "SITE_ADMIN_KEY_LOCKOUT_THRESHOLD", 2)
        seed_site_admin_key()
        invite = make_invite(groups=[(make_group(), "Data Viewer")])
        for _ in range(2):
            self._adjust(client, invite.id, {
                "site_admin_invite": "true", "own_password": TEST_PASSWORD,
                "site_admin_key": "Wrong-key-2!"})
        response = self._adjust(client, invite.id, {   # correct creds, but locked
            "site_admin_invite": "true", "own_password": TEST_PASSWORD,
            "site_admin_key": SITE_ADMIN_KEY_SECRET})
        assert "Too many failed attempts" in response.get_data(as_text=True)
        assert invite.site_admin_invite is False
        blocked = UserActivity.query.filter(
            UserActivity.activity_type == "site_admin_key_attempt",
            UserActivity.details.like("Blocked%")).all()
        assert len(blocked) == 1
        assert UserActivity.query.filter_by(
            activity_type="site_admin_key_failure").count() == 2

    def test_elevate_failure_rerenders_with_admin_state_kept(self, client, admin,
                                                             celery_stub):
        # A credential typo must not hand back a dead form: the re-render keeps the switch
        # checked (data-attempted resets the JS baseline) and the credential fields visible
        # and enabled so the admin can just retry.
        seed_site_admin_key()
        invite = make_invite(groups=[(make_group(), "Data Viewer")])
        body = self._adjust(client, invite.id, {
            "site_admin_invite": "true", "own_password": TEST_PASSWORD,
            "site_admin_key": "Wrong-key-2!"}).get_data(as_text=True)
        assert 'data-attempted="true"' in body
        assert 'id="adjust-admin-credentials" class="border rounded p-2 mb-3"' in body

    def test_elevate_unset_key_points_at_cli(self, client, admin, celery_stub):
        invite = make_invite(groups=[(make_group(), "Data Viewer")])
        response = self._adjust(client, invite.id, {
            "site_admin_invite": "true", "own_password": TEST_PASSWORD,
            "site_admin_key": "x"})
        assert "rotate-site-admin-key" in response.get_data(as_text=True)
        assert invite.site_admin_invite is False

    def test_non_site_admin_cannot_elevate(self, client, db_session, login_as,
                                           celery_stub):
        seed_site_admin_key()
        group = make_group()
        invite = make_invite(groups=[(group, "Data Viewer")])
        login_as(make_user(group=group, role="Group Admin"))
        self._adjust(client, invite.id, {
            "site_admin_invite": "true", "own_password": TEST_PASSWORD,
            "site_admin_key": SITE_ADMIN_KEY_SECRET})
        assert invite.site_admin_invite is False
        assert [ig.role for ig in invite.invite_groups] == ["Data Viewer"]

    def test_elevate_succeeds_and_clears_group_rows(self, client, admin, ses_outbox,
                                                    celery_stub):
        seed_site_admin_key()
        invite = make_invite(groups=[(make_group(), "Data Viewer")])
        response = self._adjust(client, invite.id, {
            "site_admin_invite": "true", "own_password": TEST_PASSWORD,
            "site_admin_key": SITE_ADMIN_KEY_SECRET})
        assert response.status_code == 200
        assert invite.site_admin_invite is True
        assert len(invite.invite_groups) == 0          # no orphaned hybrid rows
        assert UserActivity.query.filter_by(
            activity_type="site_admin_elevation").count() == 1
        assert len(ses_outbox) == 0                     # no emails for admin flows
        assert celery_stub.scheduled == []              # expiry task untouched
        assert f"Invite for {invite.email} upgraded to site admin" in response.get_data(as_text=True)

    def test_elevate_noop_when_already_site_admin(self, client, admin, celery_stub):
        seed_site_admin_key()
        invite = make_invite(site_admin_invite=True)
        response = self._adjust(client, invite.id, {"site_admin_invite": "true"})
        assert response.status_code == 200
        assert invite.site_admin_invite is True

    def test_demote_requires_at_least_one_group(self, client, admin, celery_stub):
        invite = make_invite(site_admin_invite=True)
        response = self._adjust(client, invite.id, {})
        assert response.headers.get("HX-Retarget") == "#modal-container"
        assert invite.site_admin_invite is True
        assert len(invite.invite_groups) == 0

    def test_demote_all_roles_invalid_keeps_admin_flag(self, client, admin, celery_stub):
        group = make_group()
        invite = make_invite(site_admin_invite=True)
        response = self._adjust(client, invite.id, {f"role_{group.id}": "Bogus Role"})
        assert response.headers.get("HX-Retarget") == "#modal-container"
        assert invite.site_admin_invite is True
        assert len(invite.invite_groups) == 0

    def test_demote_succeeds_without_credentials(self, client, admin, ses_outbox,
                                                 celery_stub):
        group = make_group()
        invite = make_invite(site_admin_invite=True)
        response = self._adjust(client, invite.id, {f"role_{group.id}": "Data Viewer"})
        assert response.status_code == 200
        assert invite.site_admin_invite is False
        assert [(ig.group_id, ig.role) for ig in invite.invite_groups] == \
            [(group.id, "Data Viewer")]
        activity = UserActivity.query.filter_by(
            activity_type="invite_permissions_adjusted").one()
        assert "Demoted from site admin invite" in activity.details
        assert len(ses_outbox) == 0
        body = response.get_data(as_text=True)
        assert f"Invite for {invite.email} demoted from site admin" in body
        assert "Invite permissions adjusted for" in body

    def test_mixed_demote_reports_both_outcomes(self, client, admin, celery_stub):
        # One valid and one bogus assignment: the demote commits, and the flashes must say
        # exactly what happened -- the demotion, what was applied, and what was rejected.
        good, bad = make_group(), make_group()
        invite = make_invite(site_admin_invite=True)
        response = self._adjust(client, invite.id, {
            f"role_{good.id}": "Data Viewer", f"role_{bad.id}": "Bogus Role"})
        body = response.get_data(as_text=True)
        assert invite.site_admin_invite is False
        assert [(ig.group_id, ig.role) for ig in invite.invite_groups] == \
            [(good.id, "Data Viewer")]
        assert f"Invite for {invite.email} demoted from site admin" in body
        assert "Some changes were not applied" in body and bad.name in body
        assert "Applied:" in body and good.name in body

    def test_rejected_role_change_keeps_row_and_reports_outcome(self, client, admin,
                                                                celery_stub):
        # Everything requested was invalid: the warning says those groups keep their
        # previous assignment -- and they actually must (a rejected change deleting the
        # existing row would make the message a lie), while the success flash states
        # exactly what was applied (nothing).
        group = make_group()
        invite = make_invite(groups=[(group, "Data Viewer")])
        response = client.post(f"/v1/invites/{invite.id}/adjust-permissions",
                               data={f"role_{group.id}": "Not A Real Role"},
                               headers={"HX-Request": "true"})
        body = response.get_data(as_text=True)
        assert "Some changes were not applied" in body
        assert "Applied: no new assignments" in body
        assert [ig.role for ig in invite.invite_groups] == ["Data Viewer"]

    def test_modal_offers_checkbox_and_credentials_to_site_admin(self, client, admin,
                                                                 celery_stub):
        invite = make_invite(groups=[(make_group(), "Data Viewer")])
        body = client.get(f"/v1/invites/{invite.id}/adjust-permissions",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="site_admin_check"' in body
        assert 'name="own_password"' in body      # elevation credentials offered
        assert "HX-Retarget" in body               # modal-stays-open guard wired

    def test_modal_on_site_admin_invite_checked_no_credentials(self, client, admin,
                                                               celery_stub):
        invite = make_invite(site_admin_invite=True)
        body = client.get(f"/v1/invites/{invite.id}/adjust-permissions",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="site_admin_check"' in body and "checked" in body
        assert 'name="own_password"' not in body   # demote spends no credentials

    def test_modal_hides_checkbox_from_group_admin(self, client, db_session, login_as,
                                                   celery_stub):
        group = make_group()
        invite = make_invite(groups=[(group, "Data Viewer")])
        login_as(make_user(group=group, role="Group Admin"))
        body = client.get(f"/v1/invites/{invite.id}/adjust-permissions",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="site_admin_check"' not in body

    def test_row_offers_adjust_on_site_admin_invite_to_site_admin(self, client, admin,
                                                                  celery_stub):
        invite = make_invite(site_admin_invite=True)
        body = client.get("/v1/invite-management",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert f"/v1/invites/{invite.id}/adjust-permissions" in body


class TestRevokeLabel:
    """all_groups_managed drives the revoke label: whole-invite revoke vs stripping only the
    actor's managed groups (it was previously undefined, so everyone saw the narrow label)."""

    def test_full_manager_sees_revoke_invite(self, client, admin, celery_stub):
        group = make_group()
        make_invite(groups=[(group, "Data Viewer")])
        body = client.get("/v1/invite-management",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "Revoke Invite" in body
        assert "Revoke Group Permission" not in body

    def test_partial_manager_sees_revoke_group_permission(self, client, db_session,
                                                          login_as, celery_stub):
        mine, other = make_group(), make_group()
        make_invite(groups=[(mine, "Data Viewer"), (other, "Data Viewer")])
        login_as(make_user(group=mine, role="Group Admin"))
        body = client.get("/v1/invite-management",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "Revoke Group Permission" in body
        assert "Revoke Invite" not in body

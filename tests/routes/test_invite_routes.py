"""Invite lifecycle: create -> revoke/renew -> accept. SES and Celery are the autouse
stubs from conftest; assertions read their capture records."""
from datetime import timedelta

import jwt
import pytest

from data_viz.database.models import Invites, User, UserActivity, UserGroups

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


class TestSiteAdminRenewGate:
    """Renewing a site-admin invite re-mints an admin credential, so it spends the shared
    site admin key like every other site-admin grant path."""

    def _renew(self, client, invite_id, own=None, removal=None):
        data = {}
        if own is not None:
            data["own_password"] = own
        if removal is not None:
            data["site_admin_key"] = removal
        return client.post(f"/v1/invites/{invite_id}/renew", data=data,
                           headers={"HX-Request": "true"})

    def test_get_serves_credentials_modal_to_site_admin(self, client, admin, celery_stub):
        seed_site_admin_key()
        invite = make_invite(status="expired", site_admin_invite=True)
        body = client.get(f"/v1/invites/{invite.id}/renew",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "site_admin_key" in body
        assert f"/v1/invites/{invite.id}/renew" in body

    def test_refused_without_credentials(self, client, admin, celery_stub):
        seed_site_admin_key()
        invite = make_invite(status="expired", site_admin_invite=True,
                             expiry_task_id="task-sa")
        response = self._renew(client, invite.id)
        assert invite.status == "expired"
        assert celery_stub.scheduled == []
        assert response.headers.get("HX-Retarget") == "#modal-container"

    def test_wrong_site_admin_key_logged_and_refused(self, client, admin, celery_stub):
        seed_site_admin_key()
        invite = make_invite(status="expired", site_admin_invite=True)
        response = self._renew(client, invite.id, own=TEST_PASSWORD, removal="Wrong-2!")
        assert invite.status == "expired"
        assert response.headers.get("HX-Retarget") == "#modal-container"
        assert UserActivity.query.filter_by(
            activity_type="site_admin_key_failure").count() == 1

    def test_unset_secret_points_at_cli(self, client, admin, celery_stub):
        invite = make_invite(status="expired", site_admin_invite=True)
        response = self._renew(client, invite.id, own=TEST_PASSWORD, removal="x")
        assert "rotate-site-admin-key" in response.get_data(as_text=True)
        assert invite.status == "expired"

    def test_hybrid_invite_not_renewable_by_group_admin(self, client, db_session,
                                                        login_as, celery_stub):
        # A site-admin invite that also carries group rows would pass the role decorator for a
        # Group Admin sharing the group -- the explicit site-admin check must still refuse.
        seed_site_admin_key()
        group = make_group()
        invite = make_invite(status="expired", site_admin_invite=True,
                             groups=[(group, "Data Viewer")])
        login_as(make_user(group=group, role="Group Admin"))
        response = self._renew(client, invite.id, own=TEST_PASSWORD,
                               removal=SITE_ADMIN_KEY_SECRET)
        assert "Only site admins" in response.get_data(as_text=True)
        assert invite.status == "expired"
        assert celery_stub.scheduled == []

    def test_succeeds_with_correct_credentials(self, client, admin, ses_outbox,
                                               celery_stub, app):
        seed_site_admin_key()
        invite = make_invite(status="expired", site_admin_invite=True,
                             expiry_task_id="task-old-sa")
        old_token = invite.generate_jwt(app.config["INVITE_JWT_SECRET"])
        response = self._renew(client, invite.id, own=TEST_PASSWORD,
                               removal=SITE_ADMIN_KEY_SECRET)
        assert response.status_code == 200
        assert invite.status == "pending"
        assert invite.token != old_token
        assert celery_stub.revoked == ["task-old-sa"]
        assert len(celery_stub.scheduled) == 1
        assert len(ses_outbox) == 1
        assert invite.token in ses_outbox[0].html

    def test_plain_invite_renewal_needs_no_credentials(self, client, admin, ses_outbox,
                                                       celery_stub):
        # Group invites keep the one-click renew -- no secret is being spent.
        invite = make_invite(status="expired")
        response = client.post(f"/v1/invites/{invite.id}/renew")
        assert response.status_code == 200
        assert invite.status == "pending"


class TestAdjustInviteSiteAdminClosed:
    def test_site_admin_invite_not_adjustable(self, client, admin, celery_stub):
        invite = make_invite(site_admin_invite=True)
        get_body = client.get(f"/v1/invites/{invite.id}/adjust-permissions",
                              headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "cannot be adjusted" in get_body
        response = client.post(f"/v1/invites/{invite.id}/adjust-permissions",
                               data={"site_admin_invite": "true"},
                               headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert invite.site_admin_invite is True   # unchanged either way
        assert len(invite.invite_groups) == 0

    def test_group_invite_cannot_be_flipped_to_site_admin(self, client, admin,
                                                          celery_stub):
        # The ungated elevation path is gone: the flag only changes through the credentialed
        # invite-form flow. The modal POST refuses and leaves the invite untouched.
        group = make_group()
        invite = make_invite(groups=[(group, "Data Viewer")])
        response = client.post(f"/v1/invites/{invite.id}/adjust-permissions",
                               data={"site_admin_invite": "true"},
                               headers={"HX-Request": "true"})
        assert "Invite User form" in response.get_data(as_text=True) or \
            response.status_code == 200
        assert invite.site_admin_invite is False
        assert [ig.role for ig in invite.invite_groups] == ["Data Viewer"]

    def test_unknown_invite_no_longer_errors_for_site_admin(self, client, admin,
                                                            celery_stub):
        response = client.post("/v1/invites/999999/adjust-permissions", data={},
                               headers={"HX-Request": "true"})
        assert response.status_code == 204

    def test_adjust_still_works_for_group_invites(self, client, admin, celery_stub):
        group = make_group()
        invite = make_invite(groups=[(group, "Data Viewer")])
        response = client.post(f"/v1/invites/{invite.id}/adjust-permissions",
                               data={f"role_{group.id}": "Group Admin"},
                               headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert [ig.role for ig in invite.invite_groups] == ["Group Admin"]


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

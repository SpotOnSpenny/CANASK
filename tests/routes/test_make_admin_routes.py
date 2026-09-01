"""Site-admin elevation: the protected Make Site Admin modal (own password + shared
site admin key, lockout shared with removal), the closed bare path in add-user, and
the site-admin-key gate on site-admin invites."""
from data_viz.database.models import Invites, UserActivity

from tests.factories import (SITE_ADMIN_KEY_SECRET, TEST_PASSWORD, make_group, make_user,
                             seed_site_admin_key, unique)

HX = {"HX-Request": "true"}


def post_elevation(client, target_id, own=TEST_PASSWORD, removal=SITE_ADMIN_KEY_SECRET):
    return client.post(f"/v1/users/{target_id}/make-admin",
                       data={"own_password": own, "site_admin_key": removal},
                       headers=HX)


class TestElevationGuards:
    def test_anonymous_redirected_to_login(self, client, db_session):
        target = make_user()
        response = client.get(f"/v1/users/{target.id}/make-admin")
        assert response.status_code == 302
        assert "/v1/login" in response.headers["Location"]

    def test_non_site_admin_refused(self, client, db_session, login_as):
        seed_site_admin_key()
        login_as(make_user())
        target = make_user()
        response = post_elevation(client, target.id)
        assert "Only site admins" in response.get_data(as_text=True)
        assert target.site_admin is False

    def test_self_target_refused(self, client, db_session, login_as):
        seed_site_admin_key()
        actor = login_as(make_user(site_admin=True))
        response = post_elevation(client, actor.id)
        assert "your own access" in response.get_data(as_text=True)

    def test_already_admin_refused(self, client, db_session, login_as):
        seed_site_admin_key()
        login_as(make_user(site_admin=True))
        target = make_user(site_admin=True)
        response = post_elevation(client, target.id)
        assert "already a site admin" in response.get_data(as_text=True)

    def test_inactive_target_refused(self, client, db_session, login_as):
        seed_site_admin_key()
        login_as(make_user(site_admin=True))
        target = make_user(status="deactivated")
        response = post_elevation(client, target.id)
        assert "not active" in response.get_data(as_text=True)
        assert target.site_admin is False

    def test_unset_site_admin_key_refuses_get_and_post(self, client, db_session,
                                                         login_as):
        login_as(make_user(site_admin=True))
        target = make_user()
        for response in (client.get(f"/v1/users/{target.id}/make-admin", headers=HX),
                         post_elevation(client, target.id)):
            assert "rotate-site-admin-key" in response.get_data(as_text=True)
        assert target.site_admin is False

    def test_get_renders_modal(self, client, db_session, login_as):
        seed_site_admin_key()
        login_as(make_user(site_admin=True))
        target = make_user()
        body = client.get(f"/v1/users/{target.id}/make-admin",
                          headers=HX).get_data(as_text=True)
        assert f"/v1/users/{target.id}/make-admin" in body
        assert "site_admin_key" in body

    def test_row_action_shown_only_for_active_non_admins(self, client, db_session,
                                                         login_as):
        login_as(make_user(site_admin=True))
        make_user()                                  # active non-admin -> action shown
        body = client.get("/v1/user-management", headers=HX).get_data(as_text=True)
        assert "Make Site Admin" in body


class TestElevationFailures:
    def test_wrong_own_password_logged_and_refused(self, client, db_session, login_as):
        seed_site_admin_key()
        actor = login_as(make_user(site_admin=True))
        target = make_user()
        response = post_elevation(client, target.id, own="Wrong-password-1!")
        assert target.site_admin is False
        assert response.headers.get("HX-Retarget") == "#modal-container"
        # The message doesn't say which field was wrong -- see _check_admin_grant_credentials.
        assert "password or the site admin key was incorrect" in response.get_data(as_text=True)
        activity = UserActivity.query.filter_by(
            activity_type="site_admin_key_failure", user_id=actor.id).one()
        assert activity.details.startswith("Failed removal")

    def test_wrong_site_admin_key_logged_and_refused(self, client, db_session,
                                                       login_as):
        seed_site_admin_key()
        actor = login_as(make_user(site_admin=True))
        target = make_user()
        response = post_elevation(client, target.id, removal="Wrong-secret-2!")
        assert target.site_admin is False
        assert response.headers.get("HX-Retarget") == "#modal-container"
        activity = UserActivity.query.filter_by(
            activity_type="site_admin_key_failure", user_id=actor.id).one()
        assert activity.details.startswith("Failed removal")

    def test_lockout_shared_with_removal_flow(self, client, db_session, login_as, app,
                                              monkeypatch):
        # Elevation failures spend the same shared secret, so they count toward -- and are
        # blocked by -- the same lockout as removal.
        monkeypatch.setitem(app.config, "SITE_ADMIN_KEY_LOCKOUT_THRESHOLD", 2)
        seed_site_admin_key()
        login_as(make_user(site_admin=True))
        admin_target = make_user(site_admin=True)
        target = make_user()
        post_elevation(client, target.id, removal="Wrong-secret-2!")
        client.post(f"/v1/users/{admin_target.id}/remove-admin", headers=HX,
                    data={"removal_action": "demote", "own_password": TEST_PASSWORD,
                          "site_admin_key": "Wrong-secret-2!"})
        response = post_elevation(client, target.id)  # correct creds, but locked
        assert "Too many failed attempts" in response.get_data(as_text=True)
        assert target.site_admin is False


class TestElevationSuccess:
    def test_elevates_audits_and_sends_no_email(self, client, db_session, login_as,
                                                ses_outbox):
        seed_site_admin_key()
        actor = login_as(make_user(site_admin=True))
        target = make_user()
        response = post_elevation(client, target.id)
        assert response.status_code == 200
        assert target.site_admin is True
        # Success re-renders the user row (no retarget) so the badge appears in place.
        assert response.headers.get("HX-Retarget") is None
        assert f'id="user-row-{target.id}"' in response.get_data(as_text=True)
        assert "Site Admin" in response.get_data(as_text=True)
        elevation = UserActivity.query.filter_by(
            activity_type="site_admin_elevation", activity_target_id=target.id).one()
        assert elevation.user_id == actor.id
        assert elevation.ip_address is not None
        # assign_site_admin's own audit row is written too.
        assert UserActivity.query.filter_by(
            activity_type="site_admin_assignment", user_id=target.id).count() == 1
        # Deliberate: no notification email on any admin-membership change.
        assert len(ses_outbox) == 0


class TestAddUserBarePathClosed:
    def test_deactivated_user_gets_honest_message_not_dead_end(self, client, db_session,
                                                               login_as):
        # The "use Make Site Admin on their row" pointer would be a dead end for a
        # deactivated account (no row action, and the route refuses non-active targets).
        login_as(make_user(site_admin=True))
        target = make_user(status="deactivated")
        response = client.post("/v1/add-user", headers=HX, data={
            "email": target.email, "group_assignment_1": "Site Wide__Site Admin"},
            follow_redirects=True)
        assert target.site_admin is False
        assert "cannot be" in response.get_data(as_text=True)

    def test_existing_user_site_admin_selection_does_not_elevate(self, client,
                                                                 db_session, login_as):
        login_as(make_user(site_admin=True))
        target = make_user()
        response = client.post("/v1/add-user", headers=HX, data={
            "email": target.email, "group_assignment_1": "Site Wide__Site Admin"})
        assert response.status_code in (200, 302)
        assert target.site_admin is False
        assert "Make Site Admin" in client.get(
            "/v1/user-management", headers=HX).get_data(as_text=True)


class TestSiteAdminInviteGate:
    def _post_invite(self, client, email, **extra):
        return client.post("/v1/invite-user", data={
            "email": email, "group_assignment_1": "Site Wide__Site Admin", **extra})

    def test_invite_refused_without_credentials(self, client, db_session, login_as,
                                                celery_stub):
        seed_site_admin_key()
        login_as(make_user(site_admin=True))
        email = f"{unique('inv')}@example.org"
        self._post_invite(client, email)
        assert Invites.query.filter_by(email=email).count() == 0

    def test_invite_refused_with_wrong_site_admin_key(self, client, db_session,
                                                        login_as, celery_stub):
        seed_site_admin_key()
        actor = login_as(make_user(site_admin=True))
        email = f"{unique('inv')}@example.org"
        self._post_invite(client, email, own_password=TEST_PASSWORD,
                          site_admin_key="Wrong-secret-2!")
        assert Invites.query.filter_by(email=email).count() == 0
        assert UserActivity.query.filter_by(
            activity_type="site_admin_key_failure", user_id=actor.id).count() == 1

    def test_invite_refused_when_secret_unset(self, client, db_session, login_as,
                                              celery_stub):
        login_as(make_user(site_admin=True))
        email = f"{unique('inv')}@example.org"
        self._post_invite(client, email, own_password=TEST_PASSWORD,
                          site_admin_key="anything")
        assert Invites.query.filter_by(email=email).count() == 0

    def test_invite_created_with_correct_credentials(self, client, db_session, login_as,
                                                     celery_stub, ses_outbox):
        seed_site_admin_key()
        login_as(make_user(site_admin=True))
        email = f"{unique('inv')}@example.org"
        self._post_invite(client, email, own_password=TEST_PASSWORD,
                          site_admin_key=SITE_ADMIN_KEY_SECRET)
        invite = Invites.query.filter_by(email=email).one()
        assert invite.site_admin_invite is True

    def test_upgrade_existing_invite_also_gated(self, client, db_session, login_as,
                                                celery_stub, ses_outbox):
        # Case 2 of the invite POST: upgrading a pending group invite to site admin mints
        # an admin just the same, so it spends the same secret.
        seed_site_admin_key()
        login_as(make_user(site_admin=True))
        group = make_group()
        email = f"{unique('inv')}@example.org"
        client.post("/v1/invite-user", data={
            "email": email, "group_assignment_1": f"{group.name}__Data Viewer"})
        invite = Invites.query.filter_by(email=email).one()
        self._post_invite(client, email)     # no credentials
        assert invite.site_admin_invite is False
        self._post_invite(client, email, own_password=TEST_PASSWORD,
                          site_admin_key=SITE_ADMIN_KEY_SECRET)
        assert invite.site_admin_invite is True

    def test_group_invite_needs_no_credentials(self, client, db_session, login_as,
                                               celery_stub, ses_outbox):
        login_as(make_user(site_admin=True))     # site admin key never set
        group = make_group()
        email = f"{unique('inv')}@example.org"
        client.post("/v1/invite-user", data={
            "email": email, "group_assignment_1": f"{group.name}__Data Viewer"})
        assert Invites.query.filter_by(email=email).count() == 1

    def test_add_user_new_email_site_admin_invite_gated(self, client, db_session,
                                                        login_as, celery_stub, ses_outbox):
        seed_site_admin_key()
        login_as(make_user(site_admin=True))
        email = f"{unique('inv')}@example.org"
        client.post("/v1/add-user", headers=HX, data={
            "email": email, "group_assignment_1": "Site Wide__Site Admin"})
        assert Invites.query.filter_by(email=email).count() == 0
        client.post("/v1/add-user", headers=HX, data={
            "email": email, "group_assignment_1": "Site Wide__Site Admin",
            "own_password": TEST_PASSWORD, "site_admin_key": SITE_ADMIN_KEY_SECRET})
        invite = Invites.query.filter_by(email=email).one()
        assert invite.site_admin_invite is True

"""Site-admin removal route: guards, the shared-secret lockout, and the two removal
modes (demote / deactivate). Rotation is deliberately CLI-only (no in-app route) so a
lone admin who knows the secret can't swap it and lock the other admins out."""
from datetime import datetime, timedelta, timezone

from data_viz.auth.auth_helpers import check_site_admin_key
from data_viz.database.models import User, UserActivity, UserGroups

from tests.factories import (SITE_ADMIN_KEY_SECRET, TEST_PASSWORD, make_group, make_user,
                             seed_site_admin_key)

HX = {"HX-Request": "true"}


def post_removal(client, target_id, own=TEST_PASSWORD, removal=SITE_ADMIN_KEY_SECRET,
                 action="demote"):
    return client.post(f"/v1/users/{target_id}/remove-admin",
                       data={"removal_action": action, "own_password": own,
                             "site_admin_key": removal},
                       headers=HX)


def seed_failed_attempt(db_session, user_id, age=timedelta(0)):
    db_session.add(UserActivity(
        user_id=user_id,
        activity_type="site_admin_key_failure",
        activity_target_type="user",
        details="Failed removal confirmation: seeded by test",
        timestamp=datetime.now(timezone.utc) - age,
        ip_address="127.0.0.1"))
    db_session.commit()


class TestRemovalGuards:
    def test_anonymous_redirected_to_login(self, client, db_session):
        target = make_user(site_admin=True)
        response = client.get(f"/v1/users/{target.id}/remove-admin")
        assert response.status_code == 302
        assert "/v1/login" in response.headers["Location"]

    def test_non_site_admin_refused(self, client, db_session, login_as):
        seed_site_admin_key()
        login_as(make_user())
        target = make_user(site_admin=True)
        response = post_removal(client, target.id)
        assert "Only site admins" in response.get_data(as_text=True)
        assert target.site_admin is True

    def test_self_target_refused(self, client, db_session, login_as):
        seed_site_admin_key()
        actor = login_as(make_user(site_admin=True))
        response = post_removal(client, actor.id)
        assert "your own site admin access" in response.get_data(as_text=True)
        assert actor.site_admin is True

    def test_non_admin_target_refused(self, client, db_session, login_as):
        seed_site_admin_key()
        login_as(make_user(site_admin=True))
        target = make_user()
        response = post_removal(client, target.id)
        assert "not a site admin" in response.get_data(as_text=True)

    def test_unset_site_admin_key_refuses_get_and_post(self, client, db_session,
                                                         login_as):
        login_as(make_user(site_admin=True))
        target = make_user(site_admin=True)
        for response in (client.get(f"/v1/users/{target.id}/remove-admin", headers=HX),
                         post_removal(client, target.id)):
            assert "rotate-site-admin-key" in response.get_data(as_text=True)
        assert target.site_admin is True

    def test_get_refusal_returns_modal_content_not_empty(self, client, db_session,
                                                         login_as):
        # The opener button always shows the modal, so a refused GET must still fill
        # #modal-container -- an empty body would pop a blank or stale modal.
        login_as(make_user(site_admin=True))
        target = make_user(site_admin=True)  # site admin key unset
        response = client.get(f"/v1/users/{target.id}/remove-admin", headers=HX)
        body = response.get_data(as_text=True)
        assert "modal-header" in body
        assert response.headers.get("HX-Reswap") != "none"

    def test_get_renders_modal(self, client, db_session, login_as):
        seed_site_admin_key()
        login_as(make_user(site_admin=True))
        target = make_user(site_admin=True)
        response = client.get(f"/v1/users/{target.id}/remove-admin", headers=HX)
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert f"/v1/users/{target.id}/remove-admin" in body
        assert "site_admin_key" in body


class TestUserManagementEntryPoints:
    def test_site_admin_sees_remove_action(self, client, db_session, login_as):
        login_as(make_user(site_admin=True))
        make_user(site_admin=True)
        body = client.get("/v1/user-management", headers=HX).get_data(as_text=True)
        assert "Remove Site Admin" in body
        # Rotation is CLI-only: no in-app entry point for any admin.
        assert "/v1/removal-password" not in body

    def test_group_admin_sees_no_remove_action(self, client, db_session, login_as):
        group = make_group()
        login_as(make_user(group=group, role="Group Admin"))
        make_user(group=group, role="Data Viewer")
        body = client.get("/v1/user-management", headers=HX).get_data(as_text=True)
        assert "Remove Site Admin" not in body

    def test_rotation_route_is_gone(self, client, db_session, login_as):
        # The in-app rotation endpoint was removed on purpose: a lone admin who knew the
        # secret could rotate it quietly and then remove every other admin. The route 404s
        # (the app-wide handler turns that into a redirect to /not-found) for everyone,
        # site admins included, and the stored secret is untouched.
        seed_site_admin_key()
        login_as(make_user(site_admin=True))
        response = client.get("/v1/removal-password", headers=HX)
        assert response.status_code == 302 and response.headers["Location"] == "/not-found"
        response = client.post("/v1/removal-password", headers=HX,
                               data={"current_removal_password": SITE_ADMIN_KEY_SECRET,
                                     "new_removal_password": "New-removal-secret-9!",
                                     "confirm_removal_password": "New-removal-secret-9!"})
        assert response.status_code == 302 and response.headers["Location"] == "/not-found"
        assert check_site_admin_key(SITE_ADMIN_KEY_SECRET) is True
        assert check_site_admin_key("New-removal-secret-9!") is False


class TestRemovalFailures:
    def test_wrong_own_password_logged_and_refused(self, client, db_session, login_as):
        seed_site_admin_key()
        actor = login_as(make_user(site_admin=True))
        target = make_user(site_admin=True)
        response = post_removal(client, target.id, own="Wrong-password-1!")
        assert target.site_admin is True
        # Field-level failures re-render the form into the open modal (retarget), so the
        # admin sees the error in place instead of the modal closing on them.
        assert response.headers.get("HX-Retarget") == "#modal-container"
        # The message doesn't say which field was wrong -- see _check_admin_grant_credentials.
        assert "password or the site admin key was incorrect" in response.get_data(as_text=True)
        activity = UserActivity.query.filter_by(
            activity_type="site_admin_key_failure", user_id=actor.id).one()
        assert activity.details.startswith("Failed credential")
        # The generic user-facing message hides which field failed, but the audit trail
        # must keep the distinction.
        assert "incorrect account password" in activity.details
        assert activity.ip_address is not None

    def test_wrong_site_admin_key_logged_and_refused(self, client, db_session,
                                                       login_as):
        seed_site_admin_key()
        actor = login_as(make_user(site_admin=True))
        target = make_user(site_admin=True)
        response = post_removal(client, target.id, removal="Wrong-secret-2!")
        assert target.site_admin is True
        assert response.headers.get("HX-Retarget") == "#modal-container"
        activity = UserActivity.query.filter_by(
            activity_type="site_admin_key_failure", user_id=actor.id).one()
        assert activity.details.startswith("Failed credential")
        assert "incorrect site admin key" in activity.details

    def test_failure_preserves_selected_action(self, client, db_session, login_as):
        # A failed deactivate attempt must not quietly reset the radio to demote --
        # a retry would otherwise perform the wrong removal mode.
        seed_site_admin_key()
        login_as(make_user(site_admin=True))
        target = make_user(site_admin=True)
        body = post_removal(client, target.id, removal="Wrong-secret-2!",
                            action="deactivate").get_data(as_text=True)
        assert 'value="deactivate" checked' in body

    def test_invalid_action_refused_without_testing_secrets(self, client, db_session,
                                                            login_as):
        seed_site_admin_key()
        actor = login_as(make_user(site_admin=True))
        target = make_user(site_admin=True)
        post_removal(client, target.id, action="banana")
        assert target.site_admin is True
        assert UserActivity.query.filter_by(
            activity_type="site_admin_key_failure", user_id=actor.id).count() == 0


class TestRemovalLockout:
    def test_lockout_after_threshold_failures(self, client, db_session, login_as, app,
                                              monkeypatch):
        monkeypatch.setitem(app.config, "SITE_ADMIN_KEY_LOCKOUT_THRESHOLD", 2)
        seed_site_admin_key()
        actor = login_as(make_user(site_admin=True))
        target = make_user(site_admin=True)
        for _ in range(2):
            post_removal(client, target.id, removal="Wrong-secret-2!")
        response = post_removal(client, target.id)  # correct creds, but locked
        assert "Too many failed attempts" in response.get_data(as_text=True)
        assert target.site_admin is True
        blocked = UserActivity.query.filter(
            UserActivity.user_id == actor.id,
            UserActivity.activity_type == "site_admin_key_attempt",
            UserActivity.details.like("Blocked%")).all()
        assert len(blocked) == 1

    def test_blocked_attempts_log_blocked_not_failed(self, client, db_session, login_as,
                                                     app, monkeypatch):
        # Blocked attempts must not count as failures, or a lockout would extend itself.
        monkeypatch.setitem(app.config, "SITE_ADMIN_KEY_LOCKOUT_THRESHOLD", 2)
        seed_site_admin_key()
        actor = login_as(make_user(site_admin=True))
        target = make_user(site_admin=True)
        for _ in range(2):
            post_removal(client, target.id, removal="Wrong-secret-2!")
        for _ in range(2):
            post_removal(client, target.id)  # correct creds, but locked
        # The type split IS the invariant now: failures carry their own counted activity_type,
        # blocked rows keep the plain one so a lockout can never extend itself.
        assert UserActivity.query.filter_by(
            activity_type="site_admin_key_failure", user_id=actor.id).count() == 2
        blocked = UserActivity.query.filter_by(
            activity_type="site_admin_key_attempt", user_id=actor.id).all()
        assert len(blocked) == 2
        assert all(a.details.startswith("Blocked") for a in blocked)
        assert target.site_admin is True

    def test_global_ceiling_blocks_fresh_actor(self, client, db_session, login_as, app,
                                               monkeypatch):
        monkeypatch.setitem(app.config, "SITE_ADMIN_KEY_LOCKOUT_GLOBAL_THRESHOLD", 2)
        seed_site_admin_key()
        other = make_user(site_admin=True)
        for _ in range(2):
            seed_failed_attempt(db_session, other.id)
        login_as(make_user(site_admin=True))
        target = make_user(site_admin=True)
        response = post_removal(client, target.id)
        assert "Too many failed attempts" in response.get_data(as_text=True)
        assert target.site_admin is True

    def test_stale_failures_outside_window_ignored(self, client, db_session, login_as,
                                                   app, monkeypatch):
        monkeypatch.setitem(app.config, "SITE_ADMIN_KEY_LOCKOUT_THRESHOLD", 2)
        seed_site_admin_key()
        actor = login_as(make_user(site_admin=True))
        target = make_user(site_admin=True)
        for _ in range(3):
            seed_failed_attempt(db_session, actor.id, age=timedelta(hours=2))
        response = post_removal(client, target.id)
        assert target.site_admin is False
        assert response.status_code == 200


class TestRemovalSuccess:
    def test_demote_clears_flag_keeps_account(self, client, db_session, login_as,
                                              ses_outbox):
        seed_site_admin_key()
        actor = login_as(make_user(site_admin=True))
        group = make_group()
        target = make_user(site_admin=True, group=group, role="Data Viewer")
        response = post_removal(client, target.id, action="demote")
        body = response.get_data(as_text=True)
        assert target.site_admin is False
        assert target.status == User.STATUS_ACTIVE
        assert UserGroups.query.filter_by(user_id=target.id).count() == 1
        assert f'id="user-row-{target.id}"' in body
        assert "Site Admin</span>" not in body
        activity = UserActivity.query.filter_by(
            activity_type="site_admin_removal", user_id=actor.id).one()
        assert activity.activity_target_id == target.id
        assert activity.ip_address is not None

    def test_deactivate_locks_account(self, client, db_session, login_as):
        seed_site_admin_key()
        login_as(make_user(site_admin=True))
        target = make_user(site_admin=True)
        post_removal(client, target.id, action="deactivate")
        assert target.status == User.STATUS_DEACTIVATED
        assert target.site_admin is False

    def test_removal_sends_no_email(self, client, db_session, login_as, ses_outbox):
        # Deliberate: admin-membership changes are quiet, audit-row-only events -- the
        # UserActivity trail is the record, and no notification tips off anyone.
        seed_site_admin_key()
        login_as(make_user(site_admin=True))
        make_user(site_admin=True)
        target = make_user(site_admin=True)
        response = post_removal(client, target.id, action="demote")
        assert response.status_code == 200
        assert target.site_admin is False
        assert len(ses_outbox) == 0

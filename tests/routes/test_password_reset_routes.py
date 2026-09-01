"""Forgot-password / reset-password lifecycle. SES is the autouse stub from
tests/routes/conftest.py; assertions read its capture records."""
from datetime import timedelta

import bcrypt
import pytest

from data_viz.database.models import PasswordResets, User, UserActivity

from tests.factories import (
    TEST_PASSWORD,
    make_invite,
    make_password_reset,
    make_user,
    unique,
)

IDENTICAL_MESSAGE = "If an account exists for that address, a password reset link has been sent."


class TestForgotPassword:
    def test_get_returns_form_cold(self, client, db_session):
        response = client.get("/v1/forgot-password")
        assert response.status_code == 200
        assert "forgot-password-form" in response.get_data(as_text=True)

    def test_get_returns_form_htmx(self, client, db_session):
        response = client.get("/v1/forgot-password", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "forgot-password-form" in response.get_data(as_text=True)

    def test_post_active_user_sends_email_and_creates_row(self, client, db_session, ses_outbox):
        user = make_user()
        response = client.post("/v1/forgot-password", data={"email": user.email})
        assert response.status_code == 302
        assert "/v1/login" in response.headers["Location"]

        assert len(ses_outbox) == 1
        assert ses_outbox[0].to == [user.email]
        assert "/v1/reset-password/" in ses_outbox[0].html

        reset = PasswordResets.query.filter_by(user_id=user.id).one()
        assert reset.used_at is None

    def test_post_unknown_email_sends_nothing(self, client, db_session, ses_outbox):
        response = client.post("/v1/forgot-password",
                               data={"email": f"{unique('nobody')}@example.org"})
        assert response.status_code == 302
        assert ses_outbox == []
        assert PasswordResets.query.count() == 0

    def test_anti_enumeration_identical_response(self, client, db_session, ses_outbox):
        user = make_user()
        known = client.post("/v1/forgot-password", data={"email": user.email},
                            follow_redirects=True)
        unknown = client.post("/v1/forgot-password",
                              data={"email": f"{unique('nobody')}@example.org"},
                              follow_redirects=True)
        assert known.status_code == unknown.status_code
        known_text = known.get_data(as_text=True)
        unknown_text = unknown.get_data(as_text=True)
        assert IDENTICAL_MESSAGE in known_text
        assert IDENTICAL_MESSAGE in unknown_text

    @pytest.mark.parametrize("status", [User.STATUS_DEACTIVATED, User.STATUS_INVITED])
    def test_post_non_active_user_sends_nothing(self, client, db_session, ses_outbox, status):
        user = make_user(status=status)
        response = client.post("/v1/forgot-password", data={"email": user.email},
                               follow_redirects=True)
        assert IDENTICAL_MESSAGE in response.get_data(as_text=True)
        assert ses_outbox == []
        assert PasswordResets.query.filter_by(user_id=user.id).count() == 0

    def test_malformed_email_surfaces_error_without_sending(self, client, db_session, ses_outbox):
        response = client.post("/v1/forgot-password", data={"email": "not-an-email"})
        assert response.status_code == 200
        assert "valid email" in response.get_data(as_text=True).lower()
        assert ses_outbox == []

    def test_send_failure_still_identical_response(self, client, db_session, monkeypatch):
        monkeypatch.setattr("data_viz.auth.auth.send_ses_email", lambda *a, **k: False)
        user = make_user()
        response = client.post("/v1/forgot-password", data={"email": user.email},
                               follow_redirects=True)
        assert IDENTICAL_MESSAGE in response.get_data(as_text=True)

    def test_second_request_supersedes_first(self, client, db_session, ses_outbox, app):
        user = make_user()
        client.post("/v1/forgot-password", data={"email": user.email})
        first = PasswordResets.query.filter_by(user_id=user.id).one()
        first_token = first.token

        client.post("/v1/forgot-password", data={"email": user.email})
        db_session.expire_all()
        assert first.used_at is not None

        # Each request creates a fresh row and marks prior unused rows used_at (rather than
        # mutating an existing row's token, as invite renewal does), so the old token is caught
        # by the used_at check and shown the generic "no longer valid" message.
        response = client.get(f"/v1/reset-password/{first_token}")
        assert response.status_code == 302
        follow = client.get(response.headers["Location"], follow_redirects=True)
        assert "no longer valid" in follow.get_data(as_text=True)

    def test_logged_in_user_bounced(self, client, db_session, login_as):
        login_as(make_user())
        get_response = client.get("/v1/forgot-password")
        assert get_response.status_code == 302
        post_response = client.post("/v1/forgot-password", data={"email": "x@example.org"})
        assert post_response.status_code == 302

    def test_activity_row_logged_no_failed_login_leak(self, client, db_session, ses_outbox):
        user = make_user()
        client.post("/v1/forgot-password", data={"email": user.email})
        rows = UserActivity.query.filter_by(user_id=user.id).all()
        assert any(r.activity_type == "password reset requested" for r in rows)
        assert not any((r.details or "").startswith("Failed login") for r in rows)


class TestResetPassword:
    def test_url_token_redirects_then_shows_form_with_email(self, client, db_session):
        user = make_user()
        reset = make_password_reset(user)
        response = client.get(f"/v1/reset-password/{reset.token}")
        assert response.status_code == 302
        assert "/v1/reset-password" in response.headers["Location"]

        follow = client.get(response.headers["Location"])
        assert follow.status_code == 200
        assert user.email in follow.get_data(as_text=True)
        assert "reset-password-form" in follow.get_data(as_text=True)

    def test_expired_token_rejected(self, client, db_session):
        user = make_user()
        reset = make_password_reset(user, expires_delta=timedelta(seconds=-60))
        response = client.get(f"/v1/reset-password/{reset.token}", follow_redirects=True)
        assert "expired" in response.get_data(as_text=True)

    def test_tampered_token_rejected(self, client, db_session):
        user = make_user()
        reset = make_password_reset(user)
        response = client.get(f"/v1/reset-password/{reset.token}x", follow_redirects=True)
        assert "invalid" in response.get_data(as_text=True)

    def test_invite_token_as_reset_token_rejected(self, client, db_session, app):
        """Both secrets fall back to the test SECRET_KEY, so an invite token's signature
        verifies here -- it must still be refused for lacking the "purpose" claim."""
        invite = make_invite()
        invite_token = invite.generate_jwt(app.config["INVITE_JWT_SECRET"])
        assert app.config["INVITE_JWT_SECRET"] == app.config["PASSWORD_RESET_JWT_SECRET"]
        response = client.get(f"/v1/reset-password/{invite_token}", follow_redirects=True)
        assert "reset-password-form" not in response.get_data(as_text=True)
        assert "invalid" in response.get_data(as_text=True)

    def test_used_token_rejected(self, client, db_session):
        user = make_user()
        reset = make_password_reset(user, used=True)
        response = client.get(f"/v1/reset-password/{reset.token}")
        assert response.status_code == 302
        follow = client.get(response.headers["Location"], follow_redirects=True)
        assert "no longer valid" in follow.get_data(as_text=True)

    def test_bare_get_without_session_token(self, client, db_session):
        response = client.get("/v1/reset-password", follow_redirects=True)
        assert "No password reset token provided" in response.get_data(as_text=True)

    def test_deactivated_user_token_rejected(self, client, db_session):
        user = make_user()
        reset = make_password_reset(user)
        user.status = User.STATUS_DEACTIVATED
        db_session.flush()
        response = client.get(f"/v1/reset-password/{reset.token}")
        assert response.status_code == 302
        follow = client.get(response.headers["Location"], follow_redirects=True)
        assert "no longer valid" in follow.get_data(as_text=True)

    def _consume_token(self, client, token):
        response = client.get(f"/v1/reset-password/{token}")
        assert response.status_code == 302
        return response.headers["Location"]

    def test_mismatched_passwords(self, client, db_session):
        user = make_user()
        reset = make_password_reset(user)
        self._consume_token(client, reset.token)
        response = client.post("/v1/reset-password", data={
            "password": "Some-Strong-pw1!", "confirm_password": "Different-pw1!"},
            follow_redirects=True)
        assert "do not match" in response.get_data(as_text=True)
        # Session token retained -- the form still renders on a follow-up GET.
        follow_up = client.get("/v1/reset-password")
        assert follow_up.status_code == 200
        assert "reset-password-form" in follow_up.get_data(as_text=True)

    def test_weak_password(self, client, db_session):
        user = make_user()
        reset = make_password_reset(user)
        self._consume_token(client, reset.token)
        response = client.post("/v1/reset-password", data={
            "password": "weak", "confirm_password": "weak"}, follow_redirects=True)
        assert "at least 12 characters" in response.get_data(as_text=True)

    def test_successful_reset(self, client, db_session, app):
        user = make_user()
        reset = make_password_reset(user)
        self._consume_token(client, reset.token)
        new_password = "Brand-New-pw1!"
        response = client.post("/v1/reset-password", data={
            "password": new_password, "confirm_password": new_password})
        assert response.status_code == 302
        assert "/v1/login" in response.headers["Location"]

        db_session.expire_all()
        assert reset.used_at is not None
        assert bcrypt.checkpw(new_password.encode("utf-8"), user.password_hash.encode("utf-8"))
        assert UserActivity.query.filter_by(user_id=user.id,
                                            activity_type="password reset").count() == 1
        with client.session_transaction() as sess:
            assert "password_reset_token" not in sess

        login_response = client.post("/v1/login",
                                     data={"username": user.username, "password": new_password},
                                     headers={"HX-Request": "true"})
        assert "HX-Push-Url" in login_response.headers

    def test_reset_to_same_password_succeeds_no_leak(self, client, db_session):
        """Proves no old-password comparison: resetting to the SAME password succeeds with the
        exact same flash/redirect as any other successful reset."""
        user = make_user()
        reset = make_password_reset(user)
        self._consume_token(client, reset.token)
        response = client.post("/v1/reset-password", data={
            "password": TEST_PASSWORD, "confirm_password": TEST_PASSWORD},
            follow_redirects=True)
        assert "Your password has been reset" in response.get_data(as_text=True)

    def test_reusing_token_after_success_rejected(self, client, db_session):
        user = make_user()
        reset = make_password_reset(user)
        self._consume_token(client, reset.token)
        new_password = "Brand-New-pw1!"
        client.post("/v1/reset-password", data={
            "password": new_password, "confirm_password": new_password})

        response = client.get(f"/v1/reset-password/{reset.token}")
        assert response.status_code == 302
        follow = client.get(response.headers["Location"], follow_redirects=True)
        assert "no longer valid" in follow.get_data(as_text=True)

    def test_logged_in_user_bounced(self, client, db_session, login_as):
        user = make_user()
        reset = make_password_reset(user)
        login_as(make_user())
        response = client.get(f"/v1/reset-password/{reset.token}")
        assert response.status_code == 302
        assert response.headers["Location"] in ("/", "http://localhost/")

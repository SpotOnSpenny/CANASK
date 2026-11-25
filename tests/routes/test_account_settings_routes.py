"""The account page's self-service options: change your password, your username and your email
(the email only after confirming a link sent to the NEW address), and see your recent sign-ins."""
import re
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from bcrypt import checkpw
from flask import current_app

from data_viz.database import db
from data_viz.database.models import PasswordResets, User, UserActivity

from tests.factories import TEST_PASSWORD, make_user, unique

HX = {"HX-Request": "true"}
NEW_PASSWORD = "Another-strong-pw2?"


def _password_is(user, password):
    return checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8"))


def _activity(user, activity_type):
    return UserActivity.query.filter_by(user_id=user.id, activity_type=activity_type).all()


class TestTabs:
    def test_all_four_options_are_offered(self, client, db_session, login_as):
        login_as(make_user())
        body = client.get("/v1/account", headers=HX).get_data(as_text=True)
        for url in ("/v1/account", "/v1/account/starting-charts", "/v1/account/password",
                    "/v1/account/sign-ins"):
            assert f'hx-get="{url}"' in body

    def test_password_tab_has_the_form(self, client, db_session, login_as):
        login_as(make_user())
        body = client.get("/v1/account/password", headers=HX).get_data(as_text=True)
        assert 'hx-post="/v1/account/password"' in body
        assert 'name="current_password"' in body

    def test_account_info_has_the_username_and_email_forms(self, client, db_session, login_as):
        login_as(make_user())
        body = client.get("/v1/account", headers=HX).get_data(as_text=True)
        assert 'hx-post="/v1/account/username"' in body
        assert 'hx-post="/v1/account/email"' in body


class TestChangePassword:
    def _post(self, client, **fields):
        data = {"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD}
        data.update(fields)
        return client.post("/v1/account/password", data=data, headers=HX)

    def test_changes_the_password(self, client, db_session, login_as):
        user = login_as(make_user())
        response = self._post(client)
        assert response.status_code == 200
        assert _password_is(user, NEW_PASSWORD)
        assert "password has been changed" in response.get_data(as_text=True)

    def test_this_session_stays_signed_in(self, client, db_session, login_as):
        login_as(make_user())
        self._post(client)
        assert client.get("/v1/account", headers=HX).status_code == 200

    def test_other_sessions_are_signed_out(self, client, db_session, login_as):
        user = login_as(make_user())
        stale_session_id = user.get_id()
        self._post(client)
        assert user.get_id() != stale_session_id

    def test_wrong_current_password_changes_nothing(self, client, db_session, login_as):
        user = login_as(make_user())
        response = self._post(client, current_password="Not-my-password-1!")
        assert _password_is(user, TEST_PASSWORD)
        assert "current password is incorrect" in response.get_data(as_text=True)

    def test_wrong_current_password_counts_as_a_failed_sign_in(self, client, db_session,
                                                               login_as):
        user = login_as(make_user())
        self._post(client, current_password="Not-my-password-1!")
        rows = _activity(user, "authentication attempt")
        assert len(rows) == 1 and rows[0].details.startswith("Failed login")

    def test_too_many_wrong_guesses_locks_the_form(self, client, db_session, login_as,
                                                   monkeypatch):
        monkeypatch.setitem(current_app.config, "LOGIN_LOCKOUT_THRESHOLD", 2)
        user = login_as(make_user())
        for _ in range(2):
            self._post(client, current_password="Not-my-password-1!")
        response = self._post(client)                       # the right password, but locked
        assert _password_is(user, TEST_PASSWORD)
        assert "Too many failed attempts" in response.get_data(as_text=True)

    def test_mismatched_confirmation_changes_nothing(self, client, db_session, login_as):
        user = login_as(make_user())
        response = self._post(client, confirm_password="Different-strong-pw3!")
        assert _password_is(user, TEST_PASSWORD)
        assert "do not match" in response.get_data(as_text=True)

    def test_weak_new_password_is_refused(self, client, db_session, login_as):
        user = login_as(make_user())
        response = self._post(client, new_password="short", confirm_password="short")
        assert _password_is(user, TEST_PASSWORD)
        assert "at least 12 characters" in response.get_data(as_text=True)

    def test_same_password_again_is_refused(self, client, db_session, login_as):
        user = login_as(make_user())
        response = self._post(client, new_password=TEST_PASSWORD, confirm_password=TEST_PASSWORD)
        assert "different from your current" in response.get_data(as_text=True)
        assert _activity(user, "password reset") == []

    def test_change_is_logged(self, client, db_session, login_as):
        user = login_as(make_user())
        self._post(client)
        (row,) = _activity(user, "password reset")
        assert "changed" in row.details.lower()

    def test_anonymous_is_sent_to_login(self, client, db_session):
        response = self._post(client)
        assert response.status_code == 302 and "/login" in response.headers["Location"]


class TestChangeUsername:
    def _post(self, client, username):
        return client.post("/v1/account/username", data={"username": username}, headers=HX)

    def test_changes_the_username(self, client, db_session, login_as):
        user = login_as(make_user())
        new = unique("renamed")
        body = self._post(client, new).get_data(as_text=True)
        assert user.username == new
        assert new in body

    def test_change_is_logged_with_both_names(self, client, db_session, login_as):
        user = login_as(make_user())
        old, new = user.username, unique("renamed")
        self._post(client, new)
        (row,) = _activity(user, "username_changed")
        assert old in row.details and new in row.details

    def test_name_taken_by_someone_else_is_refused(self, client, db_session, login_as):
        other = make_user(username=unique("Taken"))
        user = login_as(make_user())
        old = user.username
        body = self._post(client, other.username.lower()).get_data(as_text=True)   # case-insensitive
        assert user.username == old
        assert "already taken" in body

    def test_changing_only_the_case_of_your_own_name_is_allowed(self, client, db_session,
                                                                login_as):
        user = login_as(make_user(username=unique("casey")))
        new = user.username.upper()
        self._post(client, new)
        assert user.username == new

    def test_invalid_name_is_refused(self, client, db_session, login_as):
        user = login_as(make_user())
        old = user.username
        body = self._post(client, "no spaces allowed").get_data(as_text=True)
        assert user.username == old
        assert "3-30 characters" in body

    def test_same_name_changes_nothing(self, client, db_session, login_as):
        user = login_as(make_user())
        self._post(client, user.username)
        assert _activity(user, "username_changed") == []

    def test_anonymous_is_sent_to_login(self, client, db_session):
        response = self._post(client, "whoever")
        assert response.status_code == 302 and "/login" in response.headers["Location"]


class TestUsernameChangeLimit:
    """A cap on how often one account may change its username (USERNAME_CHANGE_LIMIT per
    USERNAME_CHANGE_WINDOW, default 2 per 24 hours): enough to fix a typo, not enough to cycle
    through names. Only changes that went through count."""

    def _post(self, client, username):
        return client.post("/v1/account/username", data={"username": username}, headers=HX)

    def _backdate(self, user, hours):
        """Make every username change so far look `hours` old."""
        for row in _activity(user, "username_changed"):
            row.timestamp = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
        db.session.flush()

    def test_second_change_the_same_day_is_allowed(self, client, db_session, login_as):
        user = login_as(make_user())
        self._post(client, unique("first"))
        second = unique("second")
        self._post(client, second)
        assert user.username == second

    def test_third_change_the_same_day_is_refused(self, client, db_session, login_as):
        user = login_as(make_user())
        self._post(client, unique("first"))
        second = unique("second")
        self._post(client, second)
        body = self._post(client, unique("third")).get_data(as_text=True)
        assert user.username == second
        assert "changed your username 2 times" in body
        assert len(_activity(user, "username_changed")) == 2

    def test_refusal_says_when_it_opens_up_again(self, client, db_session, login_as):
        user = login_as(make_user())
        self._post(client, unique("first"))
        self._post(client, unique("second"))
        self._backdate(user, hours=20)
        body = self._post(client, unique("third")).get_data(as_text=True)
        assert "about 4 hours" in body

    def test_changes_older_than_the_window_do_not_count(self, client, db_session, login_as):
        user = login_as(make_user())
        self._post(client, unique("first"))
        self._post(client, unique("second"))
        self._backdate(user, hours=25)
        third = unique("third")
        self._post(client, third)
        assert user.username == third

    def test_refused_attempts_do_not_use_up_the_allowance(self, client, db_session, login_as):
        taken = make_user(username=unique("Taken"))
        user = login_as(make_user())
        for _ in range(4):
            self._post(client, taken.username)       # already taken
            self._post(client, "no spaces allowed")  # invalid
        wanted = unique("wanted")
        self._post(client, wanted)
        assert user.username == wanted

    def test_the_cap_is_per_account(self, client, db_session, login_as):
        busy = login_as(make_user())
        self._post(client, unique("first"))
        self._post(client, unique("second"))
        other = login_as(make_user())
        wanted = unique("wanted")
        self._post(client, wanted)
        assert other.username == wanted
        assert busy.username != wanted

    def test_the_cap_comes_from_config(self, client, db_session, login_as, monkeypatch):
        monkeypatch.setitem(current_app.config, "USERNAME_CHANGE_LIMIT", 1)
        user = login_as(make_user())
        first = unique("first")
        self._post(client, first)
        self._post(client, unique("second"))
        assert user.username == first

    def test_at_the_cap_the_form_says_so_and_is_switched_off(self, client, db_session, login_as):
        login_as(make_user())
        self._post(client, unique("first"))
        self._post(client, unique("second"))
        body = client.get("/v1/account", headers=HX).get_data(as_text=True)
        assert "You can change it again in about 24 hours" in body
        assert 'id="account-username"' in body and "disabled" in body.split('id="account-username"')[1].split(">")[0]

    def test_under_the_cap_the_form_is_live(self, client, db_session, login_as):
        login_as(make_user())
        body = client.get("/v1/account", headers=HX).get_data(as_text=True)
        assert "disabled" not in body.split('id="account-username"')[1].split(">")[0]
        assert "You can change it again" not in body


def _confirm_link(ses_outbox):
    """The confirmation path from the one email sent so far."""
    (message,) = ses_outbox
    return re.search(r"(/v1/account/confirm-email/[A-Za-z0-9_.\-]+)", message.html).group(1)


class TestRequestEmailChange:
    def _post(self, client, new_email, password=TEST_PASSWORD):
        return client.post("/v1/account/email", headers=HX,
                           data={"new_email": new_email, "current_password": password})

    def test_sends_a_link_to_the_new_address_and_changes_nothing_yet(self, client, db_session,
                                                                     login_as, ses_outbox):
        user = login_as(make_user())
        old, new = user.email, f"{unique('fresh')}@example.org"
        body = self._post(client, new).get_data(as_text=True)
        assert user.email == old
        (message,) = ses_outbox
        assert message.to == [new]
        assert "/v1/account/confirm-email/" in message.html
        assert new in body

    def test_request_is_logged(self, client, db_session, login_as, ses_outbox):
        user = login_as(make_user())
        self._post(client, f"{unique('fresh')}@example.org")
        assert len(_activity(user, "email_change_requested")) == 1

    def test_wrong_password_sends_nothing(self, client, db_session, login_as, ses_outbox):
        user = login_as(make_user())
        body = self._post(client, f"{unique('fresh')}@example.org",
                          password="Not-my-password-1!").get_data(as_text=True)
        assert ses_outbox == []
        assert "current password is incorrect" in body
        assert _activity(user, "authentication attempt")[0].details.startswith("Failed login")

    def test_invalid_address_sends_nothing(self, client, db_session, login_as, ses_outbox):
        login_as(make_user())
        body = self._post(client, "not-an-email").get_data(as_text=True)
        assert ses_outbox == []
        assert "valid email address" in body

    def test_your_current_address_sends_nothing(self, client, db_session, login_as, ses_outbox):
        user = login_as(make_user())
        self._post(client, user.email.upper())
        assert ses_outbox == []

    def test_address_of_another_account_looks_the_same_but_sends_nothing(
            self, client, db_session, login_as, ses_outbox):
        """No "that address is taken": it would tell any signed-in user who has an account."""
        other = make_user()
        login_as(make_user())
        taken_body = self._post(client, other.email).get_data(as_text=True)
        assert ses_outbox == []
        assert "confirmation link" in taken_body
        assert "already" not in taken_body

    def test_anonymous_is_sent_to_login(self, client, db_session, ses_outbox):
        response = self._post(client, "someone@example.org")
        assert response.status_code == 302 and "/login" in response.headers["Location"]
        assert ses_outbox == []


class TestConfirmEmailChange:
    @pytest.fixture()
    def pending(self, client, db_session, login_as, ses_outbox):
        """A signed-in user with one email change requested. Returns (user, old, new, link)."""
        user = login_as(make_user())
        old, new = user.email, f"{unique('fresh')}@example.org"
        client.post("/v1/account/email", headers=HX,
                    data={"new_email": new, "current_password": TEST_PASSWORD})
        link = _confirm_link(ses_outbox)
        ses_outbox.clear()
        return user, old, new, link

    def test_opening_the_link_asks_before_changing_anything(self, client, pending):
        """A GET must not apply it: mail scanners open links."""
        user, old, new, link = pending
        body = client.get(link).get_data(as_text=True)
        assert user.email == old
        assert new in body
        assert f'hx-post="{link}"' in body

    def test_the_confirm_page_opens_when_signed_out(self, client, pending):
        user, old, new, link = pending
        with client.session_transaction() as sess:
            sess.clear()
        response = client.get(link)
        assert response.status_code == 200
        assert f'hx-post="{link}"' in response.get_data(as_text=True)
        assert user.email == old

    def test_confirming_changes_the_email(self, client, pending):
        user, old, new, link = pending
        client.post(link, headers=HX)
        assert user.email == new

    def test_confirming_is_logged_with_both_addresses(self, client, pending):
        user, old, new, link = pending
        client.post(link, headers=HX)
        (row,) = _activity(user, "email_changed")
        assert old in row.details and new in row.details

    def test_old_address_is_told(self, client, pending, ses_outbox):
        user, old, new, link = pending
        client.post(link, headers=HX)
        (notice,) = ses_outbox
        assert notice.to == [old]
        assert new in notice.html

    def test_works_when_signed_out(self, client, pending):
        """The link is opened from the new mailbox, quite possibly on another device."""
        user, old, new, link = pending
        with client.session_transaction() as sess:
            sess.clear()
        client.post(link, headers=HX)
        assert user.email == new

    def test_link_cannot_be_used_twice(self, client, pending):
        user, old, new, link = pending
        client.post(link, headers=HX)
        user.email = old            # as if they later changed back
        db.session.flush()
        client.post(link, headers=HX)
        assert user.email == old

    def test_a_second_request_is_dead_once_the_first_is_confirmed(self, client, pending,
                                                                  ses_outbox):
        user, old, new, link = pending
        other_new = f"{unique('second')}@example.org"
        client.post("/v1/account/email", headers=HX,
                    data={"new_email": other_new, "current_password": TEST_PASSWORD})
        second_link = _confirm_link(ses_outbox)
        client.post(link, headers=HX)
        client.post(second_link, headers=HX)
        assert user.email == new

    def test_link_dies_when_the_password_changes(self, client, pending):
        """Changing your password is how you shut out someone who got into your account;
        an email change they started must not survive it."""
        user, old, new, link = pending
        client.post("/v1/account/password", headers=HX,
                    data={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD})
        client.post(link, headers=HX)
        assert user.email == old

    def test_address_taken_in_the_meantime_is_refused(self, client, pending):
        user, old, new, link = pending
        make_user(email=new)
        client.post(link, headers=HX)
        assert user.email == old

    def test_deactivated_account_is_refused(self, client, pending):
        user, old, new, link = pending
        user.status = User.STATUS_DEACTIVATED
        db.session.flush()
        client.post(link, headers=HX)
        assert user.email == old

    def test_tampered_link_is_refused_and_logged(self, client, pending):
        user, old, new, link = pending
        response = client.post(link[:-3] + "abc", headers=HX)
        assert user.email == old
        assert response.status_code in (200, 204, 302)
        assert UserActivity.query.filter_by(activity_type="email_change_token_rejected").count() == 1

    def test_expired_link_is_refused(self, client, db_session, login_as):
        user = login_as(make_user())
        old = user.email
        token = jwt.encode({"purpose": "email_change", "user_id": user.id, "old_email": old,
                            "new_email": "late@example.org", "sv": user.session_version,
                            "exp": (datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp()},
                           current_app.config["PASSWORD_RESET_JWT_SECRET"], algorithm="HS256")
        client.post(f"/v1/account/confirm-email/{token}", headers=HX)
        assert user.email == old

    def test_a_password_reset_link_is_not_an_email_change_link(self, client, db_session,
                                                               login_as):
        user = login_as(make_user())
        old = user.email
        reset = PasswordResets.create(
            current_app.config["PASSWORD_RESET_JWT_SECRET"], user_id=user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30))
        client.post(f"/v1/account/confirm-email/{reset.token}", headers=HX)
        assert user.email == old


class TestSignInActivity:
    def _log(self, user, details, activity_type="authentication attempt", ip="203.0.113.7"):
        db.session.add(UserActivity(user_id=user.id, activity_type=activity_type,
                                    activity_target_type="User", activity_target_id=user.id,
                                    details=details, ip_address=ip))
        db.session.flush()

    def test_lists_your_sign_ins_and_failed_attempts(self, client, db_session, login_as):
        user = login_as(make_user())
        self._log(user, "Successful login", ip="203.0.113.7")
        self._log(user, f"Failed login attempt for {user.email}, using identifier hunter2",
                  ip="198.51.100.9")
        body = client.get("/v1/account/sign-ins", headers=HX).get_data(as_text=True)
        assert "Signed in" in body and "203.0.113.7" in body
        assert "Failed sign-in attempt" in body and "198.51.100.9" in body

    def test_does_not_echo_what_was_typed_at_the_login_form(self, client, db_session, login_as):
        """A mistyped identifier is often a password pasted into the wrong box."""
        user = login_as(make_user())
        self._log(user, f"Failed login attempt for {user.email}, using identifier hunter2")
        assert "hunter2" not in client.get("/v1/account/sign-ins", headers=HX).get_data(as_text=True)

    def test_other_peoples_activity_is_not_shown(self, client, db_session, login_as):
        other = make_user()
        self._log(other, "Successful login", ip="192.0.2.55")
        login_as(make_user())
        assert "192.0.2.55" not in client.get("/v1/account/sign-ins", headers=HX).get_data(as_text=True)

    def test_unrelated_activity_is_not_shown(self, client, db_session, login_as):
        user = login_as(make_user())
        self._log(user, "Some admin thing", activity_type="group_assignment", ip="192.0.2.77")
        assert "192.0.2.77" not in client.get("/v1/account/sign-ins", headers=HX).get_data(as_text=True)

    def test_account_changes_are_listed_too(self, client, db_session, login_as):
        user = login_as(make_user())
        client.post("/v1/account/password", headers=HX,
                    data={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD})
        body = client.get("/v1/account/sign-ins", headers=HX).get_data(as_text=True)
        assert "Password changed" in body

    def test_empty_history_says_so(self, client, db_session, login_as):
        login_as(make_user())
        body = client.get("/v1/account/sign-ins", headers=HX).get_data(as_text=True)
        assert "No recent activity" in body

    def test_only_the_most_recent_are_shown(self, client, db_session, login_as):
        user = login_as(make_user())
        for index in range(30):
            self._log(user, "Successful login", ip=f"203.0.113.{index}")
        body = client.get("/v1/account/sign-ins", headers=HX).get_data(as_text=True)
        assert body.count("Signed in") == 20

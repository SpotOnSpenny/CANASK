"""Login/logout through the real form flow (reCAPTCHA disabled by env; bcrypt real)."""
import json
import re

from tests.factories import TEST_PASSWORD, make_user


def login(client, identifier, password=TEST_PASSWORD, hx=True):
    headers = {"HX-Request": "true"} if hx else {}
    return client.post("/v1/login", data={"username": identifier, "password": password},
                       headers=headers)


class TestLogin:
    def test_successful_login_pushes_home_and_rotates_csrf(self, client, db_session):
        user = make_user()
        response = login(client, user.username)
        assert response.status_code == 200
        assert response.headers["HX-Push-Url"] == "/"
        trigger = json.loads(response.headers["HX-Trigger"])
        assert trigger["csrfTokenRefresh"]["token"]

    def test_login_by_email_case_insensitive(self, client, db_session):
        user = make_user()
        response = login(client, user.email.upper())
        assert response.status_code == 200
        assert "HX-Push-Url" in response.headers

    def test_wrong_password_generic_message(self, client, db_session):
        user = make_user()
        response = login(client, user.username, password="Wrong-password-1!")
        assert response.status_code == 200
        assert "HX-Push-Url" not in response.headers
        assert "Invalid username or password" in response.get_data(as_text=True)

    def test_unknown_user_same_generic_message(self, client, db_session):
        response = login(client, "nobody-here")
        assert "Invalid username or password" in response.get_data(as_text=True)

    def test_missing_fields_same_generic_message(self, client, db_session):
        response = client.post("/v1/login", data={"username": "x"},
                               headers={"HX-Request": "true"})
        assert "Invalid username or password" in response.get_data(as_text=True)

    def test_inactive_account_refused_even_with_correct_password(self, client, db_session):
        user = make_user(status="deactivated")
        response = login(client, user.username)
        assert "HX-Push-Url" not in response.headers
        assert "not active" in response.get_data(as_text=True)

    def test_lockout_after_threshold_failures(self, client, db_session, app, monkeypatch):
        monkeypatch.setitem(app.config, "LOGIN_LOCKOUT_THRESHOLD", 2)
        user = make_user()
        for _ in range(2):
            login(client, user.username, password="Wrong-password-1!")
        # Correct password now, but the account+IP is locked.
        response = login(client, user.username)
        assert "HX-Push-Url" not in response.headers
        assert "Too many failed login attempts" in response.get_data(as_text=True)

    def test_successful_login_resets_failure_count(self, client, db_session, app,
                                                   monkeypatch):
        monkeypatch.setitem(app.config, "LOGIN_LOCKOUT_THRESHOLD", 3)
        user = make_user()
        for _ in range(2):
            login(client, user.username, password="Wrong-password-1!")
        assert login(client, user.username).status_code == 200          # success resets
        for _ in range(2):
            login(client, user.username, password="Wrong-password-1!")  # 2 < 3 again
        assert "HX-Push-Url" in login(client, user.username).headers

    def test_get_login_page(self, client, db_session):
        response = client.get("/v1/login")
        assert response.status_code == 200


class TestLogout:
    def test_logout_htmx_redirects_home(self, client, db_session, login_as):
        login_as(make_user())
        response = client.post("/v1/logout", headers={"HX-Request": "true"})
        assert (response.status_code, response.headers["HX-Redirect"]) == (204, "/")

    def test_logout_requires_auth(self, client, db_session):
        response = client.post("/v1/logout")
        assert response.status_code == 302
        assert "/v1/login" in response.headers["Location"]


class TestCsrf:
    """The suite disables CSRF globally; these two re-enable it to prove the protection
    and the token plumbing both work."""

    def test_post_without_token_rejected(self, client, db_session, app, monkeypatch):
        monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
        response = client.post("/v1/login", data={"username": "x", "password": "y"})
        assert response.status_code == 400

    def test_post_with_meta_tag_token_accepted(self, client, db_session, app, monkeypatch):
        monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
        user = make_user()
        page = client.get("/").get_data(as_text=True)   # full page carries the meta tag
        token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
        response = client.post(
            "/v1/login", data={"username": user.username, "password": TEST_PASSWORD},
            headers={"X-CSRFToken": token, "HX-Request": "true"})
        assert response.status_code == 200
        assert "HX-Push-Url" in response.headers

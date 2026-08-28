"""The two stacking guards. Deliberate pin: failures redirect with a flash - never 403.
/v1/user-management needs Group Admin (in any group); /v1/group-management needs
Data Owner."""
from tests.factories import make_group, make_user


class TestRequireAuth:
    def test_anonymous_redirected_to_login(self, client, db_session):
        response = client.get("/v1/user-management")
        assert response.status_code == 302
        assert "/v1/login" in response.headers["Location"]

    def test_deactivated_user_treated_as_anonymous(self, client, db_session, login_as):
        user = make_user()
        login_as(user)
        user.status = "deactivated"   # kill-switch: session survives, access doesn't
        from data_viz.database import db
        db.session.flush()
        response = client.get("/v1/user-management")
        assert response.status_code == 302
        assert "/v1/login" in response.headers["Location"]


class TestRequireRole:
    def test_insufficient_role_redirects_home_never_403(self, client, db_session, login_as):
        group = make_group()
        login_as(make_user(group=group, role="Data Viewer"))
        response = client.get("/v1/user-management")
        assert response.status_code == 302
        assert response.headers["Location"] in ("/", "http://localhost/")

    def test_no_groups_at_all_redirects_home(self, client, db_session, login_as):
        login_as(make_user())
        response = client.get("/v1/user-management")
        assert response.status_code == 302

    def test_exact_role_passes(self, client, db_session, login_as):
        group = make_group()
        login_as(make_user(group=group, role="Group Admin"))
        assert client.get("/v1/user-management").status_code == 200

    def test_higher_role_passes_lower_gate(self, client, db_session, login_as):
        group = make_group()
        login_as(make_user(group=group, role="Data Owner"))
        assert client.get("/v1/user-management").status_code == 200

    def test_group_admin_blocked_from_data_owner_route(self, client, db_session, login_as):
        group = make_group()
        login_as(make_user(group=group, role="Group Admin"))
        response = client.get("/v1/group-management")
        assert response.status_code == 302

    def test_data_owner_passes_data_owner_route(self, client, db_session, login_as):
        group = make_group()
        login_as(make_user(group=group, role="Data Owner"))
        assert client.get("/v1/group-management").status_code == 200

    def test_site_admin_bypasses_all_role_checks(self, client, db_session, login_as):
        login_as(make_user(site_admin=True))   # no group memberships at all
        assert client.get("/v1/user-management").status_code == 200
        assert client.get("/v1/group-management").status_code == 200

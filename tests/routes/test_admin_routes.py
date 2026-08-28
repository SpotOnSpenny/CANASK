"""Management pages: HTMX partial vs full-page branching, group creation, add-user."""
from data_viz.database.models import Groups, Invites, UserGroups

from tests.factories import make_group, make_user, unique


class TestHtmxBranching:
    def test_full_page_load_wraps_in_base(self, client, db_session, login_as):
        login_as(make_user(site_admin=True))
        body = client.get("/v1/user-management").get_data(as_text=True)
        assert "<html" in body
        assert 'name="csrf-token"' in body

    def test_htmx_request_gets_bare_partial(self, client, db_session, login_as):
        login_as(make_user(site_admin=True))
        body = client.get("/v1/user-management",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "<html" not in body

    def test_htmx_flash_injected_as_oob_swap(self, client, db_session):
        """When a flash exists, the after_request hook appends it to HTMX HTML
        responses as an out-of-band swap into #flashed-messages-container - the id is
        a contract with base.jinja. (A failed login flashes and returns a 200 partial.)"""
        body = client.post("/v1/login", data={"username": "no", "password": "no"},
                           headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="flashed-messages-container"' in body
        assert 'hx-swap-oob="true"' in body
        assert "Invalid username or password" in body


class TestCreateGroup:
    def test_creates_group(self, client, db_session, login_as):
        login_as(make_user(site_admin=True))
        name = unique("team")
        response = client.post("/v1/create-group",
                               data={"name": name, "description": "a team"})
        assert response.status_code == 302
        assert Groups.query.filter_by(name=name).count() == 1

    def test_creator_below_site_admin_becomes_data_owner(self, client, db_session,
                                                         login_as):
        seed_group = make_group()
        owner = login_as(make_user(group=seed_group, role="Data Owner"))
        name = unique("team")
        client.post("/v1/create-group", data={"name": name})
        group = Groups.query.filter_by(name=name).one()
        membership = UserGroups.query.filter_by(user_id=owner.id, group_id=group.id).one()
        assert membership.role == "Data Owner"

    def test_duplicate_name_refused(self, client, db_session, login_as):
        login_as(make_user(site_admin=True))
        group = make_group()
        client.post("/v1/create-group", data={"name": group.name})
        assert Groups.query.filter_by(name=group.name).count() == 1

    def test_group_admin_cannot_create_groups(self, client, db_session, login_as):
        group = make_group()
        login_as(make_user(group=group, role="Group Admin"))
        name = unique("team")
        response = client.post("/v1/create-group", data={"name": name})
        assert response.status_code == 302   # redirected away, not created
        assert Groups.query.filter_by(name=name).count() == 0


class TestAddUser:
    def test_get_is_always_a_modal_partial(self, client, db_session, login_as):
        login_as(make_user(site_admin=True))
        body = client.get("/v1/add-user").get_data(as_text=True)
        assert "<html" not in body

    def test_unknown_email_falls_back_to_invite(self, client, db_session, login_as,
                                                ses_outbox, celery_stub):
        login_as(make_user(site_admin=True))
        group = make_group()
        email = f"{unique('add')}@example.org"
        response = client.post("/v1/add-user", data={
            "email": email, "group_assignment_1": f"{group.name}__Data Viewer"})
        assert response.status_code == 302
        invite = Invites.query.filter_by(email=email).one()
        assert invite.status == "pending"
        assert len(ses_outbox) == 1
        assert len(celery_stub.scheduled) == 1

    def test_existing_user_gets_membership_not_invite(self, client, db_session, login_as,
                                                      celery_stub):
        login_as(make_user(site_admin=True))
        group = make_group()
        existing = make_user()
        response = client.post("/v1/add-user", data={
            "email": existing.email, "group_assignment_1": f"{group.name}__Data Viewer"})
        assert response.status_code == 302
        membership = UserGroups.query.filter_by(user_id=existing.id,
                                                group_id=group.id).one()
        assert membership.role == "Data Viewer"
        assert Invites.query.filter_by(email=existing.email).count() == 0
        assert celery_stub.scheduled == []

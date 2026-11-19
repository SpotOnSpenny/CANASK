"""User Management for group managers (Group Admin / Data Owner, not site admins): the page
opens on the people in the groups they manage ("My groups") and has an "All users" view -- a
directory of every active account, so they can find someone to add. The directory shows
email, username and status ONLY: never which other groups a person belongs to, nor whether
they are a site admin."""
import re

import pytest

from data_viz.database.models import User, UserGroups

from tests.factories import make_group, make_user

HX = {"HX-Request": "true"}


def page(client):
    return client.get("/v1/user-management", headers=HX).get_data(as_text=True)


def row(body, user):
    """The <tr> for `user`, or None when the page does not list them."""
    match = re.search(rf'<tr id="user-row-{user.id}"[^>]*>.*?</tr>', body, re.S)
    return match.group(0) if match else None


@pytest.fixture()
def team(db_session):
    return make_group()


@pytest.fixture()
def manager(team):
    return make_user(group=team, role="Data Owner")


class TestTheTwoViews:
    def test_opens_on_my_groups_with_an_all_users_tab(self, client, login_as, manager):
        login_as(manager)
        body = page(client)
        assert 'id="user-table" data-view="mine"' in body
        assert 'data-user-view="mine"' in body and 'data-user-view="all"' in body

    def test_members_are_mine_and_everyone_else_is_other(self, client, login_as, team,
                                                         manager):
        member = make_user(group=team, role="Data Viewer")
        outsider = make_user()
        login_as(manager)
        body = page(client)
        assert 'data-scope="mine"' in row(body, member)
        assert 'data-scope="mine"' in row(body, manager)
        assert 'data-scope="other"' in row(body, outsider)

    @pytest.mark.parametrize("role", ["Data Owner", "Group Admin"])
    def test_both_manager_roles_get_the_directory(self, client, login_as, team, role):
        outsider = make_user()
        login_as(make_user(group=team, role=role))
        assert row(page(client), outsider) is not None

    def test_data_viewer_still_has_no_user_management(self, client, login_as, team):
        login_as(make_user(group=team, role="Data Viewer"))
        assert client.get("/v1/user-management").status_code == 302

    def test_site_admin_view_is_unchanged(self, client, login_as, db_session):
        other = make_user()
        login_as(make_user(site_admin=True))
        body = page(client)
        assert "data-user-view" not in body
        assert 'data-scope="mine"' in row(body, other)


class TestWhatTheDirectoryShows:
    def test_other_peoples_groups_are_not_shown(self, client, login_as, manager):
        elsewhere = make_group()
        outsider = make_user(group=elsewhere, role="Data Owner")
        login_as(manager)
        body = page(client)
        assert outsider.email in row(body, outsider)
        assert elsewhere.name not in body

    def test_a_shared_member_shows_only_the_groups_i_manage(self, client, login_as, team,
                                                            manager):
        elsewhere = make_group()
        member = make_user(group=team, role="Data Viewer")
        make_user()   # keep the factory's flush order irrelevant
        from tests.factories import add_membership
        add_membership(member, elsewhere, "Data Owner")
        login_as(manager)
        body = page(client)
        assert team.name in row(body, member)
        assert elsewhere.name not in body

    def test_site_admin_status_of_an_outsider_is_not_shown(self, client, login_as, manager):
        admin = make_user(site_admin=True)
        login_as(manager)
        assert "Site Admin" not in row(page(client), admin)

    def test_only_active_outsiders_are_listed(self, client, login_as, team, manager):
        gone = make_user(status=User.STATUS_DEACTIVATED)
        pending = make_user(status=User.STATUS_INVITED)
        gone_member = make_user(status=User.STATUS_DEACTIVATED, group=team)
        login_as(manager)
        body = page(client)
        assert row(body, gone) is None and row(body, pending) is None
        assert row(body, gone_member) is not None   # my own members: unchanged


class TestAddingFromTheDirectory:
    def test_outsider_row_offers_add_to_group_and_no_adjust(self, client, login_as, manager):
        outsider = make_user()
        login_as(manager)
        html = row(page(client), outsider)
        assert f'hx-get="/v1/add-user?user_id={outsider.id}"' in html
        assert "adjust-permissions" not in html

    def test_member_row_keeps_adjust_and_has_no_add(self, client, login_as, team, manager):
        member = make_user(group=team, role="Data Viewer")
        login_as(manager)
        html = row(page(client), member)
        assert f'hx-get="/v1/users/{member.id}/adjust-permissions"' in html
        assert "/v1/add-user?user_id=" not in html

    def test_add_user_modal_is_prefilled_for_that_person(self, client, login_as, manager):
        outsider = make_user()
        login_as(manager)
        body = client.get(f"/v1/add-user?user_id={outsider.id}",
                          headers=HX).get_data(as_text=True)
        assert f'value="{outsider.email}"' in body

    @pytest.mark.parametrize("status", [User.STATUS_DEACTIVATED, User.STATUS_INVITED])
    def test_prefill_ignores_accounts_the_directory_does_not_list(self, client, login_as,
                                                                  manager, status):
        hidden = make_user(status=status)
        login_as(manager)
        body = client.get(f"/v1/add-user?user_id={hidden.id}",
                          headers=HX).get_data(as_text=True)
        assert hidden.email not in body

    def test_prefill_ignores_junk(self, client, login_as, manager):
        login_as(manager)
        response = client.get("/v1/add-user?user_id=not-a-number", headers=HX)
        assert response.status_code == 200
        assert 'name="email"' in response.get_data(as_text=True)

    def test_adding_an_outsider_makes_them_mine(self, client, login_as, team, manager,
                                                celery_stub):
        outsider = make_user()
        login_as(manager)
        client.post("/v1/add-user", data={
            "email": outsider.email, "group_assignment_1": f"{team.name}__Data Viewer"})
        assert [m.role for m in UserGroups.query.filter_by(
            user_id=outsider.id, group_id=team.id)] == ["Data Viewer"]
        assert 'data-scope="mine"' in row(page(client), outsider)

    def test_removing_someones_last_group_returns_an_other_row(self, client, login_as, team,
                                                               manager):
        member = make_user(group=team, role="Data Viewer")
        login_as(manager)
        html = client.post(f"/v1/users/{member.id}/adjust-permissions", data={},
                           headers=HX).get_data(as_text=True)
        assert 'data-scope="other"' in html
        assert f'hx-get="/v1/add-user?user_id={member.id}"' in html


def filter_options(body):
    """{group id: label} offered by the group filter, or None when the page has no filter."""
    match = re.search(r'<select id="user-group-filter".*?</select>', body, re.S)
    if not match:
        return None
    return {int(value): label.strip() for value, label in
            re.findall(r'<option value="(\d+)">(.*?)</option>', match.group(0), re.S)}


def row_groups(body, user):
    return set(re.search(r'data-groups="([^"]*)"', row(body, user)).group(1).split())


class TestGroupFilter:
    """Someone who manages more than one group can narrow the list to one of them. The filter
    is client-side: the page supplies the options and each row's data-groups."""

    def test_manager_of_two_groups_gets_a_filter_of_exactly_those(self, client, login_as,
                                                                  team, manager):
        from tests.factories import add_membership
        second, viewed, unrelated = make_group(), make_group(), make_group()
        add_membership(manager, second, "Group Admin")
        add_membership(manager, viewed, "Data Viewer")   # a member, but not a manager, there
        login_as(manager)
        assert filter_options(page(client)) == {team.id: team.name, second.id: second.name}

    def test_manager_of_one_group_gets_no_filter(self, client, login_as, manager):
        make_group()
        login_as(manager)
        assert filter_options(page(client)) is None

    def test_rows_carry_only_the_groups_i_manage(self, client, login_as, team, manager):
        from tests.factories import add_membership
        second, elsewhere = make_group(), make_group()
        add_membership(manager, second, "Data Owner")
        in_one = make_user(group=team)
        in_both = make_user(group=team)
        add_membership(in_both, second, "Data Viewer")
        add_membership(in_one, elsewhere, "Data Owner")   # not mine to see
        outsider = make_user(group=elsewhere)
        login_as(manager)
        body = page(client)
        assert row_groups(body, in_one) == {str(team.id)}
        assert row_groups(body, in_both) == {str(team.id), str(second.id)}
        assert row_groups(body, outsider) == set()

    def test_site_admin_filters_across_every_group(self, client, login_as, db_session):
        first, second = make_group(), make_group()
        member = make_user(group=second)
        login_as(make_user(site_admin=True))
        body = page(client)
        options = filter_options(body)
        assert options[first.id] == first.name and options[second.id] == second.name
        assert row_groups(body, member) == {str(second.id)}

    def test_a_re_rendered_row_carries_its_new_groups(self, client, login_as, team, manager):
        from tests.factories import add_membership
        second = make_group()
        add_membership(manager, second, "Data Owner")
        member = make_user(group=team)
        add_membership(member, second, "Data Viewer")
        login_as(manager)
        html = client.post(f"/v1/users/{member.id}/adjust-permissions",
                           data={f"role_{second.id}": "Data Viewer"},
                           headers=HX).get_data(as_text=True)
        assert re.search(r'data-groups="([^"]*)"', html).group(1).split() == [str(second.id)]

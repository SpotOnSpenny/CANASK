"""Group roles are always listed from most access to least: Data Owner, Group Admin,
Data Viewer -- in every picker and report."""
import pytest

from data_viz.auth.auth_helpers import get_assignable_roles
from data_viz.auth.restricted import source_access_report
from data_viz.auth.role_hierarchy import GROUP_ROLES, ROLE_HIERARCHY

from tests.factories import grant_data_source, make_data_source, make_group, make_invite, make_user

MOST_TO_LEAST = ["Data Owner", "Group Admin", "Data Viewer"]


def offered_order(body, marker):
    """The roles in the order `body` lists them, located by a per-role marker string."""
    found = [(body.index(marker.format(role=role)), role) for role in MOST_TO_LEAST
             if marker.format(role=role) in body]
    return [role for _, role in sorted(found)]


def test_group_roles_constant_is_most_to_least_and_matches_the_hierarchy():
    assert GROUP_ROLES == MOST_TO_LEAST
    assert sorted(GROUP_ROLES, key=ROLE_HIERARCHY.get, reverse=True) == GROUP_ROLES
    assert set(GROUP_ROLES) == set(ROLE_HIERARCHY) - {"Site Admin"}


class TestPickers:
    @pytest.mark.parametrize("path", ["/v1/add-user", "/v1/invite-user"])
    def test_site_admin_pickers(self, client, login_as, db_session, path):
        group = make_group()
        login_as(make_user(site_admin=True))
        body = client.get(path, headers={"HX-Request": "true"}).get_data(as_text=True)
        assert offered_order(body, f'value="{group.name}__{{role}}"') == MOST_TO_LEAST

    @pytest.mark.parametrize("path", ["/v1/add-user", "/v1/invite-user"])
    def test_data_owner_pickers(self, client, login_as, db_session, path):
        group = make_group()
        login_as(make_user(group=group, role="Data Owner"))
        body = client.get(path, headers={"HX-Request": "true"}).get_data(as_text=True)
        assert offered_order(body, f'value="{group.name}__{{role}}"') == [
            "Group Admin", "Data Viewer"]

    def test_adjust_user_modal(self, client, login_as, db_session):
        make_group()
        target = make_user()
        login_as(make_user(site_admin=True))
        body = client.get(f"/v1/users/{target.id}/adjust-permissions",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert offered_order(body, '<option value="{role}"') == MOST_TO_LEAST

    def test_adjust_invite_modal(self, client, login_as, db_session):
        invite = make_invite(groups=[(make_group(), "Data Viewer")])
        login_as(make_user(site_admin=True))
        body = client.get(f"/v1/invites/{invite.id}/adjust-permissions",
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert offered_order(body, '<option value="{role}"') == MOST_TO_LEAST


class TestHelpers:
    def test_assignable_roles_for_a_site_admin_and_a_member(self, db_session):
        group = make_group()
        assert get_assignable_roles(make_user(site_admin=True), group.id) == MOST_TO_LEAST
        owner = make_user(group=group, role="Data Owner")
        assert get_assignable_roles(owner, group.id) == ["Group Admin", "Data Viewer"]

    def test_source_access_report_lists_members_most_access_first(self, db_session):
        source, group = make_data_source(), make_group()
        grant_data_source(group, source)
        for role in ["Data Viewer", "Group Admin", "Data Owner"]:
            make_user(group=group, role=role)
        (entry,) = source_access_report(source)["groups"]
        assert [m["role"] for m in entry["members"]] == MOST_TO_LEAST

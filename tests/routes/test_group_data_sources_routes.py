"""Group Management -> "Manage data sources" for a Data Owner who is not a site admin: they may
attach / detach only the sources they own (Data Owner of a group that holds it). The modal must
not present the rest of the catalogue as if it were theirs to hand out."""
from data_viz.database.models import GroupDataSources

from tests.factories import (
    add_membership,
    grant_data_source,
    make_data_source,
    make_group,
    make_user,
    make_visual,
)

HX = {"HX-Request": "true"}


def _source(name=None):
    """A data source with a visual: owned_data_sources only counts sources that have one."""
    source = make_data_source(name=name)
    make_visual(data_source=source, visibility="private")
    return source


def _owner_with_source():
    """A group, its Data Owner, and a source the group holds (so the owner owns it)."""
    group = make_group()
    owner = make_user(group=group, role="Data Owner")
    owned = _source("Owned Source")
    grant_data_source(group, owned)
    return group, owner, owned


def _held(group):
    return {row.data_source_id for row in GroupDataSources.query.filter_by(group_id=group.id)}


class TestDataOwnerModal:
    def test_owned_source_is_listed(self, client, db_session, login_as):
        group, owner, owned = _owner_with_source()
        login_as(owner)
        body = client.get(f"/v1/groups/{group.id}/data-sources", headers=HX).get_data(as_text=True)
        assert f'id="source_{owned.id}"' in body

    def test_source_they_do_not_own_is_not_listed(self, client, db_session, login_as):
        group, owner, _ = _owner_with_source()
        foreign = _source("Somebody Elses Source")
        login_as(owner)
        body = client.get(f"/v1/groups/{group.id}/data-sources", headers=HX).get_data(as_text=True)
        assert f'id="source_{foreign.id}"' not in body
        assert "Somebody Elses Source" not in body

    def test_only_owned_sources_are_listed(self, client, db_session, login_as):
        group, owner, _ = _owner_with_source()
        _source("Unrelated")
        login_as(owner)
        body = client.get(f"/v1/groups/{group.id}/data-sources", headers=HX).get_data(as_text=True)
        assert body.count('class="form-check-input source-check"') == 1

    def test_source_owned_through_another_group_can_be_attached(self, client, db_session,
                                                                login_as):
        group, owner, owned = _owner_with_source()
        second = make_group()
        add_membership(owner, second, "Data Owner")
        login_as(owner)
        body = client.get(f"/v1/groups/{second.id}/data-sources", headers=HX).get_data(as_text=True)
        assert f'id="source_{owned.id}"' in body
        client.post(f"/v1/groups/{second.id}/data-sources", headers=HX,
                    data={"source_ids": [str(owned.id)]})
        assert _held(second) == {owned.id}

    def test_posting_a_source_they_do_not_own_attaches_nothing(self, client, db_session,
                                                              login_as):
        group, owner, owned = _owner_with_source()
        foreign = _source()
        login_as(owner)
        client.post(f"/v1/groups/{group.id}/data-sources", headers=HX,
                    data={"source_ids": [str(owned.id), str(foreign.id)]})
        assert _held(group) == {owned.id}


class TestSiteAdminModal:
    def test_site_admin_still_sees_every_source(self, client, db_session, login_as):
        group = make_group()
        first, second = _source(), _source()
        login_as(make_user(site_admin=True))
        body = client.get(f"/v1/groups/{group.id}/data-sources", headers=HX).get_data(as_text=True)
        assert f'id="source_{first.id}"' in body
        assert f'id="source_{second.id}"' in body


class TestRemovingASourceNeedsConfirming:
    """Detaching a source takes the group's access to its non-public visuals, clears its visual
    grants and starting charts, and a Data Owner may not be able to add it back -- so the POST
    only removes when the request says it was confirmed (the modal's second step sets it)."""

    def test_unconfirmed_removal_changes_nothing(self, client, db_session, login_as):
        group, owner, owned = _owner_with_source()
        login_as(owner)
        response = client.post(f"/v1/groups/{group.id}/data-sources", headers=HX, data={})
        assert response.status_code == 200
        assert _held(group) == {owned.id}
        assert "confirm" in response.get_data(as_text=True).lower()

    def test_confirmed_removal_detaches(self, client, db_session, login_as):
        group, owner, owned = _owner_with_source()
        login_as(owner)
        client.post(f"/v1/groups/{group.id}/data-sources", headers=HX,
                    data={"confirm_remove": "1"})
        assert _held(group) == set()

    def test_adding_needs_no_confirmation(self, client, db_session, login_as):
        group = make_group()
        source = _source()
        login_as(make_user(site_admin=True))
        client.post(f"/v1/groups/{group.id}/data-sources", headers=HX,
                    data={"source_ids": [str(source.id)]})
        assert _held(group) == {source.id}

    def test_unconfirmed_swap_applies_neither_half(self, client, db_session, login_as):
        """One source added and another removed in the same save: without the confirmation
        nothing is applied, rather than half of what the form asked for."""
        group = make_group()
        staying, arriving = _source(), _source()
        grant_data_source(group, staying)
        login_as(make_user(site_admin=True))
        client.post(f"/v1/groups/{group.id}/data-sources", headers=HX,
                    data={"source_ids": [str(arriving.id)]})
        assert _held(group) == {staying.id}

    def test_any_other_confirm_value_is_not_a_confirmation(self, client, db_session, login_as):
        group, owner, owned = _owner_with_source()
        login_as(owner)
        client.post(f"/v1/groups/{group.id}/data-sources", headers=HX,
                    data={"confirm_remove": "0"})
        assert _held(group) == {owned.id}

    def test_modal_carries_the_confirmation_step(self, client, db_session, login_as):
        group, owner, _ = _owner_with_source()
        login_as(owner)
        body = client.get(f"/v1/groups/{group.id}/data-sources", headers=HX).get_data(as_text=True)
        assert 'id="source-remove-warning"' in body
        assert "be able to add it back" in body

    def test_site_admin_modal_does_not_warn_about_adding_it_back(self, client, db_session,
                                                                 login_as):
        group = make_group()
        grant_data_source(group, _source())
        login_as(make_user(site_admin=True))
        body = client.get(f"/v1/groups/{group.id}/data-sources", headers=HX).get_data(as_text=True)
        assert 'id="source-remove-warning"' in body
        assert "be able to add it back" not in body

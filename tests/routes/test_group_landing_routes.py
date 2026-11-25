"""Starting charts through the routes: a group's Data Owners pick what its members open a
province on (Group Management -> Starting charts), and the province page's one-time notice for
a member of two groups that disagree. A user's own choice is in test_account_routes.py."""
from data_viz.database.models import GroupLandingVisuals, UserLandingChoices

from tests.factories import (
    add_membership,
    grant_data_source,
    make_data_source,
    make_group,
    make_restricted_source,
    make_user,
    make_visual,
    set_landing,
    unique,
)

HX = {"HX-Request": "true"}


def _province(*names):
    province = unique("prov")
    return province, {name: make_visual(province=province, name=name, visibility="public",
                                        level="1", menu_name=name.title()) for name in names}


def _owned_group(role="Data Owner"):
    group = make_group()
    return group, make_user(group=group, role=role)


def _pick(group, province):
    return GroupLandingVisuals.query.filter_by(group_id=group.id, province=province).first()


class TestOpeningViewsModal:
    def test_owner_sees_the_candidates(self, client, db_session, login_as):
        province, v = _province("one", "two")
        group, owner = _owned_group()
        login_as(owner)
        body = client.get(f"/v1/groups/{group.id}/landing", headers=HX).get_data(as_text=True)
        assert f'name="landing__{province}"' in body
        assert f'value="{v["one"].id}"' in body
        assert "Two" in body
        assert "Site default" in body

    def test_groups_own_private_visual_is_offered_and_marked(self, client, db_session,
                                                             login_as):
        province, _ = _province("one")
        source = make_data_source()
        group, owner = _owned_group()
        grant_data_source(group, source)
        priv = make_visual(province=province, name="priv", visibility="private", level="1",
                           data_source=source, menu_name="Our Private Chart")
        login_as(owner)
        body = client.get(f"/v1/groups/{group.id}/landing", headers=HX).get_data(as_text=True)
        assert f'value="{priv.id}"' in body
        assert "Our Private Chart (Data Owners only)" in body
        assert "One (Data Owners only)" not in body

    def test_owner_can_pick_the_groups_own_private_visual(self, client, db_session, login_as):
        province, _ = _province("one")
        source = make_data_source()
        group, owner = _owned_group()
        grant_data_source(group, source)
        priv = make_visual(province=province, name="priv", visibility="private", level="1",
                           data_source=source)
        login_as(owner)
        client.post(f"/v1/groups/{group.id}/landing", headers=HX,
                    data={f"landing__{province}": str(priv.id)})
        assert _pick(group, province).visual_id == priv.id

    def test_current_pick_is_selected(self, client, db_session, login_as):
        province, v = _province("one", "two")
        group, owner = _owned_group()
        set_landing(group, v["two"])
        login_as(owner)
        body = client.get(f"/v1/groups/{group.id}/landing", headers=HX).get_data(as_text=True)
        assert f'value="{v["two"].id}" selected' in body

    def test_stale_pick_is_called_out(self, client, db_session, login_as):
        province, _ = _province("one", "two")
        gone = make_visual(province=province, name="gone", visibility="group", level="1",
                           menu_name="Gone")
        group, owner = _owned_group()
        set_landing(group, gone)
        login_as(owner)
        body = client.get(f"/v1/groups/{group.id}/landing", headers=HX).get_data(as_text=True)
        assert "no longer available" in body
        assert f'value="{gone.id}" selected' in body

    def test_stale_pick_is_not_named(self, client, db_session, login_as):
        """Whoever opens the modal may no longer be allowed to see that visual (a restricted
        source the group was detached from), so a stale pick is shown without its name."""
        province, _ = _province("one", "two")
        gone = make_visual(province=province, name="gone", visibility="group", level="1",
                           menu_name="Secret Visual Name")
        group, owner = _owned_group()
        set_landing(group, gone)
        login_as(owner)
        body = client.get(f"/v1/groups/{group.id}/landing", headers=HX).get_data(as_text=True)
        assert "Secret Visual Name" not in body

    def test_group_row_offers_the_action(self, client, db_session, login_as):
        group, owner = _owned_group()
        login_as(owner)
        body = client.get("/v1/group-management").get_data(as_text=True)
        assert f"/v1/groups/{group.id}/landing" in body

    def test_group_admin_is_refused(self, client, db_session, login_as):
        group, admin = _owned_group(role="Group Admin")
        login_as(admin)
        assert client.get(f"/v1/groups/{group.id}/landing").status_code == 302

    def test_owner_of_another_group_is_refused(self, client, db_session, login_as):
        group, _ = _owned_group()
        _, other_owner = _owned_group()
        login_as(other_owner)
        assert client.get(f"/v1/groups/{group.id}/landing").status_code == 302

    def test_anonymous_is_sent_to_login(self, client, db_session):
        group, _ = _owned_group()
        response = client.get(f"/v1/groups/{group.id}/landing")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


class TestSetOpeningViews:
    def test_owner_sets_a_pick(self, client, db_session, login_as):
        province, v = _province("one", "two")
        group, owner = _owned_group()
        login_as(owner)
        response = client.post(f"/v1/groups/{group.id}/landing", headers=HX,
                               data={f"landing__{province}": str(v["two"].id)})
        assert response.status_code == 200
        assert f'id="group-row-{group.id}"' in response.get_data(as_text=True)
        assert _pick(group, province).visual_id == v["two"].id

    def test_blank_goes_back_to_the_site_default(self, client, db_session, login_as):
        province, v = _province("one", "two")
        group, owner = _owned_group()
        set_landing(group, v["one"])
        login_as(owner)
        client.post(f"/v1/groups/{group.id}/landing", headers=HX,
                    data={f"landing__{province}": ""})
        assert _pick(group, province) is None

    def test_visual_the_members_cannot_see_is_refused(self, client, db_session, login_as):
        province, _ = _province("one", "two")
        hidden = make_visual(province=province, name="hidden", visibility="private", level="1")
        group, owner = _owned_group()
        login_as(owner)
        response = client.post(f"/v1/groups/{group.id}/landing", headers=HX,
                               data={f"landing__{province}": str(hidden.id)})
        assert response.status_code == 200
        assert "starting chart" in response.get_data(as_text=True)
        assert _pick(group, province) is None

    def test_garbage_value_is_refused(self, client, db_session, login_as):
        province, _ = _province("one", "two")
        group, owner = _owned_group()
        login_as(owner)
        response = client.post(f"/v1/groups/{group.id}/landing", headers=HX,
                               data={f"landing__{province}": "banana"})
        assert response.status_code == 200
        assert _pick(group, province) is None

    def test_non_ascii_digit_is_refused_not_a_server_error(self, client, db_session, login_as):
        """str.isdigit() accepts characters int() rejects; that must not escape as a 500."""
        province, _ = _province("one", "two")
        group, owner = _owned_group()
        login_as(owner)
        response = client.post(f"/v1/groups/{group.id}/landing", headers=HX,
                               data={f"landing__{province}": "\u00b2"})
        assert response.status_code == 200
        assert _pick(group, province) is None

    def test_group_admin_cannot_set(self, client, db_session, login_as):
        province, v = _province("one", "two")
        group, admin = _owned_group(role="Group Admin")
        login_as(admin)
        client.post(f"/v1/groups/{group.id}/landing",
                    data={f"landing__{province}": str(v["two"].id)})
        assert _pick(group, province) is None

    def test_owner_of_another_group_cannot_set(self, client, db_session, login_as):
        province, v = _province("one", "two")
        group, _ = _owned_group()
        _, other_owner = _owned_group()
        login_as(other_owner)
        client.post(f"/v1/groups/{group.id}/landing",
                    data={f"landing__{province}": str(v["two"].id)})
        assert _pick(group, province) is None

    def test_site_admin_can_set_for_any_open_group(self, client, db_session, login_as):
        province, v = _province("one", "two")
        group = make_group()
        login_as(make_user(site_admin=True))
        client.post(f"/v1/groups/{group.id}/landing", headers=HX,
                    data={f"landing__{province}": str(v["two"].id)})
        assert _pick(group, province).visual_id == v["two"].id

    def test_site_admin_outside_the_agreement_cannot_set_for_its_group(self, client, db_session,
                                                                       login_as):
        province, v = _province("one", "two")
        _, holder = make_restricted_source()
        login_as(make_user(site_admin=True))
        client.post(f"/v1/groups/{holder.id}/landing", headers=HX,
                    data={f"landing__{province}": str(v["two"].id)})
        assert _pick(holder, province) is None


class TestProvincePageNotice:
    """What the province API tells the page, and the notice's save call (a plain fetch)."""

    def _disagreement(self):
        first, second = make_group(), make_group()
        user = make_user()
        add_membership(user, first)
        add_membership(user, second)
        alpha = make_visual(province="yukon", name=unique("alpha"), visibility="public",
                            level="1")
        beta = make_visual(province="yukon", name=unique("beta"), visibility="public",
                           level="1")
        set_landing(first, alpha)
        set_landing(second, beta)
        return user, alpha, beta

    def test_api_carries_both_picks(self, client, db_session, login_as):
        user, alpha, beta = self._disagreement()
        login_as(user)
        payload = client.get("/api/v1/province/yukon/data").get_json()
        assert payload["default"] == alpha.name
        assert [o["visual_id"] for o in payload["landing"]["options"]] == [alpha.id, beta.id]
        assert payload["landing"]["active"] == alpha.name

    def test_picking_one_from_the_notice_saves_it(self, client, db_session, login_as):
        user, _, beta = self._disagreement()
        login_as(user)
        response = client.post("/v1/landing-choice/yukon", data={"visual_id": beta.id})
        assert response.status_code == 200
        assert response.get_json() == {"default": beta.name}
        row = UserLandingChoices.query.filter_by(user_id=user.id, province="yukon").one()
        assert row.visual_id == beta.id

    def test_api_stops_asking_once_they_have_chosen(self, client, db_session, login_as):
        user, _, beta = self._disagreement()
        login_as(user)
        client.post("/v1/landing-choice/yukon", data={"visual_id": beta.id})
        payload = client.get("/api/v1/province/yukon/data").get_json()
        assert payload["default"] == beta.name
        assert payload["landing"] is None

    def test_api_reports_nothing_to_ask_an_anonymous_visitor(self, client, db_session):
        make_visual(province="yukon", name=unique("pub"), visibility="public", level="1")
        assert client.get("/api/v1/province/yukon/data").get_json()["landing"] is None

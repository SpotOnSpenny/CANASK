"""The "My account" page, one tab per option: "Account info" (who you are, your groups and
roles) and "Starting charts" (the chart each page opens on for you, picked from every chart you
can see there)."""
from data_viz.database.models import UserLandingChoices

from tests.factories import (
    choose_landing,
    make_group,
    make_user,
    make_visual,
    set_landing,
    unique,
)

HX = {"HX-Request": "true"}


def _yukon(*labels, default=None):
    """Public top-level charts on the real "yukon" page (the account page lists real pages)."""
    return {label: make_visual(province="yukon", name=unique(label.lower()), visibility="public",
                               level="1", menu_name=f"{label} Chart",
                               is_default=(label == default)) for label in labels}


class TestAccountPage:
    def test_anonymous_is_sent_to_login(self, client, db_session):
        response = client.get("/v1/account")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_shows_who_you_are(self, client, db_session, login_as):
        user = login_as(make_user())
        body = client.get("/v1/account", headers=HX).get_data(as_text=True)
        assert user.username in body
        assert user.email in body

    def test_lists_your_groups_and_roles(self, client, db_session, login_as):
        group = make_group(name=unique("Harm Reduction Team"))
        login_as(make_user(group=group, role="Group Admin"))
        body = client.get("/v1/account", headers=HX).get_data(as_text=True)
        assert group.name in body
        assert "Group Admin" in body

    def test_does_not_list_groups_you_are_not_in(self, client, db_session, login_as):
        other = make_group(name=unique("Somebody Elses Team"))
        login_as(make_user())
        assert other.name not in client.get("/v1/account", headers=HX).get_data(as_text=True)

    def test_htmx_request_gets_the_bare_partial(self, client, db_session, login_as):
        login_as(make_user())
        body = client.get("/v1/account", headers=HX).get_data(as_text=True)
        assert "<html" not in body
        assert 'id="account-div"' in body

    def test_full_load_gets_the_page_shell(self, client, db_session, login_as):
        login_as(make_user())
        body = client.get("/v1/account").get_data(as_text=True)
        assert "<html" in body
        assert 'id="account-div"' in body
        assert "<title>CANASK | My Account</title>" in body

    def test_opens_on_the_account_info_tab(self, client, db_session, login_as):
        user = login_as(make_user())
        body = client.get("/v1/account", headers=HX).get_data(as_text=True)
        assert 'id="account-tab-info"' in body
        assert user.email in body
        assert 'id="starting-charts-section"' not in body

    def test_both_tabs_are_offered_and_the_open_one_is_marked(self, client, db_session,
                                                              login_as):
        login_as(make_user())
        body = client.get("/v1/account", headers=HX).get_data(as_text=True)
        assert 'hx-get="/v1/account"' in body
        assert 'hx-get="/v1/account/starting-charts"' in body
        assert body.count('aria-current="page"') == 1
        assert 'aria-current="page">Account info' in body

    def test_starting_charts_tab_shows_only_that_option(self, client, db_session, login_as):
        user = login_as(make_user())
        body = client.get("/v1/account/starting-charts", headers=HX).get_data(as_text=True)
        assert 'id="starting-charts-section"' in body
        assert 'aria-current="page">Starting charts' in body
        assert user.email not in body

    def test_starting_charts_tab_loads_as_a_full_page_too(self, client, db_session, login_as):
        login_as(make_user())
        body = client.get("/v1/account/starting-charts").get_data(as_text=True)
        assert "<html" in body
        assert 'id="starting-charts-section"' in body
        assert "<title>CANASK | My Account</title>" in body

    def test_unknown_tab_goes_to_account_info(self, client, db_session, login_as):
        login_as(make_user())
        response = client.get("/v1/account/nonsense")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/v1/account")

    def test_tabs_need_a_sign_in(self, client, db_session):
        response = client.get("/v1/account/starting-charts")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_rail_links_to_it_for_any_signed_in_user(self, client, db_session, login_as):
        login_as(make_user())
        assert 'hx-get="/v1/account"' in client.get("/").get_data(as_text=True)

    def test_rail_does_not_link_to_it_when_signed_out(self, client, db_session):
        assert 'hx-get="/v1/account"' not in client.get("/").get_data(as_text=True)


class TestStartingCharts:
    def test_every_chart_on_a_page_you_can_open_is_offered(self, client, db_session, login_as):
        v = _yukon("Alpha", "Beta", "Gamma")
        login_as(make_user())
        body = client.get("/v1/account/starting-charts", headers=HX).get_data(as_text=True)
        assert 'hx-post="/v1/landing-choice/yukon"' in body
        for visual in v.values():
            assert f'value="{visual.id}"' in body
        assert "Yukon" in body

    def test_needs_no_group_at_all(self, client, db_session, login_as):
        """It is a personal setting, not something that only exists when groups disagree."""
        _yukon("Alpha", "Beta")
        login_as(make_user())
        body = client.get("/v1/account/starting-charts", headers=HX).get_data(as_text=True)
        assert 'hx-post="/v1/landing-choice/yukon"' in body
        assert "more than one group" not in body

    def test_chart_you_cannot_see_is_not_offered(self, client, db_session, login_as):
        _yukon("Alpha", "Beta")
        hidden = make_visual(province="yukon", name=unique("hidden"), visibility="private",
                             level="1", menu_name="Hidden Chart")
        login_as(make_user())
        body = client.get("/v1/account/starting-charts", headers=HX).get_data(as_text=True)
        assert f'value="{hidden.id}"' not in body
        assert "Hidden Chart" not in body

    def test_page_you_cannot_open_is_not_listed(self, client, db_session, login_as):
        _yukon("Alpha", "Beta")
        make_visual(province="nunavut", name=unique("priv"), visibility="private", level="1")
        login_as(make_user())
        body = client.get("/v1/account/starting-charts", headers=HX).get_data(as_text=True)
        assert "/v1/landing-choice/nunavut" not in body

    def test_default_option_says_what_the_page_opens_on(self, client, db_session, login_as):
        _yukon("Alpha", "Beta", default="Beta")
        login_as(make_user())
        body = client.get("/v1/account/starting-charts", headers=HX).get_data(as_text=True)
        assert '<option value="" selected>Default (Beta Chart)</option>' in body

    def test_default_option_follows_your_groups_pick(self, client, db_session, login_as):
        v = _yukon("Alpha", "Beta", default="Beta")
        group = make_group()
        set_landing(group, v["Alpha"])
        login_as(make_user(group=group))
        body = client.get("/v1/account/starting-charts", headers=HX).get_data(as_text=True)
        assert "Default (Alpha Chart)" in body

    def test_your_choice_is_selected(self, client, db_session, login_as):
        v = _yukon("Alpha", "Beta", default="Beta")
        user = make_user()
        choose_landing(user, v["Alpha"])
        login_as(user)
        body = client.get("/v1/account/starting-charts", headers=HX).get_data(as_text=True)
        assert f'value="{v["Alpha"].id}" selected' in body
        assert '<option value="">Default (Beta Chart)</option>' in body

    def test_page_with_a_single_chart_offers_no_choice(self, client, db_session, login_as):
        _yukon("Only")
        login_as(make_user())
        body = client.get("/v1/account/starting-charts", headers=HX).get_data(as_text=True)
        assert "/v1/landing-choice/yukon" not in body
        assert "Only Chart" in body

    def test_no_pages_says_so(self, client, db_session, login_as):
        login_as(make_user())
        body = client.get("/v1/account/starting-charts", headers=HX).get_data(as_text=True)
        assert "Starting charts" in body
        assert "/v1/landing-choice/" not in body

    def test_choosing_saves_and_rerenders_the_section(self, client, db_session, login_as):
        v = _yukon("Alpha", "Beta", default="Beta")
        user = login_as(make_user())
        response = client.post("/v1/landing-choice/yukon", headers=HX,
                               data={"visual_id": v["Alpha"].id})
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert 'id="starting-charts-section"' in body
        assert f'value="{v["Alpha"].id}" selected' in body
        assert "Alpha Chart" in body      # the flash names what it now opens on
        row = UserLandingChoices.query.filter_by(user_id=user.id, province="yukon").one()
        assert row.visual_id == v["Alpha"].id

    def test_choosing_default_clears_your_choice(self, client, db_session, login_as):
        v = _yukon("Alpha", "Beta", default="Beta")
        user = make_user()
        choose_landing(user, v["Alpha"])
        login_as(user)
        body = client.post("/v1/landing-choice/yukon", headers=HX,
                           data={"visual_id": ""}).get_data(as_text=True)
        assert '<option value="" selected>Default (Beta Chart)</option>' in body
        assert UserLandingChoices.query.filter_by(user_id=user.id).count() == 0

    def test_refused_choice_rerenders_unchanged(self, client, db_session, login_as):
        _yukon("Alpha", "Beta", default="Beta")
        hidden = make_visual(province="yukon", name=unique("hidden"), visibility="private",
                             level="1")
        user = login_as(make_user())
        response = client.post("/v1/landing-choice/yukon", headers=HX,
                               data={"visual_id": hidden.id})
        assert response.status_code == 200
        assert '<option value="" selected>Default (Beta Chart)</option>' in response.get_data(as_text=True)
        assert UserLandingChoices.query.filter_by(user_id=user.id).count() == 0

    def test_request_without_the_field_changes_nothing(self, client, db_session, login_as):
        """Only an explicit empty value means "back to default"; a bare POST is a mistake."""
        v = _yukon("Alpha", "Beta")
        user = make_user()
        choose_landing(user, v["Alpha"])
        login_as(user)
        assert client.post("/v1/landing-choice/yukon").status_code == 400
        assert UserLandingChoices.query.filter_by(user_id=user.id).count() == 1

    def test_anonymous_cannot_choose(self, client, db_session):
        v = _yukon("Alpha", "Beta")
        response = client.post("/v1/landing-choice/yukon", data={"visual_id": v["Alpha"].id})
        assert response.status_code == 302
        assert UserLandingChoices.query.count() == 0

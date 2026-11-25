"""Saved starting views for the pages whose view lives in the address (Confluence, DAS Explorer):
saved from the page's own button or by pasting a link on My account, applied when the page is
opened with nothing in the address."""
from data_viz.database.models import UserActivity, UserStartingViews

from tests.confluence_data import das_gate, seed_confluence
from tests.factories import make_user

HX = {"HX-Request": "true"}
CONFLUENCE_VIEW = "province=saskatchewan&visual=deaths_by_opioid_type&grain=year"


def _saved(user, page):
    row = UserStartingViews.query.filter_by(user_id=user.id, page=page).first()
    return row.query_string if row else None


class TestSaveFromThePage:
    """The page's own button posts its current query string with a plain fetch."""

    def test_saves_the_current_view(self, client, db_session, login_as):
        seed_confluence()
        user = login_as(make_user())
        response = client.post("/v1/starting-view/confluence", data={"query": "?" + CONFLUENCE_VIEW})
        assert response.status_code == 200
        assert response.get_json() == {"saved": True}
        assert _saved(user, "confluence") == CONFLUENCE_VIEW

    def test_saving_again_replaces_it(self, client, db_session, login_as):
        seed_confluence()
        user = login_as(make_user())
        client.post("/v1/starting-view/confluence", data={"query": CONFLUENCE_VIEW})
        client.post("/v1/starting-view/confluence", data={"query": "province=saskatchewan"})
        assert _saved(user, "confluence") == "province=saskatchewan"
        assert UserStartingViews.query.filter_by(user_id=user.id).count() == 1

    def test_unrecognised_settings_are_not_stored(self, client, db_session, login_as):
        seed_confluence()
        user = login_as(make_user())
        client.post("/v1/starting-view/confluence",
                    data={"query": CONFLUENCE_VIEW + "&next=https://evil.example"})
        assert _saved(user, "confluence") == CONFLUENCE_VIEW

    def test_nothing_to_save_is_refused(self, client, db_session, login_as):
        seed_confluence()
        user = login_as(make_user())
        response = client.post("/v1/starting-view/confluence", data={"query": ""})
        assert response.status_code == 400
        assert "error" in response.get_json()
        assert _saved(user, "confluence") is None

    def test_das_explorer_view_is_saved(self, client, db_session, login_as):
        das_gate()
        user = login_as(make_user())
        client.post("/v1/starting-view/das-explorer",
                    data={"query": "ds=id_all&f_province=BC&chart=bar&rows=province"})
        assert _saved(user, "das-explorer") == "ds=id_all&f_province=BC&chart=bar&rows=province"

    def test_save_is_logged(self, client, db_session, login_as):
        seed_confluence()
        user = login_as(make_user())
        client.post("/v1/starting-view/confluence", data={"query": CONFLUENCE_VIEW})
        assert UserActivity.query.filter_by(user_id=user.id,
                                            activity_type="starting_view_updated").count() == 1

    def test_page_you_cannot_open_is_refused(self, client, db_session, login_as):
        das_gate(visibility="private")
        user = login_as(make_user())
        response = client.post("/v1/starting-view/das-explorer", data={"query": "ds=id_all"})
        assert response.status_code == 403
        assert _saved(user, "das-explorer") is None

    def test_unknown_page_is_refused(self, client, db_session, login_as):
        login_as(make_user())
        assert client.post("/v1/starting-view/nowhere", data={"query": "a=b"}).status_code == 404

    def test_anonymous_is_sent_to_login(self, client, db_session):
        das_gate()
        response = client.post("/v1/starting-view/das-explorer", data={"query": "ds=id_all"})
        assert response.status_code == 302 and "/login" in response.headers["Location"]
        assert UserStartingViews.query.count() == 0


class TestPasteOnTheAccountPage:
    def test_tab_lists_the_pages_you_can_open(self, client, db_session, login_as):
        seed_confluence()
        login_as(make_user())
        body = client.get("/v1/account/starting-charts", headers=HX).get_data(as_text=True)
        assert 'hx-post="/v1/starting-view/confluence"' in body
        assert 'hx-post="/v1/starting-view/das-explorer"' in body
        assert "Confluence" in body and "DAS Explorer" in body

    def test_tab_leaves_out_pages_you_cannot_open(self, client, db_session, login_as):
        das_gate(visibility="private")
        login_as(make_user())
        body = client.get("/v1/account/starting-charts", headers=HX).get_data(as_text=True)
        assert "/v1/starting-view/" not in body

    def test_das_explorer_alone_when_there_is_no_province_to_overlay(self, client, db_session,
                                                                     login_as):
        das_gate()
        login_as(make_user())
        body = client.get("/v1/account/starting-charts", headers=HX).get_data(as_text=True)
        assert 'hx-post="/v1/starting-view/das-explorer"' in body
        assert "/v1/starting-view/confluence" not in body

    def test_pasting_a_link_saves_it_and_rerenders_the_section(self, client, db_session,
                                                              login_as):
        seed_confluence()
        user = login_as(make_user())
        link = "https://canask.example/v1/national/confluence?" + CONFLUENCE_VIEW
        response = client.post("/v1/starting-view/confluence", headers=HX, data={"link": link})
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert 'id="starting-charts-section"' in body
        assert _saved(user, "confluence") == CONFLUENCE_VIEW
        assert f'href="/v1/national/confluence?{CONFLUENCE_VIEW.replace("&", "&amp;")}"' in body

    def test_link_to_the_wrong_page_is_refused(self, client, db_session, login_as):
        seed_confluence()
        user = login_as(make_user())
        body = client.post("/v1/starting-view/confluence", headers=HX,
                           data={"link": "https://canask.example/v1/national/das-explorer?ds=quant"}
                           ).get_data(as_text=True)
        assert _saved(user, "confluence") is None
        assert "Confluence" in body and 'id="starting-charts-section"' in body

    def test_clearing_removes_it(self, client, db_session, login_as):
        seed_confluence()
        user = login_as(make_user())
        client.post("/v1/starting-view/confluence", data={"query": CONFLUENCE_VIEW})
        body = client.post("/v1/starting-view/confluence", headers=HX,
                           data={"clear": "1"}).get_data(as_text=True)
        assert _saved(user, "confluence") is None
        assert "/v1/national/confluence?" not in body

    def test_saved_view_offers_clear_and_unsaved_does_not(self, client, db_session, login_as):
        seed_confluence()
        login_as(make_user())
        before = client.get("/v1/account/starting-charts", headers=HX).get_data(as_text=True)
        assert 'name="clear"' not in before
        client.post("/v1/starting-view/confluence", data={"query": CONFLUENCE_VIEW})
        after = client.get("/v1/account/starting-charts", headers=HX).get_data(as_text=True)
        assert 'name="clear"' in after


class TestPagesPickUpTheSavedView:
    def test_confluence_page_carries_your_saved_view(self, client, db_session, login_as):
        seed_confluence()
        login_as(make_user())
        client.post("/v1/starting-view/confluence", data={"query": CONFLUENCE_VIEW})
        body = client.get("/v1/national/confluence", headers=HX).get_data(as_text=True)
        assert f'data-starting-view="{CONFLUENCE_VIEW.replace("&", "&amp;")}"' in body

    def test_das_explorer_page_carries_your_saved_view(self, client, db_session, login_as):
        das_gate()
        login_as(make_user())
        client.post("/v1/starting-view/das-explorer", data={"query": "ds=id_all&rows=province"})
        body = client.get("/v1/national/das-explorer", headers=HX).get_data(as_text=True)
        assert 'data-starting-view="ds=id_all&amp;rows=province"' in body

    def test_someone_elses_saved_view_is_not_yours(self, client, db_session, login_as):
        seed_confluence()
        login_as(make_user())
        client.post("/v1/starting-view/confluence", data={"query": CONFLUENCE_VIEW})
        login_as(make_user())
        body = client.get("/v1/national/confluence", headers=HX).get_data(as_text=True)
        assert 'data-starting-view=""' in body

    def test_signed_in_visitor_gets_the_save_button(self, client, db_session, login_as):
        seed_confluence()
        login_as(make_user())
        assert 'id="save-starting-view"' in client.get("/v1/national/confluence", headers=HX).get_data(as_text=True)
        assert 'id="save-starting-view"' in client.get("/v1/national/das-explorer", headers=HX).get_data(as_text=True)

    def test_anonymous_visitor_gets_no_save_button(self, client, db_session):
        seed_confluence()      # public: the pages open without signing in
        body = client.get("/v1/national/das-explorer", headers=HX).get_data(as_text=True)
        assert 'id="das-table"' in body
        assert 'id="save-starting-view"' not in body
        assert 'data-starting-view=""' in body

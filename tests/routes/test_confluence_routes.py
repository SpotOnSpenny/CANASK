"""Confluence page + API: the 404/403/400/200 ladder rides das_access_allowed AND the
chosen visual's own visibility."""
import pytest

from tests.confluence_data import das_gate, seed_confluence
from tests.factories import grant_visual, make_group, make_user

API = "/api/v1/confluence/data"
SK_FLAT = {"province": "saskatchewan", "visual": "deaths_by_opioid_type"}


def url(**params):
    from urllib.parse import urlencode
    return f"{API}?{urlencode(params)}"


class TestAccessLadder:
    def test_no_gate_visual_403(self, client, db_session):
        assert client.get(url(**SK_FLAT)).status_code == 403

    def test_unknown_province_404(self, client, db_session):
        seed_confluence()
        assert client.get(url(province="narnia", visual="x")).status_code == 404

    def test_unknown_visual_404(self, client, db_session):
        seed_confluence()
        assert client.get(url(province="saskatchewan", visual="nope")).status_code == 404

    def test_unsupported_visual_403(self, client, db_session):
        seed_confluence()
        assert client.get(url(province="saskatchewan", visual="structural_map")).status_code == 403

    def test_private_visual_403_anonymous_200_admin(self, client, db_session, login_as):
        seed_confluence(visibility="private")
        assert client.get(url(**SK_FLAT)).status_code == 403
        login_as(make_user(site_admin=True))
        assert client.get(url(**SK_FLAT)).status_code == 200

    def test_group_grant_needs_both_visuals(self, client, db_session, login_as):
        seeded = seed_confluence(visibility="group")
        group = make_group()
        grant_visual(group, seeded["flat"])
        login_as(make_user(group=group, role="Data Viewer"))
        assert client.get(url(**SK_FLAT)).status_code == 403   # DAS gate not granted
        grant_visual(group, seeded["gate"])
        assert client.get(url(**SK_FLAT)).status_code == 200

    def test_page_denied_redirects(self, client, db_session):
        response = client.get("/v1/national/confluence", headers={"HX-Request": "true"})
        assert response.status_code == 204
        assert response.headers["HX-Redirect"] == "/"
        assert client.get("/v1/national/confluence").status_code == 302


class TestParams:
    @pytest.fixture(autouse=True)
    def _seed(self, db_session):
        seed_confluence()

    @pytest.mark.parametrize("bad", [
        {"level": "nope"}, {"basis": "nope"}, {"groups": "unicorn"},
        {"groups": "fentanyl,nitazenes,other_opioids,cocaine,methamphetamine,mdma,benzos,xylazine,medetomidine"},
        {"expr": "x" * 301},
    ])
    def test_bad_params_400(self, client, bad):
        assert client.get(url(**SK_FLAT, **bad)).status_code == 400

    def test_malformed_expression_400_with_message(self, client):
        response = client.get(url(**SK_FLAT, expr="(fentanyl"))
        assert response.status_code == 400
        assert response.get_json()["error"].startswith("Invalid filter")

    def test_duplicate_groups_collapse(self, client):
        payload = client.get(url(**SK_FLAT, groups="fentanyl,fentanyl,cocaine,fentanyl")).get_json()
        assert [k["key"] for k in payload["das"]["keys"]] == ["fentanyl", "cocaine"]

    def test_defaults(self, client):
        payload = client.get(url(**SK_FLAT)).get_json()
        assert payload["das"]["basis"] == "received"
        assert payload["das"]["level"] == "group"
        assert payload["das"]["keys"] == [{"key": "all", "label": "All samples"}]


class TestPayload:
    @pytest.fixture(autouse=True)
    def _seed(self, db_session):
        seed_confluence()

    def test_series_shape(self, client):
        payload = client.get(url(**SK_FLAT, groups="fentanyl", level="group")).get_json()
        assert set(payload) == {"province", "visual", "visual_label", "visual_metric", "grain",
                                "periods", "das"}
        assert payload["das"]["mode"] == "series"
        assert payload["das"]["series"]["fentanyl"] == {"2025": 2}

    def test_cities_shape(self, client):
        payload = client.get(url(province="saskatchewan", visual="drug_death_heatmap")).get_json()
        assert payload["das"]["mode"] == "cities"
        assert "Saskatoon, SK" in payload["das"]["cities"]


class TestPage:
    def test_full_and_partial_render(self, client, db_session, login_as):
        seed_confluence()
        full = client.get("/v1/national/confluence")
        assert full.status_code == 200
        assert b'id="confluence-chart"' in full.data
        assert b"initConfluence(" in full.data
        assert b"<title" in full.data
        partial = client.get("/v1/national/confluence", headers={"HX-Request": "true"})
        assert partial.status_code == 200
        assert b'id="confluence-chart"' in partial.data
        assert b"<html" not in partial.data

    def test_config_lists_province_and_visuals(self, client, db_session):
        seed_confluence()
        page = client.get("/v1/national/confluence").data.decode()
        assert '"saskatchewan"' in page and "deaths_by_opioid_type" in page

    def test_nav_link_needs_das_and_a_province(self, client, db_session):
        das_gate()   # DAS only, no province visuals
        assert b"/v1/national/confluence" not in client.get("/").data
        seed_confluence()
        assert b"/v1/national/confluence" in client.get("/").data

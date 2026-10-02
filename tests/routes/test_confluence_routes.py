"""Confluence page + API: the 404/403/400/200 ladder rides das_access_allowed AND the
chosen visual's own visibility."""
import pytest

from tests.confluence_data import (GRAIN_FRAMES, das_gate, seed_confluence, seed_grain_visuals,
                                   seed_month_visuals)
from tests.factories import (grant_visual, make_data_source, make_datapoint, make_group,
                             make_user, make_visual, make_visual_query)

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

    def test_public_gate_does_not_open_a_private_visual(self, client, db_session, login_as):
        # Isolates the visual half of the conjunction: DAS access alone must not expose a private
        # V1 visual's facts through payload["visual"].
        seed_confluence(visibility="private", gate_visibility="public")
        assert client.get(url(**SK_FLAT)).status_code == 403
        login_as(make_user(site_admin=True))
        assert client.get(url(**SK_FLAT)).status_code == 200

    def test_granted_gate_does_not_open_an_ungranted_visual(self, client, db_session, login_as):
        seeded = seed_confluence(visibility="group")
        group = make_group()
        grant_visual(group, seeded["gate"])
        login_as(make_user(group=group, role="Data Viewer"))
        assert client.get(url(**SK_FLAT)).status_code == 403   # flat visual not granted

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
        {"expr": "x" * 301}, {"grain": "week"}, {"grain": "Year"},
    ])
    def test_bad_params_400(self, client, bad):
        response = client.get(url(**SK_FLAT, **bad))
        assert response.status_code == 400
        assert response.get_json()["error"] != "bad confluence params"   # says what's wrong

    def test_too_many_keys_message(self, client):
        groups = "fentanyl,nitazenes,other_opioids,cocaine,methamphetamine,mdma,benzos,xylazine,medetomidine"
        assert client.get(url(**SK_FLAT, groups=groups)).get_json()["error"] == "Pick at most 8 substances."

    def test_exactly_max_keys_ok(self, client):
        groups = "fentanyl,nitazenes,other_opioids,cocaine,methamphetamine,mdma,benzos,xylazine"
        assert client.get(url(**SK_FLAT, groups=groups)).status_code == 200

    def test_group_keys_collapse_to_family_level(self, client):
        payload = client.get(url(**SK_FLAT, level="family", groups="fentanyl,nitazenes")).get_json()
        assert [k["key"] for k in payload["das"]["keys"]] == ["opioids"]
        assert set(payload["das"]["series"]) == {"opioids"}

    def test_family_key_expands_at_group_level(self, client):
        payload = client.get(url(**SK_FLAT, level="group", groups="opioids")).get_json()
        keys = [k["key"] for k in payload["das"]["keys"]]
        assert "opioids" not in keys and {"fentanyl", "nitazenes", "other_opioids"} <= set(keys)

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
                                "grains", "periods", "das"}
        assert payload["das"]["mode"] == "series"
        assert payload["das"]["series"]["fentanyl"] == {"2025": 2, "2026": 0}

    def test_cities_shape(self, client):
        payload = client.get(url(province="saskatchewan", visual="drug_death_heatmap")).get_json()
        assert payload["das"]["mode"] == "cities"
        assert "Saskatoon, SK" in payload["das"]["cities"]


class TestGrain:
    @pytest.fixture(autouse=True)
    def _seed(self, db_session):
        seed_confluence()
        seed_grain_visuals()

    def test_bad_grain_400_says_so(self, client):
        response = client.get(url(**SK_FLAT, grain="week"))
        assert response.status_code == 400
        assert "week" in response.get_json()["error"]

    def test_grain_a_year_only_visual_lacks_400(self, client):
        response = client.get(url(**SK_FLAT, grain="month"))
        assert response.status_code == 400
        assert "month" in response.get_json()["error"]

    def test_year_only_visual_defaults_to_year(self, client):
        payload = client.get(url(**SK_FLAT)).get_json()
        assert payload["grain"] == "year" and payload["grains"] == ["year"]

    @pytest.mark.parametrize("visual", ["grain_deaths", "grain_heatmap"])
    @pytest.mark.parametrize("grain", ["year", "quarter", "month"])
    def test_each_grain_ships_only_its_own_facts(self, client, visual, grain):
        response = client.get(url(province="saskatchewan", visual=visual, grain=grain))
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["grain"] == grain
        assert payload["grains"] == ["year", "quarter", "month"]
        facts = payload["visual"]["facts"]
        assert {f["g"] for f in facts} == {grain}
        assert sorted({f["t"] for f in facts}) == GRAIN_FRAMES[grain]
        assert all(p["key"] in GRAIN_FRAMES[grain] for p in payload["periods"])

    def test_default_grain_is_year(self, client):
        payload = client.get(url(province="saskatchewan", visual="grain_deaths")).get_json()
        assert payload["grain"] == "year"
        assert {f["t"] for f in payload["visual"]["facts"]} == {"2025", "2026"}

    def test_undeclared_month_only_visual_defaults_to_month(self, client):
        seed_month_visuals(months=["2025-04", "2025-05", "2025-06"])
        response = client.get(url(province="saskatchewan", visual="monthly_deaths"))
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["grain"] == "month" and payload["grains"] == ["month"]
        assert [p["key"] for p in payload["periods"]] == ["2025-04", "2025-05", "2025-06"]

    def test_page_config_lists_each_visuals_grains(self, client):
        page = client.get("/v1/national/confluence").data.decode()
        assert '"grains": ["year", "quarter", "month"]' in page
        assert '"grains": ["year"]' in page


class TestUnalignableVisual:
    @pytest.fixture(autouse=True)
    def _seed(self, db_session):
        seed_confluence()

    def _flat(self, name, frames):
        source = make_data_source(name=f"src-{name}", link="https://x.example.org", about="x")
        visual = make_visual(province="saskatchewan", name=name, visibility="public",
                             data_source=source, vis_type="flat_series", data_shape="flat_series",
                             chart_type="line", metric="deaths", geo_type="province",
                             key_kind="suffix_y", level="1", data_types="counts")
        if frames:
            make_visual_query(visual, "geo", "Saskatchewan")
        for frame in frames:
            make_datapoint(source, geo="Saskatchewan", geo_type="province", time_frame=frame,
                           data_metric="deaths", data_value=1)

    def test_quarterly_visual_400(self, client):
        self._flat("quarterly", ["2025 Q1", "2025 Q2"])
        response = client.get(url(province="saskatchewan", visual="quarterly"))
        assert response.status_code == 400
        assert "can't be aligned" in response.get_json()["error"]

    def test_mixed_grain_visual_400(self, client):
        # Both facts tagged year (the factory default), but one key is a month: the grain filter
        # keeps both and the key formats disagree.
        self._flat("mixed", ["2025", "2025-06"])
        assert client.get(url(province="saskatchewan", visual="mixed")).status_code == 400

    def test_year_tagged_month_keys_400(self, client):
        # Every fact tagged year (the factory default) but keyed YYYY-MM: the grain filter keeps
        # them all and the keys don't match their tag, so they must not be binned as years.
        self._flat("mistagged", ["2025-05", "2025-06"])
        response = client.get(url(province="saskatchewan", visual="mistagged"))
        assert response.status_code == 400
        assert "year time frames can't be aligned" in response.get_json()["error"]

    def test_visual_without_data_for_the_province_is_not_pairable(self, client):
        # No geo predicate: a province-level visual this province has no facts for.
        # displayable_visuals drops it, so it's never offered and the API refuses it (403) before
        # detect_grain's "no data" guard is reached.
        self._flat("empty", [])
        assert client.get(url(province="saskatchewan", visual="empty")).status_code == 403
        assert '"empty"' not in client.get("/v1/national/confluence").data.decode()


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

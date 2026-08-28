"""The province data API: menu + facts payload, filtered per viewer."""
from data_viz.database import db
from data_viz.database.models import VisualQuery

from tests.factories import (
    grant_visual,
    make_data_source,
    make_datapoint,
    make_group,
    make_user,
    make_visual,
)


def seed_ontario_visual(name, visibility="public"):
    source = make_data_source()
    visual = make_visual(province="ontario", name=name, visibility=visibility,
                         metric="deaths", geo_type="province", data_source=source,
                         chart_type="bar", menu_parent="Deaths", menu_name=name,
                         level="1")
    make_datapoint(source, geo="ontario", data_metric="deaths", data_value=5.0)
    db.session.add(VisualQuery(filter_type="geo", filter_value="ontario",
                               for_visual_id=visual.id))
    db.session.flush()
    return visual


class TestProvinceDataApi:
    def test_unknown_province_404(self, client, db_session):
        assert client.get("/api/v1/province/atlantis/data").status_code == 404

    def test_known_province_returns_contract_shape(self, client, db_session):
        seed_ontario_visual("api_pub")
        payload = client.get("/api/v1/province/ontario/data").get_json()
        assert set(payload) == {"data", "config", "default", "categories"}
        assert "api_pub" in payload["data"]
        assert "api_pub" in payload["config"]
        block = payload["data"]["api_pub"]
        assert block["chart_type"] == "bar"
        assert block["facts"] == [
            {"dt": "counts", "geo": "ontario", "t": "2024", "d": None, "d2": None, "v": 5}]

    def test_private_visual_hidden_from_anonymous(self, client, db_session):
        seed_ontario_visual("api_priv", visibility="private")
        payload = client.get("/api/v1/province/ontario/data").get_json()
        assert "api_priv" not in payload["data"]
        assert "api_priv" not in payload["config"]

    def test_group_visual_appears_for_member(self, client, db_session, login_as):
        visual = seed_ontario_visual("api_grp", visibility="group")
        group = make_group()
        grant_visual(group, visual)
        login_as(make_user(group=group, role="Data Viewer"))
        payload = client.get("/api/v1/province/ontario/data").get_json()
        assert "api_grp" in payload["data"]

    def test_national_scope_served_under_province_api(self, client, db_session):
        # "canada" is a data scope (drug-checking dashboard), not an active province.
        response = client.get("/api/v1/province/canada/data")
        assert response.status_code == 200

    def test_empty_province_returns_empty_contract(self, client, db_session):
        payload = client.get("/api/v1/province/nunavut/data").get_json()
        assert payload["data"] == {}
        assert payload["default"] is None

    def test_canada_expected_actual_visual_served(self, client, db_session):
        # The drug-checking expected-vs-actual visual: site-composite geo, month grain, outcome
        # dimension2 -- the full fact tuple must round-trip through the generic serve path.
        source = make_data_source()
        make_visual(province="canada", name="expected_vs_actual_samples",
                    metric="expected_vs_actual_samples", geo_type="site",
                    data_source=source, chart_type="stacked_hbar",
                    vis_type="expected_actual_bar", data_shape="expected_actual_bar",
                    dimension_type="expected_drug",
                    dimension2_type="sample_outcome", menu_parent="Drug Supply",
                    menu_name="Samples vs. Expectations", level="1")
        make_datapoint(source, geo="Alberta||QTHC", geo_type="site",
                       time_frame="2026-02", time_frame_type="month",
                       data_metric="expected_vs_actual_samples", data_value=7.0,
                       dimension_type="expected_drug", dimension_value="Fentanyl",
                       dimension2_type="sample_outcome", dimension2_value="expected_plus")
        payload = client.get("/api/v1/province/canada/data").get_json()
        assert "expected_vs_actual_samples" in payload["data"]
        block = payload["data"]["expected_vs_actual_samples"]
        assert block["chart_type"] == "stacked_hbar"
        assert block["shape"] == "expected_actual_bar"
        assert {"dt": "counts", "geo": "Alberta||QTHC", "t": "2026-02", "d": "Fentanyl",
                "d2": "expected_plus", "v": 7} in block["facts"]

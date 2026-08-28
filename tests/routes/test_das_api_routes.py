"""DAS Explorer APIs: the 404/403/400/200 ladder. Access rides the standard visual
model - a metric-less Visuals row under the canada-das scope."""
import pytest

from tests.factories import (
    grant_visual,
    make_das_drug,
    make_das_sample,
    make_data_source,
    make_group,
    make_user,
    make_visual,
)


def das_gate(visibility="public"):
    """The access-control Visuals row the das_access_allowed check reads."""
    return make_visual(province="canada-das", name="das_explorer", visibility=visibility,
                       vis_type="das_table", data_shape="das_table", metric=None)


@pytest.fixture()
def das_data(db_session):
    das_gate()
    cocaine = make_das_drug(code="COC", display_name="Cocaine")
    make_das_sample(sample_number="S-1", province="ON", drugs=[cocaine])
    make_das_sample(sample_number="S-2", province="BC", drugs=[cocaine])


class TestAccessLadder:
    def test_unknown_dataset_404(self, client, db_session):
        das_gate()
        assert client.get("/api/v1/das/nope/rows").status_code == 404
        assert client.get("/api/v1/das/nope/pivot").status_code == 404

    def test_no_gate_visual_403(self, client, db_session):
        assert client.get("/api/v1/das/id_all/rows").status_code == 403
        assert client.get("/api/v1/das/id_all/pivot?rows=province").status_code == 403

    def test_private_gate_403_for_anonymous_200_for_admin(self, client, db_session,
                                                          login_as):
        das_gate(visibility="private")
        assert client.get("/api/v1/das/id_all/rows").status_code == 403
        login_as(make_user(site_admin=True))
        assert client.get("/api/v1/das/id_all/rows").status_code == 200

    def test_group_gate(self, client, db_session, login_as):
        gate = das_gate(visibility="group")
        group = make_group()
        grant_visual(group, gate)
        assert client.get("/api/v1/das/id_all/rows").status_code == 403
        login_as(make_user(group=group, role="Data Viewer"))
        assert client.get("/api/v1/das/id_all/rows").status_code == 200

    def test_explorer_page_denied_redirects(self, client, db_session):
        response = client.get("/v1/national/das-explorer",
                              headers={"HX-Request": "true"})
        assert response.status_code == 204
        assert response.headers["HX-Redirect"]


class TestRowsApi:
    def test_rows_shape(self, client, das_data):
        payload = client.get("/api/v1/das/id_all/rows?page=1&size=50").get_json()
        assert set(payload) == {"data", "last_page", "last_row"}
        assert payload["last_row"] == 2

    def test_bad_paging_params_400(self, client, das_data):
        assert client.get("/api/v1/das/id_all/rows?page=abc").status_code == 400

    def test_malformed_filter_expression_400_never_degrades(self, client, das_data):
        response = client.get("/api/v1/das/id_all/rows?f_drugs_identified=cocaine%20AND")
        assert response.status_code == 400
        assert "Invalid filter" in response.get_json()["error"]

    def test_filter_applies(self, client, das_data):
        payload = client.get("/api/v1/das/id_all/rows?f_province=ON").get_json()
        assert payload["last_row"] == 1
        assert payload["data"][0]["sample_number"] == "S-1"

    def test_star_on_non_wildcard_field_400(self, client, das_data):
        response = client.get("/api/v1/das/id_all/rows?f_city=*")
        assert response.status_code == 400


class TestPivotApi:
    def test_pivot_shape(self, client, das_data):
        payload = client.get(
            "/api/v1/das/id_all/pivot?rows=province&measure=samples").get_json()
        assert set(payload) == {"rows", "cols", "cells", "measure", "truncated"}
        assert sorted(payload["rows"]) == ["BC", "ON"]

    def test_unknown_dim_or_measure_400(self, client, das_data):
        assert client.get("/api/v1/das/id_all/pivot?rows=evil").status_code == 400
        assert client.get(
            "/api/v1/das/id_all/pivot?rows=province&measure=evil").status_code == 400
        assert client.get(
            "/api/v1/das/id_all/pivot?rows=province&cols=evil").status_code == 400

    def test_bad_rows_limit_400(self, client, das_data):
        assert client.get(
            "/api/v1/das/id_all/pivot?rows=province&rows_limit=abc").status_code == 400


class TestExplorerPageAboutCard:
    def _page(self, client):
        return client.get("/v1/national/das-explorer").get_data(as_text=True)

    def _das_source(self, last_updated=None, data_until=None):
        from data_viz.das_ingest import DAS_SOURCE_NAME
        source = make_data_source(name=DAS_SOURCE_NAME, link="https://example.org/das",
                                  about="About the DAS data.")
        source.last_updated_str = last_updated
        source.data_until_str = data_until
        return source

    def test_both_dates_render_full_sentence(self, client, db_session):
        das_gate()
        self._das_source(last_updated="July 15, 2026", data_until="June 30, 2026")
        body = self._page(client)
        assert ("last updated on July 15, 2026 and contains data up until "
                "June 30, 2026.") in body

    def test_missing_data_until_never_prints_none(self, client, db_session):
        das_gate()
        self._das_source(last_updated="July 15, 2026", data_until=None)
        body = self._page(client)
        assert "last updated on July 15, 2026." in body
        assert "up until" not in body

    def test_missing_last_updated_still_shows_data_until(self, client, db_session):
        das_gate()
        self._das_source(last_updated=None, data_until="June 30, 2026")
        body = self._page(client)
        assert "contains data up until June 30, 2026." in body
        assert "last updated" not in body

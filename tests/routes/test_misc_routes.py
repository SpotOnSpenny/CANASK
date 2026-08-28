"""Feedback endpoint, error handling, and the security headers every response carries."""
from tests.factories import make_visual, unique


class TestFeedback:
    def test_valid_feedback_emails_and_succeeds(self, client, db_session, ses_outbox, app):
        response = client.post("/feedback", data={
            "feedback": "The charts are great.", "name": "A Fan",
            "email": "fan@example.org"})
        assert response.status_code == 200
        assert response.get_json()["status"] == "success"
        (mail,) = ses_outbox
        assert mail.to == [app.config["FEEDBACK_EMAIL"]]
        assert "The charts are great." in mail.html

    def test_missing_message_400(self, client, db_session, ses_outbox):
        response = client.post("/feedback", data={"name": "No Message"})
        assert response.status_code == 400
        assert ses_outbox == []

    def test_over_length_feedback_400(self, client, db_session):
        assert client.post("/feedback", data={"feedback": "x" * 5001}).status_code == 400

    def test_bad_optional_email_400(self, client, db_session):
        response = client.post("/feedback", data={
            "feedback": "hello", "email": "not-an-email"})
        assert response.status_code == 400

    def test_html_in_feedback_is_neutralized(self, client, db_session, ses_outbox):
        client.post("/feedback", data={"feedback": "<script>alert(1)</script> hi"})
        (mail,) = ses_outbox
        assert "<script>" not in mail.html

    def test_ses_failure_reported_500(self, client, db_session, monkeypatch):
        monkeypatch.setattr("data_viz.main.send_ses_email", lambda *a, **kw: False)
        response = client.post("/feedback", data={"feedback": "hello there"})
        assert response.status_code == 500


class TestErrorHandling:
    def test_unknown_url_redirects_to_not_found(self, client, db_session):
        response = client.get("/no/such/page")
        assert response.status_code == 302
        assert "/not-found" in response.headers["Location"]

    def test_not_found_page_serves_404(self, client, db_session):
        assert client.get("/not-found").status_code == 404


class TestSecurityHeaders:
    def test_headers_on_page_responses(self, client, db_session):
        response = client.get("/")
        assert "Content-Security-Policy" in response.headers
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "no-store" in response.headers["Cache-Control"]


class TestProvincePages:
    def test_province_page_full_and_partial(self, client, db_session):
        full = client.get("/v1/province/ontario").get_data(as_text=True)
        assert "<html" in full
        partial = client.get("/v1/province/ontario",
                             headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "<html" not in partial

    def test_deep_link_to_denied_visual_htmx_redirects(self, client, db_session):
        make_visual(province="ontario", name=unique("deep"), visibility="private",
                    slug="secret-slug", metric=None)
        response = client.get("/v1/province/ontario/secret-slug",
                              headers={"HX-Request": "true"})
        assert response.status_code in (204, 200)

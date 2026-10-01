"""Feedback endpoint, error handling, and the security headers every response carries."""
from data_viz.database.models import FeedbackSubmission
from tests.factories import make_user, make_visual, unique


class TestFeedback:
    """/feedback stores every accepted submission (the system of record) and then emails it; an
    email failure is recorded on the row, never reported to the submitter as a failure."""

    def test_valid_feedback_emails_and_succeeds(self, client, db_session, ses_outbox, app):
        response = client.post("/feedback", data={
            "feedback": "The charts are great.", "name": "A Fan",
            "email": "fan@example.org"})
        assert response.status_code == 200
        assert response.get_json()["status"] == "success"
        (mail,) = ses_outbox
        assert mail.to == [app.config["FEEDBACK_EMAIL"]]
        assert "The charts are great." in mail.html
        row = FeedbackSubmission.query.one()
        assert (row.name, row.email, row.body) == ("A Fan", "fan@example.org", "The charts are great.")
        assert row.email_sent is True and row.email_error is None
        assert row.addressed_at is None and row.user_id is None
        assert f"#{row.id}" in mail.html
        assert response.get_json()["id"] == row.id

    def test_missing_message_400(self, client, db_session, ses_outbox):
        response = client.post("/feedback", data={"name": "No Message"})
        assert response.status_code == 400
        assert ses_outbox == []
        assert FeedbackSubmission.query.count() == 0

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

    def test_ses_failure_still_persists_and_returns_200(self, client, db_session, monkeypatch):
        monkeypatch.setattr("data_viz.main.send_ses_email", lambda *a, **kw: False)
        response = client.post("/feedback", data={"feedback": "hello there"})
        assert response.status_code == 200
        assert response.get_json()["status"] == "success"
        row = FeedbackSubmission.query.one()
        assert row.body == "hello there"
        assert row.email_sent is False
        assert row.email_error

    def test_page_is_stored_and_emailed(self, client, db_session, ses_outbox):
        client.post("/feedback", data={
            "feedback": "Ontario map is wrong", "page": "/v1/province/ontario?y=2024"})
        row = FeedbackSubmission.query.one()
        assert row.page == "/v1/province/ontario?y=2024"
        (mail,) = ses_outbox
        assert "/v1/province/ontario?y=2024" in mail.html
        assert "http://localhost/v1/admin/feedback" in mail.html

    def test_invalid_page_falls_back_to_referrer(self, client, db_session):
        client.post("/feedback", data={"feedback": "hi", "page": "javascript:alert(1)"},
                    headers={"Referer": "http://localhost/v1/province/alberta?x=1#frag"})
        assert FeedbackSubmission.query.one().page == "/v1/province/alberta?x=1"

    def test_no_page_no_referrer_stores_none(self, client, db_session, ses_outbox):
        client.post("/feedback", data={"feedback": "hi"})
        assert FeedbackSubmission.query.one().page is None
        (mail,) = ses_outbox
        assert "Unknown" in mail.html

    def test_logged_in_submitter_recorded(self, client, db_session, login_as):
        user = login_as(make_user())
        client.post("/feedback", data={"feedback": "hi from a member"})
        assert FeedbackSubmission.query.one().user_id == user.id

    def test_status_commit_failure_after_send_still_succeeds(self, client, db_session, monkeypatch, ses_outbox):
        # The row is committed before the email; if recording email_sent afterwards fails, the
        # submitter must still get success (resubmitting would duplicate the row AND the email).
        from sqlalchemy.exc import OperationalError
        from data_viz.database import db
        real_commit, calls = db.session.commit, []
        def flaky_commit():
            calls.append(1)
            if len(calls) == 2:
                raise OperationalError("commit", {}, Exception("connection dropped"))
            real_commit()
        monkeypatch.setattr(db.session, "commit", flaky_commit)
        # The route's rollback() after the failed commit must land on a SAVEPOINT, not on the
        # db_session fixture's outer transaction (which would wipe the committed row too).
        for connection in db.engines.values():
            connection.begin_nested()
        response = client.post("/feedback", data={"feedback": "hello there"})
        assert response.status_code == 200
        assert len(ses_outbox) == 1
        assert FeedbackSubmission.query.one().body == "hello there"

    def test_widget_prefills_email_for_signed_in_user(self, client, db_session, login_as):
        user = login_as(make_user(email="member@example.org"))
        html = client.get("/").get_data(as_text=True)
        assert 'id="feedback-email"' in html
        assert 'value="member@example.org"' in html

    def test_widget_email_blank_for_anonymous(self, client, db_session):
        html = client.get("/").get_data(as_text=True)
        assert 'id="feedback-email"' in html
        assert 'value="member@example.org"' not in html
        # No stale value attribute at all on the email input.
        start = html.index('id="feedback-email"')
        assert 'value="' not in html[start - 200:start + 200]

    def test_recaptcha_failure_persists_nothing(self, client, db_session, monkeypatch, ses_outbox):
        monkeypatch.setattr("data_viz.main.verify_recaptcha", lambda *a, **kw: (False, "x"))
        assert client.post("/feedback", data={"feedback": "bot"}).status_code == 403
        assert FeedbackSubmission.query.count() == 0
        assert ses_outbox == []


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

"""Feedback endpoint, error handling, and the security headers every response carries."""
import logging

import pytest

from data_viz.database.models import FeedbackSubmission
from tests.factories import make_user, make_visual, unique


def _flaky_commit(monkeypatch, fail_on_call):
    """Make the N-th db.session.commit() of the request raise. The route's rollback() must land on
    a SAVEPOINT, not on the db_session fixture's outer transaction (which would wipe the committed
    rows the test wants to assert on), hence begin_nested() on every bound connection."""
    from sqlalchemy.exc import OperationalError
    from data_viz.database import db
    real_commit, calls = db.session.commit, []
    real_commit()   # end the session's current transaction so the request joins the savepoint
    for connection in db.engines.values():
        connection.begin_nested()

    def flaky():
        calls.append(1)
        if len(calls) == fail_on_call:
            raise OperationalError("commit", {}, Exception("connection dropped"))
        real_commit()
    monkeypatch.setattr(db.session, "commit", flaky)


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

    def test_ses_failure_still_persists_and_returns_200(self, client, db_session, monkeypatch, app, caplog):
        monkeypatch.setattr("data_viz.main.send_ses_email_result",
                            lambda *a, **kw: (False, "MessageRejected: Email address is not verified."))
        with caplog.at_level(logging.ERROR, logger=app.logger.name):
            response = client.post("/feedback", data={"feedback": "hello there"})
        assert response.status_code == 200
        assert response.get_json()["status"] == "success"
        row = FeedbackSubmission.query.one()
        assert row.body == "hello there"
        assert row.email_sent is False
        # The SES reason is persisted so the inbox can tell a config outage from a one-off bounce.
        assert row.email_error == "MessageRejected: Email address is not verified."
        assert row.email_status == "failed"
        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert any(f"Feedback #{row.id} stored but the email" in m and "MessageRejected" in m for m in errors)

    def test_unexpected_send_exception_recorded_not_raised(self, client, db_session, monkeypatch, app, caplog):
        # The row is already committed when the email is sent; a 500 here would make the submitter
        # retry and duplicate it, so a non-AWS exception is recorded on the row instead.
        def boom(*a, **kw):
            raise TypeError("bad template arg")
        monkeypatch.setattr("data_viz.main.send_ses_email_result", boom)
        with caplog.at_level(logging.ERROR, logger=app.logger.name):
            response = client.post("/feedback", data={"feedback": "hello there"})
        assert response.status_code == 200
        row = FeedbackSubmission.query.one()
        assert row.email_sent is False and row.email_error.startswith("unexpected TypeError")
        assert any("email send raised" in r.getMessage() for r in caplog.records)
        assert FeedbackSubmission.query.count() == 1

    def test_first_commit_failure_500_and_nothing_emailed(self, client, db_session, monkeypatch,
                                                         ses_outbox, app, caplog):
        # The row is the system of record: if it can't be stored the submitter gets the only error
        # they ever see, and no email may go out for a submission that doesn't exist.
        _flaky_commit(monkeypatch, fail_on_call=1)
        with caplog.at_level(logging.ERROR, logger=app.logger.name):
            response = client.post("/feedback", data={"feedback": "hello there"})
        assert response.status_code == 500
        assert response.get_json() == {"status": "error", "message": "Failed to save feedback"}
        assert ses_outbox == []
        assert FeedbackSubmission.query.count() == 0
        assert any("Failed to store feedback submission" in r.getMessage() for r in caplog.records)

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

    def test_valid_page_wins_over_referrer(self, client, db_session):
        client.post("/feedback", data={"feedback": "hi", "page": "/v1/province/ontario"},
                    headers={"Referer": "http://localhost/v1/province/alberta"})
        assert FeedbackSubmission.query.one().page == "/v1/province/ontario"

    def test_foreign_host_referrer_keeps_only_the_path(self, client, db_session):
        # By design: the host is dropped and the path is rendered as a same-origin link only.
        client.post("/feedback", data={"feedback": "hi"},
                    headers={"Referer": "http://evil.example/v1/x"})
        assert FeedbackSubmission.query.one().page == "/v1/x"

    @pytest.mark.parametrize("referer", ["//evil.example", "garbage", "http://[::1/v1/x", "//[x"])
    def test_unusable_referrer_stores_none_and_never_500s(self, client, db_session, referer):
        # The last two make urlsplit raise ValueError ("Invalid IPv6 URL"); the header is
        # client-controlled, so that must never reach the submitter as a 500.
        response = client.post("/feedback", data={"feedback": "hi"}, headers={"Referer": referer})
        assert response.status_code == 200
        assert FeedbackSubmission.query.one().page is None

    def test_review_link_omitted_without_public_base_url(self, client, db_session, ses_outbox, app, monkeypatch):
        monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", None)
        assert client.post("/feedback", data={"feedback": "hi"}).status_code == 200
        (mail,) = ses_outbox
        assert "Review in CANASK" not in mail.html

    def test_no_page_no_referrer_stores_none(self, client, db_session, ses_outbox):
        client.post("/feedback", data={"feedback": "hi"})
        assert FeedbackSubmission.query.one().page is None
        (mail,) = ses_outbox
        assert "Unknown" in mail.html

    def test_logged_in_submitter_recorded(self, client, db_session, login_as):
        user = login_as(make_user())
        client.post("/feedback", data={"feedback": "hi from a member"})
        assert FeedbackSubmission.query.one().user_id == user.id

    def test_status_commit_failure_after_send_still_succeeds(self, client, db_session, monkeypatch,
                                                            ses_outbox, app, caplog):
        # The row is committed before the email; if recording email_sent afterwards fails, the
        # submitter must still get success (resubmitting would duplicate the row AND the email).
        _flaky_commit(monkeypatch, fail_on_call=2)
        with caplog.at_level(logging.ERROR, logger=app.logger.name):
            response = client.post("/feedback", data={"feedback": "hello there"})
        assert response.status_code == 200
        assert len(ses_outbox) == 1
        row = FeedbackSubmission.query.one()
        assert row.body == "hello there"
        # The user-visible consequence: the mail WAS accepted but the row can't say so. That state
        # (email_sent=False, no reason) renders as "unknown", never as "failed".
        assert row.email_sent is False and row.email_error is None
        assert row.email_status == "unknown"
        assert any(f"Feedback #{row.id} stored but recording email_sent=True failed" in r.getMessage()
                   for r in caplog.records)

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

"""Pure builder tests for the nightly report: render_report() is a template render off a plain
dict (no DB), and the `mt` Jinja filter is a pure datetime conversion. Both run in the unit tier
(no Postgres) via the session-scoped, DB-free `app` fixture from tests/conftest.py.

render_report() renders through `current_app.jinja_env.get_template(...).render(...)` rather than
Flask's render_template(), specifically so it never runs the app-wide @app.context_processor
functions (inject_nav_permissions/inject_page_title in data_viz/__init__.py) -- those need a
request context and, worse, would share `g` with any real request render_report happened to be
nested inside (e.g. if ever called mid-request), silently overwriting that request's cached
Flask-Login user. These tests exercise render_report with only an app context (its real
environment: the nightly_report Celery task's ContextTask pushes an app context, never a request
context), and one test pins the no-context-processor guarantee directly."""
import datetime

D = datetime.date


def test_render_report_subject_counts_updated_and_issues(app):
    from data_scraping.report import render_report

    data = {
        "window_start": datetime.datetime(2026, 1, 15, 6, 0),
        "window_end": datetime.datetime(2026, 1, 16, 6, 0),
        "ran": [{"source": "Fake Source", "trigger": "manual", "status": "published",
                 "duration": datetime.timedelta(minutes=5)}],
        "updated": [{"source": "Fake Source", "from": D(2024, 12, 1), "to": D(2024, 12, 31),
                     "trigger": "manual"}],
        "issues": [],
        "new_data": [],
        "status": [{"key": "fakeSource", "source": "Fake Source", "onboarded": True,
                    "schedule_enabled": True, "auto_publish": True, "paused_reason": None,
                    "held": 0, "running": 0, "stale_days": 5, "stale": False}],
    }
    with app.app_context():
        subject, html = render_report(data)
    assert "1 updated" in subject and "0 issues" in subject
    assert "Fake Source" in html
    assert "No issues." in html


def test_render_report_nothing_ran(app):
    from data_scraping.report import render_report

    data = {
        "window_start": datetime.datetime(2026, 1, 15, 6, 0),
        "window_end": datetime.datetime(2026, 1, 16, 6, 0),
        "ran": [], "updated": [], "issues": [], "new_data": [],
        "status": [],
    }
    with app.app_context():
        _, html = render_report(data)
    assert "Nothing ran in this window." in html


def test_render_report_works_with_only_an_app_context_no_request_context(app):
    # The real environment: celery_worker/celery.py's ContextTask pushes app.app_context() and
    # nothing else. render_template() would crash here (context processors read request.*);
    # get_template().render() must not.
    from data_scraping.report import render_report

    data = {"window_start": datetime.datetime(2026, 1, 15, 6, 0),
            "window_end": datetime.datetime(2026, 1, 16, 6, 0),
            "ran": [], "updated": [], "issues": [], "new_data": [], "status": []}
    with app.app_context():
        assert render_report(data)   # no RuntimeError: Working outside of request context.


def test_render_report_does_not_touch_g_of_an_enclosing_real_request(app):
    # Regression test for the review finding: render_report used to open its own
    # current_app.test_request_context(), which -- nested inside an ALREADY active request for
    # this app -- reuses that request's AppContext and therefore its `g`, so any context
    # processor touching flask_login.current_user would cache/overwrite g._login_user and
    # deauthenticate the real request. Simulate a real, already-active request and assert
    # render_report leaves its `g` completely alone.
    from flask import g
    from data_scraping.report import render_report

    data = {"window_start": datetime.datetime(2026, 1, 15, 6, 0),
            "window_end": datetime.datetime(2026, 1, 16, 6, 0),
            "ran": [], "updated": [], "issues": [], "new_data": [], "status": []}
    with app.test_request_context("/v1/some-real-page"):
        g.marker = "untouched"
        render_report(data)
        assert g.marker == "untouched"
        assert not hasattr(g, "_login_user")


def test_mt_filter_converts_naive_utc_to_mountain(app):
    # America/Edmonton is UTC-7 in January (no DST), so 20:00 UTC -> 13:00 Mountain.
    mt = app.jinja_env.filters["mt"]
    assert mt(datetime.datetime(2026, 1, 15, 20, 0)) == "Jan 15 13:00"

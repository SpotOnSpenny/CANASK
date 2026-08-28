"""resolve_page_title: pure function of the current request's endpoint + view args."""
from data_viz.main import resolve_page_title


def title_for(app, path):
    with app.test_request_context(path):
        # Route matching populates request.endpoint/view_args inside the context.
        return resolve_page_title()


def test_static_page_titles(app):
    assert title_for(app, "/") == "Home"
    assert title_for(app, "/v1/login") == "Login"
    assert title_for(app, "/v1/user-management") == "User Management"
    assert title_for(app, "/v1/national/das-explorer") == "DAS Explorer"


def test_province_title_from_slug(app):
    assert title_for(app, "/v1/province/british-columbia") == "British Columbia"
    assert title_for(app, "/v1/province/ontario") == "Ontario"


def test_national_dashboard_title(app):
    assert title_for(app, "/v1/national/drug-checking") == "Drug Checking"


def test_unknown_national_dashboard_is_none(app):
    assert title_for(app, "/v1/national/not-a-dashboard") is None


def test_non_page_endpoints_resolve_none(app):
    # JSON APIs and modal/row endpoints must not disturb the page title.
    assert title_for(app, "/api/v1/province/ontario/data") is None
    assert title_for(app, "/healthz") is None

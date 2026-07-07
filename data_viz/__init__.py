# Python Standard Library Dependencies
import os

# External Dependency Imports
from flask import Flask, redirect, current_app, request, render_template, flash, get_flashed_messages, jsonify, make_response
from flask_assets import Environment, Bundle
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

# Internal Dependency Imports
from data_viz.config import configure
from data_viz.main import main_blueprint
from data_viz.database import db, migrate
from data_viz.auth import login_manager
from data_viz.auth.auth import auth_blueprint
from data_viz.cli import register_cli
from celery_worker.celery import init_celery

#######################################################################################
#                                        Notes:                                       #
#######################################################################################

# Initialize the flask application
try:
    app = Flask(__name__)
    configure(app)
    # Trust the reverse-proxy chain (Cloudflare -> nginx) so request.remote_addr / scheme /
    # host reflect the real client, not the proxy. The rate limiter keys off CF-Connecting-IP
    # directly (see data_viz/extensions.client_ip), but this keeps audit logging accurate too.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=app.config["TRUSTED_PROXY_COUNT"], x_proto=1, x_host=1)
    # Force Jinja autoescaping on for ALL templates. Flask only autoescapes .html/.xml/... by default,
    # and every template here is .jinja -> without this, every {{ }} renders raw HTML (stored/reflected
    # XSS). No template uses |safe/Markup, so escaping everything is safe and matches intended usage.
    app.jinja_env.autoescape = True
except Exception as e:
    print(f"An error occured while initializing the Flask app:")
    print(e)

# Register and bundle the static CSS and JS assets
assets = Environment(app)
assets.register(
    "css_all",
    Bundle(
        "css/master_sheet.css",
        filters="cssmin",
        output="assets/main.css"
    )
)

assets.register(
    "js_all",
    # No JS minifier: `jsmin` (Crockford's algorithm) does not understand ES6 template literals and
    # strips the spaces inside them (e.g. `${manner} ${substance} Deaths` -> `${manner}${substance}Deaths`,
    # mangling chart series labels). The vendored libs here are already pre-minified, and our hand-written
    # plotly-theme/main/visualGeneration are small, so concatenating them un-minified is correct + cheap.
    Bundle(
        "js/htmx.min.js",
        "js/plotly-2.32.0.min.js",
        "js/jquery-3.7.1.min.js",
        "js/plotly-theme.js",
        "js/main.js",
        "js/visualGeneration.js",
        output="assets/main.js"
    )
)

@app.template_filter("visual_label")
def visual_label(visual):
    """Human-readable label for a Visuals row: its authored menu_name, else the visual_id formatted
    into sentence case (deaths_by_sex_line -> "Deaths by sex line") so drill-downs without a menu_name
    don't surface raw underscore-cased ids in the UI."""
    if getattr(visual, "menu_name", None):
        return visual.menu_name
    return (visual.name or "").replace("_", " ").capitalize()

@app.after_request
def add_cache_control_headers(response):
    # Setup cache control headings
    if not request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"

    # Inject OOB swaps for HTMX requests (flash messages + browser title)
    is_htmx_html = (request.headers.get("HX-Request")
                    and response.content_type == "text/html; charset=utf-8"
                    and response.status_code not in [301, 302, 303, 307, 308])
    if is_htmx_html:
        # Hand-built (non-Jinja) HTML, so Jinja autoescaping does not apply here -- escape every
        # dynamic part. Flash messages routinely interpolate user-controlled values (usernames,
        # group names, emails), so an unescaped message is a stored-XSS sink in the admin's DOM.
        from markupsafe import escape
        messages = get_flashed_messages(with_categories=True)
        if messages:
            alerts = "".join([
                f'<div class="alert alert-{escape(category)} alert-dismissible fade show" role="alert">'
                f'{escape(message)}'
                f'<button type="button" class="btn-close" data-bs-dismiss="alert"></button>'
                f'</div>'
                for category, message in messages
            ])
            flash_html = f'''
                <div id="flashed-messages-container" hx-swap-oob="true">
                    <div class="position-fixed top-0 start-50 translate-middle-x pt-3" style="z-index: 1050; width: 50%;">
                        {alerts}
                    </div>
                </div>
            '''
            original = response.get_data(as_text=True)
            response.set_data(original + flash_html)

        # Swap the browser title so HTMX navigations keep the tab title in sync. Pages resolve to a
        # title; sub-component responses (modals/rows) resolve to None and leave the title untouched.
        from data_viz.main import resolve_page_title
        title = resolve_page_title()
        if title:
            response.set_data(response.get_data(as_text=True)
                              + f'<title hx-swap-oob="true">CANASK | {escape(title)}</title>')
    return response

# Expose the current user's nav capabilities to every template so the menu can hide links to
# pages the user can't access (route-level @require_role still enforces access).
@app.context_processor
def inject_nav_permissions():
    from flask_login import current_user
    from data_viz.auth.auth_helpers import nav_permissions
    from data_viz.visual_query import accessible_provinces
    return {"nav_perms": nav_permissions(current_user),
            "accessible_provinces": accessible_provinces(current_user)}

# Expose the current page's title to every template so base.jinja can set the <title> on full page
# loads. Distinct from the `page_title` kwarg some routes pass for the breadcrumb heading.
@app.context_processor
def inject_page_title():
    from data_viz.main import resolve_page_title
    return {"nav_title": resolve_page_title()}

# Database setup
db.init_app(app)
app_folder = os.path.dirname(os.path.abspath(__file__))
migrations_folder = os.path.join(app_folder, "database", "migrations")
migrate.init_app(app, db, directory=migrations_folder)

# Initialize CSRF protection for the application
csrf = CSRFProtect()
csrf.init_app(app)

# Initialize rate limiting (Redis-backed, keyed on the real client IP). Config comes from the
# RATELIMIT_* keys set in data_viz/config.py. Static assets are exempt so page loads with many
# CSS/JS/image requests don't burn a visitor's global allowance.
from data_viz.extensions import limiter
limiter.init_app(app)

@limiter.request_filter
def _exempt_static():
    return request.endpoint == "static"

# Register the custom CLI commands for the application
register_cli(app)

# Start celery for background job handling
celery = init_celery(app)

# Initialize the login manager for the application
login_manager.login_view = "auth.login"
login_manager.init_app(app)

# Register the blueprints for the application
app.register_blueprint(main_blueprint)
app.register_blueprint(auth_blueprint)

# Error handling for 404 errors
@app.errorhandler(404)
def page_not_found(e):
    return redirect("/not-found")

# Error handling for 429 (rate limit exceeded). Shape the response per caller: JSON for the
# data API and feedback endpoints (their callers parse JSON), an HTML page otherwise. Flask-Limiter's
# own after_request hook (RATELIMIT_HEADERS_ENABLED) injects Retry-After / X-RateLimit-* onto it.
@app.errorhandler(429)
def ratelimit_handler(e):
    if request.path.startswith("/api/") or request.path == "/feedback":
        return make_response(
            jsonify({"status": "error", "error": "rate_limited",
                     "message": "Too many requests. Please slow down and try again shortly."}),
            429)
    return make_response(render_template("429.jinja"), 429)

# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code
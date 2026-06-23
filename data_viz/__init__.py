# Python Standard Library Dependencies
import os

# External Dependency Imports
from flask import Flask, redirect, current_app, request, render_template, flash, get_flashed_messages
from flask_assets import Environment, Bundle
from flask_wtf.csrf import CSRFProtect

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

    # Inject flash messages OOB for HTMX requests
    if request.headers.get("HX-Request") and response.content_type == "text/html; charset=utf-8" and response.status_code not in [301, 302, 303, 307, 308]:
        messages = get_flashed_messages(with_categories=True)
        if messages:
            alerts = "".join([
                f'<div class="alert alert-{category} alert-dismissible fade show" role="alert">'
                f'{message}'
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

# Database setup
db.init_app(app)
app_folder = os.path.dirname(os.path.abspath(__file__))
migrations_folder = os.path.join(app_folder, "database", "migrations")
migrate.init_app(app, db, directory=migrations_folder)

# Initialize CSRF protection for the application
csrf = CSRFProtect()
csrf.init_app(app)

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

# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code
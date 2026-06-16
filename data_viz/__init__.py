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
    Bundle(
        "js/htmx.min.js",
        "js/plotly-2.32.0.min.js",
        "js/jquery-3.7.1.min.js",
        "js/plotly-theme.js",
        "js/main.js",
        "js/visualGeneration.js",
        "js/visuals.js",
        filters="jsmin",
        output="assets/main.js"
    )
)

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
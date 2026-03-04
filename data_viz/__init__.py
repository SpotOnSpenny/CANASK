# Python Standard Library Dependencies
import os

# External Dependency Imports
from flask import Flask, redirect, current_app, request
from flask_assets import Environment, Bundle
from flask_simplelogin import SimpleLogin
from flask_wtf.csrf import CSRFProtect

# Internal Dependency Imports
from data_viz.config import configure
from data_viz.main import main_blueprint
from data_viz.database import db, migrate
from data_viz.auth import login_manager
from data_viz.auth.auth import auth_blueprint
from data_viz.cli import register_cli

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
        "js/main.js",
        "js/visualGeneration.js",
        "js/jquery-3.7.1.min.js",
        "js/visuals.js",
        filters="jsmin",
        output="assets/main.js"
    )
)

# Setup cache control headings
@app.after_request
def add_cache_control_headers(response):
    if not request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
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
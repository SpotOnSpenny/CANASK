# Python Standard Library Dependencies


# External Dependency Imports
from flask import Flask, redirect
from flask_assets import Environment, Bundle

# Internal Dependency Imports
from data_viz.config import configure
from data_viz.main import main_blueprint

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
        "js/jquery-3.7.1.min.js",
        filters="jsmin",
        output="assets/main.js"
    )
)

# Register the blueprints for the application
app.register_blueprint(main_blueprint)

# Error handling for 404 errors
@app.errorhandler(404)
def page_not_found(e):
    return redirect("/not-found")

# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code
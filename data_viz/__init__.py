# Python Standard Library Dependencies


# External Dependency Imports
from flask import Flask
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
    "main_css",
    Bundle(
        # Add any CSS Files here as they are created
        filters="cssmin",
        output="assets/main.css"
    )
)

assets.register(
    "main_js",
    Bundle(
        # Add any JS Files here as they are created
        filters="jsmin",
        output="assets/main.js"
    )
)

# Register the blueprints for the application
app.register_blueprint(main_blueprint)

# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code
# Python Standard Library Dependencies
import os
import requests
from functools import wraps

# External Dependency Imports
from flask import Blueprint, render_template, redirect, url_for, request, jsonify, views, current_app, session, flash, get_flashed_messages
import pandas
from mailersend import MailerSendClient, EmailBuilder
import bleach
from flask_simplelogin import SimpleLogin

# Internal Dependency Imports
from .generateVisuals import pull_data, filter_data

#######################################################################################
#                                        Notes:                                       #
# Currently, these routes are setup to pull the data that we want from the excel and  #
# csv files that are generated from scraping, directly from the output directory.     #
# When we deplot the application or even get serious about developing it beyond just  #
# "let's see what is possible" this can, and should, be changed to pull data from a   #
# database which can be created to store the data in a more structured and            #
# standardized format. NOTE that this may also require additional scripts to clean or #
# otherwise sort the data that we have scrapped into that more structured standard    #
# format, but that is a problem for another day.                                      #
#######################################################################################

# Define the blueprint for the main application
main_blueprint = Blueprint("main", __name__)

# Init the login manager
login_manager = SimpleLogin()
login_check = login_manager._login_checker

# require_auth decorator
def require_auth(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if session.get("simplelogin", False):
            session["has_visited"] = True
            return view(**kwargs)
        elif not session.get("simplelogin", False):
            if session.get("has_visited", False):
                flash("Please login to access this page", "warning")
            return render_template("base.jinja", include_partials="login")
    return wrapped_view

##################################### ROUTES ###########################################
# Route for the login page
@main_blueprint.route("/v1/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        form_data = request.form
        if login_check(form_data): 
            session["simplelogin"] = True
            if request.headers.get("HX-Request") == "true":
                return render_template("index.jinja") 
            else:
                return render_template("base.jinja", include_partials="index", dash_template=None)
        else:
            flash("Invalid username or password", "danger")
            return render_template("base.jinja", include_partials="login")
    else:
        return render_template("base.jinja", include_partials="login")


# Routes for main index page
@main_blueprint.route("/")
@require_auth
def index():
    if request.headers.get("HX-Request") == "true":
        return render_template("introduction.jinja")
    else:
        return render_template("base.jinja", include_partials="index")

# Routes for National Trends
@main_blueprint.route("/toxicity-deaths")
@require_auth
def toxicity_deaths():
    if request.headers.get("HX-Request") == "true":
        return render_template("v0/toxicity_deaths.jinja")
    else:
        return render_template("base.jinja", include_partials="index", dash_template="v0/toxicity_deaths.jinja")

@main_blueprint.route("/province/ontario")
@require_auth
def ontario():
    if request.headers.get("HX-Request") == "true":
        return render_template("v0/provincial_on.jinja")
    else:
        return render_template("base.jinja", include_partials="index", dash_template="v0/provincial_on.jinja")

# Routes for Error Pages
@main_blueprint.route("/not-found")
def page_not_found():
    if request.headers.get("HX-Request") == "true":
        return render_template("index.jinja", dash_template="404.jinja"), 404
    else:
        return render_template("base.jinja", include_partials="index", dash_template="404.jinja"), 404

# Route for Feedback submission and recaptcha verification
@main_blueprint.route("/feedback", methods=["POST"])
def feedback():
    # Get the form data
    feedback_data = request.form
    # Create POST request to send to the recaptcha verification server
    print("sending recaptcha request")
    recaptcha_response = requests.post("https://www.google.com/recaptcha/api/siteverify", data={"secret": os.environ.get("RECAPTCHA_SECRET"), "response": feedback_data["g-recaptcha-response"]})
    if recaptcha_response.status_code != 200:
        return jsonify({"status": "error", "message": "Recaptcha verification failed"}), 500
    elif recaptcha_response.json()["success"] == False:
        return jsonify({"status": "error", "message": "Recaptcha verification failed"}), 403
    else:
        mailersend_client = MailerSendClient(os.environ.get("MAILERSEND_API_KEY"))
        mail = (EmailBuilder()
            .from_email("canask_feedback@test-65qngkd7jm8lwr12.mlsender.net")
            .to("spencer.fietz@ucalgary.ca")
            .subject("CANASK Feedback Received")
            .html(f"""
            <h2>Name:</h2>{bleach.clean(feedback_data['name']) if feedback_data['name'] else "Anonymous"} </br>
            <h2>Feedback:</h2>{bleach.clean(feedback_data['feedback'])} </br>
            <h2>Reach them at:</h2>{bleach.clean(feedback_data['email'])}
            """
            )
            .build()
        )

        try:
            response = mailersend_client.emails.send(mail)
        except Exception as e:
            print(e)
            return jsonify({"status": "error", "message": "Failed to send feedback email"}), 500
        # Return an OK response
        return jsonify({"status": "success"}), 200;

# Route for V1 data visuals
@main_blueprint.route("/v1/province/<province>")
@require_auth
def v1_province(province): 
    if request.headers.get("HX-Request") == "true":
        return render_template("v1/provincial_vis.jinja", province=province)
    else:
        return render_template("base.jinja", include_partials="index", dash_template="v1/provincial_vis.jinja", province=province)


################################# Test Code Below ######################################
if __name__ == '__main__':
    #Test update
    pass
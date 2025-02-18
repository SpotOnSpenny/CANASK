# Python Standard Library Dependencies
import os
import requests

# External Dependency Imports
from flask import Blueprint, render_template, redirect, url_for, request, jsonify
import pandas
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
import bleach

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

##################################### ROUTES ###########################################
# Routes for main index page
@main_blueprint.route("/")
def index():
    return render_template("index.jinja")

@main_blueprint.route("/introduction", defaults={"htmx_flag": None})
@main_blueprint.route("/introduction/<htmx_flag>")
def introduction(htmx_flag):
    if not htmx_flag:
        return redirect(url_for("main.index"))
    else:
        return render_template("introduction.jinja")

# Routes for National Trends
@main_blueprint.route("/toxicity-deaths", defaults={"htmx_flag": None})
@main_blueprint.route("/toxicity-deaths/<htmx_flag>")
def toxicity_deaths(htmx_flag):
    if not htmx_flag:
        return render_template("index.jinja", dash_template="toxicity_deaths.jinja")
    else:
        return render_template("toxicity_deaths.jinja")

# Routes for Provincial Dashboards
@main_blueprint.route("/province/british-columbia", defaults={"htmx_flag": None})
@main_blueprint.route("/province/british-columbia/<htmx_flag>")
def bc(htmx_flag):
    if not htmx_flag:
        return render_template("index.jinja", dash_template="provincial_bc.jinja")
    else:
        return render_template("provincial_bc.jinja")

@main_blueprint.route("/province/saskatchewan" , defaults={"htmx_flag": None})
@main_blueprint.route("/province/saskatchewan/<htmx_flag>")
def sk(htmx_flag):
    if not htmx_flag:
        return render_template("index.jinja", dash_template="provincial_sask.jinja")
    else:
        return render_template("provincial_sask.jinja")
    
@main_blueprint.route("/province/ontario" , defaults={"htmx_flag": None})
@main_blueprint.route("/province/ontario/<htmx_flag>")
def on(htmx_flag):
    if not htmx_flag:
        return render_template("index.jinja", dash_template="provincial_on.jinja")
    else:
        return render_template("provincial_on.jinja")

@main_blueprint.route("/province/alberta", defaults={"htmx_flag": None})
@main_blueprint.route("/province/alberta/<htmx_flag>")
def ab(htmx_flag):
    if not htmx_flag:
        return render_template("index.jinja", dash_template="no_data.jinja")
    else:
        return render_template("no_data.jinja")
    
# Routes for Error Pages
@main_blueprint.route("/not-found")
def page_not_found():
    return render_template("index.jinja", dash_template="404.jinja"), 404

# Route for Feedback submission and recaptcha verification
@main_blueprint.route("/feedback", methods=["POST"])
def feedback():
    # Get the form data
    feedback_data = request.form
    # Create POST request to send to the recaptcha verification server
    recaptcha_response = requests.post("https://www.google.com/recaptcha/api/siteverify", data={"secret": os.environ.get("RECAPTCHA_SECRET"), "response": feedback_data["g-recaptcha-response"]})
    if recaptcha_response.status_code != 200:
        return jsonify({"status": "error", "message": "Recaptcha verification failed"}), 500
    elif recaptcha_response.json()["success"] == False:
        return jsonify({"status": "error", "message": "Recaptcha verification failed"}), 403
    else:
        # Make the email and send it via sendgrid
        message = Mail(
            from_email="spencer.fietz@ucalgary.ca",
            to_emails="spencer.fietz@ucalgary.ca",
            subject="Dashboard Feedback Received",
            html_content=f"""
            <h2>Name:</h2>{bleach.clean(feedback_data['name']) if feedback_data['name'] else "Anonymous"} </br>
            <h2>Feedback:</h2>{bleach.clean(feedback_data['feedback'])} </br>
            <h2>Reach them at:</h2>{bleach.clean(feedback_data['email'])}
            """
        )
        try:
            sg = SendGridAPIClient(os.environ.get("SENDGRID_API_KEY"))
            response = sg.send(message)
            print(response.status_code)
        except Exception as e:
            return jsonify({"status": "error", "message": "Failed to send feedback email"}), 500
        # Return an OK response
        return jsonify({"status": "success"}), 200;

################################# Test Code Below ######################################
if __name__ == '__main__':
    all_frames = pull_data("skPubCentre")
    needed_frames = filter_data(all_frames, ["BreakdownofOpioidDrugsIdentifiedinConfirmedDrugToxicityDeathsbyMannerofDeath,2016-2024", "BreakdownofBenzodiazepineDrugsIdentifiedinConfirmedDrugToxicityDeathsbyMannerofDeath,2024"])
    print(needed_frames)

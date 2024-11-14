# Python Standard Library Dependencies
import os

# External Dependency Imports
from flask import Blueprint, render_template, redirect, url_for, request
import pandas

# Internal Dependency Imports
from .generateVisuals import pull_data, filter_data, drug_type_visual

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

################################# Test Code Below ######################################
if __name__ == '__main__':
    all_frames = pull_data("skPubCentre")
    needed_frames = filter_data(all_frames, ["BreakdownofOpioidDrugsIdentifiedinConfirmedDrugToxicityDeathsbyMannerofDeath,2016-2024", "BreakdownofBenzodiazepineDrugsIdentifiedinConfirmedDrugToxicityDeathsbyMannerofDeath,2024"])
    print(needed_frames)

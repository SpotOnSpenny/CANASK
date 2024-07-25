# Python Standard Library Dependencies
import os

# External Dependency Imports
from flask import Blueprint, render_template
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
@main_blueprint.route("/")
def index():
    return render_template("index.jinja")

@main_blueprint.route("/introduction")
def introduction():
    return render_template("introduction.jinja")

@main_blueprint.route("/toxicity-deaths")
def toxicity_deaths():
    return render_template("toxicity_deaths.jinja")

@main_blueprint.route("/no-data")
def no_data():
    return render_template("no_data.jinja")

@main_blueprint.route("/province/british-columbia")
def bc():
    return render_template("provincial_bc.jinja")
################################# Test Code Below ######################################
if __name__ == '__main__':
    all_frames = pull_data("skPubCentre")
    needed_frames = filter_data(all_frames, ["BreakdownofOpioidDrugsIdentifiedinConfirmedDrugToxicityDeathsbyMannerofDeath,2016-2024", "BreakdownofBenzodiazepineDrugsIdentifiedinConfirmedDrugToxicityDeathsbyMannerofDeath,2024"])
    print(needed_frames)

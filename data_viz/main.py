# Python Standard Library Dependencies
import os

# External Dependency Imports
from flask import Blueprint, render_template
import pandas

# Internal Dependency Imports


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

#################################### UTILITIES #########################################
# Helper function to pull data from the specified excel/csv file
def pull_data(data_source: str):
    output_dir = os.path.join(os.path.abspath(os.path.dirname(os.path.dirname(__file__))), "output")
    if any(data_source in file for file in os.listdir(output_dir)):
        file = [file for file in os.listdir(output_dir) if data_source in file][0]
        match file.split(".")[1]:
            case "csv":
                return {f"{data_source}": pandas.read_csv(os.path.join(output_dir, file))}
            case "xlsx":
                sheets = {}
                dataframes = pandas.read_excel(os.path.join(output_dir, file), sheet_name=None).values()
                for dataframe in dataframes:
                    name = list(filter(lambda value: True if "Unnamed" not in value and value != "NaN" else False, dataframe.columns))[0]
                    dataframe.set_flags(allows_duplicate_labels=False)
                    dataframe.columns = dataframe.iloc[0]
                    dataframe.dropna(axis=0, inplace=True)
                    dataframe = dataframe.drop(dataframe.columns[[0]], axis=1).reset_index(drop=True)
                    sheets[name] = dataframe
                return sheets
    else:
        raise FileNotFoundError(f"Data source {data_source} not found in the output directory!")

# Helper function to pull the data from the provided source into a dataframe
def filter_data(data: dict, find_these: list):
    dataframes = []
    for key in data.keys():
        if any(find_this == key.split(",")[0].lower().replace(" ", "") for find_this in find_these):
            dataframes.append(data[key])
    return dataframes


##################################### ROUTES ###########################################
@main_blueprint.route("/")
def index():
    
    return render_template("index.html")

# Test code below
if __name__ == '__main__':
    all_frames = pull_data("bcCoronersReport")
    needed_frames = filter_data(all_frames, ["unregulateddrugdeathsbymonth", "unregulateddrugdeathsbydrugtyperelevanttodeath"])
    print(needed_frames)

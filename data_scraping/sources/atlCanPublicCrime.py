# Python Standard Library Imports
import json
import os
import shutil
import zipfile
import pandas
import sys

import csv

# External Dependency Imports
import urllib3
from alive_progress import alive_bar

# Internal Dependency Imports
sys.dont_write_bytecode = True
sys.path.append("data_scraping/scraping_utilities")
from checkUps import checkup_output

#######################################################################################
#                                       Notes:                                        #
# This file contains the data scraping code for the New Brunswick Public Safety Crime #
# Dashboard, which pulls data from a MASSIVE stats canada dataset. The dataset that's #
# important to us is https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3510017801 #
# as it has important aggregate information about drug violations, however there's a  #
# notable lack of information relating to specific substances. It's also worth noting #
# that the data has aggregate information for all atlantic provinces (NS, NB, PE, NL) #
# and so it can be used to pull aggregate information from more than just NB.         #
#                                                                                     #
# Health Canada does make it easy to pull this incredibly large CSV, as they've made  #
# a number of API endpoints, such as one which allows us to download the entire CSV   #
# file. The docs for the API can be found at the URL below, the method we're using    #
# here is the "getFullTableDownloadCSV":                                              #
#                                                                                     #
# https://www.statcan.gc.ca/en/developers/wds/user-guide#a12-6                        #
#                                                                                     #
# This method will return a link which will download the CSV as a ZIP file. We'll     #
# then need to use __________ to extract the csv from the zip file so that we can     #
# process the information within.Considering the nature of the data, it makes sense   #
# once the massive CSV is pulled, that we should go through and process it into       #
# smaller chunks related to the specific data that we're looking for. I imagine we    #
# can do this based on the type of violation and also the Geography.                  #
#                                                                                     #
# The API also provides a method for looking identifying the tables that have changed #
# since a specific date. To avoid pulling the same data over and over again, we'll    #
# instead use this method getCubeMetadata. This allows us to see the date of the last #
# datapoint. **NOTE** This is used to name the file as well so that we can easily see #
# the date of the data, as opposed to the other sources, which are named with the     #
# date of the last pull for comparison.
#######################################################################################

# Function to pull the data used on the New Brunswick Public Safety Crime Dashboard
def nb_crime_scrape():
    # Set up the http client to make requests to the Stat Can API
    http = urllib3.PoolManager()

    # Pull the metadata to get the date of the last data point
    # TODO Add error handling here for the API request
    metadata = http.request("POST", "https://www150.statcan.gc.ca/t1/wds/rest/getCubeMetadata", body=json.dumps([{"productId": 35100178}]), headers={"Content-Type": "application/json"})
    end_date = json.loads(metadata.data.decode("utf-8"))[0]["object"]["cubeEndDate"]

    # Check the output directory for the data
    output_dir, needed_files, existing_files = checkup_output(["35100178"])
    # Check if the existing files are up to  date, exit if they are
    for file in existing_files:
        if file.split("_")[0] == end_date and file.split("_")[1] == "35100178":
            print("Data from Atlantic Canada is up to date! No need to pull it again.")
            return
    # Check existing files to see if there's a CSV that we need to delete
    for file in existing_files:
        if file.split("_")[1] == "35100178":
            existing_file = os.path.join(output_dir, file)
    # If the data is not up to date, pull the data
    response = http.request("GET",  "https://www.statcan.gc.ca/t1/wds/rest/")#"https://www150.statcan.gc.ca/t1/wds/rest/getFullTableDownloadCSV/35100178/en")
    response = json.loads(response.data.decode("utf-8"))
    #TODO Add error handling here
    # Download the zip file from the url in the response
    with http.request('GET', response["object"], preload_content=False) as data_response, open(os.path.join(output_dir, "rawCSV.zip"), 'wb') as out_file:       
        print("Downloading raw data csv zip file. This may take a while...")
        shutil.copyfileobj(data_response, out_file)
    # Release the connection stream
    data_response.release_conn()
    # Unzip the raw csv, without extracting the metadata
    print("Extracting the raw data csv file from the downloaded zip file...")
    with zipfile.ZipFile(os.path.join(output_dir, "rawCSV.zip"), "r") as zip_object:
        zip_object.extract("35100178.csv", output_dir)
    # Open the CSV  into a pandas dataframe
    raw_data = pandas.read_csv(os.path.join(output_dir, "35100178.csv"))
    # Clean out data that is out of scope (date, and non-substance related data)
    raw_data = raw_data.loc[int(raw_data["REF_DATE"]) >= 2007]
    # Cleanup output and remove files no longer needed
    


# I think the workflow will go something like this
# 1. check for the output directory and needed files
    # If one exists, then check the date
# 2. Request from the API based on the date of existing data
    # If the most recent call was today, tell them to screw off, exit the function
    # If the most recent call was yesterday or beyond, request data since then
    # If there is no existing data, request all data
# 3. Unzip the CSV and load it into a pandas dataframe
# 4. Do some data cleaning
    # We want to seperate the large dataframe into smaller ones based on Area and Criminal Code
    # We want to remove any rows that are not related to substance use
        # We also may want to think about instead, just building a better query to the API to only get the data we want, instead of parsing through it
        # Trouble is, at this stage we don't actually know what the data we want looks like which makes this difficult
            # For instance, we may want more granular aggregate geographic data later on, beyond just province wide
# 5. Save the data to the output directory under several different files based on geographic area
    # ex. date_nsRatesFatalities.csv, date_peRatesFatalities.csv, date_nlRatesFatalities.csv, etc.
    # Remember, this data source is for ALL the Atlantic provinces, so we should be able to get data for all of them from the single source

# CSV Generator function to iterate/clean the data at the same time
def data_generator(file: str, year: int):
    try:
        print("Beginning data cleaning...")
        # Open the CSV with chunks
        reader = pandas.read_csv(file, chunksize=10000)
        # Find the first chunk with data from target year
        for chunk in reader:
            if chunk.tail(1)["REF_DATE"].values[0] >= year:
                chunk_with_index = chunk
                break
        # Find the first row with data from target year
        for index, row in chunk_with_index.iterrows():
            # Find the first row with 
            if row["REF_DATE"] == year:
                first_year_row = index
                break
        print(f"Row {first_year_row} is the first row with data from {year}. Cleaining data from that row on...")
        # Instantiate the list of codes for drug related offences
        drug_offences = ["[401]", "[4140]", "[4120]", "[410]", "[4110]", "[4130]", "[420]", "[4240]", "[4340]", "[4440]", "[430]", "[4220]", "[4320]", "[440]", "[440]", "[4210]", "[4230]", "[4310]", "[4330]"]
        # Open the CSV (still in chunks), skipping to the first row with data from target year
        reader = pandas.read_csv(file, chunksize=10000, skiprows=range(1, first_year_row))
        # Iterate over each row, chunk by chunk and yield each row that's relevant
        chunk_num = 1
        for chunk in reader:
            with alive_bar(len(chunk), title=f"Iterating over data chunk {chunk_num}") as bar:
                for index, row in chunk.iterrows():
                    if any(code in row["Violations"] for code in drug_offences):
                        yield row
                    bar()
            chunk_num += 1
    except KeyboardInterrupt:
        return
    except Exception as e:
        print(e)

# Test code below
if __name__ == "__main__":
    output_dir, needed_files, existing_files = checkup_output(["35100178"])
    # Open the CSV using a generator
    print(list(data_generator("output/35100178.csv", 2007)))
    

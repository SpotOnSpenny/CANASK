# Python Standard Library Imports
import urllib3
import json
import os
import shutil
import zipfile
import pandas

# External Dependency Imports


# Internal Dependency Imports


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
#######################################################################################

# Function to pull the data used on the New Brunswick Public Safety Crime Dashboard
def nb_crime_scrape():
    # Check for output directory, make it if one doesn't exist
    output_dir = os.path.join(os.getcwd(), "output")
    if not os.path.exists(output_dir):
        print("No output directory")
    # If the rawCSV.zip file already exists, skip the download
    if os.path.exists(os.path.join(output_dir, "rawCSV.zip")):
        print("rawCSV.zip file already exists in the output directory. Skipping download.")
    # Otherwise, download the csv from the source
    else:
        print("No rawCSV.zip file found in the output directory. Downloading...")
        # Request the data using the API
        http = urllib3.PoolManager()
        response = http.request("GET", "https://www150.statcan.gc.ca/t1/wds/rest/getFullTableDownloadCSV/35100178/en")
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
    # Remove the zipfile now that we have the csv
    os.remove(os.path.join(output_dir, "rawCSV.zip"))
    # Split the CSV into smaller files based on the data we're interested in
    print("Processing the raw data csv file into smaller files...")
    raw_dataframe = pandas.read_csv(os.path.join(output_dir, "35100178.csv"))


# Test code below
if __name__ == "__main__":
    nb_crime_scrape()
# Python Standard Library Imports
import json
import os
import shutil
import zipfile
import pandas
import sys

import traceback

# External Dependency Imports
import urllib3
from alive_progress import alive_bar

# Internal Dependency Imports
sys.dont_write_bytecode = True
sys.path.append("data_scraping/scraping_utilities")
from checkUps import checkup_output

#######################################################################################
#                                       Notes:                                        #
#######################################################################################

# Function to pull the data used on the New Brunswick Public Safety Crime Dashboard
def national_population_scrape():
    # Set up the http client to make requests to the Stat Can API
    http = urllib3.PoolManager()

    # Pull the metadata to get the date of the last data point
    try:
        metadata = http.request("POST", "https://www150.statcan.gc.ca/t1/wds/rest/getCubeMetadata", body=json.dumps([{"productId": 17100005}]), headers={"Content-Type": "application/json"})
        end_date = json.loads(metadata.data.decode("utf-8"))[0]["object"]["cubeEndDate"].replace("-", "")
    except:
        print("Error pulling metadata from the Stat Can API. Exiting...")
        traceback.print_exc()
        return

    # Check the output directory for the data
    output_dir, needed_files, existing_files = checkup_output(["nationalPopulationData"])
    # Check if the existing files are up to  date, exit if they are
    for file in existing_files:
        if file.split("_")[0] == end_date and "nationalPopulationData" in file.split("_")[1]:
            print("Population data is up to date. Exiting...")
            return
        
    # Check existing files to see if there's a CSV that we need to delete
    if len(existing_files) > 0:
        for file in existing_files:
            if "nationalPopulationData" in file.split("_")[1]:
                existing_file = os.path.join(output_dir, file)
                previous_year = int(file.split("_")[0][:4])
                print(f"Found existing file {file} in the output directory. Appending data from {previous_year} onward...")
                break
    else:
        print("No existing file found, generating data from 2007 onward...")
        previous_year = 2007
        existing_file = None

    # If the data is not up to date, pull the data
    try:
        response = http.request("GET",  "https://www150.statcan.gc.ca/t1/wds/rest/getFullTableDownloadCSV/17100005/en")
        response = json.loads(response.data.decode("utf-8"))
        # Download the zip file from the url in the response
        with http.request('GET', response["object"], preload_content=False) as data_response, open(os.path.join(output_dir, "rawCSV.zip"), 'wb') as out_file:       
            print("Downloading raw data csv zip file. This may take a while...")
            shutil.copyfileobj(data_response, out_file)
    except:
        print("Error downloading the raw data csv zip file. Exiting...")
        return
    # Release the connection stream
    data_response.release_conn()
    # Unzip the raw csv, without extracting the metadata
    print("Extracting the raw data csv file from the downloaded zip file...")
    with zipfile.ZipFile(os.path.join(output_dir, "rawCSV.zip"), "r") as zip_object:
        zip_object.extract("17100005.csv", output_dir)

    # Clean out data that is out of scope (date, and non-substance related data)
    cleaned = [row for row in data_generator(os.path.join(output_dir, f"17100005.csv"), previous_year)]
    cleaned_dataframe = pandas.DataFrame(cleaned)
    # Write the cleaned data to a CSV, or append to the existing csv if it exists
    if existing_file:
        cleaned_dataframe.to_csv(existing_file, mode="a", header=False, index=False)
        os.rename(existing_file, os.path.join(output_dir, f"{end_date}_nationalPopulationData.csv"))
    else:
        cleaned_dataframe.to_csv(os.path.join(output_dir, f"{end_date}_nationalPopulationData.csv"), index=False)

    # Clean up the zip file and the raw csv
    print("Cleaning up the raw data files...")
    os.remove(os.path.join(output_dir, "rawCSV.zip"))
    os.remove(os.path.join(output_dir, "17100005.csv"))
    print("Finished extracting and cleaning the data from the New Brunswick Public Safety Crime Dashboard!")


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
        # Open the CSV (still in chunks), skipping to the first row with data from target year
        reader = pandas.read_csv(file, chunksize=10000, skiprows=range(1, first_year_row))
        # Iterate over each row, chunk by chunk and yield each row that's relevant
        chunk_num = 1
        for chunk in reader:
            with alive_bar(len(chunk), title=f"Iterating over data chunk {chunk_num}") as bar:
                for index, row in chunk.iterrows():
                    if row["Gender"] == "Total - gender" and row["Age group"] == "All ages":
                        yield row
                    bar()
            chunk_num += 1
    except KeyboardInterrupt:
        return
    except Exception as e:
        print(e)

# Test code below
if __name__ == "__main__":
    national_population_scrape()
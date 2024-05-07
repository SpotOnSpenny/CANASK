# Python Standard Library Imports
import sys
import os
import pandas
from datetime import datetime

# External Dependency Imports
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
from sodapy import Socrata

# Internal Dependency Imports
sys.dont_write_bytecode = True
sys.path.append("data_scraping/scraping_utilities")
from driver import start_driver

#######################################################################################
#                                       Notes:                                        #
# This file contains the data scraping code for the Manitoba Substance Harms          #
# Surveillance Report, found at https://manitoba.ca/mh/srh-public-report.html. It's   #
# worth noting that this report actually has 4 separate data sources, some of which   #
# we can access and others that we cannot. These sources are:                         #
#   - Office of the Chief Medical Examiner ( No Access )                              #
#   - Hospital Data: DAD and EDIS ( No Access )                                       #
#   - EMS Data ( Accessible! )                                                        #
#   - Take Home Naloxone Kit Distribution ( No Access )                               #
#                                                                                     #
# The data that we can access may change as time goes on, but for now we only have    #
# access to the EMS data. We can further investigate hostpital and OCME data which    #
# may be available with a SODA App Token, but will need to look into that later. It   #
# also may be worth noting that the Sodapy library used to access the SODA API is     #
# no longer maintained. It may be worth looking into other options to access this     #
# data to future proof it, but it seems like it will work for now. The Pypi page for  #
# the package is https://pypi.org/project/sodapy/ so we can look there for issues we  #
# can expect to run into using it. The official SODA API documentation still          #
# recomends using the library though, so for now, eso si que es!                      #
#                                                                                     #
# The EMS data is available to us in the form of an API which is awesome, as it makes #
# things easy to access in a format that we want. The difficult part is setting the   #
# limit to the number of rows that we want to access, and then consolidating the new  #
# data with any existing data. To do so, we'll collect a few pieces of information.   #
# we'll first access the data sources directly, and then make note of the "rows" that #
# the data set has which are displayed on that page. We'll then request all of the    #
# data no matter what, and replace the existing data if there is any. This data is    #
# also updated daily, which will need to be accounted for in some sort of scraping    #
# schdeule/protocol. Originally, the thought was to request only the new data, but    #
# Pandas was being difficult in comparing the dataframes, and since it's just an API  #
# call here, it's much easier just to request all of the data and remove the old file.#
#######################################################################################

# Function to scrape the EMS data used on the Manitoba Substance Harms Surveillance Report
def mn_ems_scrape(driver):
    # Instantiate a list of URLs that are the two EMS data sources for this dashboard
    ems_sources = [
        # Substance Use Patient data, each row = 1 patient and 1 substance    
        {"source": "mbSubstanceUsePatientData",
        "url": "https://data.winnipeg.ca/Fire-and-Paramedic-Service/Substance-Use/6x82-bz5y/about_data",
        "api_endpoint": "6x82-bz5y"},
        # Narcan Administration Data, each row = 1 patient
        {"source": "mbNarcanAdministrationData", 
        "url": "https://data.winnipeg.ca/Fire-and-Paramedic-Service/Narcan-Administrations/qd6b-q49i/about_data",
        "api_endpoint": "qd6b-q49i"}
    ]
    # Instantiate the SODA API client
    client = Socrata("data.winnipeg.ca", None)
    # Loop through the URLs to collect the data
    for source in ems_sources:
        # Go to the URL
        driver.get(source["url"])
        # Wait for the page to load, and get the number of rows when it does
        try:
            rows = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//dt[contains(text(), 'Rows')]/following-sibling::dd"))).text
            rows = int(float(rows.replace("K", "")) * 1000)
        except TimeoutException:
            print(f"Timed out waiting for {source['source']} to load.")
            quit(1)
        # Check if there's existing output directory
        output_dir = os.path.join(os.getcwd(), "output")
        # Instantiate a "limit" variable to determine how many rows to request from the API
        limit = None
        # If there's not, then we need to request all data
        if not os.path.exists(output_dir):
            os.mkdir(output_dir)
            print(f"No output folder found, requesting all {rows} rows from API for {source['source']}...")
            limit = rows + 100 # Add 100 to the limit to be safe, in case it's 120 rows that are different or something
        # Otherwise, check if there's an existing file for this source
        else:
            for file in os.listdir(output_dir):
                # If there is an existing file, then compare the rows to see how many new rows to request
                if source["source"] in file:
                    existing_file = file
                    limit = rows + 100 # Add 100 to the limit to be safe, in case it's 120 rows that are different or something
                    print(f"Existing data found for {source['source']} file. Requesting newest {rows} rows from API...")
            # If limit is still none, then we need to request all the data, because there was no existing file in the output directory
            if limit is None:
                print(f"Output folder exists, but contians no {source['source']} file. Requesting all {rows} rows from API...")
                limit = rows + 100 # Add 100 to the limit to be safe, in case it's 120 rows that are different or something
        # Request the data from the SODA API
        try:
            data = client.get(source["api_endpoint"], limit=limit)
        except Exception as e:
            print(f"Error requesting data from {source['source']} API: {e}")
            quit(1)
        # Convert the data to a pandas dataframe
        data = pandas.DataFrame.from_records(data)
        # Collect the current date for filename
        date = datetime.now().strftime("%Y-%m-%d").replace("-", "")
        # Remove the existing file if there is one
        if "existing_file" in locals():
            os.remove(os.path.join(output_dir, existing_file))
            print(f"Old {source['source']} data removed from output directory!")
        # Save the data to a csv file in the output directory
        data.to_csv(os.path.join(output_dir, f"{date}_{source['source']}.csv"), index=False)
        print(f"{source['source']} data saved to output directory!")


# Test code below
if __name__ == "__main__":
    driver = start_driver(headless=True)
    mn_ems_scrape(driver)
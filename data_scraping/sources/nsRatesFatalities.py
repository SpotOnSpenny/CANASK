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
from checkUps import checkup_output


#######################################################################################
#                                       Notes:                                        #
# This file contains the required code to scrape data from the Nova Scotia Numbers    #
# and Rates of Substance-Related Fatalities Dashboard. Like the mn Substance Harms    #
# dashboard, this site is just an open data source, that we can also use SODA Py to   #
# extract the data into a usable format. Again similar to the mnSubstanceHarms file   #
# we can load the web page and see how many rows there are to see how many we should  #
# request. We should also add an extra 100 here for good measure.                     #
#                                                                                     #
# The data provided by this page is all aggregate data, BUT there is A LOT of it. We  #
# should be able to use it to compare to other data sources, or at the very least to  #
# create a more centralized Canada wide aggregate. We may also be able to use it as   #
# an example of what types of aggregates we should be looking for/generating in other #
# provinces/Canada-wide.                                                              #
#######################################################################################

# Function to scrape the data used on the Nova Scotia Numbers and Rates of Substance-Related Fatalities Dashboard
def ns_ratesfatalities_scrape(driver):
    # Instantiate a list of URLs and data sources in case we want to add another later on
    ems_sources = [
        # Substance Use Patient data, each row = 1 patient and 1 substance    
        {"source": "nsRatesFatalities",
        "url": "https://data.novascotia.ca/Health-and-Wellness/Numbers-and-rates-of-substance-related-fatalities-/iu6y-z4n3/about_data",
        "api_endpoint": "iu6y-z4n3"}
    ]
    # Check for the output directory and needed files
    output_dir, needed_files, existing_files = checkup_output([source["source"] for source in ems_sources])
    # Instantiate the SODA API client
    client = Socrata("data.novascotia.ca", None)
    # Loop through the URLs to collect the data
    for source in ems_sources:
        # Go to the URL
        driver.get(source["url"])
        # Wait for the page to load, and get the number of rows when it does
        try:
            rows = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//dt[contains(text(), 'Rows')]/following-sibling::dd"))).text
            # At the time of writing, this page only has 5000 some odd rows, and it is displayed as "5,xxx", so we do not need to remove the K and *1000
            if "K" in rows:
                limit = int(float(rows.replace("K", "")) * 1000) + 100 # Add 100 to the limit to be safe, in case it's 120 rows that are different or something
            else:
                limit = int(rows.replace(",", "")) + 100 # Add 100 to the limit to be safe, in case it's 120 rows that are different or something
        except TimeoutException:
            print(f"Timed out waiting for {source['source']} to load.")
            quit(1)
        # Check if there's an existing file for this source
        existing_file = None
        if source["source"] not in needed_files:
            existing_file = [file for file in existing_files if source["source"] in file][0]
            print(f"Existing data found for {source['source']} file. Replacing with new data containing {rows} rows...")
        # If limit is still none, then we need to request all the data, because there was no existing file in the output directory
        if existing_file is None:
            print(f"Output folder exists, but contians no {source['source']} file. Requesting all {rows} rows from API...")
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
        if existing_file is not None:
            os.remove(os.path.join(output_dir, existing_file))
            print(f"Old {source['source']} data removed from output directory!")
        # Save the data to a csv file in the output directory
        data.to_csv(os.path.join(output_dir, f"{date}_{source['source']}.csv"), index=False)
        print(f"{source['source']} data saved to output directory!")


# Test code below
if __name__ == "__main__":
    driver = start_driver(headless=True)
    ns_ratesfatalities_scrape(driver)
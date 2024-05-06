# Python Standard Library Imports
import sys
import pandas
import os
import datetime

# External Dependency Imports
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
from alive_progress import alive_bar

# Internal Dependency Imports
sys.path.append("data_scraping/scraping_utilities")
from driver import start_driver

######################################################################################
#                                      Notes:                                        #
# This script is used to scrape the results table from the BC Drug sense website,    #
# which can be found at https://bccsu-drugsense.onrender.com/. This data is          #
# just in a plain HTML table and so it's easy enough to start up a selenium          #
# instance, click the "results" tab, and then scrape each row of the table. The      #
# structure of the scraped data has been formatted to match the existing structure   #
# of provided monthly DAS data, with the exception of the "Drugs" column, which on   #
# that spreadsheet is 12 separate columns (which is really not very efficient!).     #
# Also, the "visit date" column on the website table has been matched to the         #
# "received date" column on the DAS data, as it seems logical that that would be     #
# the date that the drug was "discovered" in both sources. There are also additional #
# columns on the website table which are NOT present in the DAS data, so it is not   #
# a one for one match. Instead we'll need to process these later on and decide what  #
# we'd like to do to consolidate all this data from these different sources.         #
######################################################################################

# Function to do the actual scraping
def bc_drugsense_scrape(driver):
    # Go to the website
    driver.get("https://bccsu-drugsense.onrender.com/")
    # Click the results tab and wait for it to load
    try:
        results_tab = WebDriverWait(driver, 15).until(expected_conditions.presence_of_element_located((By.XPATH, "//a[text()[contains(.,'Results Table')]]")))
        results_tab.click()
        WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//table[contains(@class, 'cell-table')]/tbody/tr")))
    except TimeoutException:
        print("Timed out waiting for page to load")
    # Collect the current page of the table, and the total number of table pages as elements
    table_pages = int(WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(@class, 'last-page')]"))).text)
    current_page = int(WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(@class, 'current-page-container')]"))).text)
    # Instantiate a dataframe that matches the DAS data structure
    drug_data = pandas.DataFrame(columns=["Visit Date", "City", "Health Authority", "Site", "Expected Drug", "Category", "Colour", "Description", "Fentanyl Strip", "Benzo Strip", "Spectrometer"])
    # Loop through each page of the table
    with alive_bar(len(list(range(0, table_pages))), theme="smooth", title="Collecting pagainated table data...") as bar:
        while current_page <= table_pages:
            # Find each table row
            for row in driver.find_elements(By.XPATH, "//table[contains(@class, 'cell-table')]/tbody/tr"):
                # if the row contains a "th" element, it's a header row, so skip it
                if row.find_elements(By.XPATH, "th"):
                    continue
                # Otherwise, extract the data from each cell into a dictionary to append to the dataframe
                data = {
                    "Visit Date": row.find_elements(By.XPATH, "td")[0].text,
                    "City": row.find_elements(By.XPATH, "td")[1].text,
                    "Health Authority": row.find_elements(By.XPATH, "td")[2].text,
                    "Site": row.find_elements(By.XPATH, "td")[3].text,
                    "Expected Drug": row.find_elements(By.XPATH, "td")[4].text,
                    "Category": row.find_elements(By.XPATH, "td")[5].text,
                    "Colour": row.find_elements(By.XPATH, "td")[6].text,
                    "Description": row.find_elements(By.XPATH, "td")[7].text,
                    "Fentanyl Strip": row.find_elements(By.XPATH, "td")[8].text,
                    "Benzo Strip": row.find_elements(By.XPATH, "td")[9].text,
                    "Spectrometer": row.find_elements(By.XPATH, "td")[10].text
                }
                drug_data = pandas.concat([drug_data, pandas.DataFrame(data, index=[0])], ignore_index=True)
            bar()
            # If there is another page, click the next button
            if current_page != table_pages:
                next_button = driver.find_element(By.XPATH, "//button[contains(@class, 'next-page')]")
                next_button.click()
                # Wait until the current page number does not match the previous page number
                WebDriverWait(driver, 10).until(lambda driver: int(driver.find_element(By.XPATH, "//div[contains(@class, 'current-page-container')]").text) != current_page)
                current_page = int(driver.find_element(By.XPATH, "//div[contains(@class, 'current-page-container')]").text)
            # Otherwise, break the loop
            else:
                current_page += 1
    # Check to ensure an output directory exists
    output_dir = os.path.join(os.getcwd(), "output")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    # Save the dataframe to a csv file
    date = datetime.datetime.now().strftime("%Y-%m-%d")
    drug_data.to_csv(os.path.join(output_dir, f"{date}_bcDrugSense.csv"), index=False)

# Test code below
if __name__ == "__main__":
    driver = start_driver(headless=False)
    print("driver started!")
    bc_drugsense_scrape(driver)
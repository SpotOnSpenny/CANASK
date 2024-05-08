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
sys.dont_write_bytecode = True
sys.path.append("data_scraping/scraping_utilities")
from driver import start_driver
from checkUps import checkup_output

#######################################################################################
#                                       Notes:                                        #
# This script is used to scrape the results table from the BC Drug sense website,     #
# which can be found at https://bccsu-drugsense.onrender.com/. This data is           #
# just in a plain HTML table and so it's easy enough to start up a selenium           #
# instance, click the "results" tab, and then scrape each row of the table. The       #
# structure of the scraped data has been formatted to match the existing structure    #
# of provided monthly DAS data, with the exception of the "spectrometer" column,      #
# that is 12 separate columns on the DAS sheet (which is really not very efficient!). #
# Also, the "visit date" column on the website table has been matched to the          #
# "received date" column on the DAS data, as it seems logical that that would be      #
# the date that the drug was "discovered" in both sources. There are also additional  #
# columns on the website table which are NOT present in the DAS data, so it is not    #
# a one for one match. Instead we'll need to process these later on and decide what   #
# we'd like to do to consolidate all this data from these different sources. All of   #
# this consolidating and matching will take place in another script that will process #
# all the data from different sources at once. TODO: add that script here when done.  #                         
#                                                                                     #   
# Before running, the script will also check to see if there's an existing file for   #
# the bcDrugSense data in the output directory. If there is, it will scrape data      #
# from the website until it finds that the last 5 rows of scraped data match the      #
# first 5 rows of the existing data. This way, we don't re-scrape data we already     #
# have. If there is no existing file, then the script will scrape all the data from   #
# the table on the website.                                                           #
#                                                                                     #
# I'm unsure how relevant this may be to whoever may read this in the future, but we  #
# also have disabled pycache with the sys.dont_write_bytecode = True line at the top  #
# under imports. I've done this because it was caching the value for the headless     #
# driver variable, which kind of takes away from the purpose of having it at all, and #
# made it more difficult to test by swapping the headless value around. Feel free to  #
# re-enable it in the future if you find it's needed by commenting that line out!     #
#######################################################################################

# Function to do the actual scraping
def bc_drugsense_scrape(driver):
    # Check for any output file in the output directory
    output_dir, needed_files, existing_files = checkup_output(["bcDrugSense"])
    # If bcDrugSense is in the needed list, scrape all the data
    if "bcDrugSense" in needed_files:
        print("No existing data found. Scraping all data from BCCSU DrugSense website...")
        scrape_all = True
    # Otherwise, check the existing data to see how much to scrape
    else:
        # If we are not scraping all the data, we need to pull the first 5 rows of the table so we can check it against the website
        print("A bcDrugSense file was found in the output folder! Scraping new data from the BCCSU DrugSense website...")
        # Also load the csv so that we can join the new data and old data together
        existing_data = pandas.read_csv(os.path.join(output_dir, existing_files[0]))
        data_to_check = existing_data.head(5).fillna("")
        scrape_all = False
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
                # If we are only scraping new data, compare the tail of this data to the head of the existing data
                if not scrape_all:
                    if data_to_check.equals(drug_data.tail(5).reset_index(drop=True)):
                        print("Data matches existing data, stopping the scrape...")
                        # Remove the last 5 rows that match
                        drug_data = drug_data[:-5]
                        # Add the new data to the existing data
                        drug_data = pandas.concat([drug_data, existing_data], ignore_index=True)
                        # Break the for loop and move to the else statement below to break the while loop and move to save data
                        current_page = table_pages
                        break
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
    date = datetime.datetime.now().strftime("%Y-%m-%d").replace("-", "")
    drug_data.to_csv(os.path.join(output_dir, f"{date}_bcDrugSense.csv"), index=False)
    print("Data scraped and saved to csv file in the output directory!")

# Test code below
if __name__ == "__main__":
    driver = start_driver(headless=True)
    print("driver started!")
    bc_drugsense_scrape(driver)
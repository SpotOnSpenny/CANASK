# Python Standard Library Imports
import sys
import os
import zipfile
import datetime

# External Dependency Imports
import pandas
import pantab
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

# Internal Dependency Imports
sys.dont_write_bytecode = True
sys.path.append("data_scraping/scraping_utilities")
from checkUps import checkup_output
from driver import start_driver


#######################################################################################
#                                       Notes:                                        #
#######################################################################################

# Function to pull the data from the Opioid Indicator Dashboard
def on_indicator_scrape(driver):
    # Instantiate any needed variables for the function
    existing_file = None
    # Check if there's anything in the output directory
    output_dir, needed_files, existing_files = checkup_output(["onOpioidIndicator"])
    # Check the last updated date on the tableau page
    driver.get("https://public.tableau.com/app/profile/odprn/viz/OpioidToolHarmReductionDashboard/Story1")
    details_container = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(@data-testid, 'VizDetails-container')]")))
    date = details_container.find_element(By.XPATH, "//strong[contains(text(), 'Updated')]/..").text.split(":")[1].strip()
    date = datetime.datetime.strptime(date, "%b %d, %Y").strftime("%Y%m%d")
    # Check if the last updated date is the same as the the date on the existing files
    if len(existing_files) > 0:
        for file in existing_files:
            if date in file:
                print("Data is already up to date. Exiting...")
                quit(0)
            else:
                existing_data = file
                print("Data was found, but is not up to date. Downloading new data from the source.")
    
    # Download the workbook as a zip file TODO Figure out whats blocking the element from being clicked
    WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//button[contains(@data-tooltip-content, 'Download')]"))).click()
    print("Clicked the button!")
    quit()
    # Extract everything from the zip file
    # Iterate through extracted files and convert and convert them to dataframes
    data = pantab.frames_from_hyper(os.path.join(output_dir, "Data/Opioid Tool/Opioid Tool 2023 Opioid-Related Harm Age Sex.hyper"))
    # Save all the dataframes as CSV files
    for index, value in enumerate(data.values()):
        value.to_csv(os.path.join(output_dir, f"{date}_onOpioidIndicator{'_' + index if index != 0 else ''}.csv"), index=False)
    # Cleanup the extracted files/zip file

# Test code below
if __name__ == "__main__":
    driver = start_driver(headless=True)
    on_indicator_scrape(driver)

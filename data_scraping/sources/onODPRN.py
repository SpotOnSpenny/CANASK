# Python Standard Library Dependencies
import sys
import os
import urllib3
import shutil
import zipfile
import datetime

# External Dependency Imports
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait

# Internal Dependency Imports
sys.dont_write_bytecode = True
sys.path.append("data_scraping/scraping_utilities")
from driver import start_driver
from checkUps import checkup_output



#######################################################################################
#                                       Notes:                                        #
#######################################################################################

def scrape_national_dashboard(driver):
    # Instantiate things we need and check to see if there's already a file in the output directory
    dataframes = []
    http = urllib3.PoolManager()
    output_dir, needed_files, existing_files = checkup_output(["onODPRN"])
    if existing_files != []:
        existing_file_updated = int(existing_files[0].split("_")[0])

    # Load the Coroners Report page and get to data, also check the date of the report against existing scrapes to see if we need to run 
    try:
        driver.get("https://odprn.ca/occ-opioid-and-suspect-drug-related-death-data/")
        monthly_data = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//h3/*[contains(text(), 'Monthly Data')]/../..")))
        download_button = monthly_data.find_element(By.XPATH, ".//a")
        download_link = download_button.get_attribute("href")
        last_updated = download_button.text
    except:
        print("Couldn't locate the download button")
        return

    #Check to see if we need to download the file using the text of the download button
    month, year = last_updated.split(" ")
    month = datetime.datetime.strptime(month.lower(), '%b').month
    last_updated = int(f"{year}{month}01")
    if existing_files != [] and last_updated <= existing_file_updated:
        print("No new data available")
        return
    else:
        print("New data available")
    
    raw_data = os.path.join(output_dir, f"{last_updated}_onODPRN.xlsx")
    with http.request('GET', download_link, preload_content=False) as data_response, open(raw_data, 'wb') as out_file:       
        print("Downloading the PDF report...")
        shutil.copyfileobj(data_response, out_file)
        print("Download complete")
    data_response.release_conn()

    # Clean up the zip and old files that have been updated
    if existing_files != []:
        print("removing old files")
        os.remove(os.path.join(output_dir, existing_files[0]))
    return

# Test code below
if __name__ == '__main__':
    driver = start_driver(headless=True, download_dir=True)
    scrape_national_dashboard(driver)
    driver.quit()
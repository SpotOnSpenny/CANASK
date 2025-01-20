# Python Standard Library Dependencies
import sys
import os
import urllib3
import shutil
import zipfile

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
#                                        Notes:                                       #
#######################################################################################

def scrape_national_dashboard(driver):
    # Instantiate things we need and check to see if there's already a file in the output directory
    dataframes = []
    http = urllib3.PoolManager()
    file_updated = False
    output_dir, needed_files, existing_files = checkup_output(["nationalHealthInfobase"])
    if existing_files != []:
        existing_file_updated = int(existing_files[0].split("_")[0])

    # Load the Coroners Report page and get to data, also check the date of the report against existing scrapes to see if we need to run 
    try:
        driver.get("https://health-infobase.canada.ca/substance-related-harms/opioids-stimulants/")
        download_link = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, '//a[contains(@class, "dataDownload")]'))).get_attribute("href")
    except:
        print("Couldn't locate the download button")
        return
    
    raw_data_zip = os.path.join(output_dir, "zipped_data.zip")
    with http.request('GET', download_link, preload_content=False) as data_response, open(raw_data_zip, 'wb') as out_file:       
        print("Downloading the PDF report...")
        shutil.copyfileobj(data_response, out_file)
        print("Download complete")
    data_response.release_conn()

    # check the contents of the zip file
    print("Checking the contents of the zip file...")
    with zipfile.ZipFile(raw_data_zip, 'r') as zip_ref:
        zip_ref.printdir()
        for file in zip_ref.namelist():
            if ".csv" in file:
                year, month, day, hour, minute, second = zip_ref.getinfo(file).date_time
                new_last_updated = int(f"{year}{month}{day}")
                if existing_files == []:
                    print(f"Extracting {file}...")
                    with zip_ref.open(file) as data_file, open(os.path.join(output_dir, f"{new_last_updated}_nationalHealthInfobase.csv"), 'wb') as out_file:
                        shutil.copyfileobj(data_file, out_file)
                    print(f"Extraction of {file} complete")
                elif new_last_updated > existing_file_updated:
                    print(f"Extracting {file}...")
                    with zip_ref.open(file) as data_file, open(os.path.join(output_dir, f"{new_last_updated}_nationalHealthInfobase.csv"), 'wb') as out_file:
                        shutil.copyfileobj(data_file, out_file)
                    print(f"Extraction of {file} complete")
                    file_updated = True
                else:
                    print(f"{file} is already up to date")
            
    # Clean up the zip and old files that have been updated
    os.remove(raw_data_zip)
    print("Existing files updated: ", file_updated)    
    if existing_files != [] and file_updated:
        print("removing old files")
        os.remove(os.path.join(output_dir, existing_files[0]))
    return

# Test code below
if __name__ == '__main__':
    driver = start_driver(headless=True, download_dir=True)
    scrape_national_dashboard(driver)
    driver.quit()
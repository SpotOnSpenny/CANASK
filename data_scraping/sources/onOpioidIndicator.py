# Python Standard Library Imports
import sys
import os
import zipfile
import datetime
import time
import shutil

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
# Turns out, Tableau is not so bad! So long as the data can be downloaded in a tableau#
# workbook, it can essentially just be saved as a zip and then extracted from there   #
# in .hyper files, which contains the data points that we need. Luckily, the pantab   #
# library can read these files and convert them into dataframes, which can then be    #
# saved as CSV files. That's all that's happening here!                               #
#                                                                                     #
# Theoretically, this could be used for any tableau workbook as well, so if we run    #
# into more of these then we'll be able to extend this function to handle those as    #
# well.                                                                               #
#######################################################################################

# Function to pull the data from the Opioid Indicator Dashboard
def on_indicator_scrape(driver):
    # Instantiate any needed variables for the function
    existing_file = None
    remove_these = []
    # Check if there's anything in the output directory
    output_dir, needed_files, existing_files = checkup_output(["onOpioidIndicator"])
    # Check the last updated date on the tableau page
    driver.get("https://public.tableau.com/app/profile/odprn/viz/OpioidToolOpioid-RelatedHarmDashboard/Story1")
    details_container = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(@data-testid, 'VizDetails-container')]")))
    date = details_container.find_element(By.XPATH, "//strong[contains(text(), 'Updated')]/..").text.split(":")[1].strip()
    date = datetime.datetime.strptime(date, "%b %d, %Y").strftime("%Y%m%d")
    title = driver.find_element(By.XPATH, "//div[contains(@class, 'title')]/h1").text

    # Check if the last updated date is the same as the the date on the existing files
    if len(existing_files) > 0:
        for file in existing_files:
            if date in file:
                print("Data is already up to date. Exiting...")
                quit(0)
            else:
                existing_data = file
                print("Data was found, but is not up to date. Downloading new data from the source.")

    # If the cookies popup is present, click the accept button
    try:
        WebDriverWait(driver, 10).until(expected_conditions.element_to_be_clickable((By.XPATH, "//button[@id='onetrust-accept-btn-handler']"))).click()
    except Exception as e:
        print(e)

    # Download the workbook as a zip file
    # Click the download button to open up the options
    time.sleep(5)
    WebDriverWait(driver, 20).until(expected_conditions.presence_of_element_located((By.XPATH, "//button[contains(@data-tooltip-content, 'Download')]"))).click()
    # Switch into the iframe to click the actual download button
    iframe = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//iframe[contains(@title, 'Data Visualization')]")))
    driver.switch_to.frame(iframe)
    # Click the download button within the iframe
    WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//button[contains(text(), 'Tableau Workbook')]"))).click()
    WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//button[contains(@data-tb-test-id, 'DownloadAsVersionDownload-Button')]"))).click()

    # Wait for download to complete
    downloaded_file = os.path.join(output_dir, f"{title}.twbx")
    while not os.path.exists(downloaded_file):
        time.sleep(1)
    
    # Convert the download to a zip file so that we can extract the data
    os.rename(downloaded_file, os.path.join(output_dir, f"{date}_onOpioidIndicator.zip"))
    downloaded_file = os.path.join(output_dir, f"{date}_onOpioidIndicator.zip")
    remove_these.append(downloaded_file)

    # Extract everything from the zip file
    with zipfile.ZipFile(downloaded_file, "r") as zip_ref:
        zip_ref.extractall(output_dir)

    # Ensure we're in the folder with the data
    extracted_list = os.listdir(os.path.join(output_dir, "Data"))
    check_next = True
    current_dir = os.path.join(output_dir, "Data")
    remove_these.append(current_dir)
    while check_next:
        if not any(os.path.isdir(f"{current_dir}/{file}") for file in extracted_list):
            check_next = False
        else:
            for file in extracted_list:
                if os.path.isdir(f"{current_dir}/{file}"):
                    extracted_list = os.listdir(f"{current_dir}/{file}")
                    current_dir = f"{current_dir}/{file}"
                    break

    # Iterate over the extracted files and create CSVs from the .hyper files
    file_count = 1
    for file in extracted_list:
        data = pantab.frames_from_hyper(os.path.join(f"{current_dir}/{file}"))
        # Save all the dataframes as CSV files
        for value in data.values():
            value.to_csv(os.path.join(output_dir, f"{date}_onOpioidIndicator{'_' + str(file_count)}.csv"), index=False)
            file_count += 1

    # Cleanup the extracted files/zip file
    for file in remove_these:
        if os.path.isdir(file):
            shutil.rmtree(file)
        else:
            os.remove(file)
    # Check for any other files in the output that aren't output
    for file in os.listdir(output_dir):
        if not file[0].isnumeric():
            os.remove(os.path.join(output_dir, file))
    # Check the current working directory for the hyperd.log and remove it if it exists
    if os.path.exists(os.path.join(os.getcwd(), "hyperd.log")):
        os.remove(os.path.join(os.getcwd(), "hyperd.log"))

# Test code below
if __name__ == "__main__":
    driver = start_driver(headless=True, download_dir=True)
    on_indicator_scrape(driver)
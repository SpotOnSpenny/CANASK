# Python Standard Library Dependencies
import sys
import os
import urllib3
import datetime
import shutil
import pandas

# External Dependency Imports
import openpyxl
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

# TODO we need to remove strict mode from the downloaded file

def scrape_national_dashboard(driver, file_in_dir = False, final_data_path = None):
    # Instantiate things we need and check to see if there's already a file in the output directory
    dataframes = []
    http = urllib3.PoolManager()
    output_dir, needed_files, existing_files = checkup_output(["onODPRN"])
    if existing_files != []:
        existing_file_updated = int(existing_files[0].split("_")[0])

    raw_data_path = os.path.join(output_dir, "ontario_data.xlsx")

    # When a file is already in the output dir, final_data_path is passed in as a bare
    # filename; resolve it against output_dir so the cleaned file is written there too.
    if file_in_dir and final_data_path is not None:
        final_data_path = os.path.join(output_dir, os.path.basename(final_data_path))

    # Load the Coroners Report page and get to data, also check the date of the report against existing scrapes to see if we need to run 
    if not file_in_dir:
        try:
            driver.get("https://odprn.ca/occ-opioid-and-suspect-drug-related-death-data/")
            monthly_data = WebDriverWait(driver, 1000).until(expected_conditions.presence_of_element_located((By.XPATH, "//h3/*[contains(text(), 'Monthly Data')]/../..")))
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
    
        final_data_path = os.path.join(output_dir, f"{last_updated}_onODPRN.xlsx")
        with http.request('GET', download_link, preload_content=False) as response, open(raw_data_path, 'wb') as out_file:
            out_file.write(response.data)
            response.release_conn()

    # Clean up the data and write it to the final file
    pandas.set_option('future.no_silent_downcasting', True)
    clean_data = {}
    data = pandas.read_excel(raw_data_path, engine="calamine", sheet_name=None) # excel file is in strict mode, so we need to use calamine
    for sheet in data.keys():
        if sheet == "Figure" and sheet != "Data Notes":
            pass
        if sheet == "Provincial Substance Toxicity":
            clean_data[sheet] = data[sheet].dropna(how="all")
            clean_data[sheet] = clean_data[sheet].iloc[3: -3]
            clean_data[sheet].iloc[:, 0] = clean_data[sheet].iloc[:, 0].ffill()
            clean_data[sheet].columns = ["year", "month", "opioid confirmed", "opioid probable", "stimulant", "other drug"]
        if sheet == "PHU Confirmed & Probable":
            clean_data[sheet] = data[sheet].dropna(how="all")
            clean_data[sheet] = clean_data[sheet].iloc[1: -3]
            clean_data[sheet] = clean_data[sheet].drop(clean_data[sheet].index[1]).drop(clean_data[sheet].index[3])
            clean_data[sheet] = clean_data[sheet].transpose().reset_index(drop=True)
            clean_data[sheet].columns = clean_data[sheet].iloc[0]
            clean_data[sheet] = clean_data[sheet].drop(clean_data[sheet].index[0])
            clean_data[sheet].iloc[:, 0] = clean_data[sheet].iloc[:, 0].ffill()
            clean_data[sheet].iloc[:, 1] = clean_data[sheet].iloc[:, 1].apply(lambda x: f"{x:02}")
            clean_data[sheet]["date"]= clean_data[sheet].apply(lambda row: f"{row.iloc[0]}{row.iloc[1]}", axis=1)
            clean_data[sheet] = clean_data[sheet].drop(clean_data[sheet].columns[0], axis=1)
            cols = clean_data[sheet].columns.tolist()
            cols = cols[-1:] + cols[:-1]
            clean_data[sheet] = clean_data[sheet][cols]

    print("writing to")
    print(final_data_path)
    print(clean_data)
    with pandas.ExcelWriter(final_data_path) as writer:
        for sheet in clean_data.keys():
            print(sheet)
            try:
                clean_data[sheet].to_excel(writer, sheet_name=sheet, index=False)
            except Exception as e:
                print(e)


    # Clean up the zip and old files that have been updated
    # if not file_in_dir:
    #     os.remove(raw_data_path)
    # if existing_files != []:
    #     print("removing old files")
    #     os.remove(os.path.join(output_dir, existing_files[0]))
    # return

#######################################################################################

# Test code below
if __name__ == '__main__':
    #driver = start_driver(headless=False, download_dir=True)
    driver = None
    scrape_national_dashboard(driver, file_in_dir = True, final_data_path = "20260619_20260501_onODPRN.xlsx")
    #driver.quit()
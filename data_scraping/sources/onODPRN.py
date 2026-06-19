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
from selenium.common.exceptions import (TimeoutException, NoSuchElementException,
                                         WebDriverException)

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
    # When re-cleaning an existing download (file_in_dir=True) no scrape runs to derive the output
    # filename, so the caller MUST supply final_data_path. Fail loudly here rather than letting it stay
    # None and surface as a cryptic pandas.ExcelWriter(None) error deep in the write step.
    if file_in_dir and final_data_path is None:
        raise ValueError("final_data_path is required when file_in_dir=True "
                         "(no scrape runs to derive the output filename).")
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
            monthly_data = WebDriverWait(driver, 30).until(expected_conditions.presence_of_element_located((By.XPATH, "//h3/*[contains(text(), 'Monthly Data')]/../..")))
            download_button = monthly_data.find_element(By.XPATH, ".//a")
            download_link = download_button.get_attribute("href")
            last_updated = download_button.text
        except (TimeoutException, NoSuchElementException, WebDriverException) as e:
            # Narrowed so an unrelated bug (or KeyboardInterrupt/SystemExit) isn't masked, and the real
            # exception is surfaced instead of a misleading "couldn't find the button". Re-raise so a
            # failed scrape fails loudly rather than silently returning None (which downstream cleaning
            # would read as "no data" and quietly produce nothing).
            print(f"onODPRN: failed to reach the Monthly Data download button "
                  f"({type(e).__name__}: {e})")
            raise

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

    print(f"onODPRN: writing {len(clean_data)} sheet(s) to {final_data_path}")
    # No per-sheet try/except: a failed sheet must abort the write, not silently emit a file that looks
    # valid but is missing sheets (downstream pull_data/cleaners would then quietly see fewer/no rows).
    with pandas.ExcelWriter(final_data_path) as writer:
        for sheet in clean_data.keys():
            clean_data[sheet].to_excel(writer, sheet_name=sheet, index=False)

    # Clean up the raw download and any superseded prior output -- only when we actually scraped a new
    # file (file_in_dir=True means we're re-cleaning an existing one, so there's nothing to discard and
    # the "old" file may be the very file we just rewrote).
    if not file_in_dir:
        if os.path.exists(raw_data_path):
            os.remove(raw_data_path)
        if existing_files != []:
            print(f"onODPRN: removing superseded file {existing_files[0]}")
            os.remove(os.path.join(output_dir, existing_files[0]))

#######################################################################################

# Test code below
if __name__ == '__main__':
    #driver = start_driver(headless=False, download_dir=True)
    driver = None
    scrape_national_dashboard(driver, file_in_dir = True, final_data_path = "20260619_20260501_onODPRN.xlsx")
    #driver.quit()
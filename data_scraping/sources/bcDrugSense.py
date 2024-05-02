# Python Standard Library Imports
import sys

# External Dependency Imports
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

# Internal Dependency Imports
sys.path.append("data_scraping/scraping_utilities")
from driver import start_driver

#####################################################################################
#                                      Notes:                                       #
# This script is used to scrape the results table from the BC Drug sense website,   #
# which can be found at https://bccsu-drugsense.onrender.com/. This data is         #
# just in a plain HTML table and so it's easy enough to start up a selenium         #
# instance, click the "results" tab, and then scrape each row of the table.         #
#####################################################################################

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
    print(results_tab.text)

# Test code below
if __name__ == "__main__":
    driver = start_driver()
    print("driver started!")
    bc_drugsense_scrape(driver)
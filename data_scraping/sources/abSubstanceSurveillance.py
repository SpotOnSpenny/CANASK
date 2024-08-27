# Python Standard Library Dependencies
import sys
import traceback
import time

# External Dependency Imports
import bs4
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, ElementClickInterceptedException

# Internal Dependency Imports
sys.dont_write_bytecode = True
sys.path.append("data_scraping/scraping_utilities")
from driver import start_driver

#######################################################################################
#                                        Notes:                                       #
#######################################################################################

def ab_substancesurveillance_scrape(driver):
    dataframes = []

    # Load the Coroners Report page and get to data, also check the date of the report against existing scrapes to see if we need to run 
    driver.get("https://healthanalytics.alberta.ca/SASVisualAnalytics/?reportUri=%2Freports%2Freports%2F1bbb695d-14b1-4346-b66e-d401a40f53e6&sectionIndex=0&sso_guest=true&reportViewOnly=true&reportContextBar=false&sas-welcome=false")
    WebDriverWait(driver, 150).until(expected_conditions.title_contains("Alberta substance use surveillance system"))
    iframe = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, '//iframe[@id="VANextLogon_iframe"]')))
    print("got the iframe")
    driver.switch_to.frame(iframe)
    print("switched to iframe") 
    date_text = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, '//*[contains(text(), "Updated")]'))).text
    print(date_text)
    pass


# Test code below
if __name__ == '__main__':
    try:
        driver = start_driver(headless=False)
        ab_substancesurveillance_scrape(driver)
        driver.quit()
    except Exception as e:
        traceback.print_exc()
        driver.quit()
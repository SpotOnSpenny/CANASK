# Python Standard Library Dependencies
import sys
import traceback
import time
import os
import base64

# External Dependency Imports
import bs4
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, ElementClickInterceptedException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

# Internal Dependency Imports
sys.dont_write_bytecode = True
sys.path.append("data_scraping/scraping_utilities")
from driver import start_driver
from checkUps import checkup_output

#######################################################################################
#                                        Notes:                                       #
#######################################################################################

def ab_substancesurveillance_scrape(driver):
    dataframes = []
    actions = ActionChains(driver)

    output_dir, needed_files, existing_files = checkup_output([])

    # Load the Coroners Report page and get to data, also check the date of the report against existing scrapes to see if we need to run 
    driver.get("https://healthanalytics.alberta.ca/SASVisualAnalytics/?reportUri=%2Freports%2Freports%2F1bbb695d-14b1-4346-b66e-d401a40f53e6&sectionIndex=0&sso_guest=true&reportViewOnly=true&reportContextBar=false&sas-welcome=false")
    WebDriverWait(driver, 150).until(expected_conditions.title_contains("Alberta substance use surveillance system"))
    iframe = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, '//iframe[@id="VANextLogon_iframe"]')))
    driver.switch_to.frame(iframe)
    date_text = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, '//*[contains(text(), "Updated")]'))).text
    # TODO Compare this to the last scrape and return if we don't need to run anything

    # Grab the menu bar so that we can iterate over the tabs
    menu_bar = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, '//*[@id="appSplitView-reportPanelView-0-sectionTabBar--header-head"]')))
    menu_tabs = menu_bar.find_elements(By.XPATH, './/div[@role="tab"]')
    if len(menu_tabs) != 17:
        print("Error: Unexpected number of tabs found")
        return
    expected_tabs = [
        "Unique AHS ODP clients", "AHS ODP sample testing outcomes", "Acute substance deaths overview", "Acute substance related deaths by age and sex",
        "Polysubstance use among acute substance related deaths", "Location of opioid deaths", "Medical history - opioid deaths",
        "Emergency Department & hospitalizations related to substance use", "EMS responses to opioid related events",
        "Dispensing -Chronic pain management", "Dispensing - Opioid dependency therapy", "Community Naloxone program",
        "Supervised consumption services utilization", "Supervised consumption services adverse events attended to"
    ]
    for target_tab in expected_tabs:
        menu_tabs = menu_bar.find_elements(By.XPATH, './/div[@role="tab"]')
        while target_tab not in [tab.text for tab in menu_tabs]:
            driver.find_element(By.XPATH, '//*[@id="appSplitView-reportPanelView-0-sectionTabBar--header-arrowScrollRight"]').click()
            time.sleep(1)
            menu_tabs = menu_bar.find_elements(By.XPATH, './/div[@role="tab"]')
        menu_bar.find_element(By.XPATH, f'.//div[contains(text(), "{target_tab}")]/..').click()
        match target_tab:
            case "Unique AHS ODP clients":
                # Click the maximize within the visual
                visual = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, '//*[@id="__uiview7canvas"]')))
                actions.move_to_element(visual).click().perform()
                actions.key_down(Keys.ALT).key_down(Keys.F11).key_up(Keys.F11).key_up(Keys.ALT).perform()
                table_canvas = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, '//section[@id="__splitter0-content-1"]/div/div/div/canvas')))
                # Resize the canvas to its full dimensions
                driver.execute_script("arguments[0].width = arguments[0].scrollWidth;", table_canvas)
                driver.execute_script("arguments[0].height = arguments[0].scrollHeight;", table_canvas)
                image_url = driver.execute_script("return arguments[0].toDataURL('image/png');", table_canvas)
                print(image_url)
                image_base64 = base64.b64decode(image_url.split(",")[1])
                time.sleep(1000)
                # Save the image
                with open(os.path.join(output_dir, "test_pic.png"), "wb") as file:
                    file.write(image_base64)

            


#######################################################################################

# Test code below
if __name__ == '__main__':
    try:
        driver = start_driver(headless=False)
        ab_substancesurveillance_scrape(driver)
        driver.quit()
    except Exception as e:
        traceback.print_exc()
        driver.quit()
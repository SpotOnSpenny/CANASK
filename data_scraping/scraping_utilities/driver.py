# Python Standard Library Imports
import platform
import os

# External Dependency Imports
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

# Internal Dependency Imports


#####################################################################################
#                                      Notes:                                       #
# This file contains the code that runs the selenium driver that is used for        #
# scraping data from the sources. The code in this file will essentially open a web #
# browser which will need to be passed through to the functions that do the actual  #
# data collection. The benefit of this structure is that the same driver can be     #
# used for multiple scrapes sequentially instead of needing to open a new browser   #
# instance, which will make things cleaner in the long run.                         #
#                                                                                   #
# It may also be worth noting that the scripts will operate using the a headless    #
# browser, which means that the browser window will not actually open on the screen.#
# Do not be alarmed if you don't actually see anything happening! This will also    #
# require that chrome is installed so that the headless version of the chromedriver #
# can run. More details on that will be available in the readme though. :)          #
#####################################################################################

# Function to start the web driver
def start_driver(headless=False):
    # Get the os type
    operating_system =  platform.system()
    # Conver the os to a filepath
    chromedriver_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"chromedriver-{operating_system.lower()}")
    # Check to ensure support, quit if unsupported
    if not os.path.exists(chromedriver_path):
        print(f"Sorry! {operating_system} is not a supported operating system. Please raise an issue on the github page to request support.")
        quit(1)
    # Instantiate Selenium Service with the path to the chromedriver
    service = Service(executable_path=chromedriver_path)
    # Add the headless option
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    # Start the driver
    driver = webdriver.Chrome(service=service, options=options)
    # Return the driver
    return driver

# Test code below
if __name__ == "__main__":
    start_driver()
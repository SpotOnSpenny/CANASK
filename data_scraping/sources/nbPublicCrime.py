# Python Standard Library Imports


# External Dependency Imports


# Internal Dependency Imports


#######################################################################################
#                                       Notes:                                        #
# This file contains the data scraping code for the New Brunswick Public Safety Crime #
# Dashboard, which pulls data from a MASSIVE stats canada dataset. The dataset that's #
# important to us is https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3510017801 #
# as it has important aggregate information about drug violations, however there's a  #
# notable lack of information relating to specific substances. It's also worth noting #
# that the data has aggregate information for all atlantic provinces (NS, NB, PE, NL) #
# and so it can be used to pull aggregate information from more than just NB.         #
#                                                                                     #
# Health Canada does make it easy to pull this incredibly large CSV, as they've made  #
# a number of API endpoints, such as one which allows us to download the entire CSV   #
# file. The docs for the API can be found at the URL below, the method we're using    #
# here is the "getFullTableDownloadCSV":                                              #
#                                                                                     #
# https://www.statcan.gc.ca/en/developers/wds/user-guide#a12-6                        #
#                                                                                     #
# This method will return a link which will download the CSV as a ZIP file. We'll     #
# then need to use __________ to extract the csv from the zip file so that we can     #
# process the information within.Considering the nature of the data, it makes sense   #
# once the massive CSV is pulled, that we should go through and process it into       #
# smaller chunks related to the specific data that we're looking for. I imagine we    #
# can do this based on the type of violation and also the Geography.                  #
#######################################################################################

# Function to pull the data used on the New Brunswick Public Safety Crime Dashboard
def 
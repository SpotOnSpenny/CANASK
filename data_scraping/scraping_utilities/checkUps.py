# Python Standard Library Imports
import os

# External Dependency Imports


# Internal Dependency Imports


#######################################################################################
#                                       Notes:                                        #
# This file contains some functions that were being done frequently across multiple   #
# data scraping sources to consolidate code into a more usable, readable, extensible  #
# format instead of re-writing the same code in multiple files. Within this file      #
# currently there are:                                                                #                    
#                                                                                     #                         
# - A function to check for the output files and directory (checkup_output())         #
#         This is something that was being done for every data source. Instead of re- #
#         writing the same code in every file, it's been consolidated here. The       # 
#         function takes in a list of files to be checked for in the output directory #
#         and returns a list of files which do NOT exist in the output directory.     #
#         This can be compared in a match/case function to determine what data should #
#         be fetched by the function in realtion to the data source. It also returns  #
#         the output directory so that new data can be written to the same place.     #
#######################################################################################

# Function to check for the output files and directory
def checkup_output(needed_files: list):
    # Get the output directory and create one if it doesn't exist
    output_dir = os.path.join(os.getcwd(), "output")
    existing_files = []
    if not os.path.exists(output_dir):
        os.mkdir(output_dir)
        # Return the full list of files because we literally just made the output dir and it is empty
        return output_dir, needed_files, existing_files
    # Check for the files in the output directory
    output_files = os.listdir(output_dir)
    # Check if the files exist in the output directory
    for file in needed_files:
        file_exists = any(file in output_file for output_file in output_files)
        if file_exists:
            needed_files.remove(file)
            for output_file in output_files:
                if file in output_file:
                    existing_files.append(output_file)
    # Return the list of files that don't exist in the output directory and the output directory itself
    return output_dir, needed_files, existing_files


# Test code below
if __name__ == "__main__":
    # Test the checkup_output function
    output_dir, needed_files, existing_files = checkup_output(["35100178", "test.txt"])
    print(existing_files)
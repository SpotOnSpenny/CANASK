# Python Standard Library Dependencies
import os

# External Dependency Imports
import pandas

# Internal Dependency Imports


#######################################################################################
#                                        Notes:                                       #
# For now, these functions include the cleaning of the dataframes required to create  #
# the visualization. In the future, a big #TODO will be to remove this step and       #
# include it in either separate scripts that pass the data to a database after        #
# scraping, or directly in the scraping scripts themselves.                           #
#######################################################################################

# Helper function to pull data from the specified excel/csv file
def pull_data(data_source: str):
    output_dir = os.path.join(os.path.abspath(os.path.dirname(os.path.dirname(__file__))), "output")
    if any(data_source in file for file in os.listdir(output_dir)):
        file = [file for file in os.listdir(output_dir) if data_source in file][0]
        match file.split(".")[1]:
            case "csv":
                return {f"{data_source}": pandas.read_csv(os.path.join(output_dir, file))}
            case "xlsx":
                sheets = {}
                dataframes = pandas.read_excel(os.path.join(output_dir, file), sheet_name=None).values()
                for dataframe in dataframes:
                    name = list(filter(lambda value: True if "Unnamed" not in value and value != "NaN" else False, dataframe.columns))[0]
                    dataframe.set_flags(allows_duplicate_labels=False)
                    dataframe.columns = dataframe.iloc[0]
                    dataframe.dropna(axis=0, inplace=True)
                    dataframe = dataframe.drop(dataframe.columns[[0]], axis=1).reset_index(drop=True)
                    sheets[name] = dataframe
                return sheets
    else:
        raise FileNotFoundError(f"Data source {data_source} not found in the output directory!")

# Helper function to pull the data from the provided source into a dataframe
def filter_data(data: dict, find_these: list):
    dataframes = []
    for key in data.keys():
        if any(find_this.split(",")[0].lower().replace(" ", "") == key.split(",")[0].lower().replace(" ", "") for find_this in find_these):
            dataframes.append(data[key])
    return dataframes

# Function to generate the graph for drugs involved in toxicity deaths
def drug_type_visual(data: dict):
    # Clean the data to get the aggregate values we need (SK Publication Centre Specific)
    pass
    # Clean the data to get the aggregate values we need (BC Coroners Report Specific)
    pass
    # Generate the visual with Plotly
    pass

# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code
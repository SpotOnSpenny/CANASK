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
def pull_data(data_source: list):
    sheets = {}
    for source in data_source:
        output_dir = os.path.join(os.path.abspath(os.path.dirname(os.path.dirname(__file__))), "output")
        if any(source in file for file in os.listdir(output_dir)):
            file = [file for file in os.listdir(output_dir) if source in file][0]
            match file.split(".")[1]:
                case "csv":
                    sheets[source] = pandas.read_csv(os.path.join(output_dir, file))
                case "xlsx":
                    dataframes = pandas.read_excel(os.path.join(output_dir, file), sheet_name=None).values()
                    for dataframe in dataframes:
                        name = list(filter(lambda value: True if "Unnamed" not in value and value != "NaN" else False, dataframe.columns))[0]
                        dataframe.set_flags(allows_duplicate_labels=False)
                        dataframe.columns = dataframe.iloc[0]
                        dataframe.dropna(axis=0, inplace=True)
                        dataframe = dataframe.drop(dataframe.columns[[0]], axis=1).reset_index(drop=True)
                        sheets[name] = dataframe
        else:
            raise FileNotFoundError(f"Data source {source} not found in the output directory!")
    return sheets

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
    sask_raw = filter_data(data, ["BreakdownofOpioidDrugsIdentifiedinConfirmedDrugToxicityDeathsbyMannerofDeath,2016-2024", "BreakdownofBenzodiazepineDrugsIdentifiedinConfirmedDrugToxicityDeathsbyMannerofDeath,2024"])
    sask_clean = {}
    for frame in sask_raw:
        for index, row in frame.iterrows():
            if row["Year"] not in sask_clean.keys():
                year = row["Year"]
                sask_clean[year] = {}
            else:
                year = row["Year"]
            for key in row.keys():
                if key != "Year" and key != "MannerOfDeath" and key not in sask_clean[year].keys() and row[key].isnumeric():
                    sask_clean[year][key] = int(row[key])
                elif key != "Year" and key != "MannerOfDeath" and key in sask_clean[year].keys() and row[key].isnumeric():
                    sask_clean[year][key] += int(row[key])

    # Clean the data to get the aggregate values we need (BC Coroners Report Specific)
    bc_raw = filter_data(data, ["Unregulated Drug Deaths by Month, 2014-2024", "Unregulated Drug Deaths by Drug Types Relevant to Death"])
    bc_clean = {}
    for name, item in bc_raw[1].items():
        if name == bc_raw[1].columns[0]:
            drugs = item
        else:
            name = name.replace(u"\xa0", "")
            bc_clean[name] = {}
            for index, row in enumerate(item):
                bc_clean[name][drugs[index]] = (int(bc_raw[0].at[12, u"{0}\xa0".format(name)]) * (float(row.replace("%", "")) / 100)).__floor__()
    # Generate the visual with Plotly
    # Use a bar chart to show the number of deaths by drug type, overlayed with a line chart to show the number of deaths by year
    pass


# Test code below
if __name__ == '__main__':
    all_frames = pull_data(["skPubCentre", "bcCoronersReport"])
    drug_type_visual(all_frames)
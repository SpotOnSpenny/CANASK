// Dictionary of visuals that each province has
const visuals = {
  "british-columbia": {
    "drug_death_heatmap": {
      "type": "heatmap",
      "parent": "Deaths and Demographics",
      "menu-name": "Drug Toxicity Deaths by Health Authority",
      "count-key":"",
      "rates-key": "",
      "second-level": {
        "deaths_by_sex_line": {
          "count-key": "",
          "rates-key": "",
          "type": "line",
        }
      },
    },
    "drug_supply_by_year": {
      "type": "line",
      "parent": "Drug Supply",
      "menu-name":"Drugs by Category",
      "count-key": "counts",
      "rates-key": "rates"
    },
    "fent_benz_by_year": {
      "type": "line",
      "parent": "Drug Supply",
      "menu-name": "Presence of Fentanly and Benzodiazepines",
      "count-key": "counts",
      "rates-key": "rates"
    }
  },
}

// Global variables to hold the current data and geojson
let currentData;
let currentGeojson;
let currentVisual;

// Function to dynamically create the menu based on the visuals object and current province
function createMenu(province) {
  // Create the menu and add all parent categories
  let menu = document.getElementById("vis-selection-menu");
  menu.innerHTML = ""; // Clear existing menu items
  let parentCategories = ["Drug Supply", "Deaths and Demographics", "Naloxone", "Safe Consumption and Drug Checking Sites", "Miscellaneous"];
  // create a menu dropdown for each parent category
  for (const parent of parentCategories) {
    let li = document.createElement("li");
    li.className = "nav-item dropdown";
    let a = document.createElement("a");
    a.className = "nav-link dropdown-toggle";
    a.href = "";
    a.id = `${parent.toLowerCase().replace(/ /g, "-")}-dropdown`;
    a.setAttribute("role", "button");
    a.setAttribute("data-bs-toggle", "dropdown");
    a.textContent = parent;
    li.appendChild(a);
    let ul = document.createElement("ul");
    ul.className = "dropdown-menu";
    ul.id = `${parent.toLowerCase().replace(/ /g, "-")}-dropdown-menu`;
    li.appendChild(ul);
    menu.appendChild(li);
  }

  for (const [visual, details] of Object.entries(visuals[province])) {
    // Create a new list item for each visual
    let li = document.createElement("li");
    li.className = "nav-item";
    
    // Create the anchor element for the visual
    let a = document.createElement("a");
    a.className = "nav-link";
    a.id = visual;
    a.href = "#";
    a.textContent = details["menu-name"];
    li.appendChild(a);
    
    // Add an onclick event to set the current visual and create the visual
    switch (details["type"]) {
      case "heatmap":
        a.onclick = function () {
          resetVisualControl();
          currentVisual = visual;
          createVisualMap(province, currentVisual, currentGeojson, currentData[currentVisual]["data"]["counts"], currentData[currentVisual]["data_source"], null);
        };
        break;
      case "line":
        a.onclick = function () {
          resetVisualControl();
          currentVisual = visual;
          createVisualLine(currentData[currentVisual]['data'], currentVisual, 'counts', currentData[currentVisual]['data_source'], currentData[currentVisual]['visual_options'], currentData[currentVisual]['additional_rows'] || null);
        };
        break;
    }
    
    // append the menu item to the appropriate parent category
    document.getElementById(`${details["parent"].toLowerCase().replace(/ /g, "-")}-dropdown-menu`).appendChild(li);
  }
    // Add a disabled class to the parent categories that have no visuals
  for (const parent of parentCategories) {
    let dropdownMenu = document.getElementById(`${parent.toLowerCase().replace(/ /g, "-")}-dropdown-menu`);
    if (dropdownMenu.children.length === 0) {
      // remove the parent li item from the menu
      let parentLi = document.querySelector(`li.dropdown > a#${parent.toLowerCase().replace(/ /g, "-")}-dropdown`);
      parentLi.remove();
      // add a new disabled a element to the menu
      let disabledA = document.createElement("a");
      disabledA.className = "nav-link disabled";
      disabledA.href = "";
      disabledA.textContent = `${parent}`;
      menu.appendChild(disabledA);
    }
  }
}

// Function to initialize the data by fetching provincial data
async function fetchRegionData(province){
    console.log(`Fetched data for ${province}`);
    //fetch the data and unpack it
    const [data, geojson] = await Promise.all([
        fetch("/static/js/visual_data.json"),
        fetch(`/static/assets/geojsons/${province.toLowerCase()}.geojson`),
    ]);
    const dataJson = await data.json();
    const geojsonJson = await geojson.json();
    currentData = dataJson[province];
    currentGeojson = geojsonJson;
}

// Function to generate heatmaps
async function createVisualMap(province, currentVisual, geojson, mapData, mapSource, mapOptions){
  // Setup the map container and other elements
  let visDiv = document.getElementById("vis-div");
  let aboutDataDiv = document.getElementById("about-data");
  let tableDiv = document.getElementById("data-table");
  let table = document.createElement("table");
  let tableTitle = document.getElementById("table-title");
  let countRateToggle = document.getElementById("count-rate-toggle");
  let countToggle = document.getElementById("counts-toggle");
  let rateToggle = document.getElementById("rates-toggle");
  let dataSlider = [];
  let steps = [];
  
  // Create the map using the provided data and options and push them into the slider
  for (let year_index = 0; year_index < mapData[Object.keys(mapData)[0]]["x"].length; year_index++) {
    let values = {};
    for (let loc_index = 0; loc_index < geojson.features.length; loc_index++) {
      values[geojson.features[loc_index].properties.ENGNAME] = mapData[geojson.features[loc_index].properties.ENGNAME]["y"][year_index];
    }
    let chartData = {
      type: "choropleth",
      locationmode: "geojson-id",
      geojson: geojson,
      locations: Object.keys(values),
      featureidkey: "properties.ENGNAME",
      z: Object.values(values),
      autocolorscale: true,
      colorbar: {
        title: "Number<br>of Deaths",
        thickness: 15,
      },
      visible: year_index === 0,
      hoverinfo: "location+z",
    };
    dataSlider.push(chartData);
  }

  // Create the slider steps
  for (let i = 0; i < dataSlider.length; i++) {
    let step = {
      method: "restyle",
      args: ["visible", Array(dataSlider.length).fill(false)],
      label: mapData[Object.keys(mapData)[0]].x[i],
    };
    step.args[1][i] = true;
    steps.push(step);
  }

  // Create the layout for the map from the mapOptions object
  let layout = {
    geo: {
      showlakes: false,
      fitbounds: "locations",
      showcoastlines: false,
    },
    hoverlabel: {
      namelength: -1,
    },
    sliders: [
      {
        active: steps.length - 1,
        steps: steps,
        x: 0.5,
        xanchor: "center",
        len: 0.95,
        y: 0,
        yanchor: "top",
        pad: { t: 0, b: 10 },
        currentvalue: {
          visible: true,
          prefix: "Year: ",
          xanchor: "right",
          font: {
            size: 20,
            color: "#666",
          },
        },
      },
    ],
    autosize: false,
    width: $("#viz-card").width(),
    height:
      window.innerWidth > 768
        ? $("#viz-card").height()
        : $("#viz-card").height(),
    title:
      window.innerWidth > 768
        ? "Unregulated Drug Toxicity Deaths in British Columbia by Health Authority"
        : "Confirmed and Probable Opioid<br>Toxicity Deaths in Ontario by<br>Public Health Unit",
    margin: window.innerWidth > 768 ? { l: 0 } : { b: 20, r: 0, l: 20, autoexpand: true },
  };

  // Insert the visual and define the callback for click events
  visDiv.innerHTML = ""; // Clear the previous content
  Plotly.purge(visDiv); // Clear any previous Plotly plots
  let vis = Plotly.react(
    visDiv,
    dataSlider,
    layout,
    (config = {
      displaylogo: false,
      responsive: false,
    })
  ).then(() => {
    visDiv.on("plotly_click", function (data) {
      if (data && data.points.length > 0) {
        // check if second level data exists for the current visual
        if (!visuals[province][currentVisual]["second-level"]) {
          console.error("No second level visual exists for this visual");
        } else {
          console.log(currentVisual)
          let location = data.points[0].location; // Get the clicked location
          let secondLevelData = getSecondLevelData(province, currentVisual, location);
          let backButton = document.getElementById("back-button");
          backButton.classList.remove("d-none");
          backButton.onclick = function () {
            // on click, re-render the map visual, and hide the back button/rates toggle again
            backButton.classList.add("d-none");
            countRateToggle.classList.add("d-none");
            countRateToggle.classList.remove("d-flex");
            //reset the count/rate toggle so that counts is selected
            countToggle.checked = true;
            rateToggle.checked = false;
            // Recreate the map visual
            createVisualMap(province, currentVisual, geojson, mapData, mapSource, mapOptions);
          };
          switch (secondLevelData["type"]) {
            case "line":
              createVisualLine(secondLevelData["data"], currentVisual, "counts", secondLevelData["data_source"], secondLevelData["visual_options"], secondLevelData["additional_rows"] || null);
              break;
            // Add more cases for other visual types as the need arises
            default:
              console.error("Unsupported visual type:", secondLevelData["type"]);
          }
        };
      }
    });
  }
  );

  //Generate the About these Data section and insert the html
  let header =`<h4 class="card-title text-center"> About these Data</h4>
  <hr />
  <h5 class="text-center">This data set was last updated in ${mapSource["last_updated"] + " "} and contains data up until ${mapSource["data_until"]}.</h5>
  `;
  let button = `<div class="text-center pb-3">
    <a target="_blank" href="${mapSource["link"]}" role="button"
          class="btn btn-primary">${mapSource["name"]}</a>
  </div>
  `;
  let aboutHTML = `${header}
  ${mapSource["about"]}
  <br></br>
  ${button}
  `;
  aboutDataDiv.innerHTML = aboutHTML;

  // Create and insert the tabular data
  table.setAttribute(
    "class",
    "mb-0 table table-striped table-bordered table-hover"
  );
  let cols = ["Health Authority"].concat(mapData[Object.keys(mapData)[0]].x);
  let tr = table.insertRow(-1);
  cols.forEach((headerText) => {
    let th = document.createElement("th"); // Create a new header cell
    th.innerText = headerText; // Set the text of the header cell
    tr.appendChild(th); // Add the header cell to the row
  });
  tableDiv.innerHTML = "";
  tableDiv.appendChild(table);
  tableTitle.innerText = "Unregulated Drug Toxicity Deaths in British Columbia by Health Authority";
  for (const [key, value] of Object.entries(mapData)) {
    if (key != "data last updated") {
      let tr = table.insertRow(-1);
      tr.setAttribute("class", "align-middle");
      let tabCell = tr.insertCell(-1);
      tabCell.innerText = key;
      value["y"].forEach((element) => {
        let tabCell = tr.insertCell(-1);
        tabCell.innerText = element;
      });
    }
  }
}

// create line chart
async function createVisualLine(lineData, currentVisual, countsOrRates, lineSource, visualOptions, additionalRows = null){
  let countRateToggle = document.getElementById("count-rate-toggle");
  let visDiv = document.getElementById("vis-div");
  let aboutDataDiv = document.getElementById("about-data");
  let tableDiv = document.getElementById("data-table");
  let table = document.createElement("table");
  let tableTitle = document.getElementById("table-title");
  let traces = [];
  let location = visualOptions["location"] || "";

  // remove the active class from other visuals and add it to the current visual
  Array.from(document.querySelectorAll(".active")).forEach((element) => {
    element.classList.remove("active");
  });
  let currentVisualElement = document.getElementById(currentVisual);
  if (currentVisualElement) {
    currentVisualElement.classList.add("active");
  } else {
    console.error(`No element found with id ${currentVisual}`);
  }

  // Check to see if we have count or rate data, default to count if not specified
  if (countsOrRates !== null){
    traceData = lineData[countsOrRates];
  } else if (lineData["counts"]) {
    traceData = lineData["counts"];
    countsOrRates = "counts";
  } else if (lineData["rates"]) {
    traceData = lineData["rates"];
    countsOrRates = "rates";
  } else {
    console.error("No counts or rates data found in lineData");
    return;
  }
  
  // check to see if we have a total
  totalPresent = !!("total_y" in traceData);

  for (const [key, value] of Object.entries(traceData)) {
    // create a trace for each y value in the lineData object entry
    if (key != "x"){
      let trace = {
        x: traceData["x"],
        y: value,
        name: key.replaceAll("_y", "").toSentenceCase(),
        type: "scatter",
        mode: "lines+markers",
        stackgroup: totalPresent && key.replaceAll("_y", "").toSentenceCase() != "Total" ? "one" : undefined, // fill if total is present and not the total line
        line: {
          width: 2,
          smoothing: 1,
        },
      };
      traces.push(trace);
    }
  }

  Plotly.purge(visDiv); // Clear any previous Plotly plots
  let vis = Plotly.react(
    visDiv,
    traces,
    (layout = {
      dragmode: "pan",
      yaxis: {
        fixedrange: true,
        title: {
          standoff: 30,
          text: countsOrRates == "counts" ? visualOptions["counts-y-axis-title"] : visualOptions["rates-y-axis-title"],
        },
      },
      xaxis: {
        fixedrange: false,
        autorange: true,
        autorangeoptions:
          window.innerWidth > 768
            ? {}
            : {
                clipmax: Number(traces[0]["x"][0]) + 2,
              },
        dtick: 1,
        title: {
          text: "Year",
          standoff: 5,
        },
        constrain: "domain",
      },
      hovermode: "x unified",
      autosize: false,
      width: $("#viz-card").width(),
      height: window.innerWidth > 768 ? $("#viz-card").height() : "auto",
      title: countsOrRates == "counts" ? visualOptions["counts-title"].replace("replace_with_health_authority", location) : visualOptions["rates-title"].replace("replace_with_health_authority", location),
      legend:
        window.innerWidth > 768
          ? {}
          : {
              orientation: "h",
              x: 0,
              y: -0.2,
              xanchor: "middle",
              yanchor: "top",
              tracegroupgap: 200,
            },
      margin: window.innerWidth > 768 ? {} : { r: 0, l: 65 },
    }),
    (config = {
      displaylogo: false,
    })
  );
  
  // Replace the tabular section with table data for this vis
  table.setAttribute(
    "class",
    "mb-0 table table-striped table-bordered table-hover"
  );
  let cols = [""].concat(traceData["x"]);
  console.log(traceData["x"]);
  let tr = table.insertRow(-1);
  cols.forEach((headerText) => {
    let th = document.createElement("th"); // Create a new header cell
    th.innerText = headerText; // Set the text of the header cell
    tr.appendChild(th); // Add the header cell to the row
  });
  tableDiv.innerHTML = "";
  tableDiv.appendChild(table);
  tableTitle.innerText = visualOptions["counts-title"].replace("replace_with_health_authority", location);
  for (const [key, value] of Object.entries(lineData)) {
    for (const [subKey, subValue] of Object.entries(value)) {
      if (subKey != "x") {
        let tr = table.insertRow(-1);
        tr.setAttribute("class", "align-middle");
        let tabCell = tr.insertCell(-1);
        tabCell.innerText = key == "counts" ? visualOptions["table-counts-row"].replace("replace_me", subKey.replaceAll("_y", "").toTitleCase()) : visualOptions["table-rates-row"].replace("replace_me", subKey.replaceAll("_y", "").toTitleCase());
        subValue.forEach((element) => {
          let tabCell = tr.insertCell(-1);
          tabCell.innerText = element;
        });
        }
    }
  }
  // If there are additional rows, add them to the table
  if (additionalRows) {
    for (const [key, value] of Object.entries(additionalRows)) {
      let tr = table.insertRow(-1);
      tr.setAttribute("class", "align-middle");
      let tabCell = tr.insertCell(-1);
      tabCell.innerText = key;
      value.forEach((element) => {
        let tabCell = tr.insertCell(-1);
        tabCell.innerText = element;
      });
    }
  }

  // If there are rates and counts, unhide the toggle button, and enable it to switch between counts and rates
  if (lineData["counts"] && lineData["rates"]) {
    countRateToggle.classList.remove("d-none");
    countRateToggle.classList.add("d-flex");
    let countToggle = document.getElementById("counts-toggle");
    let rateToggle = document.getElementById("rates-toggle");
    countRateToggle.onchange = function () {
      if (countToggle.checked) {
        // If the toggle is checked, show counts
        createVisualLine(lineData, currentVisual, "counts", lineSource, visualOptions, additionalRows);
      } else {
        // If the toggle is unchecked, show rates
        createVisualLine(lineData, currentVisual, "rates", lineSource, visualOptions, additionalRows);
      }
    };
  }
}

// Helper function to get the data for the secondary level of the visual
function getSecondLevelData(province, visual, location = null) {
  // Find the second level of the visual from the object
  try {
    let secondLevel = visuals[province][visual]["second-level"];
    let secondLevelObject= currentData[Object.keys(secondLevel)[0]];
    let visualType = secondLevel[Object.keys(secondLevel)[0]]["type"];
    
    // separate out the second level data into the data source, and actual data
    let secondLevelDataSource = secondLevelObject["data_source"]
    let secondLevelData = secondLevelObject["data"]

    // if there's a specific location, pull the data from that location
    if (location != null){
      returnObject = {
        "type": visualType,
        "data_source": secondLevelDataSource,
        "data": {
          "rates": secondLevelData["rates"][location],
          "counts": secondLevelData["counts"][location],
        }, 
        "visual_options": secondLevelObject["visual_options"] || {}
      }
      returnObject["visual_options"]["location"] = location.toTitleCase();
      return returnObject;
    } else {
      return secondLevelData
    }
  }
  catch {
    console.log("No second level visual exists");
    return null;
  }
}

// Helper function to reset the count/rates toggle
function resetVisualControl() {
  let countRateToggle = document.getElementById("count-rate-toggle");
  countRateToggle.classList.add("d-none");
  countRateToggle.classList.remove("d-flex");
  let countToggle = document.getElementById("counts-toggle");
  let rateToggle = document.getElementById("rates-toggle");
  // reset the count/rate toggle so that counts is selected
  countToggle.checked = true;
  rateToggle.checked = false;
  // remove the back button if it exists
  let backButton = document.getElementById("back-button");
  if (backButton) {
    backButton.classList.add("d-none");
  }
}
// Function to convert titles to sentence case
String.prototype.toSentenceCase = function () {
  return this.charAt(0).toUpperCase() + this.slice(1).toLowerCase();
}

String.prototype.toTitleCase = function () {
  return this.replace(/\w\S*/g, function (txt) {
    return txt.charAt(0).toUpperCase() + txt.substr(1).toLowerCase();
  });
}


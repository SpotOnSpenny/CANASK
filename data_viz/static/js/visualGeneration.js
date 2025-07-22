// TODO - Consolidate sources out of the individual visuals into a single source file that we can reference
// TODO - Create a Pie Chart function that's clickable and loads a third level visual

// Dictionary of visuals that each province has
const visuals = {
  "default-visuals": {
    "british-columbia": "drug_death_heatmap"
  },
  "british-columbia": {
    "drug_death_heatmap": {
      "type": "heatmap",
      "parent": "Deaths and Demographics",
      "menu-name": "Drug Toxicity Deaths by Health Authority",
      "second-level": {
        "deaths_by_sex_line": {
          "data-types": ["counts", "rates"],
          "type": "line",
        }
      },
    },
    "drug_supply_by_year": {
      "type": "line",
      "parent": "Drug Supply",
      "menu-name":"Drugs by Category",
      "data-types": ["counts", "rates"],
    },
    "fent_benz_by_year": {
      "type": "line",
      "parent": "Drug Supply",
      "menu-name": "Presence of Fentanly and Benzodiazepines",
      "data-types": ["counts", "rates"],
    },
    "opioid_types_by_year": {
      "type": "line",
      "parent": "Drug Supply",
      "menu-name": "Presence of Opioid Types",
      "data-types": ["counts", "rates"],
    },
    "toxicity_deaths_per_drug_by_year": {
      "type": "bar",
      "parent": "Deaths and Demographics",
      "menu-name": "Unregulated Drug Toxicity Deaths by Drug Type",
      "data-types": ["counts", "rates", "percentages"],
    },
    "drug_toxicity_deaths_by_age": {
      "type": "line",
      "parent": "Deaths and Demographics",
      "menu-name": "Unregulated Drug Toxicity Deaths by Age Group",
      "data-types": ["counts", "rates"],
    },
    "drug_supply_geographically": {
      "type": "map",
      "parent": "Drug Supply",
      "menu-name": "Drug Supply by Health Authority",
      "second-level": {
        "geographical_drug_supply_pie": {
          "type": "pie", 
          "data-types": ["counts"],
        }
      }
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
          createVisualHeatMap(province, currentVisual, currentGeojson, currentData[currentVisual]["data"]["counts"], currentData[currentVisual]["data_source"], null);
        };
        break;
      case "line":
        a.onclick = function () {
          resetVisualControl();
          currentVisual = visual;
          createVisualLine(province, currentData[currentVisual]['data'], currentVisual, 'counts', currentData[currentVisual]['data_source'], currentData[currentVisual]['visual_options'], currentData[currentVisual]['additional_rows'] || null);
        };
        break;
      case "bar":
        a.onclick = function () {
          resetVisualControl();
          currentVisual = visual;
          createVisualBar(province, currentData[currentVisual]['data'], currentVisual, "counts", currentData[currentVisual]['data_source'], currentData[currentVisual]['visual_options']);
        };
        break;
      case "map":
        a.onclick = function () {
          resetVisualControl();
          currentVisual = visual;
          createVisualMap(province, currentVisual, currentGeojson, currentData[currentVisual]["visual_options"]);
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
async function createVisualHeatMap(province, currentVisual, geojson, mapData, mapSource, mapOptions){
  // Setup the map container and other elements
  let visDiv = document.getElementById("vis-div");
  let aboutDataDiv = document.getElementById("about-data");
  let tableDiv = document.getElementById("data-table");
  let table = document.createElement("table");
  let tableTitle = document.getElementById("table-title");
  let dataSlider = [];
  let steps = [];
  
  // remove the active class from other visuals and add it to the current visual
  setActiveVisual(province, currentVisual);

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
          let location = data.points[0].location; // Get the clicked location
          let secondLevelData = getSecondLevelData(province, currentVisual, location);
          let backButton = document.getElementById("back-button");
          backButton.classList.remove("d-none");
          backButton.onclick = function () {
            // Reset the visual control
            resetVisualControl();
            // Recreate the map visual
            createVisualHeatMap(province, currentVisual, geojson, mapData, mapSource, mapOptions);
          };
          switch (secondLevelData["type"]) {
            case "line":
              // pull the data types for the second level visual
              let dataTypes = Object.keys(secondLevelData["data"]);
              createVisualLine(province, secondLevelData["data"], currentVisual, "counts", secondLevelData["data_source"], secondLevelData["visual_options"], secondLevelData["additional_rows"] || null, dataTypes);
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

// Function to generate interactive maps
async function createVisualMap(province, currentVisual, geojson, mapOptions) {
  // take the provided geojson and create a map using Plotly
  let visDiv = document.getElementById("vis-div");
  let aboutDataDiv = document.getElementById("about-data");
  let tableDiv = document.getElementById("data-table");
  let tableTitle = document.getElementById("table-title");

  // remove the active class from other visuals and add it to the current visual
  setActiveVisual(province, currentVisual);
  
  // create a data array of 0s for each location in the geojson
  let data_array = [];
  for (let loc_index = 0; loc_index < geojson.features.length; loc_index++) {
    data_array.push(0);
  };

  mapData = {
    type: "choropleth",
    geojson: geojson,
    locations: geojson.features.map(feature => feature.properties.ENGNAME),
    featureidkey: "properties.ENGNAME",
    showscale: false,
    z: data_array,
    hoverinfo: "location",
  };

  // Create the layout for the map from the mapOptions object
  let layout = {
    geo: {
      fitbounds: "locations",
      showcoastlines: false,
      showlakes: false,
    },
    width: $("#viz-card").width(),
    height:
      window.innerWidth > 768
        ? $("#viz-card").height()
        : $("#viz-card").height(),
    hoverlabel: {
      namelength: -1,
    },
    title: mapOptions["title"]
  };

  // Insert the about this data line, into the aboutDataDiv and the tableDiv
  let header = `<h4 class="card-title text-center">${mapOptions["click_line"]}</h4>`;
  aboutDataDiv.innerHTML = header;
  tableDiv.innerHTML = header;
  tableTitle.innerText = ""

  // Insert the visual and define the callback for click events
  visDiv.innerHTML = "";
  Plotly.purge(visDiv); // Clear any previous Plotly plots
  let vis = Plotly.react(
    visDiv,
    [mapData],
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
          let location = data.points[0].location; // Get the clicked location
          let secondLevelData = getSecondLevelData(province, currentVisual, location);
          let backButton = document.getElementById("back-button");
          backButton.classList.remove("d-none");
          backButton.onclick = function () {
            // Reset the visual control
            resetVisualControl();
            // Recreate the map visual
            createVisualMap(province, currentVisual, geojson, mapOptions);
          };
          switch (secondLevelData["type"]) {
            case "line":
              // pull the data types for the second level visual
              let dataTypes = Object.keys(secondLevelData["data"]);
              createVisualLine(province, secondLevelData["data"], currentVisual, "counts", secondLevelData["data_source"], secondLevelData["visual_options"], secondLevelData["additional_rows"] || null, dataTypes);
              break;
            case "pie":
              createVisualPie(province, secondLevelData["data"], currentVisual, secondLevelData["data_source"], secondLevelData["visual_options"], secondLevelData["tabular_data"], location);
              break;
            // Add more cases for other visual types as the need arises
            default:
              console.error("Unsupported visual type:", secondLevelData["type"]);
          }
        };
      }
    });
  });
};

// create line chart
async function createVisualLine(province, lineData, currentVisual, countsOrRates, lineSource, visualOptions, additionalRows = null, dataTypes = null){
  let dataTypeToggle = document.getElementById("data-type-toggle");
  let visDiv = document.getElementById("vis-div");
  let aboutDataDiv = document.getElementById("about-data");
  let tableDiv = document.getElementById("data-table");
  let table = document.createElement("table");
  let tableTitle = document.getElementById("table-title");
  let traces = [];
  let location = visualOptions["location"] || "";

  // remove the active class from other visuals and add it to the current visual
  setActiveVisual(province, currentVisual);

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

  visDiv.innerHTML = "";
  Plotly.purge(visDiv); // Clear any previous Plotly plots
  let vis = Plotly.react(
    visDiv,
    traces,
    (layout = {
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
  let tr = table.insertRow(-1);
  cols.forEach((headerText) => {
    let th = document.createElement("th"); // Create a new header cell
    th.innerText = headerText; // Set the text of the header cell
    tr.appendChild(th); // Add the header cell to the row
  });
  tableDiv.innerHTML = "";
  tableDiv.appendChild(table);
  tableTitle.innerText = visualOptions["table-title"].replace("replace_with_health_authority", location);
  for (const [key, value] of Object.entries(lineData)) {
    for (const [subKey, subValue] of Object.entries(value)) {
      if (subKey != "x") {
        let tr = table.insertRow(-1);
        tr.setAttribute("class", "align-middle");
        let tabCell = tr.insertCell(-1);
        tabCell.innerText = visualOptions[`table-${key}-row`].replace("replace_me", subKey.replaceAll("_y", "").toTitleCase());
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

  // If there is more than one data type available, create a toggle to switch between each data type
  if (dataTypes == null) {
    dataTypes = visuals[province][currentVisual]["data-types"];
  }

  if (dataTypes.length > 1) {
    for (const dataType of dataTypes) {
      // Create a toggle for each data type
      let toggle = document.createElement("input");
      toggle.type = "radio";
      toggle.className = "btn-check";
      toggle.name = "data-toggle";
      toggle.id = `${dataType}-toggle`;
      toggle.autocomplete = "off";
      if (dataType === countsOrRates) {
        toggle.checked = true;
      }
      // Add an event listener to the toggle
      toggle.onclick = function () {
        // Reset the count/rate toggle
        resetVisualControl(true);
        // Recreate the line visual with the selected data type
        createVisualLine(province, lineData, currentVisual, dataType, lineSource, visualOptions, additionalRows, dataTypes);
      };
      let label = document.createElement("label");
      label.className = "btn btn-outline-primary";
      label.setAttribute("for", `${dataType}-toggle`);
      label.innerText = dataType.charAt(0).toUpperCase() + dataType.slice(1);
      
      dataTypeToggle.appendChild(toggle);
      dataTypeToggle.appendChild(label);
    }
  }
}

// Function to generate a bar chart
async function createVisualBar(province, barData, currentVisual, dataType, barSource, visualOptions, dataTypes = null) {
  let dataTypeToggle = document.getElementById("data-type-toggle");
  let visDiv = document.getElementById("vis-div");
  let aboutDataDiv = document.getElementById("about-data");
  let tableDiv = document.getElementById("data-table");
  let table = document.createElement("table");
  let tableTitle = document.getElementById("table-title");
  let traces = [];

  // remove the active class from other visuals and add it to the current visual
  setActiveVisual(province, currentVisual);

  // Check to see if we have count, percentage, or rate data, default to count if not specified
  if (dataType !== null){
    traceData = barData[dataType];
  } else if (barData["counts"]) {
    traceData = barData["counts"];
    dataType = "counts";
  } else if (barData["rates"]) {
    traceData = barData["rates"];
    dataType = "rates";
  } else if (barData["percentages"]){
    traceData = barData["percentages"];
    dataType = "percentages";
  } else {
    console.error("No counts, percentages, or rates data found in barData");
    return;
  }

  // Create a trace for each y value in the barData object entry
  for (const [key, value] of Object.entries(traceData)) {
    if (key != "x") {
      let trace = {
        x: traceData["x"],
        y: value,
        name: key.replaceAll("_y", "").toSentenceCase(),
        type: "bar",
        marker: {
          line: {
            width: 1,
            color: "black",
          },
        },
      };
      traces.push(trace);
    }
  }
  Plotly.purge(visDiv);
  let vis = Plotly.react(
    visDiv,
    traces,
    (layout = {
      dragmode: "pan",
      yaxis: {
        fixedrange: true,
        title: {
          standoff: 30,
          text: visualOptions[`${dataType}-y-axis-title`],
        },
      },
      xaxis: {
        fixedrange: false,
        autorange: true,
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
      height:
        window.innerWidth > 768
          ? $("#viz-card").height()
          : $("#viz-card").height(),
      title: visualOptions[`${dataType}-title`],
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
  let tr = table.insertRow(-1);
  cols.forEach((headerText) => {
    let th = document.createElement("th"); // Create a new header cell
    th.innerText = headerText; // Set the text of the header cell
    tr.appendChild(th); // Add the header cell to the row
  });
  tableDiv.innerHTML = "";
  tableDiv.appendChild(table);
  tableTitle.innerText = visualOptions["table-title"];
  for (const [key, value] of Object.entries(barData)) {
    for (const [subKey, subValue] of Object.entries(value)) {
      if (subKey != "x") {
        let tr = table.insertRow(-1);
        tr.setAttribute("class", "align-middle");
        let tabCell = tr.insertCell(-1);
        tabCell.innerText = visualOptions[`table-${key}-row`].replace("replace_me", subKey.replaceAll("_y", "").toTitleCase());
        subValue.forEach((element) => {
          let tabCell = tr.insertCell(-1);
          tabCell.innerText = element;
        });
      }
    }
  }

// If there is more than one data type available, create a toggle to switch between each data type
  if (dataTypes == null) {
    dataTypes = visuals[province][currentVisual]["data-types"];
  }

  if (dataTypes.length > 1) {
    for (const data of dataTypes) {
      // Create a toggle for each data type
      let toggle = document.createElement("input");
      toggle.type = "radio";
      toggle.className = "btn-check";
      toggle.name = "data-toggle";
      toggle.id = `${data}-toggle`;
      toggle.autocomplete = "off";
      if (data === dataType) {
        toggle.checked = true;
      }
      // Add an event listener to the toggle
      toggle.onclick = function () {
        // Reset the count/rate toggle
        resetVisualControl(true);
        // Recreate the line visual with the selected data type
        createVisualBar(province, barData, currentVisual, data, barSource, visualOptions, dataTypes);
      };
      let label = document.createElement("label");
      label.className = "btn btn-outline-primary";
      label.setAttribute("for", `${data}-toggle`);
      label.innerText = data.charAt(0).toUpperCase() + data.slice(1);
      
      dataTypeToggle.appendChild(toggle);
      dataTypeToggle.appendChild(label);
    }
  }
  // Generate the About these Data section and insert the html
  let header = `<h4 class="card-title text-center"> About these Data</h4>
  <hr />
  <h5 class="text-center">This data set was last updated in ${barSource["last_updated"] + " "} and contains data up until ${barSource["data_until"]}.</h5>
  `;
  let button = `<div class="text-center pb-3">
    <a target="_blank" href="${barSource["link"]}" role="button"
          class="btn btn-primary">${barSource["name"]}</a>
  </div>
  `;
  let aboutHTML = `${header}
  ${barSource["about"]}
  <br></br>
  ${button}
  `;
  aboutDataDiv.innerHTML = aboutHTML;
}

async function createVisualPie(province, pieData, currentVisual, pieSource, visualOptions, tabularData, location = null) {
  // Setup the map container and other elements
  let visDiv = document.getElementById("vis-div");
  let aboutDataDiv = document.getElementById("about-data");
  let tableDiv = document.getElementById("data-table");
  let table = document.createElement("table");
  let tableTitle = document.getElementById("table-title");
  let dataSlider = [];
  
  // remove the active class from other visuals and add it to the current visual
  setActiveVisual(province, currentVisual);

  // Create the pie chart data with a slider for each year
  let years = Object.keys(pieData["counts"])
  for (let year of years){
    let chartData = {
      type: "pie",
      labels: Object.keys(pieData["counts"][year]),
      values: Object.values(pieData["counts"][year]),
      textinfo: "label",
      hoverinfo: "label+value+percent",
      visible: year === years[0],
      noValueFlag: false, // Flag to indicate if there are no values for this year
    }
    // If the values are all 0 for the year, display a warning and increase the visible year by one unless it's the last year
    if (Object.values(pieData["counts"][year]).every(value => value === 0)) {
      chartData["values"] = [1]; // Set a dummy value to display the pie
      chartData["noValueFlag"] = true;
      // don't display the % for this year
      chartData["textinfo"] = "label";
      chartData["showlegend"] = false;
      chartData["labels"] = [`No data available for ${year}`];
      chartData["hoverinfo"] = "none";
      chartData["marker"] = {
        colors: ["#d3d3d3"], // Light gray color for no data
      };
    }
    dataSlider.push(chartData);
  }

  let activeStep = 0;
  // loop through each chart and set the visible property to false except for the first chart with actual data
  for (let i = 0; i < dataSlider.length; i++) {
    if (dataSlider[i]["noValueFlag"] != true && i > 0) {
      dataSlider[0]["visible"] = false;
      dataSlider[i]["visible"] = true;
      activeStep = i; // Set the active step to the first chart with actual data
      break; // Stop at the first chart with actual data
    } else if (dataSlider[i]["noValueFlag"] != true && i === 0) {
      break;
    }
  }
  let steps = [];
  for (let i = 0; i < dataSlider.length; i++) {
    let step = {
      method: "restyle",
      args: ["visible", Array(dataSlider.length).fill(false)],
      label: years[i],
    };
    step.args[1][i] = true;
    steps.push(step);
  }
  // Create the layout for the pie chart
  let layout = {
    title: visualOptions["visual-title"].replace("replace_with_health_authority", visualOptions["location"].toTitleCase()),
    width: $("#viz-card").width(),
    height:
      window.innerWidth > 768
        ? $("#viz-card").height()
        : $("#viz-card").height(),
    hoverlabel: {
      namelength: -1,
    },
    sliders: [
      {
        active: activeStep,
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
  });

  //Generate the About these Data section and insert the html
  let header =`<h4 class="card-title text-center"> About these Data</h4>
  <hr />
  <h5 class="text-center">This data set was last updated in ${pieSource["last_updated"] + " "} and contains data up until ${pieSource["data_until"]}.</h5>
  `;
  let button = `<div class="text-center pb-3">
    <a target="_blank" href="${pieSource["link"]}" role="button"
          class="btn btn-primary">${pieSource["name"]}</a>
  </div>
  `;
  let aboutHTML = `${header}
  ${pieSource["about"]}
  <br></br>
  ${button}
  `;
  aboutDataDiv.innerHTML = aboutHTML;

  // Replace the tabular section with table data for this vis
  table.setAttribute(
    "class",
    "mb-0 table table-striped table-bordered table-hover"
  );
  let cols = [""].concat(years);
  let tr = table.insertRow(-1);
  cols.forEach((headerText) => {
    let th = document.createElement("th"); // Create a new header cell
    th.innerText = headerText; // Set the text of the header cell
    tr.appendChild(th); // Add the header cell to the row
  });
  tableDiv.innerHTML = "";
  tableDiv.appendChild(table);
  tableTitle.innerText = visualOptions["table-title"].replace("replace_with_health_authority", location);

  // check for a specific location, if it exists, use that to filter the data
  if (location) {
    tabularData = tabularData[location]
  }
  
  for (const [key, value] of Object.entries(tabularData)){
    let tr = table.insertRow(-1);
    tr.setAttribute("class", "align-middle");
    let tabCell = tr.insertCell(-1);
    if (key.toLowerCase().includes("total")){
      tabCell.innerText = key.toTitleCase();
    } else{
      tabCell.innerText = visualOptions[`table-counts-row`].replace("replace_me", key.toTitleCase());
    }
    value.forEach((element) => {
      let tabCell = tr.insertCell(-1);
      tabCell.innerText = element;
    });
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
        "data": { }, 
        "visual_options": secondLevelObject["visual_options"] || {},
        "tabular_data": secondLevelObject["tabular_data"] || {},
      }
      returnObject["visual_options"]["location"] = location.toTitleCase();
      for (const [key, value] of Object.entries(secondLevelData)){
        returnObject["data"][key] = value[location];
      }
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
function resetVisualControl(toggleOnly = false) {
  let dataTypeToggle = document.getElementById("data-type-toggle");
  // reset the count/rate toggle so that 
  dataTypeToggle.innerHTML = "";
  // remove the back button if it exists and toggle only is false
  let backButton = document.getElementById("back-button");
  if (backButton != null && !toggleOnly) {
    backButton.classList.add("d-none");
  }
}

// Function to set the active visual in the menu
function setActiveVisual(province, currentVisual) {
  // Remove the active class from all visuals
  Array.from(document.querySelectorAll(".nav-link")).forEach((element) => {
    element.classList.remove("active");
  });
  
  // Add the active class to the current visual
  let currentVisualElement = document.getElementById(currentVisual);
  if (currentVisualElement) {
    currentVisualElement.classList.add("active");
  } else {
    console.error(`No element found with id ${currentVisual}`);
  }

  // Add the active class to the parent dropdown if it exists
  let parentElement = document.querySelector(`#${visuals[province][currentVisual]["parent"].toLowerCase().replace(/ /g, "-")}-dropdown`);
  if (parentElement) {
    parentElement.classList.add("active");
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


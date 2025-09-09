// Global variables to hold the current data and geojson
let currentData;
let currentGeojson;
let currentVisual;
let province;
let route = [];
let lastLocation = null;

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
    // ensure the visual is a 1st level visual
    if (details["level"] !== 1) {
      continue;
    }

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
          masterLoop();
        };
        break;
      case "line":
        a.onclick = function () {
          resetVisualControl();
          currentVisual = visual;
          masterLoop();
        };
        break;
      case "bar":
        a.onclick = function () {
          resetVisualControl();
          currentVisual = visual;
          masterLoop();
        };
        break;
      case "map":
        a.onclick = function () {
          currentVisual = visual;
          masterLoop();
        };
        break;
    }
    
    // append the menu item to the appropriate parent category
    document.getElementById(`${details["menu-parent"].toLowerCase().replace(/ /g, "-")}-dropdown-menu`).appendChild(li);
  }

  for (const parent of parentCategories) {
  let dropdownMenu = document.getElementById(`${parent.toLowerCase().replace(/ /g, "-")}-dropdown-menu`);
  if (dropdownMenu.children.length === 0) {
    // Find the parent li element
    let parentLi = document.querySelector(`li.dropdown > a#${parent.toLowerCase().replace(/ /g, "-")}-dropdown`).parentElement;
    
    // Clear the li content and replace with a disabled link
    parentLi.innerHTML = "";
    parentLi.className = "nav-item"; // Remove dropdown class
    
    let disabledA = document.createElement("a");
    disabledA.className = "nav-link disabled";
    disabledA.href = "";
    disabledA.textContent = `${parent}`;
    
    parentLi.appendChild(disabledA);
  }
}
}

// Function to initialize the data by fetching provincial data
async function fetchRegionData(province){
    console.log(`Fetched data for ${province}`);
    //fetch the data and unpack it
    const [data, geojson] = await Promise.all([
        fetch("/static/js/visual_data.json", {AbortSignal: AbortSignal.timeout(5000)}),
        fetch(`/static/assets/geojsons/${province.toLowerCase()}.geojson`, {AbortSignal: AbortSignal.timeout(5000)}),
    ]);
    const dataJson = await data.json();
    const geojsonJson = await geojson.json();
    currentData = dataJson[province];
    currentGeojson = geojsonJson;
}

//Master function to initialize all visuals given the province and what the visual is
function masterLoop(location = null, year = null, category = null) {
  // Check the level, if 1, reset the route, if not, setup the back and reset buttons, and also pull data while looping
  let visualData;
  if (visuals[province][currentVisual]["level"] === 1) {
    visualData = currentData[currentVisual];
    lastLocation = null;
    route = [];
    resetVisualControl();
  } else if (visuals[province][currentVisual]["level"] === 2) {
    setupBackButton();
    visualData = getSecondLevelData(province, location);
  } else {
    setupBackButton();
    setupResetButton();
    lastLocation = location;
    visualData = getSecondLevelData(province, location, year, category);
  }
  let dataType;
  if (visuals[province][currentVisual]["type"] !== "map") {
    dataType = visuals[province][currentVisual]["data-types"][0];
  }

  //run the creation function for the visual based on its type
  switch (visuals[province][currentVisual]["type"]) {
    case "heatmap":
      createVisualHeatMap(province, currentVisual, currentGeojson, currentData[currentVisual]["data"]["counts"], currentData[currentVisual]["data_source"], currentData[currentVisual]["visual_options"]);
      break;
    case "line":
      createVisualLine(province, visualData["data"], currentVisual, dataType, currentData[currentVisual]['data_source'], currentData[currentVisual]['visual_options'], currentData[currentVisual]['additional_rows'] || null);
      break;
    case "bar":
      createVisualBar(province, visualData["data"], currentVisual, dataType, currentData[currentVisual]['data_source'], currentData[currentVisual]['visual_options']);
      break;
    case "map":
      createVisualMap(province, currentVisual, currentGeojson, currentData[currentVisual]["visual_options"]);
      break;
    case "pie":
      createVisualPie(province, visualData['data'], currentData[currentVisual]['data_source'], currentData[currentVisual]['visual_options'], currentData[currentVisual]['tabular_data'], location);
      break;
  }
}

// Function to generate heatmaps
async function createVisualHeatMap(province, visualToGen, geojson, mapData, mapSource, mapOptions){
  // Setup the map container and other elements
  let visDiv = document.getElementById("vis-div");
  let aboutDataDiv = document.getElementById("about-data");
  let tableDiv = document.getElementById("data-table");
  let table = document.createElement("table");
  let tableTitle = document.getElementById("table-title");
  let dataSlider = [];
  let steps = [];
  
  // remove the active class from other visuals and add it to the current visual
  setActiveVisual(province, visualToGen);

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
      if (!visuals[province][visualToGen]["next-vis"]) {
        console.error("No next visual exists for this visual");
        return;
      } else {
        let location = data.points[0].location; // Get the clicked location
        moveUpOneLevel(province);
        masterLoop(location)     
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
        if (!visuals[province][currentVisual]["next-vis"]) {
          console.error("No second level visual exists for this visual");
        } else {
          let location = data.points[0].location; // Get the clicked location
          moveUpOneLevel(province);
          masterLoop(location);
        }
      }
    });
  });
};

// create line chart
async function createVisualLine(province, lineData, currentVisual, dataType, lineSource, visualOptions, additionalRows = null, dataTypes = null){
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
  // Check to see if we have count, percentage, or rate data, default to count if not specified
  if (dataType !== null){
    traceData = lineData[dataType];
  } else if (lineData["counts"]) {
    traceData = lineData["counts"];
    dataType = "counts";
  } else if (lineData["rates"]) {
    traceData = lineData["rates"];
    dataType = "rates";
  } else if (lineData["percentages"]){
    traceData = lineData["percentages"];
    dataType = "percentages";
  } else {
    console.error("No counts, percentages, or rates data found in lineData");
    return;
  }
  
  // check to see if we have a total
  totalPresent = !!("total_y" in traceData);

  for (const [key, value] of Object.entries(traceData)) {
    // create a trace for each y value in the lineData object entry
    if (key != "x"){

      let filteredData = filterLeadingZeros(traceData["x"], value);

      if (filteredData.x.length > 0) {
        let trace = {
          x: filteredData.x,
          y: filteredData.y,
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
          text: visualOptions[`${dataType}-y-axis-title`].replace("replace_with_health_authority", visualOptions["location"] || "").replace("replace_with_category", visualOptions["category"] || ""),
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
      title: visualOptions[`${dataType}-title`].replace("replace_with_health_authority", visualOptions["location"] || "").replace("replace_with_category", visualOptions["category"] || ""),
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
        subValue.forEach((element, index) => {
          let tabCell = tr.insertCell(-1);
          tabCell.innerText = formatTableValue(element, index, subValue);
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
      value.forEach((element, index) => {
        let tabCell = tr.insertCell(-1);
        tabCell.innerText = formatTableValue(element, index, value);
      });
    }
  }

  // If there is more than one data type available, create a toggle to switch between each data type
  if (dataTypes == null) {
    dataTypes = visuals[province][currentVisual]["data-types"];
  }

  if (dataTypes.length > 1) {
    for (const type of dataTypes) {
      // Create a toggle for each data type
      let toggle = document.createElement("input");
      toggle.type = "radio";
      toggle.className = "btn-check";
      toggle.name = "data-toggle";
      toggle.id = `${type}-toggle`;
      toggle.autocomplete = "off";
      if (dataType === type) {
        toggle.checked = true;
      }
      // Add an event listener to the toggle
      toggle.onclick = function () {
        // Reset the count/rate toggle
        resetVisualControl();
        // Recreate the line visual with the selected data type
        createVisualLine(province, lineData, currentVisual, type, lineSource, visualOptions, additionalRows, dataTypes);
      };
      let label = document.createElement("label");
      label.className = "btn btn-outline-primary";
      label.setAttribute("for", `${type}-toggle`);
      label.innerText = type.charAt(0).toUpperCase() + type.slice(1);
      
      dataTypeToggle.appendChild(toggle);
      dataTypeToggle.appendChild(label);
    }
  }

  // Generate the About these Data section and insert the html
  let header = `<h4 class="card-title text-center"> About these Data</h4>
  <hr />
  <h5 class="text-center">This data set was last updated in ${lineSource["last_updated"] + " "} and contains data up until ${lineSource["data_until"]}.</h5>
  `;
  let button = `<div class="text-center pb-3">
    <a target="_blank" href="${lineSource["link"]}" role="button"
          class="btn btn-primary">${lineSource["name"]}</a>
  </div>
  `;
  let aboutHTML = `${header}
  ${lineSource["about"]}
  <br></br>
  ${button}
  `;
  aboutDataDiv.innerHTML = aboutHTML;
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
        hoverinfo: visualOptions["hover-info"],
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
      hoverlabel: {
        namelength: -1,
      },
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
      hovermode: visualOptions["hover-type"],
      autosize: false,
      width: $("#viz-card").width(),
      height:
        window.innerWidth > 768
          ? $("#viz-card").height()
          : $("#viz-card").height(),
      title: visualOptions[`${dataType}-title`].replace("replace_with_health_authority", visualOptions["location"] || "").replace("replace_with_category", visualOptions["category"] || ""),
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
  tableTitle.innerText = visualOptions["table-title"].replace("replace_with_health_authority", visualOptions["location"] || "").replace("replace_with_category", visualOptions["category"] || "");
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

async function createVisualPie(province, pieData, pieSource, visualOptions, tabularData, location = null) {
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
      name: `${year}/${location}`,
      type: "pie",
      labels: Object.keys(pieData["counts"][year]),
      values: Object.values(pieData["counts"][year]),
      textinfo: "label",
      hoverinfo: "label+value+percent",
      visible: year === years[0],
      noValueFlag: false, // Flag to indicate if there are no values for this year
    }
    if (Object.values(pieData["counts"][year]).some(value => value === 0)) { //remove key pair from the chartData values and lables if value is 0
      for (let i = 0; i < chartData["values"].length; i++) {
        if (chartData["values"][i] === 0) {
          chartData["values"].splice(i, 1);
          chartData["labels"].splice(i, 1);
          i--; // Adjust index after removal
        }
      }
      if (chartData["values"].length === 0) {
        chartData["noValueFlag"] = true; // Set flag if no values remain
        chartData["values"] = [1]; // Set a dummy value to display the pie
        chartData["textinfo"] = "label";
        chartData["showlegend"] = false;
        chartData["labels"] = [`No data available for ${year}`];
        chartData["hoverinfo"] = "none";
        chartData["marker"] = {
          colors: ["#d3d3d3"], // Light gray color for no data
        };
      }
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
    visDiv.on("plotly_click", function (data) {
      if (!visuals[province][currentVisual]["next-vis"]) {
        console.error("No next visual exists for this visual");
        return;
      } else if (data.points[0]["fullData"]["labels"][0].includes("No data available for ")) {
        // If the clicked pie chart has no data, do nothing
        console.warn("No data available for this year");
        return;
      } else {
        let year = data.points[0]["fullData"]["name"].split("/")[0]; // Get the clicked year
        let location = data.points[0]["fullData"]["name"].split("/")[1]; // Get the clicked location
        let category = data.points[0]["label"]; // Get the clicked category
        moveUpOneLevel(province);
        masterLoop(location, year, category)     
      }
    });
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

// Helper function to get the data for the next level of the visual
function getSecondLevelData(province, location = null, year = null, category = null) {
  // Find the second level of the visual from the object
  try {
    let secondLevelObject = currentData[currentVisual];
    let visualType = visuals[province][currentVisual]["type"];

    // separate out the second level data into the data source, and actual data
    let secondLevelDataSource = secondLevelObject["data_source"]
    let secondLevelData = secondLevelObject["data"]

    let returnObject;
    // if there's a specific location, pull  the data from that location
    if (location != null && currentVisual != "regional_drug_supply_breakdown"){
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
    } else if (location != null && currentVisual == "regional_drug_supply_breakdown") { //Find a way to consolidate this else if!
      returnObject = {
        "type": visualType,
        "data_source": secondLevelDataSource,
        "data": { }, 
        "visual_options": secondLevelObject["visual_options"] || {},
        "tabular_data": secondLevelObject["tabular_data"] || {},
      }
      returnObject["visual_options"]["location"] = location.toTitleCase();
      returnObject["visual_options"]["category"] = category.toTitleCase();
      returnObject["visual_options"]["year"] = year;
      returnObject["data"]["counts"] = secondLevelData["counts"][location][year][category];
      returnObject["data"]["counts"]["x"] = [year]
      return returnObject;

    } else {
      return secondLevelData;
    }
  }
  catch {
    console.warn("No second level visual exists");
    return null;
  }
}

// Helper function to move up one level in the visual hierarchy
function moveUpOneLevel(province, prevLocation = null) {
  // set the current visual to the next-level of the current visual
  nextLevel = visuals[province][currentVisual]["next-vis"];

  if (nextLevel == null) {
    console.error("No next level visual exists for this visual");
    return;
  }
  if (prevLocation != null) {
    route.push(`${currentVisual}/${prevLocation}`);
  } else {
    route.push(currentVisual);
  }
  currentVisual = visuals[province][currentVisual]["next-vis"];
}

// Helper function to reset the count/rates toggle
function resetVisualControl() {
  let dataTypeToggle = document.getElementById("data-type-toggle");
  // reset the count/rate toggle so that 
  dataTypeToggle.innerHTML = "";
  // remove the back button if it exists and toggle only is false
  if (route.length == 0){
    let backButton = document.getElementById("back-button");
    let resetButton = document.getElementById("reset-button");
    if (backButton.classList.contains("d-none") != true) {
      backButton.classList.add("d-none");
    }
    if (resetButton.classList.contains("d-none") != true) {
      resetButton.classList.add("d-none");
    }
  }
}

// Helper function to setup the back button
function setupBackButton() {
  let backButton = document.getElementById("back-button");
  backButton.classList.remove("d-none");
  backButton.onclick = function () {
    currentVisual = route.pop();
    masterLoop(lastLocation);
  };
}

// Helper function to setup the reset button
function setupResetButton() {
  let resetButton = document.getElementById("reset-button");
  resetButton.classList.remove("d-none");
  resetButton.onclick = function () {
    currentVisual = route[0];
    masterLoop();
  };
}

// Helper function to remove leading zeros and return filtered data
function filterLeadingZeros(xArray, yArray) {
  let firstNonZeroIndex = yArray.findIndex(value => value !== 0 && value !== "0");
  if (firstNonZeroIndex === -1) {
    // All values are zero, return empty arrays
    return { x: [], y: [] };
  }
  return {
    x: xArray.slice(firstNonZeroIndex),
    y: yArray.slice(firstNonZeroIndex)
  };
}

//Helper function to convert leading zeroes in table data to "No Data"
function formatTableValue(value, index, array){
  //first non-zero index finder
  let firstNonZero = array.findIndex(val => val !== 0 && val !== "0");
  if (index < firstNonZero){
    return "No Data";
  } else {
    return value;
  }
}

// Function to set the active visual in the menu
function setActiveVisual(province, currentVisual) {
  // If the visual is not a first level, no change in menu is needed
  if (visuals[province][currentVisual]["level"] != 1) {
    return;
  }

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
  if (route.length > 0) {
    let parentElement = document.querySelector(`#${route[0].toLowerCase().replace(/ /g, "-")}-dropdown`);
    parentElement.classList.add("active");
  } else {
    let parentElement = document.querySelector(`#${visuals[province][currentVisual]["menu-parent"].toLowerCase().replace(/ /g, "-")}-dropdown`);
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
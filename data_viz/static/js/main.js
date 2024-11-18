// Set about data html strings
let bccsu_html = `
<h4 class="card-title text-center"> About this Data</h4>
<hr />
<p class="about-viz-text">This data is collected from the British Columbia Centre on Substance
    Use (BCCSU) and is based on voluntary drug testing results. The data is collected from
    samples
    provided by individuals and organizations in British Columbia. The data is collected to help
    inform the public about the drug supply in British Columbia and to help inform harm
    reduction strategies. Please note that this data is not representative of the entire illicit
    drug supply in British Columbia, but rather provides a snapshot of the drug supply based on
    voluntary submissions.
    <br>
    <br>
    For more information, visit the BCCSU website by clicking the button below:
</p>
<div class="text-center pb-3">
    <a target="_blank" href="https://www.bccsu.ca/" role="button"
        class="btn btn-primary">BCCSU</a>
</div>
`;

let bccs_html = `
<h4 class="card-title text-center"> About this Data</h4>
<hr />
<p class="about-viz-text">This data has been collected by the British Columbia Coroners Service (BCCS), and is based on toxicology reports from individuals who have died in British Columbia where the cause of death was determined to be unregulated drugs and/or drugs sold illicitly, and does not include deaths related to an individuals prescribed drugs, or intentional deaths due to toxicity. The data is updated monthly by the BCCS.
    <br>
    <br>
    For more information, visit the BCCS website by clicking the button below:
</p>
<div class="text-center pb-3">
    <a target="_blank" href="eyJrIjoiNzE0ZmZmNTctOTE3Ny00ZmI5LTliNjctNWQ5NWI1MTI0OTJhIiwidCI6IjZmZGI1MjAwLTNkMGQtNGE4YS1iMDM2LWQzNjg1ZTM1OWFkYyJ9" role="button"
        class="btn btn-primary">BCCS</a>
</div>
`;

let skcs_html = `
<h4 class="card-title text-center"> About this Data</h4>
<hr />
<p class="about-viz-text">This data has been collected by the Saskatchewan Coroners Service (SKCS), and is based on toxicology reports from individuals who have died in Saskatchewan where the cause of death was confirmed, or suspected to be, drug toxicity. The data is updated monthly by the SKCS
    <br>
    <br>
    For more information, visit the SKCS website to view the PDF report by clicking the button below:
</p>
<div class="text-center pb-3">
    <a target="_blank" href="https://publications.saskatchewan.ca/#/products/90505" role="button"
        class="btn btn-primary">SKCS</a>
</div>
`;

// for mobile nav
let navToggle = document.querySelector(".nav-toggle");
let bars = document.querySelectorAll(".bar");
let navLinks = document.querySelectorAll(".nav-title, .mobile-nav-link");
let mobileTitle = document.querySelector(".mobile-title");
let navLinkContainer = document.querySelector("#mobile-nav-link-container");

function toggleHamburger(e) {
  bars.forEach((bar) => bar.classList.toggle("x"));
  navToggle.classList.toggle("menu-active");
  navLinks.forEach((link) => link.classList.toggle("visible"));
  navLinkContainer.classList.toggle("expanded");
  mobileTitle.classList.toggle("visible");
}

mobileTitle.addEventListener("click", function () {
  if (navLinkContainer.classList.contains("expanded")) {
    toggleHamburger();
  }
});
navToggle.addEventListener("click", toggleHamburger);
navLinks.forEach((link) => link.addEventListener("click", toggleHamburger));

// for feedback toggle
let feedbackToggle = document.querySelector(".feedback-toggle");
let feedbackContent = document.querySelector(".feedback-content-container");
let feedbackClose = document.querySelector(".feedback-close");

function toggleFeedback() {
  console.log("clicked");
  feedbackContent.classList.toggle("feedback-visible");
  feedbackToggle.classList.toggle("feedback-toggle-invisible");
}

feedbackToggle.addEventListener("click", toggleFeedback);
feedbackClose.addEventListener("click", toggleFeedback);

// General functions to fetch data, create charts, etc. within the templates
function fetchData(data_file, functionCallback) {
  return fetch(data_file)
    .then((response) =>
      response.ok ? response.json() : Promise.reject(response)
    )
    .then((data) => {
      functionCallback(data);
      return data;
    });
}

let data_promise;

// Code Below is for the BC Page
async function bcInit() {
  data_promise = fetchData("/static/js/bc_vis.json", createCategoryChartBC);
  await data_promise;
}

// Function to change the chart type
function changeChartBC(buttonElement, parentID) {
  Array.from(document.querySelectorAll(".active")).forEach((element) => {
    element.classList.remove("active");
  });
  data_promise.then((data) => {
    switch (buttonElement.id) {
      case "fent-and-benzos":
        createFentBenzChartBC(data);
        break;
      case "types-of-opioids":
        createOpioidTypeChartBC(data);
        break;
      case "drugs-by-category":
        createCategoryChartBC(data, false);
        break;
      case "deaths-by-drug":
        createDeathDrugChartBC(data);
        break;
      default:
        console.error("Unknown button ID:", buttonElement.id);
    }
    buttonElement.classList.add("active");
  });
  if (parentID != null) {
    document.getElementById(parentID).classList.add("active");
  }
}

function createCategoryChartBC(data, first_run = true) {
  let visDiv = document.getElementById("bc-vis-div");
  let data_obj = data["bc_drugs_by_category"];
  let aboutDataDiv = document.getElementsByClassName("about-data-div")[0];
  let traces = [];
  for (const [key, value] of Object.entries(data_obj)) {
    let trace = {
      x: data_obj[key]["x"],
      y: data_obj[key]["y"],
      type: "scatter",
      mode: "lines",
      name: key,
    };
    traces.push(trace);
  }
  if (first_run) {
    visDiv.innerHTML = "";
  }
  let vis = Plotly.react(
    visDiv,
    traces,
    (layout = {
      yaxis: {
        fixedrange: true,
        title: "Percent of Samples Belonging to Category of Drug",
      },
      xaxis: { fixedrange: true, title: "Year" },
      hovermode: "x unified",
      autosize: false,
      width: $("#viz-card").width(),
      height: $("#viz-card").height(),
      title: "Makeup of British Columbia Drug Supply by Category and Year",
    }),
    (config = {
      displaylogo: false,
    })
  );
  // Replace the tabular section with table data for this vis
  raw_obj = data["bc_raw_drug_category"];
  let table_div = document.getElementById("data-table");
  let table = document.createElement("table");
  let table_title = document.getElementById("table-title");
  table.setAttribute(
    "class",
    "mb-0 table table-striped table-bordered table-hover"
  );
  let cols = [""].concat(raw_obj["years"]);
  let tr = table.insertRow(-1);
  cols.forEach((headerText) => {
    let th = document.createElement("th"); // Create a new header cell
    th.innerText = headerText; // Set the text of the header cell
    tr.appendChild(th); // Add the header cell to the row
  });
  table_div.innerHTML = "";
  table_div.appendChild(table);
  table_title.innerText =
    "Makeup of British Columbia Drug Supply by Category and Year";
  aboutDataDiv.innerHTML = bccsu_html;
  for (const [key, value] of Object.entries(raw_obj)) {
    if (key != "years") {
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
}

function createFentBenzChartBC(data) {
  let visDiv = document.getElementById("bc-vis-div");
  let fent_data = data["bc_percent_fentanyl"];
  let benz_data = data["bc_percent_benzo"];
  let fent_trace = {
    x: fent_data["x"],
    y: fent_data["y"],
    type: "scatter",
    mode: "lines",
    name: "Fentanyl",
  };
  let benz_trace = {
    x: benz_data["x"],
    y: benz_data["y"],
    type: "scatter",
    mode: "lines",
    name: "Benzodiazepines",
  };
  traces = [fent_trace, benz_trace];
  let vis = Plotly.react(
    visDiv,
    traces,
    (layout = {
      yaxis: { fixedrange: true, title: "Percent of Samples Containing Drug" },
      xaxis: { fixedrange: true, title: "Year" },
      hovermode: "x unified",
      autosize: false,
      width: $("#viz-card").width(),
      height: $("#viz-card").height(),
      title:
        "Percent of Voluntarily Provided Samples Containing Fentanyl or <br> Benzodiazepines in British Columbia by Year",
    }),
    (config = {
      displaylogo: false,
    })
  );
  // Replace the tabular section with table data for this vis
  raw_obj = data["bc_raw_fent_benz"];
  let table_title = document.getElementById("table-title");
  let table_div = document.getElementById("data-table");
  let aboutDataDiv = document.getElementsByClassName("about-data-div")[0];
  table_div.innerHTML = "";
  let table = document.createElement("table");
  table.setAttribute(
    "class",
    "mb-0 table table-striped table-bordered table-hover"
  );
  let cols = [""].concat(raw_obj["years"]);
  let tr = table.insertRow(-1);
  cols.forEach((headerText) => {
    let th = document.createElement("th"); // Create a new header cell
    th.innerText = headerText; // Set the text of the header cell
    tr.appendChild(th); // Add the header cell to the row
  });
  table_div.appendChild(table);
  table_title.innerText =
    "Samples Containing Fentanyl or Benzodiazepines in British Columbia";
  aboutDataDiv.innerHTML = bccsu_html;
  for (const [key, value] of Object.entries(raw_obj)) {
    if (key != "years") {
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
}

function createOpioidTypeChartBC(data) {
  let visDiv = document.getElementById("bc-vis-div");
  let aboutDataDiv = document.getElementsByClassName("about-data-div")[0];
  let data_obj = data["bc_opioids_by_type"];
  let traces = [];
  for (const [key, value] of Object.entries(data_obj)) {
    let trace = {
      x: data_obj[key]["x"],
      y: data_obj[key]["y"],
      type: "scatter",
      mode: "lines",
      name: key,
    };
    traces.push(trace);
  }
  let vis = Plotly.react(
    visDiv,
    traces,
    (layout = {
      yaxis: {
        fixedrange: true,
        title: "Percent of Samples Belonging to Category of Drug",
      },
      xaxis: { fixedrange: true, title: "Year" },
      hovermode: "x unified",
      autosize: false,
      width: $("#viz-card").width(),
      height: $("#viz-card").height(),
      title:
        "Types of Opioids in the British Columbia Drug Supply by Year <br> as per Voluntary Drug Testing Results",
    }),
    (config = {
      displaylogo: false,
    })
  );
  // Replace the tabular section with table data for this vis
  raw_obj = data["bc_raw_opioid_type"];
  let table_title = document.getElementById("table-title");
  let table_div = document.getElementById("data-table");
  table_div.innerHTML = "";
  let table = document.createElement("table");
  table.setAttribute(
    "class",
    "mb-0 table table-striped table-bordered table-hover"
  );
  let cols = [""].concat(raw_obj["years"]);
  let tr = table.insertRow(-1);
  cols.forEach((headerText) => {
    let th = document.createElement("th"); // Create a new header cell
    th.innerText = headerText; // Set the text of the header cell
    tr.appendChild(th); // Add the header cell to the row
  });
  table_div.appendChild(table);
  table_title.innerText =
    "Types of Opioids in the British Columbia Drug Supply by Year";
  aboutDataDiv.innerHTML = bccsu_html;
  for (const [key, value] of Object.entries(raw_obj)) {
    if (key != "years") {
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
}

function createDeathDrugChartBC(data) {
  let visDiv = document.getElementById("bc-vis-div");
  let aboutDataDiv = document.getElementsByClassName("about-data-div")[0];
  let deaths_obj = data["bc_total_deaths"];
  let drugs_obj = data["bc_deaths_by_drug"];
  let traces = [];
  let deaths_trace = {
    x: deaths_obj["years"],
    y: deaths_obj["total_deaths"],
    type: "scatter",
    mode: "lines",
    name: "Total Drug Toxicity Deaths",
  };
  traces.push(deaths_trace);
  for (const [key, value] of Object.entries(drugs_obj)) {
    let trace = {
      x: deaths_obj["years"],
      y: drugs_obj[key]["deaths"],
      type: "bar",
      name: key,
    };
    traces.push(trace);
  }
  let vis = Plotly.react(
    visDiv,
    traces,
    (layout = {
      yaxis: { fixedrange: true, title: "Number of Deaths" },
      xaxis: { fixedrange: true, title: "Year" },
      hovermode: "x unified",
      autosize: false,
      width: $("#viz-card").width(),
      height: $("#viz-card").height(),
      title:
        "Drug Toxicity Deaths in British Columbia by Year and Drug Causing Death",
    }),
    (config = {
      displaylogo: false,
    })
  );
  // Replace the tabular section with table data for this vis
  raw_obj = data["bc_raw_deaths_by_drug"];
  let table_title = document.getElementById("table-title");
  let table_div = document.getElementById("data-table");
  table_div.innerHTML = "";
  let table = document.createElement("table");
  table.setAttribute(
    "class",
    "mb-0 table table-striped table-bordered table-hover"
  );
  let cols = [""].concat(deaths_obj["years"]);
  let tr = table.insertRow(-1);
  cols.forEach((headerText) => {
    let th = document.createElement("th"); // Create a new header cell
    th.innerText = headerText; // Set the text of the header cell
    tr.appendChild(th); // Add the header cell to the row
  });
  table_div.appendChild(table);
  table_title.innerText =
    "Drug Toxicity Deaths in British Columbia by Year and Drug Causing Death";
  aboutDataDiv.innerHTML = bccs_html;
  for (const [key, value] of Object.entries(raw_obj)) {
    if (key != "years") {
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
}

// Code Below is for the SK Page
async function skInit() {
  data_promise = fetchData("/static/js/sask_vis.json", createCategoryChartSK);
  await data_promise;
}

// Function to change the chart type
function changeChartSK(buttonElement, parentID) {
  Array.from(document.querySelectorAll(".active")).forEach((element) => {
    element.classList.remove("active");
  });
  switch (buttonElement.id) {
    case "fent-and-benzos":
      data_promise.then((data) => createFentBenzChart(data));
      break;
  }
  buttonElement.classList.add("active");
  if (parentID != null) {
    document.getElementById(parentID).classList.add("active");
  }
}

// Funciton to create the chart depicting type of opioid death
function createCategoryChartSK(data) {
  let raw_obj = data["raw_data"];
  let vis_div = document.getElementById("sk-vis-div");
  let years = data["years"];
  let total_deaths = data["total_deaths"];
  let deaths_by_drug = data["drug_deaths"];
  let percents_by_drugs = data["drug_percents"];
  let aboutDataDiv = document.querySelector(".about-data-div");
  traces = [];
  let total_trace = {
    x: years,
    y: total_deaths,
    type: "Scatter",
    mode: "lines",
    name: "Total Drug Toxicity Deaths",
  };
  traces.push(total_trace);
  for (let drug in deaths_by_drug) {
    let drug_trace = {
      x: years,
      y: deaths_by_drug[drug],
      type: "bar",
      name: drug,
    };
    traces.push(drug_trace);
  }
  let layout = {
    yaxis: { fixedrange: true, title: "Number of Drug Toxicity Deaths" },
    xaxis: { fixedrange: true, title: "Year" },
    hovermode: "x unified",
    autosize: false,
    width: $("#viz-card").width(),
    height: $("#viz-card").height(),
    title: "Saskatchewan Drug Toxicity Deaths by Type of Drug",
  };
  vis_div.innerHTML = "";
  let vis = Plotly.react(
    vis_div,
    traces,
    layout,
    (config = {
      displaylogo: false,
    })
  );

  // Create the table and add in tabular data
  let table_div = document.getElementById("data-table");
  let table = document.createElement("table");
  let table_title = document.getElementById("table-title");
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
  table_div.innerHTML = "";
  table_div.appendChild(table);
  table_title.innerText = "Saskatchewan Drug Toxicity Deaths by Type of Drug";
  aboutDataDiv.innerHTML = skcs_html;
  for (const [key, value] of Object.entries(raw_obj)) {
    if (key != "years") {
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
}

let tox_data;

async function toxSetUp() {
  tox_data = fetch("static/js/visualization_data.json")
    .then((response) => (data = response.json()))
    .then((data) => {
      let provinceMappings = {
        "British Columbia": "bc_line_y",
        Saskatchewan: "sk_line_y",
        Canada: "can_line_y",
      };
      totals = [];
      for (let i = 0; i < data.total_deaths.can_line_x.length; i++) {
        totals.push(
          data.total_deaths.bc_line_y[i] + data.total_deaths.sk_line_y[i]
        );
      }
      // Create the initial table of values
      let cols = ["Province"].concat(data.total_deaths.can_line_x);
      let rows = Object.keys(provinceMappings).filter(
        (province) => province !== "Canada"
      );
      rows = rows.concat(["Total"]);
      let table = document.createElement("table");
      table.setAttribute(
        "class",
        "table table-striped table-bordered table-hover"
      );
      let tr = table.insertRow(-1);
      cols.forEach((headerText) => {
        let th = document.createElement("th"); // Create a new header cell
        th.innerText = headerText; // Set the text of the header cell
        tr.appendChild(th); // Add the header cell to the row
      });
      rows.forEach((province) => {
        let tr = table.insertRow(-1);
        let cell = tr.insertCell(-1);
        cell.innerText = province;
        data.total_deaths.can_line_x.forEach((year) => {
          let cell = tr.insertCell(-1);
          if (province === "Total") {
            cell.innerText = totals[data.total_deaths.can_line_x.indexOf(year)];
          } else {
            cell.innerText =
              data.total_deaths[provinceMappings[province]][
                data.total_deaths.can_line_x.indexOf(year)
              ];
          }
        });
      });
      let tableDiv = document.getElementById("data-table");

      // Create the initial visual
      visDiv = document.getElementById("toxicity-deaths-vis");
      let skTrace = {
        x: data.total_deaths.can_line_x,
        y: data.total_deaths.sk_line_y,
        type: "scatter",
        name: "Saskatchewan",
        stackgroup: "one",
        animate: true,
        marker: { color: "rgba(7, 106, 33, 1)" },
      };
      let bcTrace = {
        x: data.total_deaths.can_line_x,
        y: data.total_deaths.bc_line_y,
        type: "scatter",
        name: "British Columbia",
        stackgroup: "one",
        animate: true,
        marker: { color: "rgba(0, 71, 187, 1)" },
      };
      let canTrace = {
        x: [2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023],
        y: totals,
        type: "scatter",
        stackgroup: "two",
        name: "Total of Selected",
        animate: true,
        marker: { color: "rgba(206, 17, 38, 1)" },
        fill: "none",
      };
      let plots = [skTrace, bcTrace, canTrace];
      let colourCode = {
        "British Columbia": "rgba(0, 71, 187, 1)",
        Saskatchewan: "rgba(7, 106, 33, 1)",
        Canada: "rgba(206, 17, 38, 1)",
      };

      // Add the created elements to the page
      visDiv.innerHTML = "";
      tableDiv.innerHTML = "";
      tableDiv.appendChild(table);
      let vis = Plotly.react(
        visDiv,
        plots,
        (layout = {
          yaxis: {
            fixedrange: true,
            title: "Deaths Resulting from Drug Toxicity",
          },
          xaxis: { fixedrange: true, title: "Year" },
          hovermode: "x unified",
          autosize: false,
          width: $("#viz-card").width(),
          height: $("#viz-card").height(),
          title: "Canadian Drug Toxicity Deaths Each Year by Province",
        }),
        (config = {
          displaylogo: false,
        })
      );
      return {
        vis: vis,
        plots: plots,
        visDiv: visDiv,
        raw_data: data,
        colourCode: colourCode,
        provinceMappings: provinceMappings,
      };
    });
}

function changeChartType(selector) {
  tox_data.then(
    ({ vis, plots, visDiv, raw_data, colourCode, provinceMappings }) => {
      if (selector.value == "2") {
        let active = document.querySelectorAll(`input[status="active"]`);
        active = Array.from(active).map((button) =>
          button.getAttribute("province")
        );
        let plotsToAdd = [];
        for (let plot of plots) {
          if (active.includes(plot.name) && plot.name != "Total of Selected") {
            plotsToAdd.push(plot);
          }
        }
        plotsToAdd[0].groupnorm = "percent";
        Plotly.react(
          visDiv,
          plotsToAdd,
          (layout = {
            yaxis: {
              fixedrange: true,
              title:
                "Percent of Deaths Resulting from Drug Toxicity in Selected Provinces",
            },
            xaxis: { fixedrange: true, title: "Year" },
            hovermode: "x unified",
            autosize: false,
            width: $("#viz-card").width(),
            height: $("#viz-card").height(),
            title: "Canadian Drug Toxicity Deaths Each Year by Province",
          }),
          (config = {
            displaylogo: false,
          })
        );
      } else if (selector.value == "1") {
        let active = document.querySelectorAll(`input[status="active"]`);
        active = Array.from(active).map((button) =>
          button.getAttribute("province")
        );
        let plotsToAdd = [];
        for (let plot of plots) {
          if (active.includes(plot.name) || plot.name === "Total of Selected") {
            plotsToAdd.push(plot);
          }
          if (plot.groupnorm != undefined) {
            delete plot.groupnorm;
          }
        }
        Plotly.react(
          visDiv,
          plotsToAdd,
          (layout = {
            yaxis: {
              fixedrange: true,
              title: "Deaths Resulting from Drug Toxicity",
            },
            xaxis: { fixedrange: true, title: "Year" },
            hovermode: "x unified",
            autosize: false,
            width: $("#viz-card").width(),
            height: $("#viz-card").height(),
            title: "Canadian Drug Toxicity Deaths Each Year by Province",
          }),
          (config = {
            displaylogo: false,
          })
        );
      }
    }
  );
}

function updateTracesTox(button) {
  tox_data.then(
    ({ vis, plots, visDiv, raw_data, colourCode, provinceMappings }) => {
      let status = button.getAttribute("status");
      let region = button.getAttribute("province");
      let index = plots.findIndex((object) => {
        return object.name === region;
      });
      let totalIndex = plots.findIndex((object) => {
        return object.name === "Total of Selected";
      });
      if (status == "inactive") {
        button.setAttribute("status", "active");
        // Find the new peak of the data including the added trace
        valuesToAdd = raw_data.total_deaths[provinceMappings[region]];
        peak = 0;
        let active = document.querySelectorAll(`input[status="active"]`);
        active = Array.from(active).map((button) =>
          button.getAttribute("province")
        );
        let total = [];
        for (let index = 0; index < plots[0].x.length; index++) {
          current = 0;
          for (let plot = 0; plot < plots.length; plot++) {
            if (active.includes(plots[plot].name)) {
              current += plots[plot].y[index];
            }
          }
          current += valuesToAdd[index];
          total.push(current);
          if (current > peak) {
            peak = current;
          }
        }
        peak = Math.ceil(peak / 100) * 100;
        // Animate the zoom out of the y axis
        Plotly.animate(
          visDiv,
          [
            {
              layout: { yaxis: { range: [0, peak] } },
              traces: [index],
            },
          ],
          {
            transition: {
              duration: 200,
              easing: "cubic-in-out",
            },
            frame: {
              duration: 500,
            },
          }
        );
        // Animate the movement of the total bar
        Plotly.animate(
          visDiv,
          [
            {
              data: [{ y: total, line: { color: colourCode.Totals } }],
              layout: {},
              traces: [totalIndex],
            },
          ],
          {
            transition: {
              duration: 500,
              easing: "cubic-in-out",
            },
            frame: {
              duration: 500,
            },
          }
        );
        // Animate the reveal of the trace
        Plotly.animate(
          visDiv,
          [
            {
              data: [
                {
                  y: valuesToAdd,
                  showlegend: true,
                  line: { color: colourCode[region] },
                  hoverinfo: "show",
                },
              ],
              layout: {},
              traces: [index],
            },
          ],
          {
            transition: {
              duration: 500,
              easing: "cubic-in-out",
            },
            frame: {
              duration: 500,
            },
          }
        );
      } else if (status == "active") {
        button.setAttribute("status", "inactive");
        // Find the peak of data that will be displayed
        peak = 0;
        totals = [];
        for (let index = 0; index < plots[0].x.length; index++) {
          current = 0;
          for (let plot = 0; plot < plots.length; plot++) {
            if (
              plots[plot].name != region &&
              plots[plot].name != "Total of Selected"
            ) {
              current += plots[plot].y[index];
            }
          }
          totals.push(current);
          if (current > peak) {
            peak = current;
          }
        }
        peak = Math.ceil(peak / 100) * 100;
        // Remove the trace by setting y values to 0 and hiding it
        Plotly.animate(
          visDiv,
          [
            {
              data: [
                {
                  y: [0, 0, 0, 0, 0, 0, 0, 0],
                  showlegend: false,
                  line: { color: "rgba(0,0,0,0)" },
                  hoverinfo: "skip",
                },
              ],
              layout: {},
              traces: [index],
            },
          ],
          {
            transition: {
              duration: 200,
              easing: "cubic-in-out",
            },
            frame: {
              duration: 500,
            },
          }
        );
        // Animate updating the total bar
        Plotly.animate(
          visDiv,
          [
            {
              data: [
                {
                  y: totals,
                  showlegend: true,
                  line: { color: colourCode["Canada"] },
                  hoverinfo: "show",
                },
              ],
              layout: {},
              traces: [totalIndex],
            },
          ],
          {
            transition: {
              duration: 200,
              easing: "cubic-in-out",
            },
            frame: {
              duration: 500,
            },
          }
        );
        // Animate the zooming in of axis on remaining data
        Plotly.animate(
          visDiv,
          [
            {
              layout: { yaxis: { range: [0, peak] } },
              traces: [index],
            },
          ],
          {
            transition: {
              duration: 200,
              easing: "cubic-in-out",
            },
            frame: {
              duration: 500,
            },
          }
        );
      }
    }
  );
}

// Handling feedback submissions
let feedbackForm = document.getElementById("feedback-form");

function feedbackSubmit(token) {
  console.log(token);
  // validate the form has required fields

  // submit the form data with the recaptcha token
  let feedbackData = new FormData(feedbackForm);
  feedbackData.append("recaptcha-token", token);
}

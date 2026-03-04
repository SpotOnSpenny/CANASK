// CSRF token handling for htmx requests
document.body.addEventListener('htmx:configRequest', (event) => {
    event.detail.headers['X-CSRFToken'] = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
});

//HTMX config to exclude history cache and require server request on back/forward
htmx.config.historyCacheSize = 0;
htmx.config.refreshOnHistoryMiss = true;

// Set about data html strings
let bccsu_html = `
<h4 class="card-title text-center"> About these Data</h4>
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
<h4 class="card-title text-center"> About these Data</h4>
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
<h4 class="card-title text-center"> About these Data</h4>
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

function generateODPRNMapAbout(lastUpdatedDate, dataUntil) {
  let html = `
  <h4 class="card-title text-center"> About these Data</h4>
  <hr />
  <h5 class="text-center">This data set was last updated in ${
    lastUpdatedDate + " "
  } and contains data up until ${dataUntil}.</h5>
  <br>
  <p class="about-viz-text">These data have been collected by the Ontario Drug Policy Research Netowrk (ODPRN) and are provided by the Office of the Chief Coroner for Ontario. The data contains the number of opioid toxicity deaths, both confirmed and probable, in each public health unit, and is updated monthly by the ODPRN.
      <br>
      <br>
      For more information, visit the ODPRN website and view the reports containing these data by clicking the button below:
  </p>
  <div class="text-center pb-3">
      <a target="_blank" href="https://odprn.ca/occ-opioid-and-suspect-drug-related-death-data/" role="button"
          class="btn btn-primary">ODPRN</a>
  </div>
  `;
  return html;
}

function generateODPRNToxAbout(lastUpdatedDate, dataUntil) {
  let html = `
  <h4 class="card-title text-center"> About these Data</h4>
  <hr />
  <h5 class="text-center">This data set was last updated in ${
    lastUpdatedDate + " "
  } and contains data up until ${dataUntil}.</h5>
  <br>
  <p class="about-viz-text">These data have been collected by the Ontario Drug Policy Research Netowrk (ODPRN) and are provided by the Office of the Chief Coroner for Ontario. This data set consists of how many drug toxicity deaths can be attributed to opioids, stimulants, and other drugs in Ontarion, and is updated monthly by the ODPRN.
      <br>
      <br>
      For more information, visit the ODPRN website and view the reports containing these data by clicking the button below:
  </p>
  <div class="text-center pb-3">
      <a target="_blank" href="https://odprn.ca/occ-opioid-and-suspect-drug-related-death-data/" role="button"
          class="btn btn-primary">ODPRN</a>
  </div>
  `;
  return html;
}

// Initiate the mobile nav when it's present on the page
function toggleHamburger(e, navToggle, bars) {
    bars.forEach((bar) => bar.classList.toggle("x"));
    navToggle.classList.toggle("menu-active");
    navLinks.forEach((link) => link.classList.toggle("visible"));
    navLinkContainer.classList.toggle("expanded");
    mobileTitle.classList.toggle("visible");
}

function initMobileNav(){
  let navToggle = document.querySelector(".nav-toggle");
  let navLinks = document.querySelectorAll(".nav-title, .mobile-nav-link");
  let mobileTitle = document.querySelector(".mobile-title");
  let navLinkContainer = document.querySelector("#mobile-nav-link-container");
  let bars = document.querySelectorAll(".bar");

  // Helper: toggle the hamburger state
  function toggleHamburger() {
    navToggle.classList.toggle("active");
    bars.forEach((bar) => bar.classList.toggle("x"));
  }

  // Click handler for the mobile title
  mobileTitle.addEventListener("click", () => {
    if (navLinkContainer.classList.contains("expanded")) {
      toggleHamburger();
    }
  });

  // Example: toggle menu open/close
  navToggle.addEventListener("click", () => {
    navLinkContainer.classList.toggle("expanded");
    toggleHamburger();
  });

  // Optional: close menu when a link is clicked
  navLinks.forEach((link) => {
    link.addEventListener("click", () => {
      if (navLinkContainer.classList.contains("expanded")) {
        navLinkContainer.classList.remove("expanded");
        toggleHamburger();
      }
    });
  });
}

// Initiate the feedback form when it's present on the page
function initFeedback() {
  let feedbackToggle = document.querySelector(".feedback-toggle");
  let feedbackContent = document.querySelector(".feedback-content-container");
  let feedbackClose = document.querySelector(".feedback-close");

  function toggleFeedback() {
    feedbackContent.classList.toggle("feedback-visible");
    feedbackToggle.classList.toggle("feedback-toggle-invisible");
  }

  feedbackToggle.addEventListener("click", toggleFeedback);
  feedbackClose.addEventListener("click", toggleFeedback);
}

function validateEmail(mail) {
  if (/^\w+([\.-]?\w+)*@\w+([\.-]?\w+)*(\.\w{2,3})+$/.test(mail)) {
    return true;
  }
  return false;
}

function feedbackSubmit(token) {
  // validate the form has required fields
  let feedbackForm = document.getElementById("feedback-form")
  let feedbackData = new FormData(feedbackForm);
  let feedbackMessage = document.getElementById("feedback-message");
  let emailField = document.getElementById("feedback-email");
  if (validateEmail(feedbackData.get("email")) == false) {
    emailField.classList.toggle("is-invalid");
    emailField.value = "";
    emailField.placeholder = `"${feedbackData.get(
      "email"
    )}"  is not a valid email address!`;
  } else if (feedbackData.get("feedback") == "") {
    feedbackMessage.classList.toggle("is-invalid");
    feedbackMessage.value = "";
    feedbackMessage.placeholder = "This field cannot be blank";
  } else {
    try {
      emailField.classList.remove("is-invalid");
      feedbackMessage.classList.remove("is-invalid");
    } catch {}
    // submit the form data with the recaptcha token
    feedbackData.append("recaptcha-token", token);
    let alertContainer = document.getElementById("form-alerts");
    fetch("/feedback", {
      method: "POST",
      headers:{
        "X-CSRFToken": document.querySelector("meta[name='csrf-token']").getAttribute("content")
      },
      body: feedbackData,
    })
      .then((response) => {
        if (response.ok) {
          return response.json();
        } else {
          console.log("response not ok");
          console.log(response);
          return Promise.reject(response);
        }
      })
      .then((data) => {
        if (data["status"] == "success") {
          let feedbackAlert = `<div class="alert alert-success alert-dismissible fade show" role="alert">
          <p style="margin-bottom:0;"><strong style="margin-right: 2px;">Success! </strong> Your feedback has been submitted. Thank you for your input. </p>
          <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
          </div>`;
          alertContainer.innerHTML = feedbackAlert;
          feedbackForm.reset();
        } else {
          let feedbackAlert = `
        <div class="alert alert-danger alert-dismissible fade show" role="alert">
          <p style="margin-bottom:0;"><strong style="margin-right: 2px;">Error! </strong>There was an error submitting your feedback. Please try again later.</p>
          <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        </div>
        `;
          alertContainer.innerHTML = feedbackAlert;
        }
      })
      .catch((error) => {
        let feedbackAlert = `
      <div class="alert alert-danger alert-dismissible fade show" role="alert">
        <p style="margin-bottom:0;"><strong style="margin-right: 2px;">Error! </strong><p>There was an error submitting your feedback. Please try again later.</p>
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
      </div>
      `;
        alertContainer.innerHTML = feedbackAlert;
      });
  }
}

// Watch for HTMX signals to know when to run the mobile nav and feedback init functions
document.body.addEventListener("htmx:afterSettle", (event) => {
  if (event.detail.target && event.detail.target.id == "page-container") {
    let scriptContainer = document.querySelector("#template-scripts-signal");
    if (scriptContainer.classList.contains("initialized")) {
      return; 
    } else {
      initMobileNav();
      initFeedback();
      scriptContainer.classList.add("initialized");
    }
  }
});

document.addEventListener("DOMContentLoaded", (event) => {
  let scriptContainer = document.querySelector("#template-scripts-signal");
  if (scriptContainer && !scriptContainer.classList.contains("initialized")) {
    initMobileNav();
    initFeedback();
    scriptContainer.classList.add("initialized");
  }
});

// General functions to fetch data, create charts, etc. within the templates
function fetchData(data_file, functionCallback = null) {
  return fetch(data_file)
    .then((response) =>
      response.ok ? response.json() : Promise.reject(response)
    )
    .then((data) => {
      if (functionCallback != null) {
        functionCallback(data);
      }
      return data;
    });
}

let data_promise;

// Code Below is for the Ontario Page
let onData;
let onGeojson;

async function onInit() {
  try {
    const [data, geojson] = await Promise.all([
      fetchData("/static/js/on_vis.json"),
      // Note that for whoever else may look at this down the line, the geojson polygons have to be "rotating"
      // a particular way, and the geojson_rewind package with the option rfc7496=False did this for me
      fetchData("/static/assets/geojsons/ontario.geojson"),
    ]);
    createDeathMapOn(data, geojson);
    onData = data;
    onGeojson = geojson;
  } catch (error) {
    console.error("Error initializing Ontario page:", error);
  }
}
function createCategoryChartON(data) {
  let visDiv = document.getElementById("on-vis-div");
  let aboutDataDiv = document.getElementsByClassName("about-data-div")[0];
  let traces = [];
  for (const [key, value] of Object.entries(
    data["provincial_toxicity_deaths"]
  )) {
    if (key == "data last updated") {
      continue;
    } else if (key == "all drugs") {
      let trace = {
        x: value["x"],
        y: value["y"],
        type: "scatter",
        mode: "lines",
        name: "Total Drug Toxicity Deaths",
      };
      traces.push(trace);
    } else {
      let trace = {
        x: value["x"],
        y: value["y"],
        type: "bar",
        name: titleCase(key),
      };
      traces.push(trace);
    }
  }

  let vis = Plotly.react(
    visDiv,
    traces,
    (layout = {
      dragmode: "pan",
      yaxis: {
        fixedrange: true,
        title: {
          standoff: 30,
          text: "Number of Deaths",
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
      title:
        window.innerWidth > 768
          ? "Drug Toxicity Deaths in Ontario by Year and Drug Causing Death"
          : "Drug Toxicity Deaths in<br>Ontario by Year<br>and Drug Causing Death",
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
  let table_title = document.getElementById("table-title");
  let table_div = document.getElementById("data-table");
  table_div.innerHTML = "";
  let table = document.createElement("table");
  table.setAttribute(
    "class",
    "mb-0 table table-striped table-bordered table-hover"
  );
  let cols = [""].concat(
    data["toxicity_phu_data"][Object.keys(data["toxicity_phu_data"])[1]]["x"]
  );
  let tr = table.insertRow(-1);
  cols.forEach((headerText) => {
    let th = document.createElement("th"); // Create a new header cell
    th.innerText = headerText; // Set the text of the header cell
    tr.appendChild(th); // Add the header cell to the row
  });
  table_div.appendChild(table);
  table_title.innerText =
    "Drug Toxicity Deaths in Ontario by Year and Drug Causing Death";
  aboutDataDiv.innerHTML = generateODPRNToxAbout(
    data["provincial_toxicity_deaths"]["data last updated"],
    data["provincial_toxicity_deaths"]["all drugs"]["up to date until"]
  );
  for (const [key, value] of Object.entries(
    data["provincial_toxicity_deaths"]
  )) {
    if (key != "data last updated") {
      let tr = table.insertRow(-1);
      tr.setAttribute("class", "align-middle");
      let tabCell = tr.insertCell(-1);
      tabCell.innerText = titleCase(key);
      value["y"].forEach((element) => {
        let tabCell = tr.insertCell(-1);
        tabCell.innerText = element;
      });
    }
  }
}

function createDeathMapOn(data, geojson, first_run = true) {
  let visDiv = document.getElementById("on-vis-div");
  let aboutDataDiv = document.querySelector(".about-data-div");
  let dataSlider = [];
  let steps = [];
  let table_div = document.getElementById("data-table");
  let table = document.createElement("table");
  let table_title = document.getElementById("table-title");
  for (
    let year_index = 0;
    year_index <
    data["toxicity_phu_data"][Object.keys(data["toxicity_phu_data"])[1]]["x"]
      .length;
    year_index++
  ) {
    let values = {};
    for (let loc_index = 0; loc_index < geojson.features.length; loc_index++) {
      values[geojson.features[loc_index].properties.ENGNAME] =
        data["toxicity_phu_data"][
          geojson.features[loc_index].properties.ENGNAME
        ]["y"][year_index];
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
  for (let i = 0; i < dataSlider.length; i++) {
    let step = {
      method: "restyle",
      args: ["visible", Array(dataSlider.length).fill(false)],
      label:
        data["toxicity_phu_data"][Object.keys(data["toxicity_phu_data"])[1]][
          "x"
        ][i],
    };
    step.args[1][i] = true;
    steps.push(step);
  }
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
        active: 0,
        steps: steps,
        x: 0.1,
        len: 0.9,
        xanchor: "left",
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
        : $("#viz-card").height() + 200,
    title:
      window.innerWidth > 768
        ? "Confirmed and Probable Opioid Toxicity Deaths<br>in Ontario by Public Health Unit"
        : "Confirmed and Probable Opioid<br>Toxicity Deaths in Ontario by<br>Public Health Unit",
    margin: window.innerWidth > 768 ? { l: 0 } : { b: 20, r: 0, l: 75 },
  };

  // Create the table and add in tabular data
  table.setAttribute(
    "class",
    "mb-0 table table-striped table-bordered table-hover"
  );
  let cols = ["Public Health Unit"].concat(
    data["toxicity_phu_data"][Object.keys(data["toxicity_phu_data"])[1]]["x"]
  );
  let tr = table.insertRow(-1);
  cols.forEach((headerText) => {
    let th = document.createElement("th"); // Create a new header cell
    th.innerText = headerText; // Set the text of the header cell
    tr.appendChild(th); // Add the header cell to the row
  });
  table_div.innerHTML = "";
  table_div.appendChild(table);
  table_title.innerText = "Ontario Drug Toxicity Deaths by Public Health Unit";
  aboutDataDiv.innerHTML = generateODPRNMapAbout(
    data["toxicity_phu_data"]["data last updated"],
    data["toxicity_phu_data"][Object.keys(data["toxicity_phu_data"])[1]][
      "up to date until"
    ]
  );
  for (const [key, value] of Object.entries(data["toxicity_phu_data"])) {
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
  if (first_run) {
    visDiv.innerHTML = "";
  }
  let vis = Plotly.react(
    visDiv,
    dataSlider,
    layout,
    (config = {
      displaylogo: false,
      responsive: true,
    })
  ).then(() => {
    visDiv.on("plotly_click", function (data) {
            if (data && data.points.length > 0) {
        let location = data.points[0].location; // Get the clicked location
        let value = data.points[0].z; // Get the value at the clicked location
        console.log(`Clicked on ${location} with value ${value}`);
      }
    });
  });
}

// Function to change the chart type
function changeChartON(buttonElement, parentID) {
  Array.from(document.querySelectorAll(".active")).forEach((element) => {
    element.classList.remove("active");
  });
  switch (buttonElement.id) {
    case "opioid-death-heatmap":
      createDeathMapOn(onData, onGeojson, false);
      break;
    case "deaths-by-drug":
      createCategoryChartON(onData);
      break;
    default:
      console.error("Unknown button ID:", buttonElement.id);
  }
  buttonElement.classList.add("active");
  if (parentID != null) {
    document.getElementById(parentID).classList.add("active");
  }
}

let tox_data;

function keyByValue(object, value) {
  return Object.keys(object).find((key) => object[key] === value);
}

async function toxSetUp() {
  tox_data = fetch("static/js/total_tox_deaths_data.json")
    .then((response) => (data = response.json()))
    .then((data) => {
      // Initial province mappings needed to read the json data
      let provinceMappings = {
        bc_line_y: "British Columbia",
        ab_line_y: "Alberta",
        sk_line_y: "Saskatchewan",
        mb_line_y: "Manitoba",
        on_line_y: "Ontario",
        qc_line_y: "Quebec",
        nb_line_y: "New Brunswick",
        ns_line_y: "Nova Scotia",
        pe_line_y: "Prince Edward Island",
        nl_line_y: "Newfoundland and Labrador",
        can_line_y: "Canada",
      };

      let colourCode = {
        "British Columbia": "rgba(0, 71, 187, 1)",
        Alberta: "rgba(12, 142, 147, 1)",
        Saskatchewan: "rgba(7, 106, 33, 1)",
        Manitoba: "rgba(200, 15, 46, 1)",
        Ontario: "rgba(243, 204, 0, 1)",
        Quebec: "rgba(0, 61, 165, 1)",
        "New Brunswick": "rgba(211, 41, 39, 1)",
        "Nova Scotia": "rgba(253, 212, 79, 1)",
        "Prince Edward Island": "rgba(42, 162, 153, 1)",
        "Newfoundland and Labrador": "rgba(255, 164, 0, 1)",
        Canada: "rgba(206, 17, 38, 1)",
      };

      // Calculate the total deaths for each year
      totals = [];
      for (let i = 0; i < data.x_axes.can_line_x.length; i++) {
        let totalForYear = 0;
        for (const [key, value] of Object.entries(data.y_axes)) {
          toAdd = Number(value[i]);
          if (isNaN(toAdd)) {
            totalForYear += 0;
          } else {
            totalForYear += toAdd;
          }
        }
        totals.push(totalForYear);
      }

      // Create the initial table of values
      let cols = ["Province"].concat(data.x_axes.can_line_x);
      let rows = Object.values(provinceMappings).filter(
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
        data.x_axes.can_line_x.forEach((year) => {
          let cell = tr.insertCell(-1);
          let y_line_key = keyByValue(provinceMappings, province);
          if (province === "Total") {
            cell.innerText = totals[data.x_axes.can_line_x.indexOf(year)];
          } else {
            cell.innerText =
              data.y_axes[y_line_key][data.x_axes.can_line_x.indexOf(year)];
          }
        });
      });
      let tableDiv = document.getElementById("data-table");

      // Create the about these data section
      aboutDataDiv = document.getElementById("about-these-data");
      blurbDiv = document.getElementById("about-these-data-blurb");
      aboutDataTitle = document.getElementById("about-these-data-title");
      let dataSourcesHTML = [];
      let sourceText;
      let buttonText;
      for (let source of data.sources) {
        if (source.name == "Opioid- and Stimulant-related Harms in Canada") {
          buttonText = "Go to national report for all other provincial data";
          sourceText =
            "All other provincial data was obtained from the Opioid- and Stimulant-related Harms in Canada report and was last udpated on " +
            source.last_updated;
        } else if (source.name == "Statistics Canada") {
          buttonText = "See population estimtes from Statistics Canada";
          sourceText =
            "Population estimates were obtained from Statistics Canada and were last updated in " +
            source.last_updated;
        } else {
          buttonText = "Go to " + source.Province + " data source";
          sourceText =
            source.Province +
            " data obtained from the " +
            source.name +
            " and was last updated on " +
            source.last_updated;
        }
        let sourceDiv = `<div class="text-center pb-3">
          <p class="mb-1">${sourceText}</p>
          <a class="btn btn-primary" href="${source.url}" role="button">${buttonText}</a>
        </div>`;
        let tempContainer = document.createElement("div");
        tempContainer.innerHTML = sourceDiv;
        if (source.name == "Opioid- and Stimulant-related Harms in Canada") {
          dataSourcesHTML = [tempContainer, ...dataSourcesHTML];
        } else {
          dataSourcesHTML.push(tempContainer);
        }
      }
      dataSourcesHTML.reverse();
      let blurbHTML = `<div>
          <p>${data.about_these_data}</p>
        </div>
      `;
      let tempContainer = document.createElement("div");
      tempContainer.innerHTML = blurbHTML;

      // Create the initial visual
      visDiv = document.getElementById("toxicity-deaths-vis");
      plots = [];
      for (const [title, y_axis] of Object.entries(data.y_axes)) {
        let trace = {
          x: data.x_axes.can_line_x,
          y: y_axis,
          type: "scatter",
          name: provinceMappings[title],
          stackgroup: "one",
          animate: true,
          marker: { color: colourCode[title] },
        };
        plots.push(trace);
      }
      plots.push({
        x: data.x_axes.can_line_x,
        y: totals,
        type: "scatter",
        stackgroup: "two",
        name: "Total of Selected",
        animate: true,
        marker: { color: "rgba(206, 17, 38, 1)" },
        fill: "none",
      });

      // Also create the data/100k visual for later
      let dataPerPopPlots = [];
      for (const [title, y_axis] of Object.entries(data.y_axes_per_100k)) {
        let plotTitle = title.replace("_per_100k", "");
        let trace = {
          x: data.x_axes.can_line_x,
          y: y_axis,
          type: "scatter",
          name: provinceMappings[plotTitle],
          stackgroup: "one",
          animate: true,
          marker: { color: colourCode[title] },
        };
        dataPerPopPlots.push(trace);
      }

      // Add the created elements to the page
      blurbDiv.innerHTML = "";
      visDiv.innerHTML = "";
      tableDiv.innerHTML = "";
      blurbDiv.appendChild(tempContainer.firstChild);
      aboutDataTitle.textContent = "About these Data";
      aboutDataTitle.insertAdjacentHTML("afterend", "<hr>");
      for (let sourceHTML of dataSourcesHTML) {
        aboutDataDiv.appendChild(sourceHTML.firstChild);
      }
      tableDiv.appendChild(table);
      let vis = Plotly.react(
        visDiv,
        plots,
        (layout = {
          dragmode: "pan",
          yaxis: {
            fixedrange: true,
            title: {
              standoff: 30,
              text:
                window.innerWidth > 768
                  ? "Deaths Resulting from Drug Toxicity"
                  : "Deaths Resulting from Drug Toxicity",
            },
          },
          xaxis: {
            fixedrange: false,
            autorange: true,
            autorangeoptions:
              window.innerWidth > 768
                ? {}
                : {
                    clipmax: Number(plots[0]["x"][0]) + 2,
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
          title:
            window.innerWidth > 768
              ? "Canadian Opioid Toxicity Deaths Each Year by Province"
              : "Canadian Opioid Toxicity Deaths<br>Each Year by Province",
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
      return {
        vis: vis,
        plots: plots,
        visDiv: visDiv,
        raw_data: data,
        colourCode: colourCode,
        provinceMappings: provinceMappings,
        dataPerPopPlots: dataPerPopPlots,
      };
    });
}

function changeChartType(selector) {
  tox_data.then(
    ({
      vis,
      plots,
      visDiv,
      raw_data,
      colourCode,
      provinceMappings,
      dataPerPopPlots,
    }) => {
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
            dragmode: "pan",
            yaxis: {
              fixedrange: true,
              title: {
                standoff: 30,
                text:
                  window.innerWidth > 768
                    ? "Percent of Deaths Resulting from Drug Toxicity in Selected Provinces"
                    : "Percent of Deaths Resulting<br>from Drug Toxicity in Selected<br>Provinces",
              },
            },
            xaxis: {
              fixedrange: false,
              autorange: true,
              autorangeoptions:
                window.innerWidth > 768
                  ? {}
                  : {
                      clipmax: Number(plots[0]["x"][0]) + 2,
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
            title:
              window.innerWidth > 768
                ? "Canadian Opioid Toxicity Deaths Each Year by Province"
                : "Canadian Opioid Toxicity<br>Deaths Each Year by<br>Province",
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
            margin: window.innerWidth > 768 ? {} : { r: 0, l: 80 },
          }),
          (config = {
            displaylogo: false,
          })
        );
      } else if (selector.value == "3") {
        let active = document.querySelectorAll(`input[status="active"]`);
        active = Array.from(active).map((button) =>
          button.getAttribute("province")
        );
        let plotsToAdd = [];
        for (let plot of dataPerPopPlots) {
          if (active.includes(plot.name) || plot.name === "Total of Selected") {
            plotsToAdd.push(plot);
          }
        }
        Plotly.react(
          visDiv,
          plotsToAdd,
          (layout = {
            dragmode: "pan",
            yaxis: {
              fixedrange: true,
              title: {
                standoff: 30,
                text:
                  window.innerWidth > 768
                    ? "Deaths per 100,000 People Resulting from Drug Toxicity"
                    : "Deaths per 100,000 People<br>Resulting from Drug Toxicity",
              },
            },
            xaxis: {
              fixedrange: false,
              autorange: true,
              autorangeoptions:
                window.innerWidth > 768
                  ? {}
                  : {
                      clipmax: Number(dataPerPopPlots[0]["x"][0]) + 2,
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
            title:
              window.innerWidth > 768
                ? "Canadian Opioid Toxicity Deaths per 100,000 People Each Year by Province"
                : "Canadian Opioid Toxicity Deaths per 100,000<br>People Each Year by Province",
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
            dragmode: "pan",
            yaxis: {
              fixedrange: true,
              title: {
                standoff: 30,
                text:
                  window.innerWidth > 768
                    ? "Deaths Resulting from Drug Toxicity"
                    : "Deaths Resulting from Drug Toxicity",
              },
            },
            xaxis: {
              fixedrange: false,
              autorange: true,
              autorangeoptions:
                window.innerWidth > 768
                  ? {}
                  : {
                      clipmax: Number(plots[0]["x"][0]) + 2,
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
            title:
              window.innerWidth > 768
                ? "Canadian Opioid Toxicity Deaths Each Year by Province"
                : "Canadian Opioid Toxicity Deaths<br>Each Year by Province",
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
      }
    }
  );
}

//TODO there's a bug in here resulting in a console error that needs to be resolved
//TODO there's a bug here resulting in issues when you click a province and then switch chart types
//TODO we need to swap out the data table for the per 100k data table when the chart type is changed
function updateTracesTox(button) {
  tox_data.then(
    ({
      vis,
      plots,
      visDiv,
      raw_data,
      colourCode,
      provinceMappings,
      dataPerPopPlots,
    }) => {
      let status = button.getAttribute("status");
      let region = button.getAttribute("province");
      let index = plots.findIndex((object) => {
        return object.name === region;
      });
      let totalIndex = plots.findIndex((object) => {
        return object.name === "Total of Selected";
      });
      chartType = document.getElementById("chart-type-select").value;
      let dataPlots;
      if (chartType == "3") {
        dataPlots = dataPerPopPlots;
      } else {
        dataPlots = plots;
      }
      if (status == "inactive") {
        button.setAttribute("status", "active");
        // Find the new peak of the data including the added trace
        let region_line_key = keyByValue(provinceMappings, region);
        let valuesToAdd;
        if (chartType == "3") {
          valuesToAdd = raw_data.y_axes_per_100k[`${region_line_key}_per_100k`];
        } else {
          valuesToAdd = raw_data.y_axes[region_line_key];
        }
        peak = 0;
        let active = document.querySelectorAll(`input[status="active"]`);
        active = Array.from(active).map((button) =>
          button.getAttribute("province")
        );
        let total = [];
        for (let index = 0; index < dataPlots[0].x.length; index++) {
          current = 0;
          for (let plot = 0; plot < dataPlots.length; plot++) {
            if (active.includes(dataPlots[plot].name)) {
              current += Number(dataPlots[plot].y[index]);
            }
          }
          current += Number(valuesToAdd[index]);
          total.push(current);
          if (current > peak) {
            peak = Math.round(current);
          }
        }
        chartType = document.getElementById("chart-type-select").value;
        if (chartType == "1" || chartType == "3") {
          digits = peak.toString().length;
          if (digits >= 4) {
            peak = Math.ceil(peak / 1000) * 1000;
          } else if ((digits = 3)) {
            peak = Math.ceil(peak / 100) * 100;
          } else {
            peak = Math.ceil(peak / 10) * 10;
          }
        } else if (chartType == "2") {
          peak = 100;
        }

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
        for (let index = 0; index < dataPlots[0].x.length; index++) {
          current = 0;
          for (let plot = 0; plot < dataPlots.length; plot++) {
            if (
              dataPlots[plot].name != region &&
              dataPlots[plot].name != "Total of Selected"
            ) {
              current += Number(dataPlots[plot].y[index]);
            }
          }
          totals.push(current);
          if (current > peak) {
            peak = current;
          }
        }
        chartType = document.getElementById("chart-type-select").value;
        if (chartType == "1") {
          digits = peak.toString().length;
          if (digits >= 4) {
            peak = Math.ceil(peak / 1000) * 1000;
          } else if ((digits = 3)) {
            peak = Math.ceil(peak / 100) * 100;
          } else {
            peak = Math.ceil(peak / 10) * 10;
          }
        } else if (chartType == "2") {
          peak = 100;
        }

        // Remove the trace by setting y values to 0 and hiding it
        Plotly.animate(
          visDiv,
          [
            {
              data: [
                {
                  y: Array.from(raw_data.x_axes.can_line_x, () => 0),
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

function titleCase(str) {
  return str.toLowerCase().replace(/(?:^|\s)\w/g, function (match) {
    return match.toUpperCase();
  });
}

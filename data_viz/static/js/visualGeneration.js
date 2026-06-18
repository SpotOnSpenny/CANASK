// Global variables to hold the current data and geojson
let currentData;
let currentGeojson;
let currentVisual;
let province;
let route = [];
let lastLocation = null;
// Menu/presentation config, populated per province from the DB response in fetchRegionData()
// (replaces the formerly-static visuals.js). Shape mirrors the old global: {province: {visual_id: cfg},
// "default-visuals": {province: visual_id}} so the existing visuals[province][...] reads work unchanged.
let visuals = {};
// Ordered top-level menu dropdown names for the current province, served by the DB (build_province_menu)
// rather than hard-coded -- derived from the visuals' menu-parent values.
let menuCategories = [];

// Slugify a display name into the DOM-id form used for menu dropdowns ("Drug Supply" -> "drug-supply").
function slugify(str) {
  return str.toLowerCase().replace(/ /g, "-");
}

// ---- Shared rendering helpers (used across the heatmap / line / bar / pie renderers) ----

// "About these Data" panel HTML built from a visual's data_source block.
function buildAboutDataHTML(source) {
  let header = `<h4 class="card-title text-center"> About these Data</h4>
  <hr />
  <h5 class="text-center">This data set was last updated in ${source["last_updated"] + " "} and contains data up until ${source["data_until"]}.</h5>
  `;
  let button = `<div class="text-center pb-3">
    <a target="_blank" href="${source["link"]}" role="button"
          class="btn btn-primary">${source["name"]}</a>
  </div>
  `;
  return `${header}
  ${source["about"]}
  <br></br>
  ${button}
  `;
}

// Responsive Plotly legend/margin: defaults on desktop, stacked horizontal legend on narrow screens.
function responsiveLegend() {
  return window.innerWidth > 768
    ? {}
    : { orientation: "h", x: 0, y: -0.2, xanchor: "middle", yanchor: "top", tracegroupgap: 200 };
}
function responsiveMargin() {
  return window.innerWidth > 768 ? {} : { r: 0, l: 65 };
}

// Build the counts/rates/percentages radio toggle group. onSelect(type) recreates the renderer with
// the chosen data type; a control reset happens first. No-op when only one data type is available.
function buildDataTypeToggles(container, dataTypes, currentDataType, onSelect) {
  if (!dataTypes || dataTypes.length <= 1) return;
  for (const type of dataTypes) {
    let toggle = document.createElement("input");
    toggle.type = "radio";
    toggle.className = "btn-check";
    toggle.name = "data-toggle";
    toggle.id = `${type}-toggle`;
    toggle.autocomplete = "off";
    if (type === currentDataType) {
      toggle.checked = true;
    }
    toggle.onclick = function () {
      resetVisualControl();
      onSelect(type);
    };
    let label = document.createElement("label");
    label.className = "btn btn-outline-primary";
    label.setAttribute("for", `${type}-toggle`);
    label.innerText = type.charAt(0).toUpperCase() + type.slice(1);
    container.appendChild(toggle);
    container.appendChild(label);
  }
}

// Function to dynamically create the menu based on the visuals object and current province
function createMenu(province) {
  console.log(`Creating menu for ${province}`);
  // Create the menu and add all parent categories
  let menu = document.getElementById("vis-selection-menu");
  menu.innerHTML = ""; // Clear existing menu items
  // Parent categories come from the DB (menuCategories), so adding a visual under a new menu-parent
  // surfaces a new dropdown with no frontend change.
  for (const parent of menuCategories) {
    let li = document.createElement("li");
    li.className = "nav-item dropdown";
    let a = document.createElement("a");
    a.className = "nav-link dropdown-toggle";
    a.href = "";
    a.id = `${slugify(parent)}-dropdown`;
    a.setAttribute("role", "button");
    a.setAttribute("data-bs-toggle", "dropdown");
    a.textContent = parent;
    li.appendChild(a);
    let ul = document.createElement("ul");
    ul.className = "dropdown-menu";
    ul.id = `${slugify(parent)}-dropdown-menu`;
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

    // Selecting any menu item switches to that visual; only maps skip the control reset.
    a.onclick = function () {
      if (details["type"] !== "map") resetVisualControl();
      currentVisual = visual;
      masterLoop();
    };

    // append the menu item to the appropriate parent category
    document.getElementById(`${slugify(details["menu-parent"])}-dropdown-menu`).appendChild(li);
  }
}

// ---- Client-side adapter from the generic fact contract to the legacy block shape ----
// The /api route serves each visual as normalized facts; this rebuilds the per-visual block shape
// the Plotly renderers consume. Verified data-identical to the former server-side reconstruction.
const SUBSTANCE_DISPLAY = { opioids: "Opioid", stimulants: "Stimulant" };

// Series key/label composed straight from the dimension values (no legacy "_y" suffix). The
// renderers use it as the legend/table label (title/sentence-cased there). "y" is the structural
// value key the heatmap reads.
function seriesKey(kind, d, d2) {
    switch (kind) {
        case "constant": return "y";
        case "suffix_y": return d2;
        case "plain": return d2;
        case "sex_substance": return `${d2} ${SUBSTANCE_DISPLAY[d] || d}`;
        case "manner_substance": return `${d2} ${SUBSTANCE_DISPLAY[d] || d} Deaths`;
        default: return d2;
    }
}

function _yearKey(y) { const m = String(y).match(/^\d+/); return m ? [parseInt(m[0], 10), String(y)] : [1e9, String(y)]; }
function _sortYears(years) {
    return Array.from(new Set(years)).sort((a, b) => { const ka = _yearKey(a), kb = _yearKey(b); return ka[0] - kb[0] || ka[1].localeCompare(kb[1]); });
}


// ---- Fact selectors: build each renderer's data on demand from a visual's normalized facts ----
// A block's facts are [{dt: data_type, geo, t: time, d: dimension, d2: dimension2, v: value}].
// These group/shape them into exactly what each renderer consumes (no intermediate "legacy" block).

// Flat / drilled-geo line+bar data: {dataType: {x:[years], "<label>":[values]}}. Pass `geo` to
// restrict to one location (geo-nested visuals drilled to a clicked area); null = all (flat).
function factsToSeries(facts, kind, geo = null) {
    const tree = {}, years = {};
    for (const f of facts) {
        if (f.dt === "additional_rows" || (geo != null && f.geo !== geo)) continue;
        const key = seriesKey(kind, f.d, f.d2);
        (tree[f.dt] = tree[f.dt] || {})[key] = tree[f.dt][key] || {}; tree[f.dt][key][f.t] = f.v;
        (years[f.dt] = years[f.dt] || new Set()).add(f.t);
    }
    const data = {};
    for (const dt of Object.keys(tree)) {
        const o = { x: _sortYears(years[dt]) };
        for (const key of Object.keys(tree[dt])) { const yv = tree[dt][key]; o[key] = _sortYears(Object.keys(yv)).map(y => yv[y]); }
        data[dt] = o;
    }
    return data;
}

// Heatmap data: {geo: {x:[years], y:[values]}} from the counts facts (single value series per area).
function factsToHeatmap(facts, kind) {
    const byGeo = {}, years = {};
    for (const f of facts) {
        if (f.dt !== "counts") continue;
        const key = seriesKey(kind, f.d, f.d2);
        (byGeo[f.geo] = byGeo[f.geo] || {})[key] = byGeo[f.geo][key] || {}; byGeo[f.geo][key][f.t] = f.v;
        (years[f.geo] = years[f.geo] || new Set()).add(f.t);
    }
    const out = {};
    for (const g of Object.keys(byGeo)) {
        const o = { x: _sortYears(years[g]) };
        for (const key of Object.keys(byGeo[g])) { const yv = byGeo[g][key]; o[key] = _sortYears(Object.keys(yv)).map(y => yv[y]); }
        out[g] = o;
    }
    return out;
}

// Table-only "additional" rows for a flat line/bar: {label: [values aligned to the year axis]}.
function factsToAdditional(facts) {
    const add = facts.filter(f => f.dt === "additional_rows");
    if (!add.length) return null;
    const rows = {}, years = new Set();
    for (const f of add) { (rows[f.d] = rows[f.d] || {})[f.t] = f.v; years.add(f.t); }
    const ordered = _sortYears(years);
    const out = {};
    for (const label of Object.keys(rows)) out[label] = ordered.map(y => (y in rows[label] ? rows[label][y] : null));
    return out;
}

// Pie data for one drilled area: { counts: {year: {category: value}},
//                                  tabular: {category: [per-year], "Total Samples": [per-year]} }.
function factsToPie(facts, geo) {
    const counts = {}, totals = {}, years = new Set();
    for (const f of facts) {
        if (f.geo !== geo) continue;
        if (f.dt === "counts") { (counts[f.t] = counts[f.t] || {})[f.d2] = f.v; years.add(f.t); }
        else if (f.dt === "additional_rows") { totals[f.t] = f.v; }
    }
    const ys = _sortYears(years);
    const countsOut = {}; ys.forEach(y => countsOut[y] = Object.assign({}, counts[y]));
    const drugs = [], seen = new Set();
    for (const y of ys) for (const d of Object.keys(counts[y] || {})) if (!seen.has(d)) { seen.add(d); drugs.push(d); }
    const tabular = {};
    drugs.forEach(d => tabular[d] = ys.map(y => (counts[y] && d in counts[y]) ? counts[y][d] : 0));
    tabular["Total Samples"] = ys.map(y => (y in totals) ? totals[y] : 0);
    return { counts: countsOut, tabular };
}

// Regional level-3 bar for a single (area, year, drug): {x:[year], "<result>":[count]}.
function factsToRegional(facts, geo, year, drug) {
    const out = { x: [year] };
    for (const f of facts) {
        if (f.geo === geo && f.t === year && f.d === drug) out[f.d2] = [f.v];
    }
    return out;
}

// ---- Treemap (category_treemap): generic, config-driven nested mosaic ----
// Facts: `geo` is an ordered "||"-joined level composite (e.g. "<Health Authority>||<Site>");
// the hierarchy/filter axes are dimension/dimension2; time is month-grain ("YYYY-MM"). Everything
// below is driven by the visual's visual_options (geo_levels / hierarchy / filters / time), so the
// same code renders any treemap from any source -- add a geo level or a filter via config, not JS.

// Value of one geo level (the i-th "||"-segment of a fact's composite geo).
function geoLevel(f, i) { return String(f.geo == null ? "" : f.geo).split("||")[i]; }

// Generic fact-axis read: "dimension" -> d, "dimension2" -> d2, "geo:<i>" -> that geo level.
function axisValue(f, axis) {
    if (axis === "dimension") return f.d;
    if (axis === "dimension2") return f.d2;
    const m = String(axis).match(/^geo:(\d+)$/);
    if (m) return geoLevel(f, parseInt(m[1], 10));
    return null;
}

// The time-bucket key a fact falls in for a given unit ("all" => null, i.e. no stratification).
function timeBucketOf(f, unit) {
    const t = String(f.t || "");
    if (unit === "year") return t.slice(0, 4);
    if (unit === "month") return t;
    if (unit === "seasonal") return t.slice(5, 7);
    return null;
}

const TREEMAP_MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"];

// Ordered slider domain for a unit, as [{value, label}], from the unfiltered facts.
function treemapTimeBuckets(facts, unit) {
    if (unit === "all") return [];
    const set = new Set();
    for (const f of facts) { const b = timeBucketOf(f, unit); if (b) set.add(b); }
    return Array.from(set).sort().map(function (v) {
        if (unit === "year") return { value: v, label: v };
        if (unit === "seasonal") return { value: v, label: TREEMAP_MONTH_NAMES[parseInt(v, 10) - 1] || v };
        const parts = v.split("-");   // month "YYYY-MM"
        return { value: v, label: (TREEMAP_MONTH_NAMES[parseInt(parts[1], 10) - 1] || parts[1]) + " " + parts[0] };
    });
}

// Aggregate facts into Plotly-treemap arrays for the current selection.
// sel = { geo: [perLevel value|null], filters: {axis: value|null}, unit, bucket }.
function factsToTreemap(facts, cfg, sel) {
    const hierarchy = (cfg && cfg.hierarchy && cfg.hierarchy.length) ? cfg.hierarchy
        : [{ axis: "dimension" }, { axis: "dimension2" }];
    const geoLevels = (cfg && cfg.geo_levels) || [];
    const filters = (cfg && cfg.filters) || [];

    const sums = new Map();   // path-key -> {path:[displayValues], value}
    let total = 0;
    for (const f of facts) {
        let keep = true;
        for (let i = 0; i < geoLevels.length; i++) {
            if (sel.geo[i] != null && geoLevel(f, i) !== sel.geo[i]) { keep = false; break; }
        }
        if (!keep) continue;
        for (const flt of filters) {
            if (sel.filters[flt.axis] != null && axisValue(f, flt.axis) !== sel.filters[flt.axis]) { keep = false; break; }
        }
        if (!keep) continue;
        if (sel.unit !== "all" && sel.bucket != null && timeBucketOf(f, sel.unit) !== sel.bucket) continue;

        const path = hierarchy.map(function (h) { return axisValue(f, h.axis); });
        if (path.some(function (p) { return p == null || p === ""; })) continue;
        const v = Number(f.v) || 0;
        const key = path.join("\u001f");
        const cur = sums.get(key) || { path: path, value: 0 };
        cur.value += v;
        sums.set(key, cur);
        total += v;
    }

    // Build node arrays. ids are keyed by the full path so a leaf appearing under two parents
    // (e.g. the same drug in two categories) never collides.
    const ids = ["ROOT"], labels = ["Total"], parents = [""], values = [total];
    const interior = new Map();   // interior-node id -> its index in `values`
    const rows = [];
    for (const entry of sums.values()) {
        let parentId = "ROOT", accPath = "";
        for (let depth = 0; depth < entry.path.length; depth++) {
            accPath += "\u001f" + entry.path[depth];
            const id = "N" + accPath;
            if (depth === entry.path.length - 1) {
                ids.push(id); labels.push(entry.path[depth]); parents.push(parentId); values.push(entry.value);
            } else if (!interior.has(id)) {
                interior.set(id, values.length);
                ids.push(id); labels.push(entry.path[depth]); parents.push(parentId); values.push(0);
            }
            parentId = id;
        }
        rows.push({ path: entry.path, value: entry.value });
    }
    // Interior values must equal the sum of their descendant leaves (branchvalues:"total").
    for (const entry of sums.values()) {
        let accPath = "";
        for (let depth = 0; depth < entry.path.length - 1; depth++) {
            accPath += "\u001f" + entry.path[depth];
            const idx = interior.get("N" + accPath);
            if (idx != null) values[idx] += entry.value;
        }
    }
    rows.sort(function (a, b) { return b.value - a.value; });
    return { ids: ids, labels: labels, parents: parents, values: values, total: total, rows: rows };
}

// Function to initialize the data by fetching provincial data
async function fetchRegionData(province){
    console.log(`Fetched data for ${province}`);
    //fetch the data and unpack it
    const [data, geojson] = await Promise.all([
        fetch(`/api/v1/province/${province}/data`, {AbortSignal: AbortSignal.timeout(5000)}),
        fetch(`/static/assets/geojsons/${province.toLowerCase()}.geojson`, {AbortSignal: AbortSignal.timeout(5000)}),
    ]);
    const payload = await data.json();
    const geojsonJson = await geojson.json();
    // payload = { data: {visual_id: {facts, key_kind, shape, ...}}, config: {visual_id: menuCfg}, default, categories }
    visuals = {
        [province]: payload.config || {},
        "default-visuals": { [province]: payload["default"] },
    };
    menuCategories = payload.categories || [];
    currentData = payload.data;   // generic blocks; renderers derive their data from .facts (above)
    currentGeojson = geojsonJson;
}

//Master function to initialize all visuals given the province and what the visual is
function masterLoop(location = null, year = null, category = null) {
  // No accessible visual for this province (e.g. RBAC withheld everything, or a direct URL to a
  // province the viewer can't see) -> show an empty state instead of dereferencing undefined config.
  if (!currentVisual || !visuals[province] || !visuals[province][currentVisual]) {
    const visDiv = document.getElementById("vis-div");
    if (visDiv) visDiv.innerHTML = '<p class="text-muted text-center py-5">No visuals are available to you for this province.</p>';
    return;
  }

  const cfg = visuals[province][currentVisual];
  const level = cfg["level"];
  // Drill state: reset at level 1, show back/reset controls deeper.
  if (level === 1) { lastLocation = null; route = []; resetVisualControl(); }
  else if (level === 2) { setupBackButton(); }
  else { setupBackButton(); setupResetButton(); lastLocation = location; }

  // Update the "you are here" breadcrumb for the current drill state
  renderBreadcrumb(location, category);

  const block = currentData[currentVisual];
  const facts = block.facts || [];
  const kind = block.key_kind;
  const source = block.data_source;
  // visual_options with the current drill context injected (renderers template against these).
  const opts = Object.assign({}, block.visual_options || {});
  if (location != null) opts["location"] = location.toTitleCase();
  if (category != null) opts["category"] = category.toTitleCase();
  if (year != null) opts["year"] = year;

  const dataType = cfg["type"] !== "map" ? cfg["data-types"][0] : null;

  //run the creation function for the visual based on its type, deriving its data from facts
  switch (cfg["type"]) {
    case "heatmap":
      createVisualHeatMap(province, currentVisual, currentGeojson, factsToHeatmap(facts, kind), source, opts);
      break;
    case "line":
      createVisualLine(province, factsToSeries(facts, kind, location), currentVisual, dataType, source, opts,
                       level === 1 ? factsToAdditional(facts) : null);
      break;
    case "bar": {
      const barData = block.shape === "regional"
        ? { counts: factsToRegional(facts, location, year, category) }
        : factsToSeries(facts, kind, location);
      createVisualBar(province, barData, currentVisual, dataType, source, opts);
      break;
    }
    case "map":
      createVisualMap(province, currentVisual, currentGeojson, opts);
      break;
    case "pie": {
      const pie = factsToPie(facts, location);
      createVisualPie(province, { counts: pie.counts }, source, opts, { [location]: pie.tabular }, location);
      break;
    }
    case "treemap":
      // Self-contained: the renderer derives every dropdown + the time control from block.facts
      // and block.visual_options, and re-renders itself in place (no masterLoop round-trip).
      createVisualTreemap(province, block, currentVisual, source);
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
      console.log(geojson.features[loc_index].properties.ENGNAME);
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
    themeChartLayout(layout),
    (config = {
      displaylogo: false,
      responsive: false,
    })
  ).then(() => {
    visDiv.on("plotly_click", function (data) {
      if (!canDrill(province, visualToGen)) {
        console.warn("No accessible next-level visual for this visual");
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
  aboutDataDiv.innerHTML = buildAboutDataHTML(mapSource);

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
    themeChartLayout(layout),
    (config = {
      displaylogo: false,
      responsive: false,
    })
  ).then(() => {
    visDiv.on("plotly_click", function (data) {
      if (data && data.points.length > 0) {
        // check if an accessible second-level visual exists for the current visual
        if (!canDrill(province, currentVisual)) {
          console.warn("No accessible second-level visual for this visual");
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
  totalPresent = !!("total" in traceData);

  // Track series index so each line gets a distinct dash + marker shape, making
  // series distinguishable without relying on color alone (WCAG).
  let seriesIndex = 0;
  for (const [key, value] of Object.entries(traceData)) {
    // create a trace for each y value in the lineData object entry
    if (key != "x"){

      let filteredData = filterLeadingZeros(traceData["x"], value);

      if (filteredData.x.length > 0) {
        let trace = {
          x: filteredData.x,
          y: filteredData.y,
          name: key.toSentenceCase(),
          type: "scatter",
          mode: "lines+markers",
          stackgroup: totalPresent && key.toSentenceCase() != "Total" ? "one" : undefined, // fill if total is present and not the total line
          line: {
            width: 2,
            smoothing: 1,
          },
          // Solid lines, but a distinct marker shape per series gives a quiet
          // non-color cue (no shimmering dash patterns).
          marker: {
            symbol: CANASK_MARKER_SYMBOLS[seriesIndex % CANASK_MARKER_SYMBOLS.length],
            size: 6,
          },
        };
        traces.push(trace);
        seriesIndex++;
      }
    }
  }

  visDiv.innerHTML = "";
  Plotly.purge(visDiv); // Clear any previous Plotly plots
  let vis = Plotly.react(
    visDiv,
    traces,
    themeChartLayout(layout = {
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
      legend: responsiveLegend(),
      margin: responsiveMargin(),
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
  // Only render the data type currently shown in the visual (counts / rates /
  // percentages). The whole function re-runs when the toggle changes, so the
  // table stays in sync with the chart.
  for (const [subKey, subValue] of Object.entries(lineData[dataType])) {
    if (subKey != "x") {
      let tr = table.insertRow(-1);
      tr.setAttribute("class", "align-middle");
      let tabCell = tr.insertCell(-1);
      tabCell.innerText = visualOptions[`table-${dataType}-row`].replace("replace_me", subKey.toTitleCase());
      subValue.forEach((element, index) => {
        let tabCell = tr.insertCell(-1);
        tabCell.innerText = formatTableValue(element, index, subValue);
      });
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
  buildDataTypeToggles(dataTypeToggle, dataTypes, dataType, (type) =>
    createVisualLine(province, lineData, currentVisual, type, lineSource, visualOptions, additionalRows, dataTypes));

  // Generate the About these Data section and insert the html
  aboutDataDiv.innerHTML = buildAboutDataHTML(lineSource);
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

  // Create a trace for each y value in the barData object entry. Each series
  // gets a distinct pattern fill + subtle theme-aware outline so bars are
  // distinguishable without relying on color alone (WCAG).
  let seriesIndex = 0;
  for (const [key, value] of Object.entries(traceData)) {
    if (key != "x") {
      let trace = {
        x: traceData["x"],
        y: value,
        hoverinfo: visualOptions["hover-info"],
        name: key.toSentenceCase(),
        type: "bar",
        // Clean solid bars with a subtle outline (no eye-straining hatch fill);
        // the data table below the chart is the non-color alternative.
        marker: {
          line: {
            width: 1,
            color: canaskMarkerLineColor(),
          },
        },
      };
      traces.push(trace);
      seriesIndex++;
    }
  }
  Plotly.purge(visDiv);
  let vis = Plotly.react(
    visDiv,
    traces,
    themeChartLayout(layout = {
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
      height: $("#viz-card").height(),
      title: visualOptions[`${dataType}-title`].replace("replace_with_health_authority", visualOptions["location"] || "").replace("replace_with_category", visualOptions["category"] || ""),
      legend: responsiveLegend(),
      margin: responsiveMargin(),
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
  // Only render the data type currently shown in the visual (counts / rates /
  // percentages); re-runs with the chart when the toggle changes.
  for (const [subKey, subValue] of Object.entries(barData[dataType])) {
    if (subKey != "x") {
      let tr = table.insertRow(-1);
      tr.setAttribute("class", "align-middle");
      let tabCell = tr.insertCell(-1);
      tabCell.innerText = visualOptions[`table-${dataType}-row`].replace("replace_me", subKey.toTitleCase());
      subValue.forEach((element) => {
        let tabCell = tr.insertCell(-1);
        tabCell.innerText = element;
      });
    }
  }

// If there is more than one data type available, create a toggle to switch between each data type
  if (dataTypes == null) {
    dataTypes = visuals[province][currentVisual]["data-types"];
  }
  buildDataTypeToggles(dataTypeToggle, dataTypes, dataType, (data) =>
    createVisualBar(province, barData, currentVisual, data, barSource, visualOptions, dataTypes));
  // Generate the About these Data section and insert the html
  aboutDataDiv.innerHTML = buildAboutDataHTML(barSource);
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
    themeChartLayout(layout),
    (config = {
      displaylogo: false,
      responsive: false,
    })
  ).then(() => {
    visDiv.on("plotly_click", function (data) {
      if (!canDrill(province, currentVisual)) {
        console.warn("No accessible next-level visual for this visual");
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
  aboutDataDiv.innerHTML = buildAboutDataHTML(pieSource);

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

// Generic config-driven treemap renderer. Reads block.visual_options for its geo levels,
// hierarchy axes, dimension filters and time control, builds those controls into #vis-stratifiers,
// and re-renders itself in place when any control changes. No literal Site/HA/Category here -- the
// same function powers any category_treemap visual from any data source.
async function createVisualTreemap(province, block, currentVisual, source) {
  const cfg = (block && block.visual_options) || {};
  const facts = (block && block.facts) || [];
  const visDiv = document.getElementById("vis-div");
  const aboutDataDiv = document.getElementById("about-data");
  const tableDiv = document.getElementById("data-table");
  const tableTitle = document.getElementById("table-title");
  const controls = document.getElementById("vis-stratifiers");

  setActiveVisual(province, currentVisual);

  const geoLevels = cfg.geo_levels || [];
  const filters = cfg.filters || [];
  const hierarchy = (cfg.hierarchy && cfg.hierarchy.length) ? cfg.hierarchy
    : [{ axis: "dimension", label: "Category" }, { axis: "dimension2", label: "Value" }];

  // Current selection (null = "All"); the slider drives unit/bucket.
  const sel = { geo: geoLevels.map(function () { return null; }), filters: {}, unit: "all", bucket: null };
  filters.forEach(function (flt) { sel.filters[flt.axis] = null; });

  function distinct(valueFn, predicate) {
    const set = new Set();
    for (const f of facts) {
      if (predicate && !predicate(f)) continue;
      const v = valueFn(f);
      if (v != null && v !== "") set.add(v);
    }
    return Array.from(set).sort();
  }

  function setSelectOptions(select, options, current) {
    select.innerHTML = "";
    const allOpt = document.createElement("option");
    allOpt.value = "__all__"; allOpt.innerText = "All";
    select.appendChild(allOpt);
    options.forEach(function (o) {
      const opt = document.createElement("option");
      if (o && typeof o === "object") { opt.value = o.value; opt.innerText = o.label; }
      else { opt.value = o; opt.innerText = o; }
      select.appendChild(opt);
    });
    select.value = (current == null) ? "__all__" : current;
  }

  function makeControl(labelText) {
    const wrap = document.createElement("div");
    wrap.className = "treemap-control text-start";
    const lab = document.createElement("label");
    lab.className = "form-label mb-0 small fw-semibold";
    lab.innerText = labelText;
    const select = document.createElement("select");
    select.className = "form-select form-select-sm";
    wrap.appendChild(lab); wrap.appendChild(select);
    controls.appendChild(wrap);
    return select;
  }

  // Distinct values for geo level i, cascaded by the levels chosen above it.
  function geoOptions(i) {
    return distinct(function (f) { return geoLevel(f, i); }, function (f) {
      for (let k = 0; k < i; k++) { if (sel.geo[k] != null && geoLevel(f, k) !== sel.geo[k]) return false; }
      return true;
    });
  }

  function buildControls() {
    controls.innerHTML = "";
    const geoSelects = [];
    geoLevels.forEach(function (levelLabel, i) {
      const select = makeControl(levelLabel);
      setSelectOptions(select, geoOptions(i), null);
      select.onchange = function () {
        sel.geo[i] = select.value === "__all__" ? null : select.value;
        // Re-cascade: clear and repopulate every lower level for the new parent choice.
        for (let j = i + 1; j < geoLevels.length; j++) {
          sel.geo[j] = null;
          setSelectOptions(geoSelects[j], geoOptions(j), null);
        }
        render();
      };
      geoSelects.push(select);
    });

    filters.forEach(function (flt) {
      const select = makeControl(flt.label || flt.axis);
      setSelectOptions(select, distinct(function (f) { return axisValue(f, flt.axis); }), null);
      select.onchange = function () {
        sel.filters[flt.axis] = select.value === "__all__" ? null : select.value;
        render();
      };
    });

    if (cfg.time) {
      const wrap = document.createElement("div");
      wrap.className = "treemap-control text-start";
      const lab = document.createElement("label");
      lab.className = "form-label mb-0 small fw-semibold";
      lab.innerText = (cfg.time && cfg.time.label) || "Date";
      const unitSelect = document.createElement("select");
      unitSelect.className = "form-select form-select-sm";
      [["all", "All time"], ["year", "Year"], ["month", "Month"], ["seasonal", "Seasonal"]].forEach(function (u) {
        const opt = document.createElement("option");
        opt.value = u[0]; opt.innerText = u[1];
        unitSelect.appendChild(opt);
      });
      const sliderWrap = document.createElement("div");
      sliderWrap.className = "mt-1";
      const slider = document.createElement("input");
      slider.type = "range"; slider.className = "form-range"; slider.min = 0; slider.step = 1;
      const readout = document.createElement("div");
      readout.className = "small text-muted";
      sliderWrap.appendChild(slider); sliderWrap.appendChild(readout);

      let buckets = [];
      function applyBucket() {
        const idx = parseInt(slider.value, 10) || 0;
        sel.bucket = buckets.length ? buckets[idx].value : null;
        readout.innerText = buckets.length ? buckets[idx].label : "";
      }
      function rebuildBuckets() {
        buckets = treemapTimeBuckets(facts, sel.unit);
        if (sel.unit === "all" || buckets.length === 0) {
          sliderWrap.style.display = "none";
          sel.bucket = null;
        } else {
          sliderWrap.style.display = "";
          slider.max = buckets.length - 1;
          slider.value = buckets.length - 1;   // default to the most recent bucket
          applyBucket();
        }
      }
      unitSelect.onchange = function () { sel.unit = unitSelect.value; rebuildBuckets(); render(); };
      slider.oninput = function () { applyBucket(); render(); };
      wrap.appendChild(lab); wrap.appendChild(unitSelect); wrap.appendChild(sliderWrap);
      controls.appendChild(wrap);
      rebuildBuckets();
    }
  }

  function renderTable(data) {
    aboutDataDiv.innerHTML = buildAboutDataHTML(source);
    tableTitle.innerText = (cfg.title || "Checked Samples") + " — totals for the current view";
    const table = document.createElement("table");
    table.setAttribute("class", "mb-0 table table-striped table-bordered table-hover");
    const headerCols = hierarchy.map(function (h) { return h.label || h.axis; }).concat(["Count", "% of shown"]);
    const headRow = table.insertRow(-1);
    headerCols.forEach(function (h) {
      const th = document.createElement("th"); th.innerText = h; headRow.appendChild(th);
    });
    data.rows.forEach(function (row) {
      const tr = table.insertRow(-1);
      tr.setAttribute("class", "align-middle");
      row.path.forEach(function (p) { tr.insertCell(-1).innerText = p; });
      tr.insertCell(-1).innerText = row.value;
      tr.insertCell(-1).innerText = data.total ? ((row.value / data.total) * 100).toFixed(1) + "%" : "0%";
    });
    tableDiv.innerHTML = "";
    tableDiv.appendChild(table);
  }

  function render() {
    const data = factsToTreemap(facts, cfg, sel);
    const trace = {
      type: "treemap",
      branchvalues: "total",
      ids: data.ids,
      labels: data.labels,
      parents: data.parents,
      values: data.values,
      tiling: { packing: "squarify", pad: 1 },
      marker: { line: { width: 1, color: canaskMarkerLineColor() } },
      texttemplate: "<b>%{label}</b><br>%{percentRoot} (%{value})",
      hovertemplate: "<b>%{label}</b><br>%{value} samples<br>%{percentRoot} of shown<extra></extra>",
      pathbar: { visible: true },
    };
    const layout = {
      title: cfg.title || "",
      width: $("#viz-card").width(),
      height: $("#viz-card").height(),
      margin: { t: 40, l: 0, r: 0, b: 0 },
    };
    visDiv.innerHTML = "";
    Plotly.purge(visDiv);
    Plotly.react(visDiv, [trace], themeChartLayout(layout), { displaylogo: false, responsive: false });
    renderTable(data);
  }

  buildControls();
  render();
}


// Whether a visual can drill into its next level: it must declare a next-vis AND that target must
// be in the set the server returned for this user (RBAC may withhold deeper drill levels).
function canDrill(province, vid) {
  const cfg = visuals[province] && visuals[province][vid];
  const next = cfg && cfg["next-vis"];
  return !!(next && visuals[province][next] && currentData && currentData[next]);
}

// Helper function to move up one level in the visual hierarchy
function moveUpOneLevel(province, prevLocation = null) {
  // set the current visual to the next-level of the current visual
  nextLevel = visuals[province][currentVisual]["next-vis"];

  if (nextLevel == null) {
    console.error("No next level visual exists for this visual");
    return;
  }
  // The drill target must be in the set the server returned (RBAC may withhold it); if not, don't navigate.
  if (!visuals[province][nextLevel] || !currentData[nextLevel]) {
    console.warn(`Drill target ${nextLevel} not available to this user`);
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
  // clear any treemap stratifier dropdowns so they don't linger on a non-treemap visual
  let stratifiers = document.getElementById("vis-stratifiers");
  if (stratifiers) stratifiers.innerHTML = "";
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

// Render the "you are here" breadcrumb for the current drill state. The trail
// grows with depth: Province > [level-1 visual] > [location] > [category].
// Each upstream crumb is clickable and navigates back to its own level.
function renderBreadcrumb(location, category) {
  let el = document.getElementById("vis-breadcrumb");
  if (!el || typeof visuals === "undefined" || !visuals[province] || !visuals[province][currentVisual]) return;

  let provLabel = province.replace(/-/g, " ").replace(/\b\w/g, function (c) {
    return c.toUpperCase();
  });
  let level = visuals[province][currentVisual]["level"] || 1;
  let rootVisual = (route && route.length > 0) ? String(route[0]).split("/")[0] : currentVisual;
  let rootMeta = visuals[province][rootVisual];
  let rootName = rootMeta && rootMeta["menu-name"] ? rootMeta["menu-name"] : null;

  // Build the trail. `target` is the level a crumb navigates to (null = current,
  // non-clickable).
  let trail = [{ label: provLabel, target: null }];
  if (rootName) trail.push({ label: rootName, target: level > 1 ? 1 : null });
  if (location && level >= 2) trail.push({ label: location, target: level > 2 ? 2 : null });
  if (category && level >= 3) trail.push({ label: category, target: null });

  el.innerHTML = trail
    .map(function (c, i) {
      let isLast = i === trail.length - 1;
      if (c.target != null) {
        return '<button type="button" class="crumb crumb-link" data-level="' + c.target + '">' + c.label + "</button>";
      }
      return '<span class="crumb ' + (isLast ? "crumb-current" : "crumb") + '">' + c.label + "</span>";
    })
    .join('<i class="bi bi-chevron-right crumb-sep" aria-hidden="true"></i>');

  Array.prototype.forEach.call(el.querySelectorAll("[data-level]"), function (btn) {
    btn.onclick = function () {
      breadcrumbGoToLevel(parseInt(btn.getAttribute("data-level"), 10));
    };
  });
}

// Navigate back to a specific level in the current drill chain.
function breadcrumbGoToLevel(target) {
  if (typeof visuals === "undefined" || !visuals[province] || !visuals[province][currentVisual]) return;
  let level = visuals[province][currentVisual]["level"] || 1;
  if (!route || target >= level) return;
  if (target <= 1) {
    // Back to the level-1 root (same as Reset)
    currentVisual = route[0];
    masterLoop();
    return;
  }
  // Back to an intermediate level: that level's visual is route[target - 1].
  currentVisual = route[target - 1];
  route = route.slice(0, target - 1);
  masterLoop(lastLocation);
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
    let parentElement = document.querySelector(`#${slugify(route[0])}-dropdown`);
    parentElement.classList.add("active");
  } else {
    let parentElement = document.querySelector(`#${slugify(visuals[province][currentVisual]["menu-parent"])}-dropdown`);
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
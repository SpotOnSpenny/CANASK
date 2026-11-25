// Global variables to hold the current data and geojson
let currentData;
let currentGeojson;
let currentVisual;
let province;
// Public URL base for this page's deep links, set from the server (e.g. "/v1/province/alberta" or
// "/v1/national/drug-checking"). Province pages and national dashboards share the same template but
// live at different URLs, so the address bar is keyed off this rather than the internal scope key.
let urlBase;
let route = [];
let lastLocation = null;
// Menu/presentation config, populated per province from the DB response in fetchRegionData()
// (replaces the formerly-static visuals.js). Shape mirrors the old global: {province: {visual_id: cfg},
// "default-visuals": {province: visual_id}} so the existing visuals[province][...] reads work unchanged.
let visuals = {};
// Ordered top-level menu dropdown names for the current province, served by the DB (build_province_menu)
// rather than hard-coded -- derived from the visuals' menu-parent values.
let menuCategories = [];
// payload.landing: only for a viewer who has not set their own starting chart for this page and
// whose groups picked DIFFERENT ones -- {options: [{group, visual, visual_id, label}], active:
// visual name}. null for everyone else. It drives a one-time notice (renderLandingNotice); the
// lasting control, where they can pick any chart they can see, is on the My account page.
let landingChoice = null;

// Province-scale `geo` layout settings, spread into each map's `layout.geo` alongside `fitbounds`.
// Two Plotly defaults fight province maps:
//  * The default projection is equirectangular (plate carree), which draws a degree of longitude as
//    wide as a degree of latitude; at Canadian latitudes a degree of longitude is really only
//    ~0.5-0.7 as wide, so every province came out squashed (BC rendered ~2.1x wider than tall
//    instead of its true ~1.2x). A conic conformal projection centred on the province, with its
//    standard parallels at the province's own latitude extent (the Lambert set-up Statistics Canada
//    uses for provincial maps), keeps local shapes right.
//  * The default scope is "world", whose drag handler ROTATES the projection like a globe (and
//    reshapes a conic map as it swings). A regional scope switches dragging to a plain pan (Plotly
//    moves `geo.center`, rotation untouched) and scroll to a zoom about the cursor. "north america"
//    is just the scope whose drag model we want; its own defaults (central meridian -100, country
//    borders on) are overridden below so the cone stays centred on the province and nothing but
//    the province's polygons is drawn. (In world scope `fitbounds` ignores `rotation.lon`; in a
//    regional scope it is honoured, which is what lets the cone follow the province.)
function provinceGeoLayout(geojson) {
  let lon0 = Infinity, lon1 = -Infinity, lat0 = Infinity, lat1 = -Infinity;
  const walk = (coords) => {
    if (typeof coords[0] === "number") {
      lon0 = Math.min(lon0, coords[0]); lon1 = Math.max(lon1, coords[0]);
      lat0 = Math.min(lat0, coords[1]); lat1 = Math.max(lat1, coords[1]);
    } else {
      coords.forEach(walk);
    }
  };
  ((geojson && geojson.features) || []).forEach((feature) => {
    if (feature.geometry && feature.geometry.coordinates) walk(feature.geometry.coordinates);
  });
  const projection = isFinite(lat0)
    ? { type: "conic conformal", rotation: { lon: (lon0 + lon1) / 2 }, parallels: [lat0, lat1] }
    : { type: "mercator" };   // empty/odd geojson: still a conformal projection
  return { scope: "north america", projection: projection, showcountries: false };
}

// Cursor-following tooltip for the geo maps. Plotly pins a choropleth's hover label at the centroid
// of the WHOLE feature, so for a two-part region (Vancouver Coastal is split by Island Health) the
// label lands in the water between the parts, far from wherever the mouse actually is. The map
// traces therefore set `hoverinfo: "none"` (Plotly still hit-tests the polygons and fires
// plotly_hover/plotly_unhover; only its label is suppressed) and this draws one shared tip div
// beside the cursor instead, moving it with the mouse while a region is under it. `describe(point)`
// returns {name, value} for a point this map wants a tip for, or null to leave the point to Plotly
// (Confluence keeps the native label on its DAS city bubbles). Call after each Plotly.react: the
// previous call's mousemove listener and plotly_hover/plotly_unhover handlers on the same div are
// removed first, so a div re-rendered without Plotly.purge (Confluence's map) never stacks them.
function attachGeoHoverTip(gd, describe) {
  let tip = document.getElementById("canask-geo-tip");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "canask-geo-tip";
    tip.className = "canask-geo-tip";
    tip.setAttribute("role", "tooltip");
    tip.innerHTML = '<div class="canask-geo-tip-name"></div><div class="canask-geo-tip-value"></div>';
    document.body.appendChild(tip);
  }
  const hide = () => { tip.style.display = "none"; };
  const place = (clientX, clientY) => {
    const pad = 14;
    let left = clientX + pad, top = clientY + pad;
    if (left + tip.offsetWidth > window.innerWidth - 8) left = clientX - pad - tip.offsetWidth;
    if (top + tip.offsetHeight > window.innerHeight - 8) top = clientY - pad - tip.offsetHeight;
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = `${Math.max(0, top)}px`;
  };
  hide();
  if (gd._canaskGeoTipMove) gd.removeEventListener("mousemove", gd._canaskGeoTipMove);
  gd._canaskGeoTipMove = (event) => { if (tip.style.display !== "none") place(event.clientX, event.clientY); };
  gd.addEventListener("mousemove", gd._canaskGeoTipMove);
  if (gd._canaskGeoTipHover && gd.removeListener) {
    gd.removeListener("plotly_hover", gd._canaskGeoTipHover);
    gd.removeListener("plotly_unhover", gd._canaskGeoTipUnhover);
  }
  gd._canaskGeoTipHover = (data) => {
    const point = data.points && data.points[0];
    const info = point && describe(point);
    if (!info) { hide(); return; }
    tip.querySelector(".canask-geo-tip-name").textContent = info.name;
    const valueEl = tip.querySelector(".canask-geo-tip-value");
    valueEl.textContent = info.value == null ? "" : info.value;
    valueEl.style.display = info.value == null ? "none" : "";
    tip.style.display = "block";
    if (data.event) place(data.event.clientX, data.event.clientY);
  };
  gd._canaskGeoTipUnhover = hide;
  gd.on("plotly_hover", gd._canaskGeoTipHover);
  gd.on("plotly_unhover", gd._canaskGeoTipUnhover);
}

// ---- Deep-linkable URL state ----
// The address bar is kept in sync with the current visual + drill path so visuals can be linked to
// and Back/Forward navigates between them. `isReplaying` suppresses URL writes while we rebuild state
// from a URL (deep link or popstate) so the replay's masterLoop calls don't push bogus history.
// `suppressInitialPush` makes the very first render replaceState (canonicalize) instead of pushState.
let isReplaying = false;
let suppressInitialPush = true;
// Self-contained renderers (the treemap, the found-substance lines) don't drill through masterLoop,
// so their selection rides the URL as query params instead of path segments. Each keeps this string
// current on every render ("t.g0=bc&t.f0=opioids&..." / "fs.e=fentanyl&fs.g=..."), and buildVisualUrl
// appends it whenever the active visual is one of them, so the existing syncUrl push/replace +
// Back/Forward machinery deep-links the selection for free.
let activeRendererParams = "";
const SELF_CONTAINED_URL_TYPES = new Set(["treemap", "found_substance_lines", "stacked_hbar", "grouped_bar", "stacked_bar"]);
// Time grain + alternate chart type for grain-capable visuals (manifest visual_options.time_grains /
// chart_types). Module state like the treemap params: masterLoop clamps both to what the current
// visual offers, the toggles set them and re-run masterLoop, and buildVisualUrl serializes them as
// ?grain= / ?chart= (omitted at their defaults) so deep links and Back/Forward restore the view.
let activeGrain = "year";
let activeChartType = null;   // null = the visual's own chart_type
// A heatmap's Measure (counts / rates) -- module state like the grain, so it survives the grain and
// Map/Trend toggles and carries into the drilled child. null = the visual's first data type; rides
// the URL as ?measure= (omitted at that default).
let activeMeasure = null;
// Where each heatmap's slider is parked, by visual id. Absent = the latest period, the default,
// which is deliberately NOT recorded: a link without it keeps opening on the newest data. Rides
// the URL as ?period=<key> for that visual's map view only. A key that isn't on the slider
// (another grain: "2025" vs "2025-Q2" vs "2025-04") is dropped by the renderer. Reset per page
// by fetchRegionData. (createVisualPie has a year slider too, but no visual is a pie today, so
// it is not wired up.)
let activePeriods = {};
const GRAIN_ORDER = ["year", "quarter", "month"];
const GRAIN_LABELS = { year: "Year", quarter: "Quarter", month: "Month" };
const CHART_TYPE_LABELS = { heatmap: "Map", line: "Trend" };
// Colorbar title + hover label per measure a heatmap can draw (visual_definitions.HEATMAP_DATA_TYPES).
const HEATMAP_MEASURE_LABELS = {
  counts: { colorbar: "Number<br>of Deaths", hover: "Number of deaths" },
  rates: { colorbar: "Deaths per<br>100,000", hover: "Deaths per 100,000" },
};

// Slugify a display name into the DOM-id form used for menu dropdowns ("Drug Supply" -> "drug-supply").
// Collapse every run of non-alphanumeric characters to a single hyphen so names with punctuation
// (e.g. "Hospitalizations & Other Harms") yield a valid CSS id / URL segment, not "...-&-...".
function slugify(str) {
  return String(str).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

// Reverse a slug (a deep-link query value) back to the live value it came from: the first
// slug-equal candidate wins; null for an absent param or no match. Shared by every self-contained
// renderer that round-trips its selection through the URL.
function matchSlug(candidates, raw) {
  if (!raw) return null;
  const hit = candidates.find(c => slugify(c) === raw);
  return hit == null ? null : hit;
}

// Province / site picker options for the drug-checking renderers, whose facts carry geo =
// "Province||Site" (parsed rows with .prov / .site): the distinct provinces, the sites of one
// province (every site when null) and the two level labels (visual_options.geo_levels).
function geoPickerOptions(parsed, cfg) {
  const distinctSorted = (fn, pred) => Array.from(new Set(parsed.filter(pred || (() => true)).map(fn).filter(v => v))).sort();
  return {
    provinces: distinctSorted(p => p.prov),
    sitesFor: prov => distinctSorted(p => p.site, p => prov == null || p.prov === prov),
    levels: Array.isArray(cfg.geo_levels) && cfg.geo_levels.length === 2 ? cfg.geo_levels : ["Province", "Site/Organization"],
  };
}

// URL-safe slug for a path segment: slugify, then percent-encode so values containing / # ? % (e.g. a
// health-authority name with a slash) can't corrupt the path. Reverse-matching uses the same pair.
function urlSegment(value) {
  return encodeURIComponent(slugify(String(value)));
}

// Build the canonical URL for the current visual + drill context. The path always uses the LEVEL-1
// root visual's slug (route[0] when drilled, else currentVisual); location/category extend it.
// year, when present, rides as ?y= (kept out of the pretty path, mirroring the breadcrumb).
function buildVisualUrl(location, year, category) {
  let path = urlBase;
  const cfgs = visuals[province] || {};
  const rootVid = (route && route.length > 0) ? String(route[0]).split("/")[0] : currentVisual;
  const rootCfg = cfgs[rootVid];
  const rootSlug = rootCfg && rootCfg["slug"];
  if (rootSlug) {
    path += "/" + encodeURIComponent(rootSlug);
    if (location != null) path += "/" + urlSegment(location);
    if (category != null) path += "/" + urlSegment(category);
  }
  // year rides as ?y=; a treemap's filter state rides as its own t.* params (no location/category path).
  const query = [];
  if (year != null) query.push("y=" + encodeURIComponent(year));
  if (rootCfg && SELF_CONTAINED_URL_TYPES.has(rootCfg["type"]) && activeRendererParams) query.push(activeRendererParams);
  // grain / chart ride only on a grain-capable root, and only away from their defaults.
  const rootBlock = currentData && currentData[rootVid];
  if (rootBlock && activeGrain !== defaultGrain(rootBlock) && availableGrains(rootBlock).includes(activeGrain)) {
    query.push("grain=" + encodeURIComponent(activeGrain));
  }
  if (rootBlock && activeChartType && activeChartType !== rootCfg["type"]
      && availableChartTypes(rootBlock, rootCfg).includes(activeChartType)) {
    query.push("chart=" + encodeURIComponent(activeChartType));
  }
  const rootTypes = (rootCfg && rootCfg["data-types"]) || [];
  if (rootCfg && rootCfg["type"] === "heatmap" && activeMeasure && activeMeasure !== rootTypes[0]
      && rootTypes.includes(activeMeasure)) {
    query.push("measure=" + encodeURIComponent(activeMeasure));
  }
  // The map's slider position: only on the map itself (not drilled, not its Trend view).
  const shownPeriod = activePeriods[rootVid];
  if (shownPeriod && location == null && rootCfg && rootCfg["type"] === "heatmap"
      && (activeChartType || "heatmap") === "heatmap") {
    query.push("period=" + encodeURIComponent(shownPeriod));
  }
  return path + (query.length ? "?" + query.join("&") : "");
}

// Keep the address bar in sync with the current render. No-op while replaying (popstate/deep link).
function syncUrl(location, year, category) {
  if (isReplaying) return;
  const url = buildVisualUrl(location, year, category);
  if (url === window.location.pathname + window.location.search) return;
  if (suppressInitialPush) {
    // First render: rewrite the current entry to the canonical URL without adding history.
    suppressInitialPush = false;
    history.replaceState({ v1deeplink: true }, "", url);
    return;
  }
  history.pushState({ v1deeplink: true }, "", url);
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
// the chosen data type (which rebuilds this group; the container is cleared here first rather than by
// resetVisualControl, which would also wipe the grain / chart-type toggles). No-op (empty group) when
// only one data type is available.
function buildDataTypeToggles(container, dataTypes, currentDataType, onSelect) {
  container.innerHTML = "";
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
      onSelect(type);
    };
    let label = document.createElement("label");
    label.className = "btn btn-outline-primary";
    label.setAttribute("for", `${type}-toggle`);
    label.innerText = type.charAt(0).toUpperCase() + type.slice(1);
    container.appendChild(toggle);
    container.appendChild(label);
  }
  fitChartToCard();
}

// The renderers size the chart from #viz-card's height, but the Measure group is only filled AFTER
// the chart is drawn. masterLoop shows the control bar first and .vis-controls reserves one row
// (min-height), so the usual case is a no-op; this re-fit is the fallback for a bar whose final height
// differs from that row (e.g. its groups wrap onto a second line on a narrow card).
function fitChartToCard() {
  const gd = document.getElementById("vis-div");
  if (!gd || !gd._fullLayout || gd.classList.contains("treemap-layout")) return;
  requestAnimationFrame(() => {
    if (!gd._fullLayout) return;
    const height = chartAreaHeight();
    if (height > 0 && Math.abs((gd._fullLayout.height || 0) - height) > 2) Plotly.relayout(gd, { height: height });
  });
}

// The visual's title is an HTML heading (#vis-title) above the Plotly figure rather than Plotly's
// layout.title: it wraps like ordinary text and can never run into the mode bar, legend or plot,
// which all live inside the figure below it. Renderers call this with the resolved title (or null
// for the treemap-style visuals that draw their own header) BEFORE sizing the figure, so
// chartAreaHeight() can subtract the heading's height.
function setVisualTitle(text) {
  const el = document.getElementById("vis-title");
  if (!el) return;
  const title = text == null ? "" : String(text).replace(/<br\s*\/?>/gi, " ").trim();
  el.textContent = title;
  el.classList.toggle("d-none", !title);
}

// What the PNG export (chartConfig in plotly-theme.js) calls this chart: the heading above the figure,
// or the header the treemap-style visuals draw inside #vis-div, plus the page's jurisdiction. Read
// when the download button is clicked, so it follows the filters and toggles.
function visualExportInfo() {
  const heading = document.getElementById("vis-title");
  const own = document.querySelector("#vis-div .treemap-title");
  const title = (heading && !heading.classList.contains("d-none") && heading.textContent.trim())
    || (own && own.textContent.trim()) || "";
  const place = String(province || "").replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  return { title: title, place: place };
}

// Height left for the Plotly figure inside #viz-card once the heading (and its margin) is placed.
function chartAreaHeight() {
  const card = $("#viz-card").height();
  const el = document.getElementById("vis-title");
  const heading = el && !el.classList.contains("d-none") ? el.getBoundingClientRect().height + 8 : 0;
  return Math.max(120, Math.floor(card - heading));
}

// Empty a grain / chart-type toggle group and drop its d-flex (the template ships both hidden). Its
// .vis-control wrapper in the control bar hides itself via CSS :has once the group is empty.
function clearToggleGroup(container) {
  if (!container) return;
  container.innerHTML = "";
  container.classList.remove("d-flex");
  container.classList.add("d-none");
}

// Show / hide the control bar beneath the chart (#vis-controls). Called by masterLoop before the
// renderer sizes the chart, and by the empty-state renderer.
function setVisControlsVisible(shown) {
  const bar = document.getElementById("vis-controls");
  if (bar) bar.classList.toggle("d-none", !shown);
}

// Renderers that put their own filter dropdowns in the control bar's #vis-renderer-controls slot (on
// the same line as Measure / Period / View), so masterLoop shows the bar for them regardless of toggles.
const BAR_CONTROL_TYPES = new Set(["found_substance_lines"]);
function clearRendererControls() {
  const slot = document.getElementById("vis-renderer-controls");
  if (slot) slot.innerHTML = "";
}

// Shared radio-group builder for the grain / chart-type toggles (same markup as the data-type group,
// with its own radio name + id prefix). No-op when there is at most one option.
function buildRadioToggleGroup(container, name, idPrefix, options, current, labels, onSelect) {
  clearToggleGroup(container);
  if (!container || !options || options.length <= 1) return;
  for (const option of options) {
    const toggle = document.createElement("input");
    toggle.type = "radio";
    toggle.className = "btn-check";
    toggle.name = name;
    toggle.id = `${idPrefix}-${option}-toggle`;
    toggle.autocomplete = "off";
    toggle.checked = option === current;
    toggle.onclick = function () { onSelect(option); };
    const label = document.createElement("label");
    label.className = "btn btn-outline-primary";
    label.setAttribute("for", toggle.id);
    label.innerText = labels[option] || option;
    container.appendChild(toggle);
    container.appendChild(label);
  }
  container.classList.remove("d-none");
  container.classList.add("d-flex");
}

// Year / Quarter / Month toggle (radios `grain-toggle`, ids `grain-<g>-toggle`).
function buildTimeGrainToggles(container, grains, current, onSelect) {
  buildRadioToggleGroup(container, "grain-toggle", "grain", grains, current, GRAIN_LABELS, onSelect);
}

// Map / Trend toggle (radios `chart-toggle`, ids `chart-<t>-toggle`).
function buildChartTypeToggles(container, chartTypes, current, onSelect) {
  buildRadioToggleGroup(container, "chart-toggle", "chart", chartTypes, current, CHART_TYPE_LABELS, onSelect);
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
  renderLandingNotice();
}

// One-time notice above the page (#vis-landing-notice) for someone whose groups picked different
// starting charts for this page and who has not set their own yet (landingChoice is non-null).
// It asks which chart they want to see first and lists every pick as a button, the one showing
// now included -- pressing any of them is saved as their own starting chart, so the notice does
// not come back. The x only hides it for this browser session. Group and visual names go in via
// textContent only.
function landingNoticeKey(scope) { return `canask-landing-notice-dismissed:${scope}`; }

function renderLandingNotice() {
  const host = document.getElementById("vis-landing-notice");
  if (!host) return;
  host.innerHTML = "";
  host.classList.add("d-none");
  if (!landingChoice || !Array.isArray(landingChoice.options) || landingChoice.options.length < 2) return;
  try { if (sessionStorage.getItem(landingNoticeKey(province))) return; } catch (e) { /* storage blocked */ }

  const box = document.createElement("div");
  box.className = "alert alert-info landing-notice mb-0";
  box.setAttribute("role", "status");

  const text = document.createElement("div");
  text.className = "landing-notice-text";
  const lead = document.createElement("strong");
  lead.textContent = "Which chart do you want to see first on this page?";
  text.appendChild(lead);
  text.appendChild(document.createTextNode(" You're in more than one group, and each has picked a different one. You can choose any other chart, or change your mind later, in "));
  const link = document.createElement("a");
  link.href = "/v1/account/starting-charts";
  link.className = "alert-link";
  link.textContent = "My account";
  text.appendChild(link);
  text.appendChild(document.createTextNode("."));
  box.appendChild(text);

  const actions = document.createElement("div");
  actions.className = "landing-notice-actions";
  for (const option of landingChoice.options) {
    const showing = option.visual === landingChoice.active;
    const button = document.createElement("button");
    button.type = "button";
    button.className = showing ? "btn btn-sm btn-outline-secondary" : "btn btn-sm btn-primary";
    button.textContent = showing ? `${option.label} (${option.group}, showing now)` : `${option.label} (${option.group})`;
    button.onclick = () => chooseLanding(option);
    actions.appendChild(button);
  }
  box.appendChild(actions);

  const close = document.createElement("button");
  close.type = "button";
  close.className = "btn-close landing-notice-close";
  close.setAttribute("aria-label", "Not now");
  close.title = "Not now";
  close.onclick = () => {
    try { sessionStorage.setItem(landingNoticeKey(province), "1"); } catch (e) { /* storage blocked */ }
    host.innerHTML = "";
    host.classList.add("d-none");
  };
  box.appendChild(close);

  host.appendChild(box);
  host.classList.remove("d-none");
}

// Save the chart picked in the notice as the viewer's own starting chart (also when it is the one
// already showing: that is what stops the notice coming back), then show it. A failed save leaves
// the page as it was and says so. `province`, `landingChoice` and `visuals` are page globals that
// an HTMX navigation replaces, so the ones this click belongs to are captured first and nothing
// is written if the page changed during the save.
async function chooseLanding(option) {
  const scope = province;
  const choice = landingChoice;
  const body = new FormData();
  body.append("visual_id", option.visual_id);
  let saved;
  try {
    const response = await fetch(`/v1/landing-choice/${encodeURIComponent(scope)}`, {
      method: "POST",
      headers: { "X-CSRFToken": document.querySelector("meta[name='csrf-token']").getAttribute("content") },
      body,
      signal: AbortSignal.timeout(10000),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data["default"]) throw new Error(data.error || `status ${response.status}`);
    saved = data["default"];
  } catch (error) {
    console.error("Could not save the starting chart:", error);
    // A timeout only aborts this side; the server may still have saved it, so point at a reload.
    showFlash("We couldn't confirm that was saved. Reload the page to check.", "danger");
    return;
  }
  if (province !== scope || landingChoice !== choice) return;   // navigated away meanwhile
  landingChoice = null;   // they have their own starting chart now: nothing left to ask
  visuals["default-visuals"][scope] = saved;
  renderLandingNotice();
  showFlash(`Saved. This page will open on ${option.label} from now on.`, "success");
  if (saved === currentVisual || !visuals[scope] || !visuals[scope][saved]) return;
  if (visuals[scope][saved]["type"] !== "map") resetVisualControl();
  currentVisual = saved;
  masterLoop();
}

// ---- Client-side adapter from the generic fact contract to the legacy block shape ----
// The /api route serves each visual as normalized facts; this rebuilds the per-visual block shape
// the Plotly renderers consume. Verified data-identical to the former server-side reconstruction.
const SUBSTANCE_DISPLAY = { opioids: "Opioid", stimulants: "Stimulant" };

// A fact value can be a reported number (incl a genuine 0), this SUPPRESSED sentinel (the source
// confirms data exists but hides the small count for privacy), or null (not reported / no data).
// Renderers: reported -> a point; SUPPRESSED -> a dashed "bridge" between neighbours; null -> a gap.
const SUPPRESSED = "Suppr.";
// A series is worth drawing only if it has at least one reported, non-zero value (so all-zero,
// all-suppressed, and all-missing series are hidden from both the chart and the table).
function hasReportedNonZero(arr) {
  return Array.isArray(arr) && arr.some(v => typeof v === "number" && v !== 0);
}

// A factsToSeries data-type object ({x, "<series>":[...], ...}) is worth showing if any of its
// series has visible (reported, non-zero) data.
function dataTypeHasData(seriesObj) {
  return !!seriesObj && Object.keys(seriesObj).some(k => k !== "x" && hasReportedNonZero(seriesObj[k]));
}

// Render an empty-state message and clear the chart / table / data-type toggle / about-data. Used
// when a visual has no reportable data for any data type -- e.g. a territory whose every series is
// suppressed or not reported (the visual still exists in the menu, so this is reachable).
function renderEmptyVisual(message) {
  const visDiv = document.getElementById("vis-div");
  const tableDiv = document.getElementById("data-table");
  const toggle = document.getElementById("data-type-toggle");
  const aboutDiv = document.getElementById("about-data");
  const stratifiers = document.getElementById("vis-stratifiers");
  // Reset the treemap's "treemap-layout" class so the empty state doesn't inherit its flex/100%-height
  // sizing, and clear any leftover stratifier dropdowns -- both are reachable when the empty state
  // follows a treemap (the class is set in createVisualTreemap and never otherwise cleared here).
  if (visDiv) { Plotly.purge(visDiv); visDiv.className = ""; visDiv.innerHTML = `<p class="text-muted text-center py-5">${message}</p>`; }
  if (tableDiv) tableDiv.innerHTML = "";
  if (toggle) toggle.innerHTML = "";
  clearRendererControls();
  setVisualTitle(null);
  setVisControlsVisible(false);
  if (aboutDiv) aboutDiv.innerHTML = "";
  if (stratifiers) stratifiers.innerHTML = "";
}

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
        // Align every series to the shared year axis: a year the series has no fact for becomes null
        // (a "not reported" gap). Reported numbers (incl 0) and the "Suppr." sentinel ride through
        // verbatim -- the renderers interpret them (0 = a real point, "Suppr." = a dashed bridge).
        for (const key of Object.keys(tree[dt])) { const yv = tree[dt][key]; o[key] = o.x.map(y => (y in yv ? yv[y] : null)); }
        data[dt] = o;
    }
    return data;
}

// Heatmap data: {geo: {x:[periods], y:[values]}} from one data type's facts (counts unless asked;
// a single value series per area). Every area shares ONE period axis (the union of all areas'
// periods; a period an area has no fact for is null), so the slider's step i is the same period for
// every area.
function factsToHeatmap(facts, kind, dataType = "counts") {
    const byGeo = {}, years = new Set();
    for (const f of facts) {
        if (f.dt !== dataType) continue;
        const key = seriesKey(kind, f.d, f.d2);
        (byGeo[f.geo] = byGeo[f.geo] || {})[key] = byGeo[f.geo][key] || {}; byGeo[f.geo][key][f.t] = f.v;
        years.add(f.t);
    }
    const axis = _sortYears(years);
    const out = {};
    for (const g of Object.keys(byGeo)) {
        const o = { x: axis };
        for (const key of Object.keys(byGeo[g])) { const yv = byGeo[g][key]; o[key] = axis.map(y => (y in yv ? yv[y] : null)); }
        out[g] = o;
    }
    return out;
}

// ---- Time grain (year / quarter / month) + alternate chart type ----
// A fact's grain is its served time_frame_type ("g"); facts predating the field are yearly.
function factGrain(f) { return f.g || "year"; }
function factsAtGrain(facts, grain) { return facts.filter(f => factGrain(f) === grain); }

// Grains a visual can be shown at: its manifest's visual_options.time_grains, intersected with the
// grains its (non-additional) facts actually carry, in GRAIN_ORDER. A heatmap draws only its counts,
// so only counts facts count there. [] = not grain-capable.
function availableGrains(block) {
    const listed = block && block.visual_options && block.visual_options.time_grains;
    if (!Array.isArray(listed)) return [];
    const drawn = block.chart_type === "heatmap" ? (f => f.dt === "counts") : (f => f.dt !== "additional_rows");
    const present = new Set();
    for (const f of (block.facts || [])) if (drawn(f)) present.add(factGrain(f));
    return GRAIN_ORDER.filter(g => listed.includes(g) && present.has(g));
}

// The grain a grain-capable visual opens on: visual_options.default_grain (validated by define-visuals
// against time_grains) else year -- the finest-first order of GRAIN_ORDER would otherwise open a
// quarter+month visual on quarters.
function defaultGrain(block) {
    const preferred = block && block.visual_options && block.visual_options.default_grain;
    return GRAIN_ORDER.includes(preferred) ? preferred : "year";
}

// Chart types a level-1 geo_series visual can switch between (visual_options.chart_types, limited to
// the map and the per-area trend line). [] = no chart-type toggle.
function availableChartTypes(block, cfg) {
    if (!block || block.shape !== "geo_series" || !cfg || cfg["level"] !== 1) return [];
    const listed = (block.visual_options || {}).chart_types;
    if (!Array.isArray(listed)) return [];
    return listed.filter(t => t === "heatmap" || t === "line");
}

// Month-grain axes get crowded fast (13+ "YYYY-MM" categories, and more as history accumulates), so
// label only the quarter-start months (Jan / Apr / Jul / Oct); every month still has its point and
// hover. Falls back to every period when there are fewer than two quarter starts to show.
function quarterStartTicks(periods) {
  const all = periods || [];
  const starts = all.filter(p => /^\d{4}-(01|04|07|10)$/.test(String(p)));
  // Long histories (BCCSU runs from 2018): past ~5 years of quarters, fall back to one tick a year.
  if (starts.length > 20) return all.filter(p => /^\d{4}-01$/.test(String(p)));
  return starts.length >= 2 ? starts : all;
}
// Plotly axis settings for a time axis at the given grain: category axes for quarters/months (so
// "2025-Q2"/"2025-04" aren't parsed as dates), quarter-start ticks for months, yearly dtick for years.
function grainXAxis(grain, periods) {
  if (grain === "month") return { type: "category", tickmode: "array", tickvals: quarterStartTicks(periods) };
  if (grain === "quarter") return { type: "category" };
  return { dtick: 1 };
}
// Re-word a yearly title for a finer grain: "by Year" -> "by Month", "Annual" -> "Monthly", else
// append " (Monthly)". Yearly (and non-string) titles pass through untouched.
function grainTitle(text, grain) {
    if (typeof text !== "string" || !grain || grain === "year") return text;
    const adjective = { quarter: "Quarterly", month: "Monthly" }[grain] || GRAIN_LABELS[grain];
    if (/\bby Year\b/.test(text)) return text.replace(/\bby Year\b/g, `by ${GRAIN_LABELS[grain]}`);
    if (/\bAnnual\b/.test(text)) return text.replace(/\bAnnual\b/g, adjective);
    return `${text} (${adjective})`;
}

// Areas a map visual's geojson and its facts disagree on: outlines with no data in any period (drawn
// blank) and areas with data but no outline (not drawn on the map; legend-only in the Trend view). The
// province-wide total (e.g. "British Columbia") never has an outline and isn't a mismatch. A geojson /
// cleaner name drift otherwise renders silently as a blank region and a hidden line.
function geoMismatch(geojson, facts, provinceSlug) {
  const outlines = new Set(((geojson && geojson.features) || []).map(ft => ft.properties.ENGNAME));
  const total = String(provinceSlug || "").replace(/-/g, " ").toTitleCase();
  const geos = new Set(facts.filter(f => f.dt === "counts" && f.geo != null).map(f => f.geo));
  return {
    noData: Array.from(outlines).filter(name => !geos.has(name)).sort(),
    noOutline: Array.from(geos).filter(geo => !outlines.has(geo) && geo !== total).sort(),
  };
}

// Show (or clear, with null) the map/data mismatch note under the table caption, and log it.
function setGeoMismatchNote(mismatch) {
  const note = document.getElementById("vis-geo-note");
  const parts = [];
  if (mismatch && mismatch.noOutline.length) parts.push(`No map outline for: ${mismatch.noOutline.join("; ")} (shown in the table only).`);
  if (mismatch && mismatch.noData.length) parts.push(`No data for: ${mismatch.noData.join("; ")} (drawn blank).`);
  if (parts.length) console.warn("Map/data area mismatch:", mismatch);
  if (!note) return;
  note.textContent = parts.join(" ");
  note.classList.toggle("d-none", parts.length === 0);
}

// Per-area trend lines for a geo_series map: {counts|rates: {x, "<area>": [...]}}. geo_series' key_kind
// is "constant", which would collapse every area into one series, so key each fact by its geo instead.
function factsToGeoSeries(facts) {
    return factsToSeries(facts.map(f => Object.assign({}, f, { d2: f.geo })), "plain");
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
    //fetch the data and unpack it (`signal:` is the correct option name; a 5s timeout aborts a hung request)
    // The province data is REQUIRED; the geojson is OPTIONAL -- only the map/heatmap renderers use it,
    // and provinces with just line/bar charts (e.g. the territories) ship no geojson file. A missing or
    // failed geojson must NOT block the page, so it falls back to null (`.catch` covers a network/timeout
    // rejection; the `.ok` check below covers a 404).
    const [data, geojson] = await Promise.all([
        fetch(`/api/v1/province/${province}/data`, {signal: AbortSignal.timeout(5000)}),
        fetch(`/static/assets/geojsons/${province.toLowerCase()}.geojson`, {signal: AbortSignal.timeout(5000)})
            .catch(() => null),
    ]);
    // Fail loudly on a non-2xx response instead of calling .json() on an error body and rendering garbage.
    // A 429 (rate limited) is flagged so the caller can show a friendly "slow down" message rather than
    // the generic error state.
    if (data.status === 429) {
        const rateErr = new Error("Province data request rate limited");
        rateErr.rateLimited = true;
        throw rateErr;
    }
    if (!data.ok) throw new Error(`Province data request failed: ${data.status} ${data.statusText}`);
    const payload = await data.json();
    // payload = { data: {visual_id: {facts, key_kind, shape, ...}}, config: {visual_id: menuCfg}, default, categories }
    visuals = {
        [province]: payload.config || {},
        "default-visuals": { [province]: payload["default"] },
    };
    menuCategories = payload.categories || [];
    activePeriods = {};   // slider positions belong to the page they were set on
    landingChoice = payload.landing || null;
    currentData = payload.data;   // generic blocks; renderers derive their data from .facts (above)
    currentGeojson = (geojson && geojson.ok) ? await geojson.json() : null;   // null = province has no map
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
  // A map click drills (or a toggle redraws) without a plotly_unhover, so drop any geo tip left up.
  const geoTip = document.getElementById("canask-geo-tip");
  if (geoTip) geoTip.style.display = "none";
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

  // Time grain: clamp to what this visual offers (a visual without time_grains -- e.g. the yearly
  // drill-down line -- resets it to year and keeps every fact, exactly as before grains existed).
  const grains = availableGrains(block);
  if (!grains.includes(activeGrain)) activeGrain = grains.includes(defaultGrain(block)) ? defaultGrain(block) : (grains.includes("year") || grains.length === 0 ? "year" : grains[0]);
  const gFacts = grains.length ? factsAtGrain(facts, activeGrain) : facts;
  opts["grain"] = activeGrain;
  // Chart type: a level-1 geo_series map can also be drawn as per-area trend lines.
  const chartTypes = availableChartTypes(block, cfg);
  if (!chartTypes.includes(activeChartType)) activeChartType = null;
  const effectiveType = activeChartType || cfg["type"];
  for (const key of ["heatmap-title", "heatmap-rates-title", "counts-title", "rates-title", "percentages-title", "table-title"]) {
    if (key in opts) opts[key] = grainTitle(opts[key], activeGrain);
  }
  if (effectiveType === "line" && cfg["type"] === "heatmap") {
    // The heatmap's table-title is a drill placeholder; the trend view's table mirrors the chart title
    // of whichever measure is drawn.
    for (const dt of cfg["data-types"] || []) opts[`table-${dt}-title`] = opts[`${dt}-title`];
    // Series key verbatim (area names are proper nouns), and the province total -- the area with no
    // polygon on the map -- starts legend-only so it doesn't dwarf the health-authority lines.
    opts["series-names-verbatim"] = true;
    const mapped = new Set(((currentGeojson && currentGeojson.features) || []).map(ft => ft.properties.ENGNAME));
    opts["legend-only"] = Array.from(new Set(gFacts.map(f => f.geo))).filter(g => g != null && !mapped.has(g));
  }
  // A map visual whose areas don't line up with its geojson says so (both the map and Trend views).
  setGeoMismatchNote(cfg["type"] === "heatmap" && currentGeojson ? geoMismatch(currentGeojson, facts, province) : null);
  // Grain / chart-type toggles live at level 1 only (masterLoop re-runs with the new state).
  const grainToggle = document.getElementById("time-grain-toggle");
  const chartToggle = document.getElementById("chart-type-toggle");
  clearToggleGroup(grainToggle);
  clearToggleGroup(chartToggle);
  clearRendererControls();
  if (level === 1) {
    buildTimeGrainToggles(grainToggle, grains, activeGrain, (g) => { activeGrain = g; masterLoop(); });
    buildChartTypeToggles(chartToggle, chartTypes, effectiveType, (t) => { activeChartType = t; masterLoop(); });
  }
  // The control bar under the chart is shown (or not) BEFORE the renderer measures #viz-card, so the
  // chart's height already accounts for it. The data-type group itself is filled by the renderer
  // (only the types with data), hence the decision is made from the declared data types here.
  const offersChoice = (level === 1 && (grains.length > 1 || chartTypes.length > 1))
    || BAR_CONTROL_TYPES.has(cfg["type"])
    || ((cfg["data-types"] || []).length > 1 && cfg["type"] !== "treemap" && cfg["type"] !== "stacked_bar" && cfg["type"] !== "expected_actual_bar");
  setVisControlsVisible(offersChoice);

  // Measure: a heatmap (both its views) and its drilled children open on the active measure when they
  // offer it; the heatmap's Measure toggle sets it and re-runs masterLoop, so it survives the grain /
  // Map-Trend toggles. Any other level-1 visual drops it (its own toggle stays renderer-local).
  if (level === 1 && cfg["type"] !== "heatmap") activeMeasure = null;
  const declaredTypes = cfg["data-types"] || [];
  const dataType = cfg["type"] === "map" ? null
    : (activeMeasure && declaredTypes.includes(activeMeasure) ? activeMeasure : declaredTypes[0]);
  if (cfg["type"] === "heatmap") {
    activeMeasure = dataType;   // pin the default too, so a drill from the map keeps what it showed
    opts["on-data-type"] = (t) => { activeMeasure = t; masterLoop(); };
  }

  // The map/heatmap renderers dereference geojson.features; if this province ships no geojson, show an
  // empty state instead of crashing (shouldn't happen for a correctly-configured province, but keeps a
  // geojson-less province's other visuals working even if a map visual is mistakenly configured).
  if ((cfg["type"] === "heatmap" || cfg["type"] === "map") && !currentGeojson) {
    const visDiv = document.getElementById("vis-div");
    if (visDiv) visDiv.innerHTML = '<p class="text-muted text-center py-5">Map data is unavailable for this province.</p>';
    syncUrl(location, year, category);
    return;
  }

  //run the creation function for the visual based on its type, deriving its data from facts
  switch (cfg["type"]) {
    case "heatmap":
      if (effectiveType === "line") {
        // Trend view of the map: one line per area (no click-drill here -- the map view drills).
        createVisualLine(province, factsToGeoSeries(gFacts), currentVisual, dataType, source, opts, null);
      } else {
        const heat = Object.fromEntries(declaredTypes.map(dt => [dt, factsToHeatmap(gFacts, kind, dt)]));
        createVisualHeatMap(province, currentVisual, currentGeojson, heat, source, opts, dataType, declaredTypes);
      }
      break;
    case "line":
      createVisualLine(province, factsToSeries(gFacts, kind, location), currentVisual, dataType, source, opts,
                       level === 1 ? factsToAdditional(gFacts) : null);
      break;
    case "bar": {
      const barData = block.shape === "regional"
        ? { counts: factsToRegional(gFacts, location, year, category) }
        : factsToSeries(gFacts, kind, location);
      createVisualBar(province, barData, currentVisual, dataType, source, opts);
      break;
    }
    case "map":
      createVisualMap(province, currentVisual, currentGeojson, opts);
      break;
    case "pie": {
      const pie = factsToPie(gFacts, location);
      createVisualPie(province, { counts: pie.counts }, source, opts, { [location]: pie.tabular }, location);
      break;
    }
    case "treemap":
      // Self-contained: the renderer derives every dropdown + the time control from block.facts
      // and block.visual_options, and re-renders itself in place (no masterLoop round-trip).
      createVisualTreemap(province, block, currentVisual, source);
      break;
    case "grouped_bar":
    case "stacked_bar":
      // Self-contained like the treemap: a substance filter + year slider, re-rendering in place.
      // chart_type picks the Plotly barmode -- grouped sub-bars side by side, or a stacked total
      // split into its sex sub-bars (total height + breakdown in one bar).
      createVisualStratifiedBar(province, block, currentVisual, source,
                                cfg["type"] === "stacked_bar" ? "stack" : "group");
      break;
    case "stacked_hbar":
      // Self-contained: horizontal 100%-stacked bars with a client-side "Date checked" filter,
      // re-rendering in place like the treemap.
      createVisualExpectedActual(province, block, currentVisual, source);
      break;
    case "found_substance_lines":
      // Self-contained (expected drug / substance group / geo pickers), but the Period toggle is
      // masterLoop's: it gets only the active grain's facts and re-renders through masterLoop.
      createVisualFoundSubstances(province, Object.assign({}, block, { facts: gFacts }), currentVisual, source, opts);
      break;
  }

  // Reflect the current visual + drill context in the address bar (single sync point for every
  // navigation path: menu picks, drills, breadcrumb, back/reset all funnel through masterLoop).
  syncUrl(location, year, category);
}

// A cell counts as numeric only if the WHOLE trimmed cell matches one of these shapes -- a bare
// number/percent, or the "82 (96.5%)" composite the expected-vs-actual table uses. Anchored so a
// label that merely starts with a digit (e.g. the drug name "4-MMC") is never misread as numeric.
const CANASK_NUMERIC_CELL_RE = /^-?[\d,]+(\.\d+)?%?( \(-?[\d,]+(\.\d+)?%\))?$/;
function canaskCellNumericValue(text) {
  const t = (text || "").trim();
  if (t === "" || !CANASK_NUMERIC_CELL_RE.test(t)) return null;
  const n = parseFloat(t.replace(/,/g, ""));
  return isNaN(n) ? null : n;
}

// Wires click-to-sort onto every #data-table built by the createVisual* renderers below, mirroring
// the .sortable/.sort-icon convention already used on the User/Group management tables (see
// master_sheet.css "Invite management sortable headers"). Most of these tables build their header
// as a plain first row (no <thead>) via `table.insertRow(-1)`, so promote it into a real <thead>
// first -- that's what keeps the header out of the sort and (for tables with a <tfoot> Total row,
// e.g. the drug-checking visuals) keeps the total pinned at the bottom regardless of sort order.
// Call this once, after every row has been inserted, right before the table replaces #data-table.
function makeSortableTable(table) {
  if (!table.tHead) {
    const firstRow = table.rows[0];
    if (!firstRow) return;
    table.createTHead().appendChild(firstRow);
  }
  const headerRow = table.tHead.rows[0];
  const body = table.tBodies[0];
  if (!body || !body.rows.length) return;

  // Snapshot the as-rendered order (whatever the renderer's own logic produced -- chart-matching,
  // largest-first, whatever) so "Reset sort" can put rows back exactly where they started, not just
  // re-sort by some default column. Lives in the table's own <caption> so every renderer gets the
  // control for free without threading tableDiv through this function or touching #data-table markup.
  const originalOrder = Array.from(body.rows);
  const caption = table.createCaption();
  caption.style.captionSide = "top";
  caption.style.textAlign = "right";
  const resetBtn = document.createElement("button");
  resetBtn.type = "button";
  resetBtn.className = "btn btn-link btn-sm p-0 text-decoration-none";
  resetBtn.innerHTML = '<i class="bi bi-arrow-counterclockwise"></i> Reset sort';
  resetBtn.disabled = true;
  caption.appendChild(resetBtn);

  const clearIcons = () => Array.from(headerRow.cells).forEach(h => {
    h._ascending = true;
    const i = h.querySelector(".sort-icon");
    if (i) i.textContent = "↕";
  });
  resetBtn.addEventListener("click", () => {
    originalOrder.forEach(row => body.appendChild(row));
    clearIcons();
    resetBtn.disabled = true;
  });

  Array.from(headerRow.cells).forEach((th, colIndex) => {
    if (!th.textContent.trim()) return;   // a blank header cell has nothing meaningful to sort by
    th.classList.add("sortable");
    th.style.cursor = "pointer";
    th.appendChild(document.createTextNode(" "));
    const icon = document.createElement("span");
    icon.className = "sort-icon";
    icon.textContent = "↕";
    th.appendChild(icon);

    const sort = () => {
      const ascending = th._ascending !== false;
      Array.from(headerRow.cells).forEach(h => {
        if (h !== th) { h._ascending = true; const i = h.querySelector(".sort-icon"); if (i) i.textContent = "↕"; }
      });
      const numeric = Array.from(body.rows).some(r => canaskCellNumericValue(r.cells[colIndex].innerText) !== null);
      const compare = (a, b) => {
        const aCell = a.cells[colIndex].innerText, bCell = b.cells[colIndex].innerText;
        let cmp;
        if (numeric) {
          const an = canaskCellNumericValue(aCell), bn = canaskCellNumericValue(bCell);
          cmp = an === null ? (bn === null ? 0 : 1) : (bn === null ? -1 : an - bn);   // blanks/text sort last
        } else {
          cmp = aCell.trim().toLowerCase().localeCompare(bCell.trim().toLowerCase());
        }
        return ascending ? cmp : -cmp;
      };
      // Grouped tables (rows tagged data-group-head / data-group, e.g. the treemap's category
      // headings) sort the headings among themselves and each heading's children beneath it, so a
      // child never floats away from its group.
      const heads = Array.from(body.rows).filter(r => r.dataset.groupHead);
      let rows;
      if (heads.length) {
        const children = {};
        Array.from(body.rows).forEach(r => {
          if (r.dataset.group) (children[r.dataset.group] = children[r.dataset.group] || []).push(r);
        });
        heads.sort(compare);
        rows = [];
        heads.forEach(h => { rows.push(h); rows.push(...(children[h.dataset.groupHead] || []).sort(compare)); });
      } else {
        rows = Array.from(body.rows).sort(compare);
      }
      th._ascending = !ascending;
      icon.textContent = ascending ? "↓" : "↑";
      rows.forEach(row => body.appendChild(row));
      resetBtn.disabled = false;
    };
    th.addEventListener("click", sort);
  });
}

// Function to generate heatmaps. heatByType = {dataType: factsToHeatmap(...)}; the Measure toggle offers
// the declared types that have any value at this grain, and hands the choice to
// mapOptions["on-data-type"] (masterLoop, so the grain / chart toggles keep it).
async function createVisualHeatMap(province, visualToGen, geojson, heatByType, mapSource, mapOptions, dataType, dataTypes){
  // Setup the map container and other elements
  let dataTypeToggle = document.getElementById("data-type-toggle");
  let visDiv = document.getElementById("vis-div");
  let aboutDataDiv = document.getElementById("about-data");
  let tableDiv = document.getElementById("data-table");
  let table = document.createElement("table");
  let tableTitle = document.getElementById("table-title");
  let dataSlider = [];
  let steps = [];
  
  // remove the active class from other visuals and add it to the current visual
  setActiveVisual(province, visualToGen);

  // Every area shares one period axis (factsToHeatmap); no value in any declared measure -> empty state.
  const hasValues = (byGeo) => Object.values(byGeo || {}).some(o => o.y.some(v => v != null));
  const available = (dataTypes || []).filter(dt => hasValues(heatByType[dt]));
  if (available.length === 0) {
    renderEmptyVisual("No data is available for this visual.");
    return;
  }
  dataType = available.includes(dataType) ? dataType : available[0];
  // The measure drawn is the measure the grain / Map-Trend toggles, a drill and ?measure= carry
  // forward -- not the one asked for when this grain lacks it.
  activeMeasure = dataType;
  const mapData = heatByType[dataType];
  const periods = (Object.values(mapData)[0] || {}).x || [];
  // Where the slider starts: the linked / last-used period for THIS map if it exists at this
  // grain, else the latest. Anything else recorded for this map is stale (e.g. the grain changed).
  let startIndex = periods.length - 1;
  if (activePeriods[visualToGen] != null) {
    const wanted = periods.indexOf(activePeriods[visualToGen]);
    if (wanted >= 0 && wanted < periods.length - 1) startIndex = wanted;
    else delete activePeriods[visualToGen];
  }
  const measure = HEATMAP_MEASURE_LABELS[dataType] || HEATMAP_MEASURE_LABELS.counts;
  const mapTitle = dataType === "counts" ? mapOptions["heatmap-title"] : mapOptions[`heatmap-${dataType}-title`];

  // Create the map using the provided data and options and push them into the slider
  for (let year_index = 0; year_index < periods.length; year_index++) {
    let values = {};
    for (let loc_index = 0; loc_index < geojson.features.length; loc_index++) {
      const name = geojson.features[loc_index].properties.ENGNAME;
      // An area with no facts at this grain is drawn blank rather than crashing the map.
      values[name] = mapData[name] ? mapData[name]["y"][year_index] : null;
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
        title: measure.colorbar,
        thickness: 15,
        // On a phone the chart is short and the title sits snug above the map, so a full-height
        // colorbar would put its own label up against the chart title: centre a shorter bar.
        ...(window.innerWidth > 768 ? {} : { len: 0.6, y: 0.5, yanchor: "middle", thickness: 12, nticks: 4 }),
      },
      visible: year_index === startIndex,   // matches the slider's start (the latest unless linked)
      hoverinfo: "none",   // label drawn by attachGeoHoverTip (events still fire)
    };
    dataSlider.push(chartData);
  }

  // Create the slider steps. Every step carries its full period: Plotly reads the current-value
  // readout from the active step's label, and already thins the tick labels to what fits (labelStride),
  // so a long month axis stays legible without blanking any label.
  for (let i = 0; i < dataSlider.length; i++) {
    let step = {
      method: "restyle",
      args: ["visible", Array(dataSlider.length).fill(false)],
      label: periods[i],
    };
    step.args[1][i] = true;
    steps.push(step);
  }

  // Create the layout for the map from the mapOptions object (the title is the HTML heading above)
  setVisualTitle(mapTitle);
  let layout = {
    geo: {
      ...provinceGeoLayout(geojson),
      showlakes: false,
      fitbounds: "locations",
      showcoastlines: false,
    },
    hoverlabel: {
      namelength: -1,
    },
    sliders: [
      {
        active: startIndex,
        steps: steps,
        x: 0.5,
        xanchor: "center",
        len: 0.95,
        y: 0,
        yanchor: "top",
        pad: { t: 0, b: 10 },
        currentvalue: {
          visible: true,
          prefix: `${GRAIN_LABELS[mapOptions["grain"]] || "Year"}: `,
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
    height: chartAreaHeight(),
    // t = the mode bar's band, so its buttons never sit on the colorbar's label
    margin: window.innerWidth > 768 ? { l: 0, t: 44 } : { b: 20, r: 0, l: 20, t: 44, autoexpand: true },
  };

  // Insert the visual and define the callback for click events
  visDiv.innerHTML = ""; // Clear the previous content
  Plotly.purge(visDiv); // Clear any previous Plotly plots
  let vis = Plotly.react(
    visDiv,
    dataSlider,
    themeChartLayout(layout),
    chartConfig({ responsive: false }, visualExportInfo)
  ).then(() => {
    // The tip's measure label is spelled out rather than read from the colorbar title: Plotly
    // rewrites a string colorbar title into {text} on gd.data.
    attachGeoHoverTip(visDiv, (point) => (point.location == null ? null
      : { name: point.location, value: `${measure.hover}: ${point.z}` }));
    // Moving the slider records the period in the address (replaceState: not a step to go Back
    // through), so a copied link opens on the same map. The latest period is the default and is
    // left out. visDiv was purged before this render, so the listener does not stack.
    visDiv.on("plotly_sliderchange", function (event) {
      const index = event.slider.active;
      if (index === steps.length - 1) delete activePeriods[visualToGen];
      else activePeriods[visualToGen] = steps[index].label;
      if (!isReplaying) history.replaceState(history.state, "", buildVisualUrl(null, null, null));
    });
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
  let cols = [(mapOptions["table-geo-header"] || "Health Authority")].concat(periods);
  let tr = table.insertRow(-1);
  cols.forEach((headerText) => {
    let th = document.createElement("th"); // Create a new header cell
    th.innerText = headerText; // Set the text of the header cell
    tr.appendChild(th); // Add the header cell to the row
  });
  tableDiv.innerHTML = "";
  tableDiv.appendChild(table);
  tableTitle.innerText = mapTitle;
  for (const [key, value] of Object.entries(mapData)) {
    if (key != "data last updated") {
      let tr = table.insertRow(-1);
      tr.setAttribute("class", "align-middle");
      let tabCell = tr.insertCell(-1);
      tabCell.innerText = key;
      value["y"].forEach((element) => {
        let tabCell = tr.insertCell(-1);
        tabCell.innerText = formatTableValue(element);
      });
    }
  }
  makeSortableTable(table);

  buildDataTypeToggles(dataTypeToggle, available, dataType, (type) => mapOptions["on-data-type"](type));
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
    hoverinfo: "none",   // label drawn by attachGeoHoverTip (events still fire)
  };

  // Create the layout for the map from the mapOptions object (the title is the HTML heading above)
  setVisualTitle(mapOptions["title"]);
  let layout = {
    geo: {
      ...provinceGeoLayout(geojson),
      fitbounds: "locations",
      showcoastlines: false,
      showlakes: false,
    },
    width: $("#viz-card").width(),
    height: chartAreaHeight(),
    margin: { t: 44 },   // the mode bar's band
    hoverlabel: {
      namelength: -1,
    },
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
    chartConfig({ responsive: false }, visualExportInfo)
  ).then(() => {
    attachGeoHoverTip(visDiv, (point) => (point.location == null ? null : { name: point.location, value: null }));
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

  // Restrict the data-type toggle to the types that actually carry visible data for this visual.
  // A visual with no reportable data in ANY type (e.g. a territory whose every series is suppressed
  // or not reported) is still in the menu, so render an empty state instead of dereferencing
  // undefined data below.
  if (dataTypes == null) {
    dataTypes = visuals[province][currentVisual]["data-types"];
  }
  const available = (dataTypes || []).filter(dt => dataTypeHasData(lineData[dt]));
  if (available.length === 0) {
    renderEmptyVisual("No data is available for this visual.");
    return;
  }
  dataType = (dataType !== null && available.includes(dataType)) ? dataType : available[0];
  traceData = lineData[dataType];
  // The title is the HTML heading above the figure; set it (for the data type actually drawn) before
  // the figure is sized so chartAreaHeight() can allow for it.
  setVisualTitle(visualOptions[`${dataType}-title`].replace("replace_with_health_authority", visualOptions["location"] || "").replace("replace_with_category", visualOptions["category"] || ""));

  // check to see if we have a total
  totalPresent = !!("total" in traceData);

  // Track series index so each line gets a distinct dash + marker shape, making
  // series distinguishable without relying on color alone (WCAG).
  let seriesIndex = 0;
  const colorway = canaskColorway();
  for (const [key, value] of Object.entries(traceData)) {
    if (key === "x") continue;
    // Hide a series with no reported non-zero value (all 0 / suppressed / missing).
    if (!hasReportedNonZero(value)) continue;

    const color = colorway[seriesIndex % colorway.length];
    // Solid trace: reported points only. Suppressed ("Suppr.") and not-reported (null) become gaps
    // (connectgaps:false), so a genuine 0 plots at zero but a hidden/absent value never draws a fake 0.
    let trace = {
      x: traceData["x"],
      y: value.map(v => (typeof v === "number" ? v : null)),
      name: visualOptions["series-names-verbatim"] ? key : key.toSentenceCase(),
      legendgroup: key,
      type: "scatter",
      mode: "lines+markers",
      connectgaps: false,
      stackgroup: totalPresent && key.toSentenceCase() != "Total" ? "one" : undefined, // fill if total is present and not the total line
      line: {
        width: 2,
        smoothing: 1,
        color: color,
      },
      // Solid lines, but a distinct marker shape per series gives a quiet
      // non-color cue (no shimmering dash patterns).
      marker: {
        symbol: CANASK_MARKER_SYMBOLS[seriesIndex % CANASK_MARKER_SYMBOLS.length],
        size: 6,
        color: color,
      },
    };
    const legendOnly = (visualOptions["legend-only"] || []).includes(key);
    if (legendOnly) trace.visible = "legendonly";
    traces.push(trace);

    // Dashed "bridge" across interior gaps that are ENTIRELY suppressed: connect the two surrounding
    // reported points with a dashed segment to convey the trend through hidden small counts. A
    // not-reported gap (null) or a leading/trailing gap is left as a real break (no bridge).
    const reportedIdx = [];
    value.forEach((v, i) => { if (typeof v === "number") reportedIdx.push(i); });
    const bridgeX = [], bridgeY = [];
    for (let k = 0; k + 1 < reportedIdx.length; k++) {
      const a = reportedIdx[k], b = reportedIdx[k + 1];
      if (b > a + 1 && value.slice(a + 1, b).every(v => v === SUPPRESSED)) {
        bridgeX.push(traceData["x"][a], traceData["x"][b], null);
        bridgeY.push(value[a], value[b], null);
      }
    }
    if (bridgeX.length) {
      traces.push({
        x: bridgeX,
        y: bridgeY,
        // Same legendgroup as the parent series so toggling it in the legend hides/shows the bridge too.
        legendgroup: key,
        type: "scatter",
        mode: "lines",
        connectgaps: false,
        line: { width: 2, dash: "dash", color: color },
        hoverinfo: "skip",
        showlegend: false,
        ...(legendOnly ? { visible: "legendonly" } : {}),
      });
    }
    seriesIndex++;
  }

  // Every series was hidden (no reported data for this view) -> empty state instead of a broken plot.
  if (traces.length === 0) {
    visDiv.innerHTML = '<p class="text-muted text-center py-5">No data is available for this view.</p>';
    if (tableDiv) tableDiv.innerHTML = "";
    if (aboutDataDiv) aboutDataDiv.innerHTML = ""; // drop the about-data loading skeleton too
    return;
  }

  const nonYearGrain = !!visualOptions["grain"] && visualOptions["grain"] !== "year";
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
      // Finer grains ("2025-Q2" / "2025-04") are categories: no yearly dtick, and no mobile clipmax
      // (Number() of a non-year key is NaN).
      xaxis: {
        fixedrange: false,
        autorange: true,
        autorangeoptions:
          window.innerWidth > 768 || nonYearGrain
            ? {}
            : {
                clipmax: Number(traces[0]["x"][0]) + 2,
              },
        ...grainXAxis(visualOptions["grain"], traces[0] && traces[0].x),
        automargin: true,   // rotated/dense tick labels push the axis title down instead of under them
        title: {
          text: nonYearGrain ? GRAIN_LABELS[visualOptions["grain"]] : "Year",
          standoff: 12,
        },
        constrain: "domain",
      },
      hovermode: "x unified",
      autosize: false,
      width: $("#viz-card").width(),
      height: window.innerWidth > 768 ? chartAreaHeight() : "auto",
      legend: responsiveLegend(),
      margin: responsiveMargin(),
    }),
    chartConfig({}, visualExportInfo)
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
  tableTitle.innerText = (visualOptions[`table-${dataType}-title`] || visualOptions["table-title"])
    .replace("replace_with_health_authority", location);
  // Only render the data type currently shown in the visual (counts / rates /
  // percentages). The whole function re-runs when the toggle changes, so the
  // table stays in sync with the chart.
  for (const [subKey, subValue] of Object.entries(lineData[dataType])) {
    // Mirror the chart: skip "x" and any series hidden as all-zero/suppressed/missing.
    if (subKey != "x" && hasReportedNonZero(subValue)) {
      let tr = table.insertRow(-1);
      tr.setAttribute("class", "align-middle");
      let tabCell = tr.insertCell(-1);
      tabCell.innerText = visualOptions[`table-${dataType}-row`].replace("replace_me", subKey.toTitleCase());
      subValue.forEach((element) => {
        let tabCell = tr.insertCell(-1);
        tabCell.innerText = formatTableValue(element);
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
      value.forEach((element) => {
        let tabCell = tr.insertCell(-1);
        tabCell.innerText = formatTableValue(element);
      });
    }
  }
  makeSortableTable(table);

  // Toggle only between the data types that actually have data (computed above). A heatmap's Trend view
  // routes the choice through masterLoop instead (on-data-type), so its measure outlives this render.
  buildDataTypeToggles(dataTypeToggle, available, dataType, (type) => visualOptions["on-data-type"]
    ? visualOptions["on-data-type"](type)
    : createVisualLine(province, lineData, currentVisual, type, lineSource, visualOptions, additionalRows, dataTypes));

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

  // Restrict to the data types that actually carry visible data; render an empty state if a visual
  // (still listed in the menu) has none -- avoids dereferencing undefined data below.
  if (dataTypes == null) {
    dataTypes = visuals[province][currentVisual]["data-types"];
  }
  const available = (dataTypes || []).filter(dt => dataTypeHasData(barData[dt]));
  if (available.length === 0) {
    renderEmptyVisual("No data is available for this visual.");
    return;
  }
  dataType = (dataType !== null && available.includes(dataType)) ? dataType : available[0];
  traceData = barData[dataType];
  // The title is the HTML heading above the figure; set it (for the data type actually drawn) before
  // the figure is sized so chartAreaHeight() can allow for it.
  setVisualTitle(visualOptions[`${dataType}-title`].replace("replace_with_health_authority", visualOptions["location"] || "").replace("replace_with_category", visualOptions["category"] || ""));

  // Create a trace for each y value in the barData object entry. Each series
  // gets a distinct pattern fill + subtle theme-aware outline so bars are
  // distinguishable without relying on color alone (WCAG).
  let seriesIndex = 0;
  const colorway = canaskColorway();
  for (const [key, value] of Object.entries(traceData)) {
    if (key === "x") continue;
    // Hide a series with no reported non-zero value (all 0 / suppressed / missing).
    if (!hasReportedNonZero(value)) continue;

    const color = colorway[seriesIndex % colorway.length];
    let trace = {
      x: traceData["x"],
      // Suppressed ("Suppr.") and not-reported (null) draw no bar; a genuine 0 is a zero-height bar.
      y: value.map(v => (typeof v === "number" ? v : null)),
      hoverinfo: visualOptions["hover-info"],
      name: key.toSentenceCase(),
      type: "bar",
      // Clean solid bars with a subtle outline (no eye-straining hatch fill);
      // the data table below the chart is the non-color alternative.
      marker: {
        color: color,
        line: {
          width: 1,
          color: canaskMarkerLineColor(),
        },
      },
    };
    traces.push(trace);
    seriesIndex++;
  }

  // Every series was hidden (no reported data for this view) -> empty state instead of a broken plot.
  if (traces.length === 0) {
    visDiv.innerHTML = '<p class="text-muted text-center py-5">No data is available for this view.</p>';
    if (tableDiv) tableDiv.innerHTML = "";
    if (aboutDataDiv) aboutDataDiv.innerHTML = ""; // drop the about-data loading skeleton too
    return;
  }
  const nonYearGrain = !!visualOptions["grain"] && visualOptions["grain"] !== "year";
  visDiv.innerHTML = ""; // Clear the previous content (incl. the loading skeleton)
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
        ...grainXAxis(visualOptions["grain"], barData[dataType] && barData[dataType].x),
        automargin: true,   // rotated/dense tick labels push the axis title down instead of under them
        title: {
          text: nonYearGrain ? GRAIN_LABELS[visualOptions["grain"]] : "Year",
          standoff: 12,
        },
        constrain: "domain",
      },
      hovermode: visualOptions["hover-type"],
      autosize: false,
      width: $("#viz-card").width(),
      height: chartAreaHeight(),
      legend: responsiveLegend(),
      margin: responsiveMargin(),
    }),
    chartConfig({}, visualExportInfo)
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
    // Mirror the chart: skip "x" and any series hidden as all-zero/suppressed/missing.
    if (subKey != "x" && hasReportedNonZero(subValue)) {
      let tr = table.insertRow(-1);
      tr.setAttribute("class", "align-middle");
      let tabCell = tr.insertCell(-1);
      tabCell.innerText = visualOptions[`table-${dataType}-row`].replace("replace_me", subKey.toTitleCase());
      subValue.forEach((element) => {
        let tabCell = tr.insertCell(-1);
        tabCell.innerText = formatTableValue(element);
      });
    }
  }
  makeSortableTable(table);

  // Toggle only between the data types that actually have data (computed above).
  buildDataTypeToggles(dataTypeToggle, available, dataType, (data) =>
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
  setVisualTitle(visualOptions["visual-title"].replace("replace_with_health_authority", visualOptions["location"].toTitleCase()));
  let layout = {
    width: $("#viz-card").width(),
    height: chartAreaHeight(),
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
    chartConfig({ responsive: false }, visualExportInfo)
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
  makeSortableTable(table);
}

// Generic config-driven treemap renderer. Reads block.visual_options for its geo levels,
// hierarchy axes, dimension filters and time control, builds those controls into #vis-stratifiers,
// and re-renders itself in place when any control changes. No literal Site/HA/Category here -- the
// same function powers any category_treemap visual from any data source.
async function createVisualTreemap(province, block, currentVisual, source) {
  setVisualTitle(null);   // these visuals draw their own header inside #vis-div
  const cfg = (block && block.visual_options) || {};
  const facts = (block && block.facts) || [];
  const visDiv = document.getElementById("vis-div");
  const aboutDataDiv = document.getElementById("about-data");
  const tableDiv = document.getElementById("data-table");
  const tableTitle = document.getElementById("table-title");
  const controls = document.getElementById("vis-stratifiers");

  setActiveVisual(province, currentVisual);

  // Layout: title, then the chart, then a horizontal filter bar beneath it -- stacked vertically
  // inside #vis-div (not the absolute overlay, so nothing covers the treemap). The chart draws into
  // its own child so a filter change re-renders only the tiles, leaving the controls intact; and
  // because the whole stack lives in #vis-div, any later visual that overwrites #vis-div tears it down
  // with it (auto-cleanup).
  visDiv.className = "treemap-layout";
  visDiv.innerHTML = "";
  const headerEl = document.createElement("div");
  headerEl.className = "treemap-header";
  const titleEl = document.createElement("div");
  titleEl.className = "treemap-title";
  titleEl.innerText = cfg.title || "";
  headerEl.appendChild(titleEl);
  const chartDiv = document.createElement("div");
  chartDiv.className = "treemap-chart";
  const footerEl = document.createElement("div");
  footerEl.className = "treemap-footer";
  const controlHost = document.createElement("div");
  controlHost.className = "treemap-controls-row";
  footerEl.appendChild(controlHost);
  visDiv.appendChild(headerEl);
  visDiv.appendChild(chartDiv);
  visDiv.appendChild(footerEl);

  const geoLevels = cfg.geo_levels || [];
  const filters = cfg.filters || [];
  const hierarchy = (cfg.hierarchy && cfg.hierarchy.length) ? cfg.hierarchy
    : [{ axis: "dimension", label: "Category" }, { axis: "dimension2", label: "Value" }];

  // Current selection (null = "All"); the slider drives unit/bucket.
  const sel = { geo: geoLevels.map(function () { return null; }), filters: {}, unit: "all", bucket: null };
  filters.forEach(function (flt) { sel.filters[flt.axis] = null; });

  // Reset-filters button, kept at renderer scope so render() can refresh its enabled state on every
  // filter change (filter onchange handlers call render() without rebuilding the controls).
  let resetBtn = null;

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
    controlHost.appendChild(wrap);
    return select;
  }

  // Distinct values for geo level i, cascaded by the levels chosen above it.
  function geoOptions(i) {
    return distinct(function (f) { return geoLevel(f, i); }, function (f) {
      for (let k = 0; k < i; k++) { if (sel.geo[k] != null && geoLevel(f, k) !== sel.geo[k]) return false; }
      return true;
    });
  }

  // ---- URL <-> selection (deep-linkable filters) ----
  // Values travel as slugs (the same slugify used for the path), so the query string stays readable and
  // round-trips through reverse-matching against the live facts (matchSlug).

  // Serialize the current selection into a query string: geo levels -> t.g<i>, filters -> t.f<i>
  // (index into cfg.filters), time -> t.u (unit) + t.b (bucket). Defaults ("All"/all-time) are omitted
  // so an unfiltered treemap keeps a clean URL.
  function treemapSelToParams() {
    const p = new URLSearchParams();
    geoLevels.forEach(function (lvl, i) { if (sel.geo[i] != null) p.set("t.g" + i, slugify(sel.geo[i])); });
    filters.forEach(function (flt, i) {
      if (sel.filters[flt.axis] != null) p.set("t.f" + i, slugify(sel.filters[flt.axis]));
    });
    if (sel.unit !== "all") {
      p.set("t.u", sel.unit);
      if (sel.bucket != null) p.set("t.b", slugify(sel.bucket));
    }
    return p.toString();
  }

  // Seed `sel` from the current URL (deep link / Back-Forward). Geo levels are reverse-matched top-down
  // so each level's cascade sees the parent already chosen; unknown/stale slugs are ignored (stay "All").
  function seedSelFromUrl() {
    const params = new URLSearchParams(window.location.search || "");
    geoLevels.forEach(function (lvl, i) {
      const raw = params.get("t.g" + i);
      if (!raw) return;
      const m = matchSlug(geoOptions(i), raw);
      if (m != null) sel.geo[i] = m;
    });
    filters.forEach(function (flt, i) {
      const raw = params.get("t.f" + i);
      if (!raw) return;
      const m = matchSlug(distinct(function (f) { return axisValue(f, flt.axis); }), raw);
      if (m != null) sel.filters[flt.axis] = m;
    });
    const u = params.get("t.u");
    if (u === "year" || u === "month" || u === "seasonal") {
      sel.unit = u;
      const b = params.get("t.b");
      if (b) {
        const found = treemapTimeBuckets(facts, u).find(function (bk) { return slugify(bk.value) === b; });
        if (found) sel.bucket = found.value;
      }
    }
  }

  // Push the current selection onto the address bar through the shared syncUrl (which handles the
  // first-render replaceState + isReplaying guard). buildVisualUrl reads activeRendererParams.
  function syncTreemapUrl() {
    activeRendererParams = treemapSelToParams();
    syncUrl(null, null, null);
  }

  // Clear every geo/filter selection + time control back to its default ("All" / all-time), rebuild the
  // controls so their displayed values match, and drop the filter params from the URL.
  function resetFilters() {
    sel.geo = geoLevels.map(function () { return null; });
    filters.forEach(function (flt) { sel.filters[flt.axis] = null; });
    sel.unit = "all";
    sel.bucket = null;
    buildControls();
    render();
    syncTreemapUrl();
  }

  // True when any geo/filter/time control is away from its default -- drives the reset button's
  // enabled state so it reads as a no-op when there's nothing to clear.
  function filtersActive() {
    return sel.geo.some(function (g) { return g != null; })
      || filters.some(function (flt) { return sel.filters[flt.axis] != null; })
      || sel.unit !== "all";
  }

  function buildControls() {
    controlHost.innerHTML = "";
    const geoSelects = [];
    geoLevels.forEach(function (levelLabel, i) {
      const select = makeControl(levelLabel);
      // Reflect the live selection (seeded from the URL on a deep link), not always "All".
      setSelectOptions(select, geoOptions(i), sel.geo[i]);
      select.onchange = function () {
        sel.geo[i] = select.value === "__all__" ? null : select.value;
        // Re-cascade: clear and repopulate every lower level for the new parent choice.
        for (let j = i + 1; j < geoLevels.length; j++) {
          sel.geo[j] = null;
          setSelectOptions(geoSelects[j], geoOptions(j), null);
        }
        render();
        syncTreemapUrl();
      };
      geoSelects.push(select);
    });

    filters.forEach(function (flt) {
      const select = makeControl(flt.label || flt.axis);
      setSelectOptions(select, distinct(function (f) { return axisValue(f, flt.axis); }), sel.filters[flt.axis]);
      select.onchange = function () {
        sel.filters[flt.axis] = select.value === "__all__" ? null : select.value;
        render();
        syncTreemapUrl();
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
      unitSelect.value = sel.unit;   // reflect a URL-seeded unit, not always "All time"
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
          // Honor a URL-seeded bucket if it still exists; otherwise default to the most recent.
          let idx = buckets.length - 1;
          if (sel.bucket != null) {
            const found = buckets.findIndex(function (b) { return b.value === sel.bucket; });
            if (found >= 0) idx = found;
          }
          slider.value = idx;
          applyBucket();
        }
      }
      // Switching unit resets to the latest bucket (clear the seeded one first so rebuild picks default).
      unitSelect.onchange = function () { sel.unit = unitSelect.value; sel.bucket = null; rebuildBuckets(); render(); syncTreemapUrl(); };
      // Live-render while dragging, but only push history once on release (oninput would spam the stack).
      slider.oninput = function () { applyBucket(); render(); };
      slider.onchange = function () { syncTreemapUrl(); };
      wrap.appendChild(lab); wrap.appendChild(unitSelect); wrap.appendChild(sliderWrap);
      controlHost.appendChild(wrap);
      rebuildBuckets();
    }

    // Reset-filters control: sits at the end of the row, aligned to the bottom of the dropdowns, and
    // clears every selection back to "All". Disabled when nothing is filtered so it can't read as live.
    const resetWrap = document.createElement("div");
    resetWrap.className = "treemap-control treemap-reset text-start d-flex align-items-end";
    resetBtn = document.createElement("button");
    resetBtn.type = "button";
    resetBtn.className = "btn btn-outline-secondary btn-sm";
    resetBtn.innerHTML = '<i class="bi bi-arrow-counterclockwise"></i> Reset filters';
    resetBtn.disabled = !filtersActive();
    resetBtn.onclick = resetFilters;
    resetWrap.appendChild(resetBtn);
    controlHost.appendChild(resetWrap);
  }

  // Share of the shown total as a percentage at two significant digits, matching the tiles'
  // "%{percentRoot:.2p}" (Number() strips toPrecision's exponent form for 100).
  function sigPercent(fraction) {
    return Number((fraction * 100).toPrecision(2)) + "%";
  }

  function renderTable(data) {
    aboutDataDiv.innerHTML = buildAboutDataHTML(source);
    tableTitle.innerText = (cfg.title || "Checked Samples") + ": totals for the current view";
    const table = document.createElement("table");
    table.setAttribute("class", "mb-0 table table-striped table-bordered table-hover");
    // One label column: category heading rows with the drugs indented beneath them (see below)
    // instead of a mostly-empty Category column repeated down every row.
    const grouped = hierarchy.length > 1;
    const labelHeader = hierarchy.map(function (h) { return h.label || h.axis; }).join(" / ");
    const headerCols = [labelHeader, "Count", "% of area shown"];
    const headRow = table.insertRow(-1);
    headerCols.forEach(function (h) {
      const th = document.createElement("th"); th.innerText = h; headRow.appendChild(th);
    });
    // Group the leaves under a heading row per top-level node (the category) carrying that node's
    // own count / share, so "all stimulants" and "speed" each get a row; a one-level hierarchy
    // stays flat. The sorter keeps children under their heading (see makeSortableTable).
    const groups = new Map();   // first path value -> {value, rows} in largest-first order
    data.rows.forEach(function (row) {
      const key = grouped ? row.path[0] : "";
      const g = groups.get(key) || { value: 0, rows: [] };
      g.value += row.value;
      g.rows.push(row);
      groups.set(key, g);
    });
    const order = Array.from(groups.entries()).sort(function (a, b) { return b[1].value - a[1].value; });
    const pct = function (value) { return data.total ? sigPercent(value / data.total) : "0%"; };
    let groupIndex = 0;
    order.forEach(function (entry) {
      const key = entry[0], g = entry[1];
      const gid = "g" + (groupIndex++);
      if (grouped) {
        const head = table.insertRow(-1);
        head.className = "align-middle vis-group-row";
        head.dataset.groupHead = gid;
        const label = head.insertCell(-1);
        const chevron = document.createElement("i");
        chevron.className = "bi bi-chevron-down vis-group-chevron";
        label.appendChild(chevron);
        label.appendChild(document.createTextNode(key));
        const n = document.createElement("span");
        n.className = "vis-group-n";
        n.innerText = g.rows.length + (g.rows.length === 1 ? " drug" : " drugs");
        label.appendChild(n);
        head.insertCell(-1).innerText = g.value;
        head.insertCell(-1).innerText = pct(g.value);
        // The heading toggles its members. State lives on the rows themselves (a class), so the
        // sorter's re-appending keeps collapsed groups collapsed.
        head.setAttribute("role", "button");
        head.tabIndex = 0;
        head.setAttribute("aria-expanded", "true");
        const toggle = function () { setGroupCollapsed(table, head, head.getAttribute("aria-expanded") === "true"); };
        head.addEventListener("click", toggle);
        head.addEventListener("keydown", function (ev) {
          if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); }
        });
      }
      g.rows.forEach(function (row) {
        const tr = table.insertRow(-1);
        tr.className = grouped ? "align-middle vis-group-child" : "align-middle";
        if (grouped) tr.dataset.group = gid;
        tr.insertCell(-1).innerText = (grouped ? row.path.slice(1) : row.path).join(" \u203a ");
        tr.insertCell(-1).innerText = row.value;
        tr.insertCell(-1).innerText = pct(row.value);
      });
    });
    // Total row so the sample count backing the current (filtered) view is visible at a glance,
    // without having to sum the Count column by hand.
    const foot = table.createTFoot();
    const totalRow = foot.insertRow(-1);
    totalRow.className = "fw-bold table-group-divider";
    const totalLabel = totalRow.insertCell(-1);
    totalLabel.innerText = "Total";
    totalRow.insertCell(-1).innerText = data.total;
    totalRow.insertCell(-1).innerText = "100%";
    makeSortableTable(table);
    if (grouped && table.caption) {
      // Collapse all / Expand all beside the sorter's Reset sort control.
      const allBtn = document.createElement("button");
      allBtn.type = "button";
      allBtn.className = "btn btn-link btn-sm p-0 text-decoration-none me-3";
      const syncAll = function () {
        const heads = Array.from(table.querySelectorAll("tr[data-group-head]"));
        const anyOpen = heads.some(function (h) { return h.getAttribute("aria-expanded") === "true"; });
        allBtn.innerHTML = anyOpen ? '<i class="bi bi-chevron-contract"></i> Collapse all'
                                   : '<i class="bi bi-chevron-expand"></i> Expand all';
        allBtn.onclick = function () {
          heads.forEach(function (h) { setGroupCollapsed(table, h, anyOpen); });
        };
      };
      table.addEventListener("vis-group-toggle", syncAll);
      syncAll();
      table.caption.insertBefore(allBtn, table.caption.firstChild);
    }
    tableDiv.innerHTML = "";
    tableDiv.appendChild(table);
  }

  function setGroupCollapsed(table, head, collapsed) {
    head.setAttribute("aria-expanded", collapsed ? "false" : "true");
    head.classList.toggle("vis-group-collapsed", collapsed);
    table.querySelectorAll('tr[data-group="' + head.dataset.groupHead + '"]').forEach(function (r) {
      r.classList.toggle("d-none", collapsed);
    });
    table.dispatchEvent(new CustomEvent("vis-group-toggle"));
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
      // d3 ".2p": fraction -> percent at two significant digits ("12%", "4.6%", "0.45%"); the bare
      // %{percentRoot} renders the raw fraction ("0.12").
      texttemplate: "<b>%{label}</b><br>%{percentRoot:.2p} (%{value})",
      hovertemplate: "<b>%{label}</b><br>%{value} samples<br>%{percentRoot:.2p} of area shown<extra></extra>",
      pathbar: { visible: true },
    };
    // Title (header) and filters (footer) are HTML stacked around the chart; size the treemap to the
    // space left between them so the whole stack fits the card without the tiles overlapping either.
    const cardW = $("#viz-card").width();
    const cardH = $("#viz-card").height();
    const layout = {
      width: cardW,
      height: Math.max(220, cardH - headerEl.offsetHeight - footerEl.offsetHeight - 6),
      margin: { t: 6, l: 0, r: 0, b: 0 },
    };
    Plotly.purge(chartDiv);
    Plotly.react(chartDiv, [trace], themeChartLayout(layout), chartConfig({ responsive: false }, visualExportInfo));
    renderTable(data);
    if (resetBtn) resetBtn.disabled = !filtersActive();   // keep reset button in sync with live filters
  }

  // Seed the selection from the URL (deep link / Back-Forward) before building the controls, and prime
  // activeRendererParams so masterLoop's trailing syncUrl canonicalizes the address bar with these filters
  // (replaceState on first load, otherwise a no-op since the URL already matches).
  seedSelFromUrl();
  activeRendererParams = treemapSelToParams();
  buildControls();
  render();
}


// ---- Stratified bar (cross_tab_bar): age on the x-axis, split by sex, with a substance filter + year ----
// Facts: d = age band (x-axis), d2 = "<substance>|<sex>" composite, t = year, v = count (counts only,
// real source numbers). Self-contained like the treemap: it builds its own substance dropdown + year slider
// and re-renders in place (no masterLoop round-trip). Substance is a hard filter and the year slider picks
// the snapshot, so the chart stays readable. `barmode` ("group" | "stack") comes from the chart_type:
// grouped draws the sex sub-bars side by side; stacked draws one total bar per age band split into its
// sex sub-bars (so the bar height reads as the total and the segments as the breakdown).
const _ageBandRank = v => { const m = String(v).match(/^\d+/); return m ? parseInt(m[0], 10) : 1e9; };
const _SEX_RANK = { Female: 0, Male: 1 };

async function createVisualStratifiedBar(province, block, currentVisual, source, barmode = "group") {
  setVisualTitle(null);   // these visuals draw their own header inside #vis-div
  const cfg = (block && block.visual_options) || {};
  const facts = (block && block.facts) || [];
  const visDiv = document.getElementById("vis-div");
  const aboutDataDiv = document.getElementById("about-data");
  const tableDiv = document.getElementById("data-table");
  const tableTitle = document.getElementById("table-title");
  const toggle = document.getElementById("data-type-toggle");

  setActiveVisual(province, currentVisual);
  if (toggle) toggle.innerHTML = "";   // single data type (counts) -> no data-type toggle

  // Split the composite dimension2 once: each counts fact -> {age, substance, sex, year, v}.
  const parsed = [];
  for (const f of facts) {
    if (f.dt !== "counts") continue;
    const bits = String(f.d2 == null ? "" : f.d2).split("|");
    if (bits.length !== 2) continue;
    parsed.push({ age: f.d, substance: bits[0], sex: bits[1], year: String(f.t), v: f.v });
  }
  if (parsed.length === 0) { renderEmptyVisual("No data is available for this visual."); return; }

  const substances = Array.from(new Set(parsed.map(p => p.substance))).sort();
  const years = _sortYears(parsed.map(p => p.year));
  const latestYear = years[years.length - 1];
  const sel = { substance: substances[0], year: latestYear };   // default: first substance, latest year
  // Deep link / Back-Forward: sb.s (substance slug) and sb.y (year); each is left out at its
  // default, so a link without sb.y keeps opening on the newest year.
  const urlParams = new URLSearchParams(window.location.search || "");
  sel.substance = matchSlug(substances, urlParams.get("sb.s")) || substances[0];
  if (years.includes(urlParams.get("sb.y"))) sel.year = urlParams.get("sb.y");
  function selToParams() {
    const p = new URLSearchParams();
    if (sel.substance !== substances[0]) p.set("sb.s", slugify(sel.substance));
    if (sel.year !== latestYear) p.set("sb.y", sel.year);
    return p.toString();
  }
  function syncBarUrl() { activeRendererParams = selToParams(); syncUrl(null, null, null); }
  const substanceLabel = s => (SUBSTANCE_DISPLAY[s] || s.toSentenceCase());

  // Layout: title (header), chart, controls row (footer) -- all inside #vis-div so the next visual's
  // render tears the whole stack down with it (auto-cleanup), reusing the treemap's stacked layout CSS.
  visDiv.className = "treemap-layout";
  visDiv.innerHTML = "";
  const headerEl = document.createElement("div"); headerEl.className = "treemap-header";
  const titleEl = document.createElement("div"); titleEl.className = "treemap-title";
  headerEl.appendChild(titleEl);
  const chartDiv = document.createElement("div"); chartDiv.className = "treemap-chart";
  const footerEl = document.createElement("div"); footerEl.className = "treemap-footer";
  const controlHost = document.createElement("div"); controlHost.className = "treemap-controls-row";
  controlHost.style.alignItems = "center";   // balance the dropdown against the taller slider+readout
  footerEl.appendChild(controlHost);
  visDiv.appendChild(headerEl); visDiv.appendChild(chartDiv); visDiv.appendChild(footerEl);

  function makeSelect(labelText, options, current, onChange) {
    const wrap = document.createElement("div"); wrap.className = "treemap-control";
    wrap.style.textAlign = "center";
    const lab = document.createElement("label"); lab.className = "form-label mb-0 small fw-semibold";
    lab.innerText = labelText;
    const select = document.createElement("select"); select.className = "form-select form-select-sm";
    options.forEach(o => { const opt = document.createElement("option"); opt.value = o.value; opt.innerText = o.label; select.appendChild(opt); });
    select.value = current;
    select.onchange = () => { onChange(select.value); render(); syncBarUrl(); };
    wrap.appendChild(lab); wrap.appendChild(select); controlHost.appendChild(wrap);
  }

  // A range slider over ordered values (e.g. years): the thumb position picks one value, shown in a
  // readout beneath it. Mirrors the treemap's time slider.
  function makeSlider(labelText, values, currentIdx, onChange) {
    const wrap = document.createElement("div"); wrap.className = "treemap-control";
    wrap.style.textAlign = "center"; wrap.style.minWidth = "12rem";   // give the track room; match the dropdown's centring
    const lab = document.createElement("label"); lab.className = "form-label mb-0 small fw-semibold";
    lab.innerText = labelText;
    const slider = document.createElement("input");
    slider.type = "range"; slider.className = "form-range"; slider.min = 0; slider.step = 1;
    slider.max = Math.max(0, values.length - 1); slider.value = currentIdx;
    const readout = document.createElement("div"); readout.className = "small text-muted"; readout.innerText = values[currentIdx];
    slider.oninput = () => { const i = parseInt(slider.value, 10) || 0; readout.innerText = values[i]; onChange(values[i]); render(); };
    slider.onchange = () => { syncBarUrl(); };   // on release, like the treemap: one history entry per pick
    wrap.appendChild(lab); wrap.appendChild(slider); wrap.appendChild(readout); controlHost.appendChild(wrap);
  }

  function render() {
    const fillTokens = s => String(s || "").replace("replace_substance", substanceLabel(sel.substance)).replace("replace_year", sel.year);
    titleEl.innerText = fillTokens(cfg.title || "Hospitalizations by Age and Sex");

    const ages = Array.from(new Set(parsed.map(p => p.age))).sort((a, b) => _ageBandRank(a) - _ageBandRank(b));
    const sexes = Array.from(new Set(parsed.map(p => p.sex))).sort((a, b) => (_SEX_RANK[a] ?? 9) - (_SEX_RANK[b] ?? 9));
    const lookup = {};   // sex -> {age -> value}
    for (const p of parsed) {
      if (p.substance !== sel.substance || p.year !== sel.year) continue;
      (lookup[p.sex] = lookup[p.sex] || {})[p.age] = p.v;
    }

    const colorway = canaskColorway();
    const traces = sexes.map((sex, i) => ({
      x: ages,
      y: ages.map(a => { const v = lookup[sex] && lookup[sex][a]; return (typeof v === "number") ? v : null; }),
      name: sex,
      type: "bar",
      marker: { color: colorway[i % colorway.length], line: { width: 1, color: canaskMarkerLineColor() } },
    }));

    // Stacked mode: label each bar's total above it so the stack reads as "total split into sub-bars".
    const totals = ages.map(a => sexes.reduce((sum, sex) => {
      const v = lookup[sex] && lookup[sex][a]; return sum + (typeof v === "number" ? v : 0);
    }, 0));
    const totalAnnotations = barmode !== "stack" ? [] : ages.map((a, i) => ({
      x: a, y: totals[i], text: `<b>${totals[i]}</b>`, showarrow: false, yshift: 10,
      font: { size: 11 },
    }));

    const cardW = $("#viz-card").width(), cardH = $("#viz-card").height();
    Plotly.purge(chartDiv);
    Plotly.react(chartDiv, traces, themeChartLayout({
      barmode: barmode,
      width: cardW,
      height: Math.max(220, cardH - headerEl.offsetHeight - footerEl.offsetHeight - 6),
      xaxis: { title: { text: cfg.x_axis_title || "Age Group", standoff: 5 }, fixedrange: true },
      yaxis: { title: { text: cfg.y_axis_title || "Number of Hospitalizations", standoff: 20 }, fixedrange: true },
      hovermode: "x unified",
      hoverlabel: { namelength: -1 },
      annotations: totalAnnotations,
      legend: responsiveLegend(),
      margin: responsiveMargin(),
    }), chartConfig({ responsive: false }, visualExportInfo));

    // Table: one row per sex, a column per age band (mirrors the chart's current substance+year view).
    tableTitle.innerText = fillTokens(cfg.table_title || cfg.title || "Hospitalizations by Age and Sex");
    const table = document.createElement("table");
    table.setAttribute("class", "mb-0 table table-striped table-bordered table-hover");
    const head = table.insertRow(-1);
    [""].concat(ages).forEach(h => { const th = document.createElement("th"); th.innerText = h; head.appendChild(th); });
    sexes.forEach(sex => {
      const tr = table.insertRow(-1); tr.setAttribute("class", "align-middle");
      tr.insertCell(-1).innerText = sex;
      ages.forEach(a => { const v = lookup[sex] && lookup[sex][a]; tr.insertCell(-1).innerText = formatTableValue(typeof v === "number" ? v : null); });
    });
    makeSortableTable(table);
    tableDiv.innerHTML = ""; tableDiv.appendChild(table);
    aboutDataDiv.innerHTML = buildAboutDataHTML(source);
  }

  makeSelect(cfg.filter_label || "Substance", substances.map(s => ({ value: s, label: substanceLabel(s) })), sel.substance, v => { sel.substance = v; });
  makeSlider(cfg.time_label || "Year", years, years.indexOf(sel.year), v => { sel.year = v; });
  render();
  syncBarUrl();
}


// ---- Expected vs. actual (expected_actual_bar): horizontal 100%-stacked bars per expected drug ----
// Facts: d = expected drug (chart row), d2 = outcome key (segment: expected_only / expected_plus /
// not_expected), t = month ("YYYY-MM"), v = sample count. Self-contained like the treemap: it builds
// its own "Date checked" unit select + period slider (same rig as the treemap's time control) and
// re-renders in place. Percentages are computed here from the counts left after the date filter, so
// each row always reads as 100% of its currently-selected samples; segment colors are semantic
// (green = as expected, yellow = expected + other noteworthy, red = not the expected substance).
async function createVisualExpectedActual(province, block, currentVisual, source) {
  setVisualTitle(null);   // these visuals draw their own header inside #vis-div
  const cfg = (block && block.visual_options) || {};
  const facts = (block && block.facts) || [];
  const visDiv = document.getElementById("vis-div");
  const aboutDataDiv = document.getElementById("about-data");
  const tableDiv = document.getElementById("data-table");
  const tableTitle = document.getElementById("table-title");
  const toggle = document.getElementById("data-type-toggle");

  setActiveVisual(province, currentVisual);
  if (toggle) toggle.innerHTML = "";   // single data type (counts) -> no data-type toggle

  const parsed = [];
  for (const f of facts) {
    if (f.dt !== "counts" || typeof f.v !== "number") continue;
    const geoBits = String(f.geo == null ? "" : f.geo).split("||");
    parsed.push({ drug: f.d, outcome: f.d2, t: String(f.t), v: f.v, prov: geoBits[0] || "", site: geoBits[1] || "" });
  }
  if (parsed.length === 0) { renderEmptyVisual("No data is available for this visual."); return; }
  // Province / site options (geo = "Province||Site"), cascading like the treemap's geo levels.
  const { provinces, sitesFor, levels: geoLevels } = geoPickerOptions(parsed, cfg);

  const segments = (cfg.segments && cfg.segments.length) ? cfg.segments : [
    { key: "expected_only", label: "Only expected substance", color: "success" },
    { key: "expected_plus", label: "Expected substance + other noteworthy substances", color: "warning" },
    { key: "not_expected", label: "Did not contain expected substance", color: "danger" },
  ];
  const sel = { unit: "all", bucket: null, prov: null, site: null };
  // Deep link / Back-Forward: ea.p / ea.s (geo slugs), ea.u / ea.b (date unit + bucket slug, applied
  // once the date control has built its buckets below).
  const urlParams = new URLSearchParams(window.location.search || "");
  sel.prov = matchSlug(provinces, urlParams.get("ea.p"));
  sel.site = matchSlug(sitesFor(sel.prov), urlParams.get("ea.s"));
  if (["year", "month"].includes(urlParams.get("ea.u"))) sel.unit = urlParams.get("ea.u");
  function selToParams() {
    const p = new URLSearchParams();
    if (sel.prov != null) p.set("ea.p", slugify(sel.prov));
    if (sel.site != null) p.set("ea.s", slugify(sel.site));
    if (sel.unit !== "all") { p.set("ea.u", sel.unit); if (sel.bucket != null) p.set("ea.b", slugify(sel.bucket)); }
    return p.toString();
  }
  function syncEaUrl() { activeRendererParams = selToParams(); syncUrl(null, null, null); }
  function filtersActive() { return sel.prov != null || sel.site != null || sel.unit !== "all"; }
  let resetBtn = null;

  // Layout: title + subtitle (header), chart, controls + note (footer) -- all inside #vis-div so the
  // next visual's render tears the whole stack down (auto-cleanup), reusing the treemap layout CSS.
  visDiv.className = "treemap-layout";
  visDiv.innerHTML = "";
  const headerEl = document.createElement("div"); headerEl.className = "treemap-header";
  const titleEl = document.createElement("div"); titleEl.className = "treemap-title";
  titleEl.innerText = cfg.title || "How do samples compare to what people expect?";
  headerEl.appendChild(titleEl);
  if (cfg.subtitle) {
    // var(--c-muted) rather than .text-muted: the token adapts to the dark theme, Bootstrap's
    // class does not (same below for the note).
    const subEl = document.createElement("div"); subEl.className = "small";
    subEl.style.color = "var(--c-muted)";
    subEl.innerText = cfg.subtitle;
    headerEl.appendChild(subEl);
  }
  const chartDiv = document.createElement("div"); chartDiv.className = "treemap-chart";
  const footerEl = document.createElement("div"); footerEl.className = "treemap-footer";
  // The key is plain HTML (not Plotly's legend) so it wraps on narrow cards instead of overlapping
  // the axis, and the figure's height budget can include it.
  const legendEl = document.createElement("div"); legendEl.className = "ea-legend";
  footerEl.appendChild(legendEl);
  const controlHost = document.createElement("div"); controlHost.className = "treemap-controls-row";
  controlHost.style.alignItems = "center";
  footerEl.appendChild(controlHost);
  if (cfg.note) {
    const noteEl = document.createElement("div"); noteEl.className = "small mt-1";
    noteEl.style.color = "var(--c-muted)";
    noteEl.innerText = cfg.note;
    footerEl.appendChild(noteEl);
  }
  visDiv.appendChild(headerEl); visDiv.appendChild(chartDiv); visDiv.appendChild(footerEl);

  function render() {
    // Aggregate the facts surviving the date filter: per-drug totals + per-segment counts.
    const totals = {};    // drug -> n samples in the current view
    const bySeg = {};     // outcome key -> {drug -> count}
    for (const p of parsed) {
      if (sel.prov != null && p.prov !== sel.prov) continue;
      if (sel.site != null && p.site !== sel.site) continue;
      if (sel.unit !== "all" && sel.bucket != null && timeBucketOf(p, sel.unit) !== sel.bucket) continue;
      totals[p.drug] = (totals[p.drug] || 0) + p.v;
      if (!bySeg[p.outcome]) bySeg[p.outcome] = {};
      bySeg[p.outcome][p.drug] = (bySeg[p.outcome][p.drug] || 0) + p.v;
    }

    // Row order from config, keeping only drugs present in the current view (a narrow date pick can
    // empty a row); any unexpected drug values are appended so new data surfaces rather than vanishing.
    const ordered = (cfg.row_order && cfg.row_order.length) ? cfg.row_order : Object.keys(totals).sort();
    const rows = ordered.filter(d => totals[d] > 0)
      .concat(Object.keys(totals).filter(d => totals[d] > 0 && !ordered.includes(d)));
    if (resetBtn) resetBtn.disabled = !filtersActive();
    if (rows.length === 0) {
      Plotly.purge(chartDiv);
      chartDiv.innerHTML = '<p class="text-muted text-center py-5">No samples were checked for this selection.</p>';
      tableTitle.innerText = ""; tableDiv.innerHTML = "";
      aboutDataDiv.innerHTML = buildAboutDataHTML(source);
      return;
    }
    chartDiv.innerHTML = "";
    // One line per row ("Ketamine (159)") so each label sits level with its bar.
    const rowLabels = rows.map(d => `${d} (${totals[d]})`);
    const segCount = (seg, drug) => (bySeg[seg.key] && bySeg[seg.key][drug]) || 0;
    const segPct = (seg, drug) => totals[drug] ? (100 * segCount(seg, drug)) / totals[drug] : 0;

    const colors = canaskSemanticColors();
    legendEl.innerHTML = "";
    segments.forEach(seg => {
      const item = document.createElement("span"); item.className = "ea-legend-item";
      const swatch = document.createElement("span"); swatch.className = "ea-legend-swatch";
      swatch.style.background = colors[seg.color] || colors.success;
      item.appendChild(swatch); item.appendChild(document.createTextNode(seg.label));
      legendEl.appendChild(item);
    });
    const traces = segments.map(seg => {
      const pcts = rows.map(d => segPct(seg, d));
      return {
        type: "bar",
        orientation: "h",
        y: rowLabels,
        x: pcts,
        name: seg.label,
        customdata: rows.map(d => segCount(seg, d)),
        // Overlay the rounded percentage on each segment; slivers that would round to 0% stay
        // unlabeled (their exact share is still in the hover + table).
        text: pcts.map(p => (Math.round(p) >= 1 ? Math.round(p) + "%" : "")),
        textposition: "inside",
        insidetextanchor: "middle",
        hovertemplate: "%{x:.1f}% (%{customdata} samples)<extra>" + seg.label + "</extra>",
        marker: { color: colors[seg.color] || colors.success, line: { width: 1, color: canaskMarkerLineColor() } },
      };
    });

    const cardW = $("#viz-card").width(), cardH = $("#viz-card").height();
    // The figure never exceeds what the card has left after the header and footer (key + controls),
    // or it spills out of the card; bargap widens the gutters between the bars within that budget.
    const budget = cardH - headerEl.offsetHeight - footerEl.offsetHeight - 6;
    Plotly.purge(chartDiv);
    Plotly.react(chartDiv, traces, themeChartLayout({
      barmode: "stack",
      bargap: 0.42,
      width: cardW,
      height: Math.max(240, budget),
      xaxis: { range: [0, 100], ticksuffix: "%", title: { text: cfg.x_axis_title || "% of drug samples checked", standoff: 5 }, fixedrange: true },
      // Reversed so row_order[0] renders at the top (Plotly stacks category axes bottom-up).
      yaxis: { title: { text: cfg.y_axis_title || "Expected drug", standoff: 10 }, autorange: "reversed", automargin: true, fixedrange: true },
      // The key is the HTML legendEl in the footer (see above), so the bars get the full card width
      // and the figure's bottom margin only needs the axis.
      showlegend: false,
      margin: Object.assign(responsiveMargin(), { b: 52 }),
    }), chartConfig({ responsive: false }, visualExportInfo));

    // Table: one row per expected drug -- sample count plus each segment as "count (share%)".
    tableTitle.innerText = cfg.table_title || cfg.title || "Samples vs. Expectations";
    const table = document.createElement("table");
    table.setAttribute("class", "mb-0 table table-striped table-bordered table-hover");
    const head = table.insertRow(-1);
    ["Expected drug", "Samples"].concat(segments.map(s => s.label)).forEach(h => {
      const th = document.createElement("th"); th.innerText = h; head.appendChild(th);
    });
    rows.forEach(drug => {
      const tr = table.insertRow(-1); tr.setAttribute("class", "align-middle");
      tr.insertCell(-1).innerText = drug;
      tr.insertCell(-1).innerText = totals[drug];
      segments.forEach(seg => {
        tr.insertCell(-1).innerText = `${segCount(seg, drug)} (${segPct(seg, drug).toFixed(1)}%)`;
      });
    });
    // Total row: the grand sample count (each row's expected drug is mutually exclusive, so the
    // rows sum cleanly) plus each segment's combined count and share, so the total backing the
    // whole chart is visible without adding up rows by hand.
    const grandTotal = rows.reduce((sum, drug) => sum + totals[drug], 0);
    const foot = table.createTFoot();
    const totalRow = foot.insertRow(-1);
    totalRow.className = "fw-bold table-group-divider";
    totalRow.insertCell(-1).innerText = "Total";
    totalRow.insertCell(-1).innerText = grandTotal;
    segments.forEach(seg => {
      const segTotal = rows.reduce((sum, drug) => sum + segCount(seg, drug), 0);
      const segPctTotal = grandTotal ? (100 * segTotal) / grandTotal : 0;
      totalRow.insertCell(-1).innerText = `${segTotal} (${segPctTotal.toFixed(1)}%)`;
    });
    makeSortableTable(table);
    tableDiv.innerHTML = ""; tableDiv.appendChild(table);
    aboutDataDiv.innerHTML = buildAboutDataHTML(source);
  }

  // Province / site dropdowns first in the footer row, cascading (a province narrows the sites),
  // mirroring the treemap's geo controls so the two visuals filter the same way.
  const geoSelects = {};
  function makeGeoSelect(name, labelText, options, current, onChange) {
    const wrap = document.createElement("div");
    wrap.className = "treemap-control text-start";
    const lab = document.createElement("label");
    lab.className = "form-label mb-0 small fw-semibold";
    lab.innerText = labelText;
    const select = document.createElement("select");
    select.className = "form-select form-select-sm";
    select.id = `ea-${name}-select`; lab.htmlFor = select.id;
    const fill = (opts, cur) => {
      select.innerHTML = "";
      [{ value: "", label: "All" }].concat(opts.map(v => ({ value: v, label: v }))).forEach(o => {
        const opt = document.createElement("option"); opt.value = o.value; opt.innerText = o.label; select.appendChild(opt);
      });
      select.value = cur == null ? "" : cur;
    };
    fill(options, current);
    select.onchange = () => { onChange(select.value === "" ? null : select.value); render(); syncEaUrl(); };
    wrap.appendChild(lab); wrap.appendChild(select); controlHost.appendChild(wrap);
    geoSelects[name] = { select, fill };
  }
  makeGeoSelect("prov", geoLevels[0], provinces, sel.prov,
                v => { sel.prov = v; sel.site = null; geoSelects.site.fill(sitesFor(sel.prov), null); });
  makeGeoSelect("site", geoLevels[1], sitesFor(sel.prov), sel.site, v => { sel.site = v; });

  // "Date checked" control: unit select + period slider, mirroring the treemap's time control (the
  // sibling visual on this dashboard) so the two filters read as one pattern. No seasonal unit here
  // -- a season row would mix years and misread as a period.
  let dateCtl = null;
  (function buildDateControl() {
    const wrap = document.createElement("div");
    wrap.className = "treemap-control text-start";
    const lab = document.createElement("label");
    lab.className = "form-label mb-0 small fw-semibold";
    lab.innerText = (cfg.time && cfg.time.label) || "Date checked";
    const unitSelect = document.createElement("select");
    unitSelect.className = "form-select form-select-sm";
    [["all", "All time"], ["year", "Year"], ["month", "Month"]].forEach(u => {
      const opt = document.createElement("option");
      opt.value = u[0]; opt.innerText = u[1];
      unitSelect.appendChild(opt);
    });
    unitSelect.value = sel.unit;
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
      buckets = treemapTimeBuckets(parsed, sel.unit);
      if (sel.unit === "all" || buckets.length === 0) {
        sliderWrap.style.display = "none";
        sel.bucket = null;
      } else {
        sliderWrap.style.display = "";
        slider.max = buckets.length - 1;
        slider.value = buckets.length - 1;   // default to the most recent period
        // A deep-linked bucket (ea.b) wins over "latest" the first time the buckets are built.
        const seeded = urlParams.get("ea.b");
        if (seeded) {
          const idx = buckets.findIndex(bk => slugify(bk.value) === seeded);
          if (idx >= 0) slider.value = idx;
          urlParams.delete("ea.b");
        }
        applyBucket();
      }
    }
    unitSelect.onchange = () => { sel.unit = unitSelect.value; sel.bucket = null; rebuildBuckets(); render(); syncEaUrl(); };
    slider.oninput = () => { applyBucket(); render(); };
    slider.onchange = () => { syncEaUrl(); };
    wrap.appendChild(lab); wrap.appendChild(unitSelect); wrap.appendChild(sliderWrap);
    controlHost.appendChild(wrap);
    rebuildBuckets();
    dateCtl = { unitSelect, rebuildBuckets };
  })();

  // Reset filters: all provinces / sites, all time. Disabled when nothing is filtered.
  const resetWrap = document.createElement("div");
  resetWrap.className = "treemap-control treemap-reset text-start d-flex align-items-end";
  resetBtn = document.createElement("button");
  resetBtn.type = "button";
  resetBtn.className = "btn btn-outline-secondary btn-sm";
  resetBtn.innerHTML = '<i class="bi bi-arrow-counterclockwise"></i> Reset filters';
  resetBtn.onclick = () => {
    sel.prov = null; sel.site = null; sel.unit = "all"; sel.bucket = null;
    geoSelects.prov.select.value = "";
    geoSelects.site.fill(sitesFor(null), null);
    dateCtl.unitSelect.value = "all";
    dateCtl.rebuildBuckets();
    render(); syncEaUrl();
  };
  resetWrap.appendChild(resetBtn);
  controlHost.appendChild(resetWrap);

  // Prime the URL params before the first render so masterLoop's trailing syncUrl canonicalizes a
  // deep link (replaceState) instead of dropping the seeded selection.
  activeRendererParams = selToParams();
  render();
}


// ---- Found substances (found_substance_series): what was in samples expected to be X -------------
// Facts (counts, month AND quarter grain -- masterLoop hands us the active grain's facts): d = expected
// drug, d2 = "__total__" (samples checked) | "<group>|__any__" (samples with ANY substance of the
// group) | "<group>|<label>" (samples with that substance), geo = "Province||Site", v = sample count.
// Self-contained like the treemap: the viewer picks the expected drug (dropdown), a substance group
// (clickable list beside the chart; a dropdown on phones) and province/site (cascading dropdowns);
// counts are summed over the selected geos FIRST, then turned into % of the samples checked, so one
// line per substance + a bold "Any <group>" line. Group order/labels come from visual_options.
// found_groups (published by gen-visuals from the crosswalk; a group only in the facts falls back to
// its key). Selection rides the URL as fs.e / fs.g / fs.p / fs.s via activeRendererParams.
const FOUND_TOTAL_KEY = "__total__";
const FOUND_ANY_LABEL = "__any__";
// "Fentanyl & analogues" -> "fentanyl & analogues" for mid-sentence use, but "MDMA & analogues" /
// "GHB & other sedatives" keep their acronym (a first word with 2+ capitals is left alone).
function sentenceCaseGroup(label) {
  const text = String(label || "");
  const first = text.split(/\s/)[0];
  return (first.replace(/[^A-Z]/g, "").length >= 2) ? text : text.charAt(0).toLowerCase() + text.slice(1);
}

async function createVisualFoundSubstances(province, block, currentVisual, source, opts) {
  setVisualTitle(null);   // draws its own header inside #vis-div
  const cfg = (block && block.visual_options) || {};
  const facts = (block && block.facts) || [];
  const grain = (opts && opts.grain) || "month";
  const visDiv = document.getElementById("vis-div");
  const aboutDataDiv = document.getElementById("about-data");
  const tableDiv = document.getElementById("data-table");
  const tableTitle = document.getElementById("table-title");
  const toggle = document.getElementById("data-type-toggle");

  setActiveVisual(province, currentVisual);
  if (toggle) toggle.innerHTML = "";   // single data type (counts) -> no Measure toggle

  // Parse once: {prov, site, t, expected, group, sub, v}. The total row has group null.
  const parsed = [];
  for (const f of facts) {
    if (f.dt !== "counts" || typeof f.v !== "number" || f.d == null || f.d2 == null) continue;
    const geoBits = String(f.geo == null ? "" : f.geo).split("||");
    const d2 = String(f.d2);
    let group = null, sub = FOUND_TOTAL_KEY, method = null;
    if (d2 !== FOUND_TOTAL_KEY) {
      const cut = d2.indexOf("|");
      if (cut < 0) continue;
      group = d2.slice(0, cut); sub = d2.slice(cut + 1);
      // "<group>|<label>|ftir" / "|strip": how many of the samples containing <label> each method
      // found it in (a sample both methods found counts in both -- a breakdown, not a sum).
      const m = sub.match(/^(.*)\|(ftir|strip)$/);
      if (m) { sub = m[1]; method = m[2]; }
    }
    parsed.push({ prov: geoBits[0] || "", site: geoBits[1] || "", t: String(f.t), expected: String(f.d), group, sub, method, v: f.v });
  }
  if (parsed.length === 0) { renderEmptyVisual("No data is available for this visual."); return; }

  const minN = Number.isFinite(cfg.min_n) ? cfg.min_n : 5;
  const maxLines = Number.isFinite(cfg.max_lines) ? cfg.max_lines : 10;

  // Groups in crosswalk order (+ any the facts carry that the option doesn't, so new data surfaces).
  const groupLabel = {};
  const groupKeys = [];
  (Array.isArray(cfg.found_groups) ? cfg.found_groups : []).forEach(g => {
    if (g && g.key && !(g.key in groupLabel)) { groupKeys.push(g.key); groupLabel[g.key] = g.label || g.key; }
  });
  for (const p of parsed) if (p.group && !(p.group in groupLabel)) { groupKeys.push(p.group); groupLabel[p.group] = p.group; }

  // Expected drugs, most-sampled first (all geos, all periods at this grain).
  const expectedTotals = {};
  for (const p of parsed) if (p.sub === FOUND_TOTAL_KEY) expectedTotals[p.expected] = (expectedTotals[p.expected] || 0) + p.v;
  const expectedList = Object.keys(expectedTotals).sort((a, b) => (expectedTotals[b] - expectedTotals[a]) || a.localeCompare(b));
  if (expectedList.length === 0) { renderEmptyVisual("No data is available for this visual."); return; }

  const { provinces, sitesFor, levels: geoLevels } = geoPickerOptions(parsed, cfg);

  const sel = { expected: null, group: null, prov: null, site: null };

  // ---- aggregation for the current selection ----
  const inSelection = p => p.expected === sel.expected && (sel.prov == null || p.prov === sel.prov) && (sel.site == null || p.site === sel.site);
  function aggregate() {
    const totals = {};             // period -> samples checked
    const hits = {};               // group -> sub -> period -> samples
    const methods = {};            // group -> sub -> method -> period -> samples
    for (const p of parsed) {
      if (!inSelection(p)) continue;
      if (p.sub === FOUND_TOTAL_KEY) { totals[p.t] = (totals[p.t] || 0) + p.v; continue; }
      if (p.method) {
        const mg = methods[p.group] || (methods[p.group] = {});
        const ms = mg[p.sub] || (mg[p.sub] = {});
        const mm = ms[p.method] || (ms[p.method] = {});
        mm[p.t] = (mm[p.t] || 0) + p.v;
        continue;
      }
      const g = hits[p.group] || (hits[p.group] = {});
      const s = g[p.sub] || (g[p.sub] = {});
      s[p.t] = (s[p.t] || 0) + p.v;
    }
    const periods = Object.keys(totals).sort();   // "YYYY-MM" / "YYYY-Qn" sort lexically
    const grand = periods.reduce((sum, t) => sum + totals[t], 0);
    const sumOf = series => periods.reduce((sum, t) => sum + ((series && series[t]) || 0), 0);
    return { totals, hits, methods, periods, grand, sumOf };
  }

  // ---- selection seed: URL (deep link / Back-Forward) -> manifest default -> most sampled ----
  (function seedSelFromUrl() {
    const params = new URLSearchParams(window.location.search || "");
    sel.expected = matchSlug(expectedList, params.get("fs.e"))
      || (expectedList.includes(cfg.default_expected) ? cfg.default_expected : expectedList[0]);
    sel.prov = matchSlug(provinces, params.get("fs.p"));
    sel.site = matchSlug(sitesFor(sel.prov), params.get("fs.s"));
    sel.group = matchSlug(groupKeys, params.get("fs.g"));
  })();
  // The first group with any hit for the current expected drug + geos (`agg` = their aggregate,
  // computed here when the caller hasn't already).
  function defaultGroup(agg = aggregate()) {
    return groupKeys.find(g => agg.sumOf(agg.hits[g] && agg.hits[g][FOUND_ANY_LABEL]) > 0) || groupKeys[0] || null;
  }
  if (sel.group == null) sel.group = defaultGroup();

  function selToParams() {
    const p = new URLSearchParams();
    if (sel.expected != null) p.set("fs.e", slugify(sel.expected));
    if (sel.group != null) p.set("fs.g", slugify(sel.group));
    if (sel.prov != null) p.set("fs.p", slugify(sel.prov));
    if (sel.site != null) p.set("fs.s", slugify(sel.site));
    return p.toString();
  }
  function syncFoundUrl() { activeRendererParams = selToParams(); syncUrl(null, null, null); }

  // ---- layout: header / (group list + chart) / controls + note, all inside #vis-div ----
  visDiv.className = "treemap-layout found-layout";
  visDiv.innerHTML = "";
  const headerEl = document.createElement("div"); headerEl.className = "treemap-header";
  const titleEl = document.createElement("div"); titleEl.className = "treemap-title";
  headerEl.appendChild(titleEl);
  // The subtitle carries the expected drug and the period grain (replace_expected / replace_period),
  // so it is (re)filled on every render alongside the title.
  let subEl = null;
  if (cfg.subtitle) {
    subEl = document.createElement("div"); subEl.className = "small";
    subEl.style.color = "var(--c-muted)";
    headerEl.appendChild(subEl);
  }
  const bodyEl = document.createElement("div"); bodyEl.className = "found-body";
  const listEl = document.createElement("div"); listEl.className = "found-groups";
  listEl.setAttribute("role", "listbox"); listEl.setAttribute("aria-label", "Substance group");
  const chartDiv = document.createElement("div"); chartDiv.className = "treemap-chart found-chart";
  bodyEl.appendChild(listEl); bodyEl.appendChild(chartDiv);
  const footerEl = document.createElement("div"); footerEl.className = "treemap-footer";
  // Always occupies its line (visibility, not display): the height budget below is measured once,
  // so a line that appeared later would push the chart out of the card.
  const moreEl = document.createElement("div"); moreEl.className = "small";
  moreEl.style.color = "var(--c-muted)"; moreEl.style.visibility = "hidden"; moreEl.innerText = "\u00a0";
  footerEl.appendChild(moreEl);
  visDiv.appendChild(headerEl); visDiv.appendChild(bodyEl); visDiv.appendChild(footerEl);
  // The filters live in the control bar under the card (#vis-controls), on the same line as the
  // Period toggle, so the chart keeps the whole card. masterLoop empties the slot before each render.
  const controlHost = document.getElementById("vis-renderer-controls");
  controlHost.innerHTML = "";

  const selects = {};   // name -> <select>, so a change elsewhere (group list click, province cascade) refreshes them
  function makeSelect(name, labelText, options, current, onChange, extraClass) {
    const wrap = document.createElement("div"); wrap.className = "vis-control" + (extraClass ? " " + extraClass : "");
    const lab = document.createElement("label"); lab.className = "vis-control-label mb-0";
    lab.innerText = labelText;
    const select = document.createElement("select"); select.className = "form-select form-select-sm";
    select.id = `found-${name}-select`; lab.htmlFor = select.id;
    const fill = (opts, cur) => {
      select.innerHTML = "";
      opts.forEach(o => { const opt = document.createElement("option"); opt.value = o.value; opt.innerText = o.label; select.appendChild(opt); });
      select.value = cur == null ? "" : cur;
    };
    fill(options, current);
    select.onchange = () => { onChange(select.value === "" ? null : select.value); render(); syncFoundUrl(); };
    wrap.appendChild(lab); wrap.appendChild(select); controlHost.appendChild(wrap);
    selects[name] = { select, fill };
  }
  const withAll = (values, allLabel) => [{ value: "", label: allLabel }].concat(values.map(v => ({ value: v, label: v })));
  const expectedOption = e => ({
    value: e,
    label: expectedTotals[e] < 10 ? `${e} (n=${expectedTotals[e]}, small sample)` : `${e} (n=${expectedTotals[e]})`,
  });

  makeSelect("expected", cfg.expected_label || "Expected drug", expectedList.map(expectedOption), sel.expected,
             v => { sel.expected = v; if (sel.group == null) sel.group = defaultGroup(); });
  makeSelect("group", cfg.group_label || "Substances found", groupKeys.map(g => ({ value: g, label: groupLabel[g] })), sel.group,
             v => { sel.group = v; }, "found-group-select");
  makeSelect("prov", geoLevels[0], withAll(provinces, "All"), sel.prov,
             v => { sel.prov = v; sel.site = null; selects.site.fill(withAll(sitesFor(sel.prov), "All"), null); });
  makeSelect("site", geoLevels[1], withAll(sitesFor(sel.prov), "All"), sel.site, v => { sel.site = v; });

  // ---- Reset filters: back to the manifest's default expected drug, its first non-empty group, all geos ----
  const defaultExpected = expectedList.includes(cfg.default_expected) ? cfg.default_expected : expectedList[0];
  function filtersActive(agg) {
    if (sel.prov != null || sel.site != null || sel.expected !== defaultExpected) return true;
    return sel.group !== defaultGroup(agg);   // the selection IS the default expected + geos here, so its aggregate serves
  }
  function resetFilters() {
    sel.prov = null; sel.site = null; sel.expected = defaultExpected;
    sel.group = defaultGroup();
    selects.expected.select.value = sel.expected;
    selects.prov.select.value = "";
    selects.site.fill(withAll(sitesFor(null), "All"), null);
    selects.group.select.value = sel.group == null ? "" : sel.group;
    render(); syncFoundUrl();
  }
  const resetWrap = document.createElement("div");
  resetWrap.className = "vis-control treemap-reset";
  const resetBtn = document.createElement("button");
  resetBtn.type = "button";
  resetBtn.className = "btn btn-outline-secondary btn-sm";
  resetBtn.innerHTML = '<i class="bi bi-arrow-counterclockwise"></i> Reset filters';
  resetBtn.onclick = resetFilters;
  resetWrap.appendChild(resetBtn);
  controlHost.appendChild(resetWrap);

  // ---- the clickable group list beside the chart ----
  function renderGroupList(agg) {
    listEl.innerHTML = "";
    const head = document.createElement("div"); head.className = "found-groups-head";
    head.innerText = `${sel.expected} · ${agg.grand} sample${agg.grand === 1 ? "" : "s"}`;
    listEl.appendChild(head);
    groupKeys.forEach(g => {
      const any = agg.sumOf(agg.hits[g] && agg.hits[g][FOUND_ANY_LABEL]);
      const pct = agg.grand ? (100 * any) / agg.grand : 0;
      const row = document.createElement("button");
      row.type = "button";
      row.className = "found-group" + (g === sel.group ? " active" : "") + (any === 0 ? " empty" : "");
      row.setAttribute("role", "option"); row.setAttribute("aria-selected", g === sel.group ? "true" : "false");
      row.title = `${groupLabel[g]}: ${any} of ${agg.grand} samples`;
      const name = document.createElement("span"); name.className = "found-group-name"; name.innerText = groupLabel[g];
      const val = document.createElement("span"); val.className = "found-group-pct"; val.innerText = any === 0 ? "–" : `${Math.round(pct)}%`;
      row.appendChild(name); row.appendChild(val);
      row.onclick = () => { if (sel.group === g) return; sel.group = g; selects.group.select.value = g; render(); syncFoundUrl(); };
      listEl.appendChild(row);
    });
  }

  // Height budget for the list + chart row, measured ONCE while the row is still empty (the card is
  // content-sized, so measuring after the 16-row list is in would just report the list's own height
  // and the card would outgrow its 80vh row). Pinning the row to it makes the list scroll instead.
  let bodyBudget = null;
  function settleBodyBudget() {
    if (bodyBudget != null) return;
    const cardH = $("#viz-card").height();
    bodyBudget = Math.max(260, cardH - headerEl.offsetHeight - footerEl.offsetHeight - 6);
    bodyEl.style.height = bodyBudget + "px";
  }

  function render() {
    const agg = aggregate();
    resetBtn.disabled = !filtersActive(agg);   // disabled at the default view so it can't read as live
    const periodWords = grain === "quarter" ? "quarter by quarter" : "month by month";
    const fillTokens = s => String(s || "").replace(/replace_expected/g, sel.expected).replace(/replace_period/g, periodWords);
    titleEl.innerText = fillTokens(cfg.title || "Substances found in samples expected to be replace_expected");
    if (subEl) subEl.innerText = fillTokens(cfg.subtitle);
    settleBodyBudget();
    renderGroupList(agg);
    aboutDataDiv.innerHTML = buildAboutDataHTML(source);

    if (agg.periods.length === 0 || sel.group == null) {
      Plotly.purge(chartDiv);
      chartDiv.innerHTML = '<p class="text-muted text-center py-5">No samples were checked for this selection.</p>';
      tableTitle.innerText = ""; tableDiv.innerHTML = ""; moreEl.style.visibility = "hidden"; moreEl.innerText = "\u00a0";
      return;
    }
    chartDiv.innerHTML = "";

    // Series: "Any <group>" first, then substances by overall share (desc); the top maxLines get lines.
    const groupHits = agg.hits[sel.group] || {};
    const subs = Object.keys(groupHits).filter(s => s !== FOUND_ANY_LABEL)
      .sort((a, b) => (agg.sumOf(groupHits[b]) - agg.sumOf(groupHits[a])) || a.localeCompare(b));
    const drawn = subs.slice(0, maxLines);
    const hidden = subs.length - drawn.length;
    moreEl.style.visibility = hidden > 0 ? "visible" : "hidden";
    moreEl.innerText = hidden > 0 ? `${hidden} more substance${hidden === 1 ? "" : "s"} in the table below.` : "\u00a0";

    const pctOf = (series, t) => agg.totals[t] ? (100 * ((series && series[t]) || 0)) / agg.totals[t] : null;
    const hover = "%{y:.0f}% (%{customdata[0]} of %{customdata[1]} samples%{customdata[2]})%{customdata[3]}<extra>%{fullData.name}</extra>";
    // Method of detection, per substance: the per-method counts ride the hover ("found by FTIR in
    // 30, by test strip in 25") and tag the legend name; the FTIR-only default needs no tag beyond
    // the subtitle's "Detected by FTIR or test strip".
    const methodsOf = s => (agg.methods[sel.group] && agg.methods[sel.group][s]) || {};
    const methodTag = s => {
      if (/test strip/i.test(s)) return "";
      const m = methodsOf(s), ftir = agg.sumOf(m.ftir) > 0, strip = agg.sumOf(m.strip) > 0;
      return strip ? (ftir ? " (FTIR + test strip)" : " (test strip)") : "";
    };
    const methodText = (s, t) => {
      const m = methodsOf(s), ftir = (m.ftir && m.ftir[t]) || 0, strip = (m.strip && m.strip[t]) || 0;
      if (!ftir && !strip) return "";
      const parts = [];
      if (ftir) parts.push(`FTIR ${ftir}`);
      if (strip) parts.push(`test strip ${strip}`);
      return `<br>found by ${parts.join(", ")}`;
    };
    const customdata = (series, s) => agg.periods.map(t => [(series && series[t]) || 0, agg.totals[t],
                                                            agg.totals[t] < minN ? " · small sample" : "", s ? methodText(s, t) : ""]);
    // The samples checked per period, written across the top of the plot so every percentage reads
    // against its n at a glance (thinned when there are many periods; the last period always shows).
    const nStep = Math.max(1, Math.ceil(agg.periods.length / 14));
    const nRow = agg.periods.map((t, i) => ((i % nStep === 0 || i === agg.periods.length - 1) ? {
      x: t, y: 107, xref: "x", yref: "y", text: (i === 0 ? "n = " : "") + agg.totals[t], showarrow: false,
      font: { size: 10, color: canaskMarkerLineColor() }, xanchor: "center", yanchor: "middle",
    } : null)).filter(Boolean);
    const colorway = canaskColorway();
    const anyLabel = `Any ${sentenceCaseGroup(groupLabel[sel.group])}`;
    // Series that coincide at EVERY period (two substances always found together, or a group's only
    // substance tracking its own "Any" line) would hide one another. The second, third, ... of each
    // identical set get a dash pattern and a different marker shape so both stay visible; the first
    // (the "Any" line when it is one of them) keeps the solid style.
    const DUP_STYLES = [
      { dash: "solid", symbol: "circle" }, { dash: "dash", symbol: "diamond" },
      { dash: "dot", symbol: "square" }, { dash: "dashdot", symbol: "triangle-up" },
    ];
    const seenShapes = new Map();   // y-signature -> how many series already drawn with it
    const styleFor = series => {
      const sig = JSON.stringify(agg.periods.map(t => pctOf(series, t)));
      const n = seenShapes.get(sig) || 0;
      seenShapes.set(sig, n + 1);
      const st = DUP_STYLES[Math.min(n, DUP_STYLES.length - 1)];
      return { dash: st.dash, symbols: agg.periods.map(t => (agg.totals[t] < minN ? st.symbol + "-open" : st.symbol)) };
    };
    const anyStyle = styleFor(groupHits[FOUND_ANY_LABEL]);
    const traces = [{
      type: "scatter", mode: "lines+markers", name: anyLabel,
      x: agg.periods, y: agg.periods.map(t => pctOf(groupHits[FOUND_ANY_LABEL], t)),
      customdata: customdata(groupHits[FOUND_ANY_LABEL], null), hovertemplate: hover,
      line: { width: 3.5, color: canaskMarkerLineColor(), dash: anyStyle.dash },
      marker: { size: 8, symbol: anyStyle.symbols, color: canaskMarkerLineColor() },
    }].concat(drawn.map((s, i) => {
      const st = styleFor(groupHits[s]);
      return {
        type: "scatter", mode: "lines+markers", name: s + methodTag(s),
        x: agg.periods, y: agg.periods.map(t => pctOf(groupHits[s], t)),
        customdata: customdata(groupHits[s], s), hovertemplate: hover,
        line: { width: 2, color: colorway[i % colorway.length], dash: st.dash },
        marker: { size: st.dash === "solid" ? 6 : 7, symbol: st.symbols, color: colorway[i % colorway.length] },
      };
    }));

    const cardW = $("#viz-card").width();
    const listW = listEl.offsetParent ? listEl.offsetWidth : 0;   // 0 when the list is hidden (phones)
    Plotly.purge(chartDiv);
    Plotly.react(chartDiv, traces, themeChartLayout({
      width: Math.max(240, (chartDiv.clientWidth || (cardW - listW)) - 2),
      height: bodyBudget - 2,
      xaxis: Object.assign({ title: { text: cfg.x_axis_title || "Date checked", standoff: 12 }, automargin: true, fixedrange: true },
                           grainXAxis(grain, agg.periods)),
      // Headroom above 100% holds the n row (annotations at y=107); ticks stop at 100.
      yaxis: { range: [0, 114], tickvals: [0, 20, 40, 60, 80, 100], ticksuffix: "%",
               title: { text: cfg.y_axis_title || "% of samples checked", standoff: 10 }, fixedrange: true },
      annotations: nRow,
      hovermode: "closest",
      hoverlabel: { namelength: -1 },
      legend: responsiveLegend(),
      margin: responsiveMargin(),
    }), chartConfig({ responsive: false }, visualExportInfo));

    // Table: samples checked, the group total, then EVERY substance (not just the drawn ones).
    tableTitle.innerText = fillTokens(cfg.table_title || cfg.title || "What's in the Sample?") + `: ${groupLabel[sel.group]}`;
    const table = document.createElement("table");
    table.setAttribute("class", "mb-0 table table-striped table-bordered table-hover");
    const head = table.insertRow(-1);
    [""].concat(agg.periods).forEach(h => { const th = document.createElement("th"); th.innerText = h; head.appendChild(th); });
    const addRow = (label, series, bold, s) => {
      const tr = table.insertRow(-1); tr.setAttribute("class", "align-middle" + (bold ? " fw-bold" : ""));
      tr.insertCell(-1).innerText = s ? s + methodTag(s) : label;
      agg.periods.forEach(t => {
        const n = (series && series[t]) || 0;
        const pct = pctOf(series, t);
        const cell = tr.insertCell(-1);
        cell.innerText = pct == null ? "–" : `${n} (${pct.toFixed(1)}%)`;
        if (s) {
          const m = methodsOf(s), ftir = (m.ftir && m.ftir[t]) || 0, strip = (m.strip && m.strip[t]) || 0;
          if (ftir && strip) cell.title = `FTIR ${ftir}, test strip ${strip}`;
        }
      });
    };
    const totalsRow = table.insertRow(-1); totalsRow.setAttribute("class", "align-middle fw-bold");
    totalsRow.insertCell(-1).innerText = "Samples checked";
    agg.periods.forEach(t => { totalsRow.insertCell(-1).innerText = String(agg.totals[t]); });
    addRow(anyLabel, groupHits[FOUND_ANY_LABEL], true);
    subs.forEach(s => addRow(s, groupHits[s], false, s));
    makeSortableTable(table);
    tableDiv.innerHTML = "";
    if (cfg.note) {   // the methods footnote sits with the table, so the chart keeps the whole card
      const noteEl = document.createElement("p"); noteEl.className = "table-caption text-start";
      noteEl.innerText = cfg.note;
      tableDiv.appendChild(noteEl);
    }
    tableDiv.appendChild(table);
  }

  // Prime the URL params so masterLoop's trailing syncUrl canonicalizes the address bar (replaceState
  // on first load; a no-op when it already matches), then draw.
  activeRendererParams = selToParams();
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

// ---- Deep-link replay: rebuild drill state from a URL (initial load + Back/Forward) ----

// Reverse a slugified location segment back to a real geo value by matching against a visual's facts.
function reverseSlugGeo(facts, slug) {
  for (const f of facts) {
    if (f.geo != null && urlSegment(f.geo) === slug) return f.geo;
  }
  return null;
}

// Reverse a slugified category segment back to a real category, scoped to one location's count facts.
function reverseSlugCategory(facts, location, slug) {
  for (const f of facts) {
    if (f.geo === location && f.dt === "counts" && f.d2 != null && urlSegment(f.d2) === slug) return f.d2;
  }
  return null;
}

// Pick the year for a level-3 pie drill: the explicit ?y= if it has data for (location, category),
// else the latest year that does.
function resolveYear(facts, location, category, yearParam) {
  const years = [];
  for (const f of facts) {
    if (f.geo === location && f.dt === "counts" && f.d2 === category && f.t != null) {
      if (yearParam != null && String(f.t) === String(yearParam)) return f.t;
      years.push(f.t);
    }
  }
  if (years.length === 0) return null;
  return _sortYears(years).slice(-1)[0];
}

// Resolve a level-1 entry slug to its visual id (used on popstate, which has no server round-trip).
function slugToVisualId(slug) {
  const cfgs = visuals[province] || {};
  for (const vid in cfgs) {
    if (cfgs[vid]["level"] === 1 && cfgs[vid]["slug"] === slug) return vid;
  }
  return null;
}

// Split a deep-link path into its drill segments + ?y= year (null entrySlug => province root).
function parseVisualPath(pathname, search) {
  const prefix = urlBase + "/";
  let rest = pathname.startsWith(prefix) ? pathname.slice(prefix.length) : "";
  const segs = rest.split("/").filter(Boolean).map(decodeURIComponent);
  return {
    entrySlug: segs[0] || null,
    locationSlug: segs[1] || null,
    categorySlug: segs[2] || null,
    year: new URLSearchParams(search || "").get("y"),
  };
}

// Rebuild the drilled view from a parsed URL. Each drill is gated by canDrill + a successful reverse
// slug match; any failure stops at the deepest valid level (the initial replaceState then rewrites the
// URL to the resolved state). entryVid is a level-1 visual id (server-resolved, or slugToVisualId'd).
function replayFromPath(entryVid, locationSlug, categorySlug, yearParam) {
  seedGrainFromUrl();
  // ?period= is the slider position of the map this link lands on, so it is assigned just before
  // that visual renders; the renderer drops a period that isn't on its slider, and no ?period=
  // means the latest.
  const linkedPeriod = new URLSearchParams(window.location.search || "").get("period");
  const show = function (...args) {
    if (linkedPeriod) activePeriods[currentVisual] = linkedPeriod;
    else delete activePeriods[currentVisual];
    masterLoop(...args);
  };
  if (!entryVid || !visuals[province] || !visuals[province][entryVid] || !currentData[entryVid]) {
    currentVisual = visuals["default-visuals"][province];
    show();
    return;
  }
  currentVisual = entryVid;
  route = [];
  lastLocation = null;

  if (!locationSlug) { show(); return; }                       // level 1
  if (!canDrill(province, currentVisual)) { show(); return; }
  // The location lives in the DRILL TARGET's facts, not the entry's: a level-1 map (map_none) has no
  // facts of its own, and even for a heatmap the drilled child carries the same geo. Resolve the slug
  // against next-vis's geo values so map and heatmap entries both drill correctly.
  const nextVid = visuals[province][currentVisual]["next-vis"];
  const location = reverseSlugGeo((currentData[nextVid] || {}).facts || [], locationSlug);
  if (location == null) { show(); return; }                    // unknown region -> stay level 1
  moveUpOneLevel(province);
  if (!categorySlug) { show(location); return; }               // level 2
  if (!canDrill(province, currentVisual)) { show(location); return; }
  const pieFacts = currentData[currentVisual].facts || [];
  const category = reverseSlugCategory(pieFacts, location, categorySlug);
  if (category == null) { show(location); return; }            // unknown category -> stay level 2
  const year = resolveYear(pieFacts, location, category, yearParam);
  moveUpOneLevel(province);
  show(location, year, category);                              // level 3
}

// Seed the time grain, chart type and heatmap measure from ?grain= / ?chart= / ?measure= (deep link
// or Back/Forward). Absent or unknown values reset to the defaults; masterLoop then clamps them to
// what the visual offers.
function seedGrainFromUrl() {
  const params = new URLSearchParams(window.location.search || "");
  const grain = params.get("grain");
  activeGrain = GRAIN_ORDER.includes(grain) ? grain : "year";
  const chart = params.get("chart");
  activeChartType = Object.prototype.hasOwnProperty.call(CHART_TYPE_LABELS, chart) ? chart : null;
  const measure = params.get("measure");
  activeMeasure = Object.prototype.hasOwnProperty.call(HEATMAP_MEASURE_LABELS, measure) ? measure : null;
}

// Back/Forward: re-derive state from the URL without pushing history or reloading (data is already
// client-side). isReplaying makes the replay's masterLoop->syncUrl a no-op, so no feedback loop.
function onVisualPopState() {
  isReplaying = true;
  try {
    const parsed = parseVisualPath(window.location.pathname, window.location.search);
    const entryVid = parsed.entrySlug ? slugToVisualId(parsed.entrySlug)
                                      : visuals["default-visuals"][province];
    replayFromPath(entryVid, parsed.locationSlug, parsed.categorySlug, parsed.year);
  } finally {
    isReplaying = false;
  }
}

// Helper function to reset the count/rates toggle
function resetVisualControl() {
  let dataTypeToggle = document.getElementById("data-type-toggle");
  // reset the count/rate toggle so that
  dataTypeToggle.innerHTML = "";
  // and the time-grain / chart-type toggles (masterLoop rebuilds them for a grain-capable visual)
  clearToggleGroup(document.getElementById("time-grain-toggle"));
  clearToggleGroup(document.getElementById("chart-type-toggle"));
  clearRendererControls();
  // clear any treemap stratifier dropdowns so they don't linger on a non-treemap visual
  let stratifiers = document.getElementById("vis-stratifiers");
  if (stratifiers) stratifiers.innerHTML = "";
  // Reset the treemap's "treemap-layout" class so a later non-treemap visual doesn't inherit its
  // flex/100%-height sizing (the class is set in createVisualTreemap and the stratified-bar renderer,
  // and is never otherwise cleared). resetVisualControl runs at level 1 before the next renderer draws.
  let visDiv = document.getElementById("vis-div");
  if (visDiv) visDiv.className = "";
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

// Render a single table cell, distinguishing the three data states (a genuine 0 is shown as "0",
// never hidden): a reported number -> itself; the SUPPRESSED sentinel -> "Suppressed"; null /
// undefined (not reported) -> "No data".
function formatTableValue(value){
  if (value === SUPPRESSED) return "Suppressed";
  if (value === null || value === undefined || value === "") return "No data";
  return value;
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
// ---------------------------------------------------------------------------
// DAS Explorer (v1/das_explorer.jinja)
// ---------------------------------------------------------------------------
// A row-level explorer over the das_* tables: a Tabulator table with remote
// (server-side) pagination/sort/filter against /api/v1/das/<dataset>/rows, and
// a pivot-style "Visualize" builder that renders /api/v1/das/<dataset>/pivot
// aggregations with Plotly. Bundled after visualGeneration.js in one shared
// script scope, so it can read/write its globals (currentVisual) and use the
// plotly-theme helpers directly. All server-driven config (columns, pivot
// dimensions, measures) arrives via initDasExplorer(cfg) from the template.
// ---------------------------------------------------------------------------

var DAS_API_BASE = "/api/v1/das";
var DAS_CHART_TYPES = [
    { key: "bar", label: "Bar (grouped)" },
    { key: "stacked_bar", label: "Bar (stacked)" },
    { key: "line", label: "Line" },
    { key: "pie", label: "Pie" },
    { key: "heatmap", label: "Heatmap" },
    { key: "map_province", label: "Map (by province)" },
    { key: "map_city", label: "Map (by city)" },
];
// Each map type plots exactly one geographic dimension; the chart-type option only appears
// for datasets whose pivotDims carry that field, and Rows is snapped/validated against it.
var DAS_MAP_ROWS_DIM = { map_province: "province", map_city: "city" };
// Survives HTMX swaps (the bundle loads once per full page load); initDasExplorer
// rebuilds it, destroying any previous Tabulator instance bound to removed DOM.
var dasState = null;

function initDasExplorer(cfg) {
    if (!document.getElementById("das-table")) return;
    // Globals persist across HTMX swaps: a stale currentVisual would make the theme toggle's
    // canaskRedrawCharts() replay a province chart into DOM this page doesn't have.
    if (typeof currentVisual !== "undefined") currentVisual = null;
    if (dasState && dasState.table) {
        try { dasState.table.destroy(); } catch (e) { /* already-removed DOM */ }
    }
    dasState = { cfg: cfg, dataset: Object.keys(cfg.datasets)[0], table: null, pivot: null };
    dasBuildDatasetToggle();
    dasBuildPivotControls();
    dasBuildTable();
}

function dasDatasetCfg() {
    return dasState.cfg.datasets[dasState.dataset];
}

// --------------------------------- table -----------------------------------

function dasBuildDatasetToggle() {
    const host = document.getElementById("das-dataset-toggle");
    host.innerHTML = "";
    Object.entries(dasState.cfg.datasets).forEach(([key, ds]) => {
        const input = document.createElement("input");
        input.type = "radio";
        input.className = "btn-check";
        input.name = "das-dataset";
        input.id = `das-dataset-${key}`;
        input.autocomplete = "off";
        input.checked = key === dasState.dataset;
        input.onchange = function () {
            dasState.dataset = key;
            dasState.pivot = null;
            dasResetChart(document.getElementById("das-pivot-chart"), "");
            document.getElementById("das-pivot-truncated").classList.add("d-none");
            dasSetUnmappedNote("");
            dasBuildPivotControls();
            dasBuildTable();
        };
        const label = document.createElement("label");
        label.className = "btn btn-outline-secondary btn-sm";
        label.setAttribute("for", input.id);
        label.textContent = ds.label;
        host.appendChild(input);
        host.appendChild(label);
    });
}

// Client-side mirror of the server's filter expression grammar (data_viz/das_filter_expr.py):
// AND / OR / NOT, parentheses, quoted phrases, adjacent bare words merging into one phrase,
// precedence NOT > AND > OR, plus a standalone * wildcard on fields listed in the dataset
// config's `wildcards` (allowStar). Purely a typing-time convenience -- it never decides
// results, only whether an expression is worth sending (the server 400s on anything malformed
// regardless).
function dasAllowStar(field) {
    return (dasDatasetCfg().wildcards || []).includes(field);
}

function dasValidExpression(text, allowStar) {
    if (text.length > 300) return false;
    const tokens = [];
    let words = [];
    const flush = () => { if (words.length) { tokens.push(["phrase", words.join(" ")]); words = []; } };
    let i = 0;
    while (i < text.length) {
        const ch = text[i];
        if (/\s/.test(ch)) { i++; }
        else if (ch === "(" || ch === ")") { flush(); tokens.push(["paren", ch]); i++; }
        else if (ch === '"') {
            const end = text.indexOf('"', i + 1);
            if (end === -1 || !text.slice(i + 1, end).trim()) return false;
            flush(); tokens.push(["phrase", text.slice(i + 1, end).trim()]); i = end + 1;
        } else {
            let end = i;
            while (end < text.length && !/[\s()"]/.test(text[end])) end++;
            const word = text.slice(i, end);
            const upper = word.toUpperCase();
            if (word === "*") { flush(); tokens.push(["star", "*"]); }
            else if (upper === "AND" || upper === "OR" || upper === "NOT") { flush(); tokens.push(["op", upper]); }
            else words.push(word);
            i = end;
        }
    }
    flush();
    if (!tokens.length) return false;
    let pos = 0;
    const peek = () => (pos < tokens.length ? tokens[pos] : [null, null]);
    function parseOr(depth) {
        if (!parseAnd(depth)) return false;
        while (peek()[0] === "op" && peek()[1] === "OR") { pos++; if (!parseAnd(depth)) return false; }
        return true;
    }
    function parseAnd(depth) {
        // An infix NOT implies the AND ("a NOT b" == "a AND NOT b"); parseNot consumes it.
        if (!parseNot(depth)) return false;
        while (peek()[0] === "op" && (peek()[1] === "AND" || peek()[1] === "NOT")) {
            if (peek()[1] === "AND") pos++;
            if (!parseNot(depth)) return false;
        }
        return true;
    }
    function parseNot(depth) {
        if (peek()[0] === "op" && peek()[1] === "NOT") { pos++; return parseNot(depth); }
        return parseAtom(depth);
    }
    function parseAtom(depth) {
        const [kind, value] = peek();
        if (kind === "paren" && value === "(") {
            if (depth >= 10) return false;
            pos++;
            if (!parseOr(depth + 1)) return false;
            if (!(peek()[0] === "paren" && peek()[1] === ")")) return false;
            pos++;
            return true;
        }
        if (kind === "phrase") { pos++; return true; }
        if (kind === "star") { pos++; return allowStar === true; }
        return false;
    }
    return parseOr(0) && pos === tokens.length;
}

// Serialize filters into f_<field> params: multiselect dropdowns hold arrays (one repeated param
// per picked value -> OR'd server-side), text expressions that don't parse are dropped (the
// server would only 400 on them). Shared by the rows URL builder and the pivot fetch.
function dasAppendFilterParams(query, filters) {
    const kinds = dasDatasetCfg().kinds || {};
    filters.forEach(f => {
        const values = Array.isArray(f.value) ? f.value : [f.value];
        values.forEach(value => {
            if (value === "" || value == null) return;
            if (kinds[f.field] === "text" && !dasValidExpression(String(value).trim(), dasAllowStar(f.field))) return;
            query.append(`f_${f.field}`, value);
        });
    });
}

// Serialize Tabulator's remote params ourselves (page/size/sort=field.dir/f_<field>=value): the
// server parser stays trivial and we don't depend on Tabulator's default nested encoding.
function dasRowsUrl(url, config, params) {
    const query = new URLSearchParams({ page: params.page, size: params.size });
    (params.sort || []).forEach(s => query.append("sort", `${s.field}.${s.dir}`));
    dasAppendFilterParams(query, params.filter || []);
    return `${url}?${query.toString()}`;
}

function dasBuildTable() {
    const ds = dasDatasetCfg();
    document.getElementById("das-table-title").textContent = ds.label;
    if (dasState.table) {
        try { dasState.table.destroy(); } catch (e) { /* already-removed DOM */ }
    }
    // Column defs arrive as JSON, but multiselect list filters need a function attached: the
    // default empty-check treats a deselect-all [] as a live filter, which would never clear.
    const columns = ds.columns.map(col =>
        (ds.kinds || {})[col.field] === "select" && col.headerFilterParams && col.headerFilterParams.multiselect
            ? Object.assign({}, col, { headerFilterEmptyCheck: v => !v || (Array.isArray(v) && !v.length) })
            : col);
    dasState.table = new Tabulator("#das-table", {
        height: "60vh",
        layout: "fitDataStretch",
        movableColumns: true,
        placeholder: "No rows match the current filters.",
        columns: columns,
        pagination: true,
        paginationMode: "remote",
        paginationSize: 50,
        paginationSizeSelector: [25, 50, 100, 200],
        sortMode: "remote",
        filterMode: "remote",
        ajaxURL: `${DAS_API_BASE}/${dasState.dataset}/rows`,
        ajaxURLGenerator: dasRowsUrl,
        // Don't fire a request the server would 400: while a text filter holds a (usually
        // mid-typing) invalid expression, keep showing the last good result. Returning false
        // aborts the request before it leaves.
        ajaxRequesting: function () {
            const kinds = dasDatasetCfg().kinds || {};
            const filters = dasState.table ? dasState.table.getHeaderFilters() : [];
            return !filters.some(f =>
                kinds[f.field] === "text" && f.value && !dasValidExpression(String(f.value).trim(), dasAllowStar(f.field)));
        },
        // Surface the (filtered) total row count next to the dataset label; last_row rides the
        // rows endpoint's response alongside Tabulator's data/last_page contract.
        ajaxResponse: function (url, params, response) {
            if (response.last_row != null) {
                document.getElementById("das-table-title").textContent =
                    `${ds.label} — ${Number(response.last_row).toLocaleString()} rows`;
            }
            return response;
        },
    });
    dasState.table.on("dataLoadError", function (error) {
        const message = String(error);
        let hint = "";
        if (message.includes("429")) hint = " (too many requests, wait a moment)";
        else if (message.includes("400")) hint = " (check your filter syntax)";
        document.getElementById("das-table-title").textContent = `${ds.label} — data failed to load${hint}`;
    });
    dasWireFilterValidation();
}

// Live red-ring feedback while typing an expression into a text column's header filter. Delegated
// from the (page-stable) #das-table host so it survives table rebuilds; attached once per element.
function dasWireFilterValidation() {
    const host = document.getElementById("das-table");
    if (!host || host.dataset.dasFilterValidation) return;
    host.dataset.dasFilterValidation = "true";
    host.addEventListener("input", function (event) {
        const input = event.target;
        if (!input.matches(".tabulator-header-filter input")) return;
        const cell = input.closest(".tabulator-col");
        const field = cell ? cell.getAttribute("tabulator-field") : null;
        if (!field || (dasDatasetCfg().kinds || {})[field] !== "text") return;
        const value = input.value.trim();
        input.classList.toggle("das-filter-invalid", value !== "" && !dasValidExpression(value, dasAllowStar(field)));
    });
}

// --------------------------------- pivot -----------------------------------

function dasMakeSelect(host, labelText, options, id) {
    const wrap = document.createElement("div");
    const label = document.createElement("label");
    label.className = "form-label mb-1 small";
    label.textContent = labelText;
    label.setAttribute("for", id);
    const select = document.createElement("select");
    select.className = "form-select form-select-sm";
    select.id = id;
    options.forEach(([value, text]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        select.appendChild(option);
    });
    wrap.appendChild(label);
    wrap.appendChild(select);
    host.appendChild(wrap);
    return select;
}

function dasBuildPivotControls() {
    const host = document.querySelector(".das-pivot-controls");
    if (!host) return;
    host.innerHTML = "";
    const ds = dasDatasetCfg();
    const dims = ds.pivotDims.map(d => [d.field, d.label]);
    dasMakeSelect(host, "Rows", dims, "das-pivot-rows");
    dasMakeSelect(host, "Columns (split by)", [["", "None"]].concat(dims), "das-pivot-cols");
    const measureSelect = dasMakeSelect(host, "Measure", ds.measures.map(m => [m.field, m.label]), "das-pivot-measure");
    if (ds.measures.length < 2) measureSelect.parentElement.classList.add("d-none");
    const dimFields = ds.pivotDims.map(d => d.field);
    const chartTypes = DAS_CHART_TYPES.filter(t =>
        !DAS_MAP_ROWS_DIM[t.key] || dimFields.includes(DAS_MAP_ROWS_DIM[t.key]));
    const typeSelect = dasMakeSelect(host, "Chart type", chartTypes.map(t => [t.key, t.label]), "das-pivot-type");
    // Picking a map type snaps Rows to its geographic dimension (the guard in dasApplyPivot
    // still enforces it if the user changes Rows afterwards).
    typeSelect.onchange = function () {
        const required = DAS_MAP_ROWS_DIM[typeSelect.value];
        if (required) document.getElementById("das-pivot-rows").value = required;
    };

    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "btn btn-primary btn-sm";
    apply.innerHTML = '<i class="bi bi-play-fill"></i> Build chart';
    apply.onclick = dasApplyPivot;
    host.appendChild(apply);
}

// Replacing the chart div's contents must go through Plotly.purge first: once a plot lives there,
// wiping its DOM with innerHTML alone leaves Plotly's internal state attached to the element and
// the next Plotly.react against it silently renders nothing (the "second Build chart does nothing"
// failure mode).
function dasResetChart(chart, html) {
    Plotly.purge(chart);
    chart.innerHTML = html;
}

async function dasApplyPivot() {
    const rows = document.getElementById("das-pivot-rows").value;
    const cols = document.getElementById("das-pivot-cols").value;
    const measure = document.getElementById("das-pivot-measure").value;
    const chartType = document.getElementById("das-pivot-type").value;
    const chart = document.getElementById("das-pivot-chart");
    dasSetUnmappedNote("");

    if (chartType === "heatmap" && !cols) {
        dasResetChart(chart, '<p class="text-warning text-center py-3">A heatmap needs a Columns dimension.</p>');
        return;
    }
    const mapRowsDim = DAS_MAP_ROWS_DIM[chartType];
    if (mapRowsDim) {
        const pivotDims = dasDatasetCfg().pivotDims;
        if (rows !== mapRowsDim) {
            const dim = pivotDims.find(d => d.field === mapRowsDim);
            dasResetChart(chart, '<p class="text-warning text-center py-3"></p>');
            chart.firstChild.textContent = `This map needs Rows = ${dim ? dim.label : mapRowsDim}.`;
            return;
        }
        // Columns is the map's time slider: a frame per month or year, nothing else.
        const colSpec = cols ? pivotDims.find(d => d.field === cols) : null;
        if (colSpec && colSpec.kind !== "date") {
            dasResetChart(chart, '<p class="text-warning text-center py-3">Map charts can only be split by a month or year dimension.</p>');
            return;
        }
    }
    const query = new URLSearchParams({ rows: rows, measure: measure });
    // Pie charts read a single dimension; every other type splits series by the Columns choice.
    if (cols && chartType !== "pie") query.append("cols", cols);
    // A map plots every place, not a top-N: opt up from the default 40-row clip.
    if (chartType === "map_city") query.append("rows_limit", "1000");
    // The chart respects whatever the user has narrowed the table to -- but not while a text
    // filter holds an invalid expression (its input is already ringed red).
    const headerFilters = dasState.table ? dasState.table.getHeaderFilters() : [];
    const kinds = dasDatasetCfg().kinds || {};
    if (headerFilters.some(f => kinds[f.field] === "text" && f.value && !dasValidExpression(String(f.value).trim(), dasAllowStar(f.field)))) {
        dasResetChart(chart, '<p class="text-warning text-center py-3">Fix the invalid filter expression (outlined in red in the table) first.</p>');
        return;
    }
    dasAppendFilterParams(query, headerFilters);

    dasResetChart(chart, '<div class="skeleton skeleton-chart" role="status" aria-label="Building chart"></div>');
    try {
        const [response] = await Promise.all([
            fetch(`${DAS_API_BASE}/${dasState.dataset}/pivot?${query.toString()}`,
                  { signal: AbortSignal.timeout(10000) }),
            dasEnsureGeoAssets(chartType),
        ]);
        if (response.status === 429) {
            dasResetChart(chart, '<p class="text-warning text-center py-3">You\'re building charts a little too quickly. Wait a moment and try again.</p>');
            return;
        }
        if (response.status === 400) {
            // The server's message can echo pieces of the user's expression -- textContent, never HTML.
            const detail = await response.json().catch(() => null);
            dasResetChart(chart, '<p class="text-warning text-center py-3"></p>');
            chart.firstChild.textContent = (detail && detail.error) || "Check your filter syntax and try again.";
            return;
        }
        if (!response.ok) throw new Error(`pivot request failed: ${response.status}`);
        const pivot = await response.json();
        chart.innerHTML = "";
        dasState.pivot = { data: pivot, chartType: chartType };
        dasRenderPivot();
    } catch (error) {
        console.error("DAS pivot failed:", error);
        dasResetChart(chart, '<p class="text-danger text-center py-3">Sorry, that chart couldn\'t be built. Please try again.</p>');
    }
}

// Map assets, fetched once per full page load and cached (they survive dataset switches and
// theme redraws). provinceNames is a code -> display-name lookup built from the geojson.
var dasGeoAssets = { provinces: null, provinceNames: null, cities: null };

async function dasEnsureGeoAssets(chartType) {
    if (chartType === "map_province" && !dasGeoAssets.provinces) {
        const response = await fetch("/static/assets/geojsons/canada-provinces.geojson",
                                     { signal: AbortSignal.timeout(10000) });
        if (!response.ok) throw new Error(`province geojson fetch failed: ${response.status}`);
        const geojson = await response.json();
        dasGeoAssets.provinceNames = {};
        geojson.features.forEach(f => { dasGeoAssets.provinceNames[f.properties.code] = f.properties.name; });
        dasGeoAssets.provinces = geojson;
    }
    if (chartType === "map_city" && !dasGeoAssets.cities) {
        const response = await fetch("/static/assets/das_city_coords.json",
                                     { signal: AbortSignal.timeout(10000) });
        if (!response.ok) throw new Error(`city coords fetch failed: ${response.status}`);
        dasGeoAssets.cities = await response.json();
    }
}

// The honest-gaps line under the chart (places the map can't draw). Names come from the DB, so
// textContent only. Empty text hides it.
function dasSetUnmappedNote(text) {
    const note = document.getElementById("das-pivot-unmapped");
    if (!note) return;
    note.textContent = text || "";
    note.classList.toggle("d-none", !text);
}

// One {label, values: {rowKey: value}} per Columns period (the slider frames), or a single
// all-time frame when Columns is None. Null cells are omitted -- a province renders unfilled and
// a bubble is absent, never coerced to zero.
function dasMapFrames(p) {
    const frame = j => {
        const values = {};
        p.rows.forEach((r, i) => { if (p.cells[i][j] != null) values[r] = p.cells[i][j]; });
        return values;
    };
    if (!p.cols.length) return [{ label: null, values: frame(0) }];
    return p.cols.map((col, j) => ({ label: col, values: frame(j) }));
}

// Shared geo layout: fixed Canada framing (fitbounds would reframe as slider frames or filters
// change which places have data), plus the slider when there is more than one frame. Only the
// active frame's trace is visible; steps restyle `visible` exactly like the V1 heatmap slider.
function dasMapLayout(layout, frames, t) {
    layout.margin = { t: 30, r: 20, b: frames.length > 1 ? 80 : 20, l: 20 };
    layout.geo = {
        scope: "north america",
        projection: { type: "conic conformal", rotation: { lon: -96 } },
        lataxis: { range: [40, 84] },
        lonaxis: { range: [-142, -50] },
        showcoastlines: false,
        showlakes: false,
        showland: true,
        landcolor: t.grid,
        countrycolor: t.border,
        subunitcolor: t.border,
    };
    if (frames.length > 1) {
        layout.sliders = [{
            active: frames.length - 1,
            currentvalue: { font: { color: t.font } },
            font: { color: t.tick },
            bgcolor: t.grid,
            bordercolor: t.border,
            tickcolor: t.tick,
            activebgcolor: t.accent,
            steps: frames.map((f, j) => ({
                label: f.label,
                method: "restyle",
                args: ["visible", frames.map((_, k) => k === j)],
            })),
        }];
    }
}

function dasMapColorbar(p, t) {
    return {
        thickness: 15,
        outlinewidth: 0,
        title: { text: p.measure, side: "right", font: { color: t.font } },
        tickfont: { color: t.tick },
    };
}

function dasRenderPivot() {
    const chart = document.getElementById("das-pivot-chart");
    if (!chart || !dasState || !dasState.pivot) return;
    const p = dasState.pivot.data;
    const chartType = dasState.pivot.chartType;
    const colors = canaskColorway();
    const layout = { height: 420, margin: { t: 30, r: 20, b: 80, l: 60 } };
    let traces;
    dasSetUnmappedNote("");

    if (chartType === "pie") {
        traces = [{
            type: "pie",
            labels: p.rows,
            values: p.cells.map(row => row[0]),
            hole: 0.4,
            marker: { colors: colors },
            textinfo: "label+percent",
        }];
    } else if (chartType === "heatmap") {
        traces = [{
            type: "heatmap",
            x: p.cols,
            y: p.rows,
            z: p.cells,
            colorscale: document.documentElement.getAttribute("data-theme") === "dark" ? "Cividis" : "YlOrRd",
            hoverongaps: false,
        }];
        layout.yaxis = { automargin: true };
        layout.xaxis = { automargin: true };
    } else if (chartType === "map_province") {
        const t = canaskChartTheme();
        const frames = dasMapFrames(p);
        const names = dasGeoAssets.provinceNames || {};
        // The scale is fixed across every frame so the slider never rescales the colors.
        const allValues = frames.flatMap(f => Object.values(f.values));
        const zmin = Math.min(0, ...allValues);
        const zmax = Math.max(0, ...allValues);
        traces = frames.map((f, j) => {
            const codes = Object.keys(f.values).filter(code => code in names);
            return {
                type: "choropleth",
                visible: j === frames.length - 1,
                locationmode: "geojson-id",
                geojson: dasGeoAssets.provinces,
                featureidkey: "properties.code",
                locations: codes,
                z: codes.map(code => f.values[code]),
                zauto: false,
                zmin: zmin,
                zmax: zmax,
                text: codes.map(code => names[code]),
                hovertemplate: "%{text}: %{z}<extra></extra>",
                // Plotly's stock YlOrRd runs dark-red -> pale-yellow, which would paint the
                // BUSIEST province palest; flip it so more samples = darker. Cividis already
                // runs dark -> bright, which is the right way up on the dark theme.
                colorscale: t.dark ? "Cividis" : "YlOrRd",
                reversescale: !t.dark,
                marker: { line: { color: t.border, width: 0.5 } },
                colorbar: dasMapColorbar(p, t),
            };
        });
        dasMapLayout(layout, frames, t);
        // "Unknown" (null province) has no polygon; anything else missing would be a data bug --
        // either way it's surfaced, never silently dropped.
        const skipped = p.rows.filter(r => !(r in names));
        if (skipped.length) {
            dasSetUnmappedNote(skipped.includes("Unknown") && skipped.length === 1
                ? "Results with no recorded province/territory are not shown on the map."
                : `Not shown on the map (no matching region): ${skipped.join("; ")}`);
        }
    } else if (chartType === "map_city") {
        const t = canaskChartTheme();
        const frames = dasMapFrames(p);
        const coords = dasGeoAssets.cities || {};
        const known = p.rows.filter(r => r !== "Unknown" && coords[r]);
        const unmapped = p.rows.filter(r => r !== "Unknown" && !coords[r]);
        // Fixed bubble scale across frames, sized so the busiest city renders ~30px wide.
        const allValues = frames.flatMap(f => known.map(r => f.values[r]).filter(v => v != null));
        const maxValue = Math.max(0, ...allValues);
        const sizeref = maxValue > 0 ? (2 * maxValue) / (30 ** 2) : 1;
        traces = frames.map((f, j) => {
            const keys = known.filter(r => f.values[r] != null);
            return {
                type: "scattergeo",
                visible: j === frames.length - 1,
                mode: "markers",
                lat: keys.map(r => coords[r][0]),
                lon: keys.map(r => coords[r][1]),
                text: keys.map(r => `${r}: ${f.values[r]}`),
                hoverinfo: "text",
                marker: {
                    sizemode: "area",
                    size: keys.map(r => f.values[r]),
                    sizeref: sizeref,
                    sizemin: 3,
                    color: colors[0],
                    opacity: 0.75,
                    line: { color: canaskMarkerLineColor(), width: 1 },
                },
            };
        });
        dasMapLayout(layout, frames, t);
        const notes = [];
        if (unmapped.length) {
            const shown = unmapped.slice(0, 5).join("; ") + (unmapped.length > 5 ? "; …" : "");
            notes.push(`${unmapped.length} ${unmapped.length === 1 ? "city" : "cities"} with no known map location not shown: ${shown}`);
        }
        if (p.rows.includes("Unknown")) notes.push("Results with no recorded city are not shown.");
        if (notes.length) dasSetUnmappedNote(notes.join(" "));
    } else {
        const mode = chartType === "line" ? "scatter" : "bar";
        if (p.cols.length) {
            traces = p.cols.map((col, j) => ({
                type: mode === "scatter" ? "scatter" : "bar",
                mode: mode === "scatter" ? "lines+markers" : undefined,
                name: col,
                x: p.rows,
                y: p.cells.map(row => row[j]),
                marker: { color: colors[j % colors.length] },
            }));
        } else {
            traces = [{
                type: mode === "scatter" ? "scatter" : "bar",
                mode: mode === "scatter" ? "lines+markers" : undefined,
                x: p.rows,
                y: p.cells.map(row => row[0]),
                marker: { color: colors[0] },
            }];
        }
        if (chartType === "stacked_bar") layout.barmode = "stack";
        layout.xaxis = { automargin: true, type: "category" };
        layout.yaxis = { title: { text: p.measure }, rangemode: "tozero" };
    }

    document.getElementById("das-pivot-truncated").classList.toggle("d-none", !p.truncated);
    Plotly.react(chart, traces, themeChartLayout(layout), { displaylogo: false, responsive: true });
}

// Theme toggle hook: canaskRedrawCharts() calls this so the pivot chart's trace colors follow the
// palette (relayout alone re-chromes but can't recolor traces). Re-renders from the cached response.
window.dasRedrawPivot = function () {
    if (dasState && dasState.pivot && document.getElementById("das-pivot-chart")) {
        dasRenderPivot();
    }
};

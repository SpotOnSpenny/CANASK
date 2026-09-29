// ---------------------------------------------------------------------------
// Confluence (v1/confluence.jinja)
// ---------------------------------------------------------------------------
// Overlays DAS seizure counts on one V1 visual of a province. The server
// (/api/v1/confluence/data, data_viz/confluence.py) returns one pre-aligned
// payload: the visual's own fact block, the shared period list, and the DAS
// half cut to match (a series per substance key for flat charts, or counts per
// city for health-authority maps). This file only draws. It is bundled after
// visualGeneration.js and dasExplorer.js in one shared script scope, so it
// reuses their fact selectors (factsToSeries / factsToHeatmap), map helpers
// (dasMapLayout / dasFitMapHeight / dasEnsureGeoAssets) and theme helpers.
// ---------------------------------------------------------------------------

var CONFLUENCE_API = "/api/v1/confluence/data";
// Survives HTMX swaps (the bundle loads once per full page load); initConfluence rebuilds it.
var confluenceState = null;

function initConfluence(cfg) {
    if (!document.getElementById("confluence-chart")) return;
    // seq/controller: only the newest Overlay request may draw (see confluenceApply).
    confluenceState = { cfg: cfg, data: null, geojson: null, geojsons: {}, seq: 0, controller: null };

    const provinceSelect = document.getElementById("confluence-province");
    const slugs = Object.keys(cfg.provinces).sort((a, b) =>
        cfg.provinces[a].label.localeCompare(cfg.provinces[b].label));
    provinceSelect.innerHTML = "";
    if (!slugs.length) {
        confluenceEmptyState("No province visuals are available to pair with the DAS data.");
        document.querySelectorAll(".confluence-controls select, .confluence-controls button, #confluence-expr")
            .forEach(el => { el.disabled = true; });
        return;
    }
    slugs.forEach(slug => provinceSelect.appendChild(new Option(cfg.provinces[slug].label, slug)));
    provinceSelect.onchange = function () { confluenceFillVisuals(); confluenceSyncChips(null); };
    document.getElementById("confluence-visual").onchange = function () { confluenceSyncChips(null); };
    document.querySelectorAll('input[name="confluence-level"]').forEach(radio => {
        radio.onchange = function () { confluenceSyncChips(confluenceCheckedKeys(), radio.value); };
    });
    const expr = document.getElementById("confluence-expr");
    expr.oninput = function () {
        expr.classList.toggle("das-filter-invalid", !dasValidExpression(expr.value, true));
    };
    expr.onkeydown = function (event) { if (event.key === "Enter") confluenceUserApply(); };
    document.getElementById("confluence-build").onclick = confluenceUserApply;

    confluenceFillVisuals();
    if (!confluenceReplayFromUrl()) confluenceSyncChips(null);
}

// --------------------------------- controls ----------------------------------

function confluenceProvinceCfg() {
    return confluenceState.cfg.provinces[document.getElementById("confluence-province").value];
}

function confluenceVisualCfg() {
    const id = document.getElementById("confluence-visual").value;
    return (confluenceProvinceCfg().visuals || []).find(v => v.id === id) || null;
}

function confluenceLevel() {
    const checked = document.querySelector('input[name="confluence-level"]:checked');
    return checked ? checked.value : "group";
}

function confluenceFillVisuals() {
    const select = document.getElementById("confluence-visual");
    select.innerHTML = "";
    confluenceProvinceCfg().visuals.forEach(v => select.appendChild(new Option(v.menu_name, v.id)));
}

// Move a set of keys between the two levels: group -> its family, family -> its member groups.
function confluenceConvertKeys(keys, toLevel) {
    const cfg = confluenceState.cfg;
    const out = new Set();
    keys.forEach(key => {
        if (toLevel === "family") {
            out.add(cfg.groups[key] ? cfg.groups[key].family : key);
        } else if (cfg.families[key]) {
            cfg.families[key].members.forEach(m => out.add(m));
        } else {
            out.add(key);
        }
    });
    return out;
}

// Rebuild the substance chips for the current level. `preset` (a Set/array of keys at any level)
// wins; otherwise the chosen visual's own resolved terms pre-check the chips. `level` lets a
// level-radio change pass its new value before the DOM query would see it.
function confluenceSyncChips(preset, level) {
    level = level || confluenceLevel();
    const cfg = confluenceState.cfg;
    const visual = confluenceVisualCfg();
    const source = preset ? Array.from(preset) : (visual ? visual.terms_resolved : []);
    const checked = confluenceConvertKeys(source, level);
    const universe = level === "family" ? cfg.families : cfg.groups;
    const host = document.getElementById("confluence-chips");
    host.innerHTML = "";
    Object.keys(universe).forEach(key => {
        const input = document.createElement("input");
        input.type = "checkbox";
        input.className = "btn-check";
        input.id = `confluence-key-${key}`;
        input.value = key;
        input.autocomplete = "off";
        input.checked = checked.has(key);
        const label = document.createElement("label");
        label.className = "btn btn-outline-secondary btn-sm";
        label.setAttribute("for", input.id);
        label.textContent = universe[key].label;
        host.appendChild(input);
        host.appendChild(label);
    });
}

function confluenceCheckedKeys() {
    return Array.from(document.querySelectorAll("#confluence-chips input:checked")).map(i => i.value);
}

// Deep links: ?province=&visual=&groups=&level=&basis=&expr= (written by confluenceApply).
// Returns true when it found a valid pairing and kicked off the fetch. A link that no longer
// resolves (or names unknown substances) says so rather than quietly showing something else.
function confluenceReplayFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const cfg = confluenceState.cfg;
    const province = params.get("province");
    if (!province) return false;
    if (!cfg.provinces[province]) {
        confluenceSetNote("confluence-link-note", "The linked province isn't available, so the default pairing is shown instead.");
        return false;
    }
    document.getElementById("confluence-province").value = province;
    confluenceFillVisuals();
    const visual = params.get("visual");
    if (!visual || !cfg.provinces[province].visuals.some(v => v.id === visual)) {
        confluenceSetNote("confluence-link-note", "The linked visual isn't available for this province. Pick one and press Overlay.");
        return false;
    }
    document.getElementById("confluence-visual").value = visual;
    const level = cfg.levels.includes(params.get("level")) ? params.get("level") : "group";
    document.getElementById(`confluence-level-${level}`).checked = true;
    const basis = params.get("basis");
    if (cfg.bases.includes(basis)) document.getElementById("confluence-basis").value = basis;
    document.getElementById("confluence-expr").value = params.get("expr") || "";
    let groups = null;
    if (params.has("groups")) {
        const linked = params.get("groups").split(",").filter(k => k);
        groups = linked.filter(k => cfg.groups[k] || cfg.families[k]);
        const dropped = linked.filter(k => !groups.includes(k));
        if (dropped.length) {
            confluenceSetNote("confluence-link-note", `Some linked substances aren't recognised and were dropped: ${dropped.join(", ")}.`);
        }
    }
    confluenceSyncChips(groups, level);
    confluenceApply();
    return true;
}

// ----------------------------------- fetch -----------------------------------

async function confluenceEnsureGeojson(slug) {
    const cache = confluenceState.geojsons;
    if (!(slug in cache)) {
        const response = await fetch(`/static/assets/geojsons/${slug}.geojson`,
                                     { signal: AbortSignal.timeout(10000) });
        // A missing or stub file (some provinces have no health-authority polygons) is a real
        // state the page must explain, not an error. Anything else is a failure: don't cache it,
        // so the next Overlay retries.
        if (response.status === 404) cache[slug] = null;
        else if (!response.ok) throw new Error(`geojson fetch failed: ${response.status}`);
        else cache[slug] = await response.json();
    }
    return cache[slug];
}

function confluenceEmptyState(message) {
    const chart = document.getElementById("confluence-chart");
    dasResetChart(chart, "");
    const p = document.createElement("p");
    p.className = "text-muted text-center py-5 mb-0";
    p.textContent = message;
    chart.appendChild(p);
    document.getElementById("confluence-table").innerHTML = "";
    document.getElementById("confluence-table-title").textContent = "";
}

// The request's abort signal: superseded OR timed out. AbortSignal.any is recent (2023-24), so
// older browsers fall back to a manual timeout on the same controller.
function confluenceSignal(controller, ms) {
    if (AbortSignal.any) return AbortSignal.any([controller.signal, AbortSignal.timeout(ms)]);
    setTimeout(() => controller.abort(), ms);
    return controller.signal;
}

// The Overlay button / Enter: a fresh user choice supersedes any note about the incoming link.
function confluenceUserApply() {
    confluenceSetNote("confluence-link-note", "");
    confluenceApply();
}

async function confluenceApply() {
    const state = confluenceState;
    const visual = confluenceVisualCfg();
    if (!visual) return;
    const expr = document.getElementById("confluence-expr").value.trim();
    if (expr && !dasValidExpression(expr, true)) {
        document.getElementById("confluence-expr").classList.add("das-filter-invalid");
        return;
    }
    const province = document.getElementById("confluence-province").value;
    const level = confluenceLevel();
    const keys = confluenceCheckedKeys();
    const params = new URLSearchParams({
        province: province, visual: visual.id, level: level,
        basis: document.getElementById("confluence-basis").value,
    });
    if (keys.length) params.set("groups", keys.join(","));
    if (expr) params.set("expr", expr);

    // Only the newest request may draw: abort the one in flight, and drop any response that
    // resolves after a newer Overlay (or a page re-init) has started.
    const seq = ++state.seq;
    if (state.controller) state.controller.abort();
    state.controller = null;
    const stale = () => state !== confluenceState || seq !== state.seq;
    // Drop the previous overlay before fetching: a failed re-apply must not leave a stale
    // payload for the theme/resize redraw hooks to replay over the error message.
    state.data = null;
    ["confluence-incomplete", "confluence-truncated", "confluence-undated", "confluence-unmapped"]
        .forEach(id => document.getElementById(id).classList.add("d-none"));
    if (keys.length > state.cfg.maxKeys) {
        confluenceEmptyState(`Pick at most ${state.cfg.maxKeys} substances.`);
        return;
    }
    const controller = new AbortController();
    state.controller = controller;
    const chart = document.getElementById("confluence-chart");
    dasResetChart(chart, '<div class="skeleton skeleton-chart"></div>');
    const needsMap = visual.shape === "geo_series";
    try {
        const [response, geojson] = await Promise.all([
            fetch(`${CONFLUENCE_API}?${params.toString()}`,
                  { signal: confluenceSignal(controller, 15000) }),
            needsMap
                ? Promise.all([dasEnsureGeoAssets("map_city"), confluenceEnsureGeojson(province)]).then(r => r[1])
                : Promise.resolve(null),
        ]);
        if (stale()) return;
        if (response.status === 429) {
            confluenceEmptyState("Too many requests. Please wait a moment and try again.");
            return;
        }
        if (response.status === 400) {
            const body = await response.json();
            if (stale()) return;
            confluenceEmptyState(body.error || "That overlay couldn't be built.");
            return;
        }
        if (response.status === 403) {
            confluenceEmptyState("You no longer have access to this data. Try signing in again.");
            return;
        }
        if (response.status === 404) {
            confluenceEmptyState("That province or visual is no longer available. Pick another.");
            return;
        }
        if (!response.ok) throw new Error(`confluence fetch failed: ${response.status}`);
        const data = await response.json();
        if (stale()) return;
        state.data = data;
        state.geojson = geojson;
        // Plotly.react draws beside existing children rather than replacing them, so the
        // skeleton must go before the first render (dasApplyPivot does the same).
        chart.innerHTML = "";
        window.history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
        confluenceRender();
    } catch (error) {
        if (stale()) return;   // superseded (and aborted) by a newer Overlay
        console.error("Confluence failed:", error);
        confluenceEmptyState("Sorry, that overlay couldn't be built. Please try again.");
    }
}

// ---------------------------------- render -----------------------------------

function confluenceSetNote(id, text) {
    const note = document.getElementById(id);
    if (!note) return;
    note.textContent = text || "";
    note.classList.toggle("d-none", !text);
}

// The DAS half's colour: one that reads against both the V1 colorway and the map colour scale.
function confluenceDasColor(index, t) {
    const palette = t.dark ? ["#fb923c", "#f472b6", "#a3e635", "#22d3ee", "#facc15", "#c084fc", "#f87171", "#34d399"]
                           : ["#2563eb", "#db2777", "#16a34a", "#0891b2", "#ca8a04", "#7c3aed", "#dc2626", "#0d9488"];
    return palette[index % palette.length];
}

// The visual's series aligned to the shared period axis, raw values kept (a number, the
// "Suppr." sentinel, or null for no fact): {dataType, series: [{key, values: {period: raw}}]}.
// Both the chart and the table read from this so they can't drift.
function confluenceAlignedSeries(d) {
    const series = factsToSeries(d.visual.facts, d.visual.key_kind);
    const dataType = series.counts ? "counts" : Object.keys(series)[0];
    const block = series[dataType] || { x: [] };
    const out = [];
    Object.keys(block).filter(k => k !== "x").forEach(key => {
        const values = {};
        block.x.forEach((x, j) => { values[String(x)] = block[key][j]; });
        out.push({ key: key, values: values });
    });
    return { dataType: dataType, series: out };
}

// A heatmap area's raw value for one period (null when the area has no fact for it).
function confluenceHeatValue(heat, geo, period) {
    const idx = heat[geo].x.map(String).indexOf(period);
    return idx >= 0 ? heat[geo].y[idx] : null;
}

function confluenceRender() {
    const state = confluenceState;
    const d = state && state.data;
    const chart = document.getElementById("confluence-chart");
    if (!d || !chart) return;
    const title = `${d.visual_label} · DAS samples (${d.das.basis} date, ${d.province.label})`;
    document.getElementById("confluence-chart-title").textContent = title;

    const incomplete = d.periods.filter(p => !p.das_complete).map(p => p.key);
    confluenceSetNote("confluence-incomplete", incomplete.length
        ? `DAS counts for ${incomplete.join(", ")} are incomplete (DAS doesn't fully cover those periods).`
        : "");
    // Date-kind pivots keep the newest periods, so a clip drops the oldest ones; a city pivot can
    // also drop its smallest cities.
    confluenceSetNote("confluence-truncated", !d.das.truncated ? ""
        : d.das.mode === "cities"
            ? "Some DAS results were clipped (the smallest cities or the oldest periods). Narrow the advanced filter to see the rest."
            : "Some of the oldest DAS periods were clipped.");
    const undated = Object.entries(d.das.undated || {}).filter(entry => entry[1] > 0);
    const keyLabel = key => (d.das.keys.find(k => k.key === key) || { label: key }).label;
    confluenceSetNote("confluence-undated", !undated.length ? ""
        : d.das.mode === "cities"
            ? `${undated[0][1]} DAS samples have no ${d.das.basis} date and aren't shown.`
            : `DAS samples with no ${d.das.basis} date aren't plotted (${undated.map(([k, n]) => `${keyLabel(k)}: ${n}`).join(", ")}).`);
    confluenceSetNote("confluence-unmapped", "");

    const about = document.getElementById("confluence-about-visual");
    if (about && d.visual.data_source) about.innerHTML = buildAboutDataHTML(d.visual.data_source);

    if (!d.periods.length) {
        const cov = d.das.coverage;
        confluenceEmptyState(cov
            ? `This visual's time periods don't overlap the DAS data (DAS covers ${cov.first_month} to ${cov.last_month}).`
            : "No DAS data has been ingested yet.");
        return;
    }
    if (d.das.mode === "cities") confluenceRenderCities(d, chart);
    else confluenceRenderSeries(d, chart);
    confluenceRenderTable(d);
}

// Flat visual + DAS series: the visual's own series on the left axis, DAS keys dashed on the
// right. Periods DAS hasn't finished reporting get hollow markers under a shaded band.
function confluenceRenderSeries(d, chart) {
    const t = canaskChartTheme();
    const colors = canaskColorway();
    const periods = d.periods.map(p => p.key);
    const aligned = confluenceAlignedSeries(d);
    const dataType = aligned.dataType;
    const options = d.visual.visual_options || {};
    const isBar = d.visual.chart_type === "bar";
    const traces = [];
    aligned.series.forEach((s, i) => {
        // Only numbers plot; the "Suppr." sentinel and missing facts are gaps here (the table
        // still shows the sentinel verbatim).
        const y = periods.map(p => (typeof s.values[p] === "number" ? s.values[p] : null));
        if (!y.some(v => v != null && v !== 0)) return;   // like the province page: hide empty series
        traces.push({
            type: isBar ? "bar" : "scatter",
            mode: isBar ? undefined : "lines+markers",
            name: s.key,
            x: periods,
            y: y,
            marker: { color: colors[i % colors.length] },
            connectgaps: false,
            legendgroup: "visual",
            legendgrouptitle: { text: dataType.charAt(0).toUpperCase() + dataType.slice(1) },
        });
    });
    d.das.keys.forEach((k, i) => {
        const values = d.das.series[k.key] || {};
        const color = confluenceDasColor(i, t);
        traces.push({
            type: "scatter",
            mode: "lines+markers",
            name: `DAS: ${k.label}`,
            x: periods,
            y: periods.map(p => (p in values ? values[p] : null)),
            yaxis: "y2",
            line: { dash: "dash", color: color, width: 2 },
            marker: {
                symbol: d.periods.map(p => (p.das_complete ? "diamond" : "diamond-open")),
                size: 9, color: color, line: { color: color, width: 2 },
            },
            connectgaps: false,
            legendgroup: "das",
            legendgrouptitle: { text: "DAS samples" },
        });
    });
    // One shaded band per run of not-yet-complete periods (category axes take index positions).
    const shapes = [];
    let start = null;
    d.periods.forEach((p, i) => {
        if (!p.das_complete && start === null) start = i;
        const end = !p.das_complete && i === d.periods.length - 1 ? i : (p.das_complete ? i - 1 : null);
        if (start !== null && end !== null && (p.das_complete || i === d.periods.length - 1)) {
            shapes.push({ type: "rect", xref: "x", yref: "paper", x0: start - 0.5, x1: end + 0.5,
                          y0: 0, y1: 1, fillcolor: t.accent, opacity: 0.08, line: { width: 0 }, layer: "below" });
            start = null;
        }
    });
    const layout = {
        height: 460,
        margin: { t: 30, r: 70, b: 80, l: 60 },
        barmode: "group",
        xaxis: { type: "category", automargin: true },
        yaxis: { title: { text: options[`${dataType}-y-axis-title`] || dataType }, rangemode: "tozero" },
        // themeChartLayout only themes xaxis/yaxis, so the second axis is dressed here.
        yaxis2: {
            title: { text: "DAS samples", font: { color: t.font } },
            overlaying: "y", side: "right", rangemode: "tozero", showgrid: false,
            tickfont: { color: t.tick }, linecolor: t.axisLine, zerolinecolor: t.zero,
        },
        legend: { orientation: "h", x: 0, y: -0.18, xanchor: "left", yanchor: "top" },
        shapes: shapes,
        hovermode: "x unified",
    };
    Plotly.react(chart, traces, themeChartLayout(layout), { displaylogo: false, responsive: true });
}

// Health-authority map + DAS bubbles: per period one choropleth (the visual's counts) and one
// scattergeo (DAS samples by city), only the active period's pair visible; the slider steps both.
function confluenceRenderCities(d, chart) {
    const geojson = confluenceState.geojson;
    if (!geojson || !Array.isArray(geojson.features) || !geojson.features.length) {
        confluenceEmptyState("Map data is unavailable for this province.");
        return;
    }
    const t = canaskChartTheme();
    const periods = d.periods.map(p => p.key);
    const heat = factsToHeatmap(d.visual.facts, d.visual.key_kind);
    const names = new Set(geojson.features.map(f => f.properties.ENGNAME));
    const unmatchedAreas = Object.keys(heat).filter(geo => !names.has(geo)).sort();
    const frames = periods.map(p => {
        const locations = [], z = [];
        Object.keys(heat).forEach(geo => {
            if (!names.has(geo)) return;
            const value = confluenceHeatValue(heat, geo, p);
            if (typeof value === "number") { locations.push(geo); z.push(value); }
        });
        return { label: p, locations: locations, z: z };
    });
    const allZ = frames.flatMap(f => f.z);
    const zmin = Math.min(0, ...allZ), zmax = Math.max(0, ...allZ);

    const coords = dasGeoAssets.cities || {};
    const cityKeys = Object.keys(d.das.cities).filter(c => c !== "Unknown");
    const known = cityKeys.filter(c => coords[c]);
    const unmapped = cityKeys.filter(c => !coords[c]);
    const allValues = known.flatMap(c => Object.values(d.das.cities[c]));
    const maxValue = Math.max(0, ...allValues);
    const sizeref = maxValue > 0 ? (2 * maxValue) / (30 ** 2) : 1;
    const bubble = confluenceDasColor(0, t);
    const metricLabel = d.visual_metric || "count";

    const traces = [];
    frames.forEach((f, j) => {
        const active = j === frames.length - 1;
        traces.push({
            type: "choropleth",
            visible: active,
            locationmode: "geojson-id",
            geojson: geojson,
            featureidkey: "properties.ENGNAME",
            locations: f.locations,
            z: f.z,
            zauto: false, zmin: zmin, zmax: zmax,
            hovertemplate: `%{location}: %{z} ${metricLabel}<extra></extra>`,
            colorscale: t.dark ? "Cividis" : "YlOrRd",
            reversescale: !t.dark,
            marker: { line: { color: t.border, width: 0.5 } },
            colorbar: dasMapColorbar({ measure: metricLabel.charAt(0).toUpperCase() + metricLabel.slice(1) }, t),
            showlegend: false,
        });
        const keys = known.filter(c => d.das.cities[c][f.label] != null);
        traces.push({
            type: "scattergeo",
            visible: active,
            mode: "markers",
            lat: keys.map(c => coords[c][0]),
            lon: keys.map(c => coords[c][1]),
            text: keys.map(c => `${c}: ${d.das.cities[c][f.label]} DAS samples`),
            hoverinfo: "text",
            marker: {
                sizemode: "area", size: keys.map(c => d.das.cities[c][f.label]),
                sizeref: sizeref, sizemin: 4, color: bubble, opacity: 0.7,
                line: { color: canaskMarkerLineColor(), width: 1 },
            },
            name: "DAS samples",
            showlegend: false,
        });
    });
    const layout = { height: 460, margin: { t: 30, r: 20, b: 20, l: 20 } };
    dasMapLayout(layout, frames, t);
    // dasMapLayout frames all of Canada for the explorer; here the province's own polygons set the
    // view, and each slider step must toggle a PAIR of traces.
    layout.geo = {
        fitbounds: "geojson", showcoastlines: false, showlakes: false, showland: false,
        showcountries: false, showframe: false, bgcolor: "rgba(0,0,0,0)",
    };
    if (layout.sliders) {
        layout.sliders[0].steps.forEach((step, j) => {
            step.args = ["visible", traces.map((_, i) => Math.floor(i / 2) === j)];
        });
    }
    const notes = [];
    if (unmatchedAreas.length) {
        notes.push(`${unmatchedAreas.length} ${unmatchedAreas.length === 1 ? "area has" : "areas have"} no map outline and ${unmatchedAreas.length === 1 ? "isn't" : "aren't"} drawn (see the table): ${unmatchedAreas.join("; ")}.`);
    }
    if (unmapped.length) {
        const shown = unmapped.slice(0, 5).join("; ") + (unmapped.length > 5 ? "; …" : "");
        notes.push(`${unmapped.length} ${unmapped.length === 1 ? "city is" : "cities are"} missing from the map (no known coordinates): ${shown}`);
    }
    if ("Unknown" in d.das.cities) {
        notes.push("DAS results with no recorded city aren't shown on the map (they're counted in the table's total).");
    }
    confluenceSetNote("confluence-unmapped", notes.join(" "));
    Plotly.react(chart, traces, themeChartLayout(layout), { displaylogo: false, responsive: true })
        .then(() => dasFitMapHeight(chart))
        .catch(error => {
            console.error("Confluence map render failed:", error);
            confluenceEmptyState("Sorry, that map couldn't be drawn. Please press Overlay again.");
        });
}

// Aligned table: one row per period; the visual's series (or health authorities) beside the DAS
// keys (or the DAS city total for a map pairing).
function confluenceRenderTable(d) {
    const host = document.getElementById("confluence-table");
    const periods = d.periods.map(p => p.key);
    const columns = [], rows = periods.map(p => [p]);
    if (d.das.mode === "cities") {
        const heat = factsToHeatmap(d.visual.facts, d.visual.key_kind);
        Object.keys(heat).sort().forEach(geo => {
            columns.push(geo);
            periods.forEach((p, i) => rows[i].push(confluenceHeatValue(heat, geo, p)));
        });
        columns.push("DAS samples (all cities)");
        periods.forEach((p, i) => {
            // Every shared period touches DAS coverage, so no city having it means zero samples.
            let total = 0;
            Object.values(d.das.cities).forEach(byPeriod => {
                if (p in byPeriod) total = (total || 0) + byPeriod[p];
            });
            rows[i].push(total);
        });
    } else {
        confluenceAlignedSeries(d).series.forEach(s => {
            columns.push(s.key);
            periods.forEach((p, i) => rows[i].push(p in s.values ? s.values[p] : null));
        });
        d.das.keys.forEach(k => {
            columns.push(`DAS: ${k.label}`);
            const values = d.das.series[k.key] || {};
            periods.forEach((p, i) => rows[i].push(p in values ? values[p] : null));
        });
    }
    const table = document.createElement("table");
    table.className = "mb-0 table table-striped table-bordered table-hover";
    const head = table.insertRow(-1);
    ["Period"].concat(columns).forEach(text => {
        const th = document.createElement("th");
        th.innerText = text;
        head.appendChild(th);
    });
    rows.forEach((cells, i) => {
        const tr = table.insertRow(-1);
        tr.className = "align-middle";
        cells.forEach(value => { tr.insertCell(-1).innerText = value == null ? "" : value; });
        if (!d.periods[i].das_complete) tr.title = "DAS doesn't fully cover this period, so its counts are incomplete";
    });
    host.innerHTML = "";
    host.appendChild(table);
    document.getElementById("confluence-table-title").textContent =
        document.getElementById("confluence-chart-title").textContent;
    makeSortableTable(table);
}

// Keep a map pairing's height matched to its width (see dasFitMapHeight).
let confluenceResizeTimer = null;
window.addEventListener("resize", () => {
    if (!confluenceState || !confluenceState.data || confluenceState.data.das.mode !== "cities") return;
    clearTimeout(confluenceResizeTimer);
    confluenceResizeTimer = setTimeout(() => {
        const chart = document.getElementById("confluence-chart");
        if (chart) dasFitMapHeight(chart);
    }, 200);
});

// Theme toggle hook: canaskRedrawCharts() calls this so trace colours follow the palette.
window.confluenceRedraw = function () {
    if (confluenceState && confluenceState.data && document.getElementById("confluence-chart")) {
        try {
            confluenceRender();
        } catch (error) {
            console.error("Confluence redraw failed:", error);
            confluenceEmptyState("Sorry, that overlay couldn't be redrawn. Please press Overlay again.");
        }
    }
};

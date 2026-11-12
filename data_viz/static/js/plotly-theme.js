// ---------------------------------------------------------------------------
// CANASK shared Plotly theme
// ---------------------------------------------------------------------------
// Provides a single source of truth for how every Plotly visual is styled so
// that charts feel consistent and adapt to the active light / dark theme.
//
// Usage in a chart function:
//     Plotly.react(div, traces, themeChartLayout(layout), config)
//
// `themeChartLayout` only *fills in* styling defaults (fonts, backgrounds,
// gridlines, colorway, hover styling, axis colors). Any value a chart sets
// explicitly always wins, so existing per-chart options are preserved.
//
// This file is bundled together with main.js / visualGeneration.js, so it
// shares their script scope and can reference `masterLoop` / `currentVisual`
// directly when redrawing after a theme change.
// ---------------------------------------------------------------------------

var CANASK_FONT_STACK =
  'Inter, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

// Categorical palettes tuned to read clearly on white (light) and slate (dark)
// while keeping the brand orange first.
var CANASK_COLORWAY_LIGHT = [
  "#f57b00", "#2563eb", "#16a34a", "#9333ea", "#dc2626",
  "#0891b2", "#7d8410", "#db2777", "#0f766e", "#ca8a04",
];
var CANASK_COLORWAY_DARK = [
  "#fb8c1a", "#60a5fa", "#4ade80", "#c084fc", "#f87171",
  "#22d3ee", "#cdd45a", "#f472b6", "#2dd4bf", "#fbbf24",
];

// The active categorical palette for the current theme. Charts that build multi-trace series
// (e.g. a solid line plus a dashed "bridge" overlay for suppressed points) read this so both
// traces share one explicit color, matching the colorway Plotly would otherwise auto-assign.
function canaskColorway() {
  return document.documentElement.getAttribute("data-theme") === "dark"
    ? CANASK_COLORWAY_DARK
    : CANASK_COLORWAY_LIGHT;
}

// Semantic good/caution/bad colors for charts whose segments carry meaning (e.g. the
// expected-vs-actual sample bars). Mirrors the --c-success/--c-warning/--c-danger tokens in
// master_sheet.css -- keep the two in sync if either changes.
function canaskSemanticColors() {
  return document.documentElement.getAttribute("data-theme") === "dark"
    ? { success: "#4ade80", warning: "#fbbf24", danger: "#f87171" }
    : { success: "#16a34a", warning: "#d97706", danger: "#dc2626" };
}

// A quiet, non-color cue for line series: a distinct marker shape each, so
// series stay distinguishable without busy dash patterns. (Bars rely on the
// data table below the chart as their non-color alternative.)
var CANASK_MARKER_SYMBOLS = ["circle", "square", "diamond", "triangle-up", "triangle-down", "circle-open", "square-open", "diamond-open"];

// Subtle, theme-aware outline for bar markers (replaces a hardcoded black that
// read poorly on dark backgrounds).
function canaskMarkerLineColor() {
  return document.documentElement.getAttribute("data-theme") === "dark"
    ? "rgba(226,232,240,0.35)"
    : "rgba(15,23,42,0.45)";
}

// Resolve the design tokens for the currently active theme.
function canaskChartTheme() {
  var dark =
    document.documentElement.getAttribute("data-theme") === "dark";
  if (dark) {
    return {
      dark: true,
      fontFamily: CANASK_FONT_STACK,
      font: "#cbd5e1", // slate-300
      tick: "#94a3b8", // slate-400
      grid: "#1e293b", // slate-800
      zero: "#334155", // slate-700
      axisLine: "#334155",
      hoverBg: "#0f172a",
      hoverFont: "#f1f5f9",
      border: "#334155",
      accent: "#fb8c1a",
      colorway: CANASK_COLORWAY_DARK,
    };
  }
  return {
    dark: false,
    fontFamily: CANASK_FONT_STACK,
    font: "#334155", // slate-700
    tick: "#64748b", // slate-500
    grid: "#e2e8f0", // slate-200
    zero: "#cbd5e1", // slate-300
    axisLine: "#cbd5e1",
    hoverBg: "#ffffff",
    hoverFont: "#0f172a",
    border: "#e2e8f0",
    accent: "#f57b00",
    colorway: CANASK_COLORWAY_LIGHT,
  };
}

// Recursively fill `target` with values from `defaults` without overwriting
// anything the caller already set. Plain objects are merged; everything else
// (arrays, primitives) is only applied when the key is missing.
function canaskFillDefaults(target, defaults) {
  Object.keys(defaults).forEach(function (key) {
    var dv = defaults[key];
    var tv = target[key];
    var dvIsObj =
      dv && typeof dv === "object" && !Array.isArray(dv);
    if (tv === undefined || tv === null) {
      target[key] = dvIsObj ? canaskFillDefaults({}, dv) : dv;
    } else if (
      dvIsObj &&
      typeof tv === "object" &&
      !Array.isArray(tv)
    ) {
      canaskFillDefaults(tv, dv);
    }
  });
  return target;
}

// Public: apply the CANASK theme to a Plotly layout object (returns the same
// object so it can be passed straight into Plotly.react).
function themeChartLayout(layout) {
  layout = layout || {};
  var t = canaskChartTheme();

  canaskFillDefaults(layout, {
    font: { family: t.fontFamily, color: t.font, size: 13 },
    // Titles are HTML headings outside the figure (see setVisualTitle), so Plotly's default 100px
    // top margin is dead space: keep just enough for the mode bar.
    margin: { t: 40 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    colorway: t.colorway,
    hoverlabel: {
      bgcolor: t.hoverBg,
      bordercolor: t.border,
      font: { family: t.fontFamily, color: t.hoverFont },
    },
    legend: {
      bgcolor: "rgba(0,0,0,0)",
      font: { color: t.font, family: t.fontFamily },
    },
    modebar: {
      bgcolor: "rgba(0,0,0,0)",
      color: t.tick,
      activecolor: t.accent,
    },
  });

  // Axis colors only make sense on cartesian charts that already declare axes.
  ["xaxis", "yaxis"].forEach(function (ax) {
    if (layout[ax]) {
      canaskFillDefaults(layout[ax], {
        gridcolor: t.grid,
        zerolinecolor: t.zero,
        linecolor: t.axisLine,
        tickcolor: t.axisLine,
        tickfont: { color: t.tick, family: t.fontFamily },
        title: { font: { color: t.font, family: t.fontFamily } },
      });
    }
  });

  if (layout.geo) {
    canaskFillDefaults(layout.geo, { bgcolor: "rgba(0,0,0,0)" });
  }

  return layout;
}

// Lightweight chrome-only update (no trace recolor) used to retheme any chart
// in place on a theme switch.
function canaskChartRelayout() {
  var t = canaskChartTheme();
  return {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    "font.color": t.font,
    "legend.font.color": t.font,
    "hoverlabel.bgcolor": t.hoverBg,
    "hoverlabel.bordercolor": t.border,
    "hoverlabel.font.color": t.hoverFont,
    "modebar.color": t.tick,
    "modebar.activecolor": t.accent,
    "xaxis.gridcolor": t.grid,
    "xaxis.zerolinecolor": t.zero,
    "xaxis.linecolor": t.axisLine,
    "xaxis.tickcolor": t.axisLine,
    "xaxis.tickfont.color": t.tick,
    "yaxis.gridcolor": t.grid,
    "yaxis.zerolinecolor": t.zero,
    "yaxis.linecolor": t.axisLine,
    "yaxis.tickcolor": t.axisLine,
    "yaxis.tickfont.color": t.tick,
  };
}

// Public: redraw / retheme every Plotly chart currently on the page. Called by
// the theme toggle in base.jinja.
function canaskRedrawCharts() {
  if (typeof Plotly === "undefined") return;
  var partial = canaskChartRelayout();
  var graphs = document.querySelectorAll(".js-plotly-plot");
  graphs.forEach(function (g) {
    try {
      Plotly.relayout(g, partial);
    } catch (e) {
      /* chart type may not support an attribute; ignore */
    }
  });
  // Full rebuild for the active V1 visual so trace colors pick up the new
  // colorway (relayout alone cannot recolor existing traces).
  // Guarded on the province chart container being present: currentVisual is a page-lifetime
  // global that outlives an HTMX navigation to a page without that chart.
  try {
    if (
      typeof masterLoop === "function" &&
      typeof currentVisual !== "undefined" &&
      currentVisual &&
      document.getElementById("vis-div")
    ) {
      masterLoop(typeof lastLocation !== "undefined" ? lastLocation : null);
    }
  } catch (e) {
    /* no active V1 chart */
  }
  // Same full-rebuild treatment for the DAS Explorer's pivot chart, when that page is active.
  try {
    if (typeof window.dasRedrawPivot === "function") {
      window.dasRedrawPivot();
    }
  } catch (e) {
    /* no active DAS pivot chart */
  }
  // ...and for the Confluence overlay, when that page is active.
  try {
    if (typeof window.confluenceRedraw === "function") {
      window.confluenceRedraw();
    }
  } catch (e) {
    /* no active Confluence chart */
  }
}

// Expose the entry points the (non-bundled) base.jinja toggle script needs.
window.themeChartLayout = themeChartLayout;
window.canaskRedrawCharts = canaskRedrawCharts;

// ---------------------------------------------------------------------------
// PNG export: a named file with the title drawn into the image
// ---------------------------------------------------------------------------
// Chart titles are HTML headings outside the Plotly figure (see setVisualTitle), so Plotly's own
// camera button saves an untitled image called "newplot.png". chartConfig() swaps that button for
// one that exports a copy of the figure with the title (and the jurisdiction beneath it) drawn
// above the plot, on a solid background, named after both. The on-page chart is never touched.
//
// Usage:  Plotly.react(div, traces, themeChartLayout(layout), chartConfig({ responsive: false }, info))
// `info` is { title, place, note } (note: an optional small line under the heading, e.g. the
// filters applied) or a function returning it; a function is read when the button is
// clicked, so it always describes the chart as it is then.

var CANASK_EXPORT_PREFIX = "CANASK";

function canaskSlug(text) {
  return String(text == null ? "" : text)
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

// The jurisdiction is left out when the title already names it ("... in British Columbia").
function canaskTitleNamesPlace(title, place) {
  var p = canaskSlug(place);
  return !p || ("-" + canaskSlug(title) + "-").indexOf("-" + p + "-") !== -1;
}

// File name (no extension) for an exported chart: "CANASK-<title>-<jurisdiction>", slugified.
function chartExportName(title, place) {
  var suffix = canaskTitleNamesPlace(title, place) ? "" : canaskSlug(place);
  // A long title is cut short rather than the jurisdiction after it.
  var head = canaskSlug(title).slice(0, 120 - suffix.length).replace(/-+$/, "");
  return [CANASK_EXPORT_PREFIX, head, suffix].filter(Boolean).join("-") + (head || suffix ? "" : "-chart");
}

// Plotly titles don't wrap, so break the text into lines that fit `width` px at `fontSize`.
function canaskWrapText(text, width, fontSize) {
  var perLine = Math.max(16, Math.floor(width / (fontSize * 0.56)));
  var lines = [];
  var line = "";
  String(text).split(/\s+/).filter(Boolean).forEach(function (word) {
    if (line && (line + " " + word).length > perLine) {
      lines.push(line);
      line = word;
    } else {
      line = line ? line + " " + word : word;
    }
  });
  if (line) lines.push(line);
  return lines;
}

function canaskEscapeChartText(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// The figure to export: the chart's current data + layout, made taller by a heading band that
// holds the title, with a solid background (the page's is transparent). Pure apart from reading
// the theme, so the plot area keeps exactly its on-screen size.
function chartExportFigure(gd, info) {
  var t = canaskChartTheme();
  var full = gd._fullLayout || {};
  var width = Math.round(full.width || gd.clientWidth || 900);
  var height = Math.round(full.height || gd.clientHeight || 500);
  var title = String((info && info.title) || "").trim();
  var place = String((info && info.place) || "").trim();
  var titleSize = 18;
  var placeSize = 14;

  var titleLines = title ? canaskWrapText(title, width - 48, titleSize) : [];
  var showPlace = !!place && !canaskTitleNamesPlace(title, place);
  var text = titleLines.map(canaskEscapeChartText).join("<br>");
  if (showPlace) {
    text += (text ? "<br>" : "") +
      '<span style="font-size:' + placeSize + "px;color:" + t.tick + '">' + canaskEscapeChartText(place) + "</span>";
  }
  // An optional note under the heading (e.g. the filters a chart was built with), small and muted.
  var note = String((info && info.note) || "").trim();
  var noteSize = 13;
  var noteLines = note ? canaskWrapText(note, width - 48, noteSize) : [];
  if (noteLines.length) {
    text += (text ? "<br>" : "") +
      '<span style="font-size:' + noteSize + "px;color:" + t.tick + '">' +
      noteLines.map(canaskEscapeChartText).join("<br>") + "</span>";
  }
  var lineCount = titleLines.length + (showPlace ? 1 : 0) + noteLines.length;
  var band = text ? Math.round(titleLines.length * titleSize * 1.3 + (showPlace ? placeSize * 1.5 : 0) +
    noteLines.length * noteSize * 1.5 + 14) : 0;

  var data = gd.data || [];
  try {
    data = structuredClone(data);
  } catch (e) { /* not cloneable: Plotly only reads the traces, so share them */ }

  var layout = Object.assign({}, gd.layout || {});
  var topMargin = (full.margin && typeof full.margin.t === "number") ? full.margin.t : 40;
  layout.margin = Object.assign({}, layout.margin, { t: topMargin + band });
  layout.width = width;
  layout.height = height + band;
  layout.autosize = false;
  layout.paper_bgcolor = t.dark ? "#0f172a" : "#ffffff"; // --c-surface
  if (text) {
    layout.title = {
      text: text,
      x: 0.5,
      xanchor: "center",
      xref: "container",
      y: 1,
      yref: "container",
      yanchor: "top",
      // Plotly 2.32 lifts a multi-line title about 13px above where it puts a one-line title.
      pad: { t: lineCount > 1 ? 27 : 14 },
      font: { family: t.fontFamily, color: t.dark ? "#f1f5f9" : "#0f172a", size: titleSize },
    };
  }
  return { data: data, layout: layout, width: width, height: height + band };
}

function chartDownloadPng(gd, info) {
  var resolved = (typeof info === "function" ? info(gd) : info) || {};
  var fig = chartExportFigure(gd, resolved);
  return Plotly.downloadImage(
    { data: fig.data, layout: fig.layout },
    { format: "png", width: fig.width, height: fig.height, scale: 2, filename: chartExportName(resolved.title, resolved.place) }
  );
}

// Public: the Plotly config for a chart, with the stock camera button replaced by ours.
function chartConfig(base, info) {
  var config = Object.assign({ displaylogo: false }, base || {});
  config.modeBarButtonsToRemove = (config.modeBarButtonsToRemove || []).concat(["toImage"]);
  config.modeBarButtonsToAdd = (config.modeBarButtonsToAdd || []).concat([{
    name: "canaskDownloadPng",
    title: "Download chart as a PNG",
    icon: Plotly.Icons.camera,
    click: function (gd) {
      chartDownloadPng(gd, info).catch(function (err) {
        console.error("Chart export failed", err);
      });
    },
  }]);
  return config;
}

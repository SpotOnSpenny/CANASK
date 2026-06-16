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
  try {
    if (
      typeof masterLoop === "function" &&
      typeof currentVisual !== "undefined" &&
      currentVisual
    ) {
      masterLoop(typeof lastLocation !== "undefined" ? lastLocation : null);
    }
  } catch (e) {
    /* no active V1 chart */
  }
}

// Expose the entry points the (non-bundled) base.jinja toggle script needs.
window.themeChartLayout = themeChartLayout;
window.canaskRedrawCharts = canaskRedrawCharts;

# Python Standard Library Dependencies
from functools import wraps

# External Dependency Imports
from flask import Blueprint, render_template, redirect, url_for, request, jsonify, views, current_app, session, flash, get_flashed_messages
import bleach

# Internal Dependency Imports
from data_viz.extensions import limiter
from data_viz.email import send_ses_email
from data_viz.recaptcha import verify_recaptcha
from data_viz.validation import validate_email, validate_text, MAX_FEEDBACK_NAME, MAX_FEEDBACK_BODY


# Define the blueprint for the main application
main_blueprint = Blueprint("main", __name__)

##################################### ROUTES ###########################################
# Liveness probe for container/nginx healthchecks. Deliberately does NO database or template work so it
# stays a true liveness signal (the app process is up and serving) and can't itself 500 when the DB is
# down. Exempt from rate limiting in data_viz/__init__.py so frequent probes don't burn the budget.
@main_blueprint.route("/healthz")
def healthz():
    return "ok", 200

# Routes for main index page
# Public landing: anonymous visitors get the home page, but the menu/payload only surfaces visuals
# whose visibility is "public" (the access filtering lives in visual_query.allowed_visuals).
@main_blueprint.route("/")
def index():
    if request.headers.get("HX-Request") == "true":
        return render_template("introduction.jinja")
    else:
        return render_template("base.jinja", include_partials="index")

# Routes for Error Pages
@main_blueprint.route("/not-found")
def page_not_found():
    if request.headers.get("HX-Request") == "true":
        return render_template("index.jinja", dash_template="404.jinja"), 404
    else:
        return render_template("base.jinja", include_partials="index", dash_template="404.jinja"), 404

# Route for Feedback submission and recaptcha verification
@main_blueprint.route("/feedback", methods=["POST"])
# Per-IP cap counts every attempt (also protects the reCAPTCHA quota + compute). The global cap is a
# hard ceiling on SES emails across ALL clients, but only deducts on a successful send (status 200)
# so a flood of reCAPTCHA-failing requests can't exhaust the budget and lock out real feedback.
@limiter.limit(lambda: current_app.config["RATELIMIT_FEEDBACK"])
@limiter.limit(lambda: current_app.config["RATELIMIT_FEEDBACK_GLOBAL"],
               key_func=lambda: "feedback-global",
               deduct_when=lambda response: response.status_code == 200)
def feedback():
    feedback_data = request.form

    # Validate + normalize submitted fields before any external work. The message is required; name
    # and email are optional but still length/format-checked when present. Length caps keep the SES
    # email bounded and stop over-length values from ever reaching a String(255)-backed sink.
    ok, feedback_body = validate_text(feedback_data.get("feedback"), "Feedback", MAX_FEEDBACK_BODY, required=True, multiline=True)
    if not ok:
        return jsonify({"status": "error", "message": feedback_body}), 400
    ok, name = validate_text(feedback_data.get("name"), "Name", MAX_FEEDBACK_NAME, required=False)
    if not ok:
        return jsonify({"status": "error", "message": name}), 400
    ok, email = validate_email(feedback_data.get("email"), required=False)
    if not ok:
        return jsonify({"status": "error", "message": email}), 400

    # Verify the reCAPTCHA v3 token via the shared verifier (fails closed on missing secret,
    # transport error, action mismatch, or below-threshold score -- never falls through to sending
    # an email). See data_viz/recaptcha.verify_recaptcha.
    recaptcha_ok, _ = verify_recaptcha(feedback_data.get("recaptcha-token"), "feedback")
    if not recaptcha_ok:
        return jsonify({"status": "error", "message": "Recaptcha verification failed"}), 403

    # Send the feedback email. Values are length-capped above and bleach-cleaned here before being
    # embedded in the HTML body.
    html_body = f"""
        <h2>Name:</h2>{bleach.clean(name) if name else "Anonymous"} </br>
        <h2>Feedback:</h2>{bleach.clean(feedback_body)} </br>
        <h2>Reach them at:</h2>{bleach.clean(email) if email else "Not provided"}
        """
    if not send_ses_email([current_app.config["FEEDBACK_EMAIL"]], "CANASK Feedback Received", html_body):
        return jsonify({"status": "error", "message": "Failed to send feedback email"}), 500
    return jsonify({"status": "success"}), 200

# Route for V1 data visuals
# Could automate this "active provinces check" but honestly this is easier and works fine for now
active_provinces = ["alberta", "british-columbia", "saskatchewan", "manitoba", "ontario", "new-brunswick", "nova-scotia",
                    "quebec", "prince-edward-island", "newfoundland-and-labrador", "yukon", "northwest-territories", "nunavut"]
# National dashboards render with the per-province machinery but are NOT provinces -- they live under
# their own "/v1/national/<dashboard>" URL (canada is a data scope, not a place). `scope` is the
# province key the Visuals/DataPoints rows are stored under; `title` overrides the page heading.
NATIONAL_DASHBOARDS = {
    "drug-checking": {"scope": "canada", "title": "Drug Checking"},
}
# Province keys the data API will serve in addition to active_provinces (the national scopes above).
national_scopes = {dash["scope"] for dash in NATIONAL_DASHBOARDS.values()}

# Endpoints that represent a navigable "page" -> human title. Endpoints absent from here (modals,
# row re-renders, JSON APIs) intentionally resolve to None so their HTMX responses don't disturb the
# current page title. Read by the title OOB swap + context processor in data_viz/__init__.py.
STATIC_PAGE_TITLES = {
    "main.index": "Home",
    "main.page_not_found": "Page Not Found",
    "main.das_explorer_page": "DAS Explorer",
    "auth.login": "Login",
    "auth.invite_user": "Invite User",
    "auth.invite_management": "Invite Management",
    "auth.user_management": "User Management",
    "auth.group_management": "Group Management",
    "auth.data_ownership": "Data Ownership",
    "auth.accept_invite": "Accept Invite",
}

def resolve_page_title():
    """Bare page title (no 'CANASK | ' prefix) for the current request's endpoint, or None for
    non-page responses (modals/rows/APIs) that must not touch the title."""
    ep = request.endpoint
    args = request.view_args or {}
    if ep in ("main.v1_province", "main.v1_province_visual"):
        return args["province"].replace("-", " ").title()
    if ep in ("main.v1_national", "main.v1_national_visual"):
        entry = NATIONAL_DASHBOARDS.get(args.get("dashboard"))
        return entry["title"] if entry else None
    return STATIC_PAGE_TITLES.get(ep)
# Public-capable: anonymous visitors may load a province page; build_province_generic/menu filter the
# content down to that viewer's accessible (e.g. public-only) visuals.
def _render_province(province, **initial):
    """Render the province page (HX partial vs full page), optionally booting straight into a deep-
    linked visual + drill path via the initial_* kwargs the template's boot script reads."""
    # The client builds/parses deep-link URLs off this base. It defaults to the province URL, but
    # national dashboards serve the same page under /v1/national/<dashboard> -- pass url_base so the
    # address bar matches the actual page and never points at the non-existent /v1/province/<scope>.
    initial.setdefault("url_base", f"/v1/province/{province}")
    if request.headers.get("HX-Request") == "true":
        return render_template("v1/provincial_vis.jinja", province=province, **initial)
    return render_template("base.jinja", include_partials="index",
                           dash_template="v1/provincial_vis.jinja", province=province, **initial)


@main_blueprint.route("/v1/province/<province>")
def v1_province(province):
    if province not in active_provinces:
        return redirect(url_for("main.page_not_found"))
    return _render_province(province)


# Deep link straight to a visual: <entrySlug>[/<location>[/<category>]] within a scope (a province or
# a national dashboard's scope). The entry slug is resolved + permission-checked server-side here; the
# drill segments are replayed client-side. A slug the viewer can't see (or that doesn't exist)
# redirects home with a flash.
def _render_scope_visual(scope, rest, **extra):
    from flask_login import current_user
    from .visual_query import displayable_visuals
    segs = [s for s in rest.split("/") if s]
    entry_slug = segs[0] if segs else None
    # Only level-1 visuals are valid entry points; displayable_visuals already prunes by visibility +
    # drill hierarchy + data presence, so membership here is the full check (permission AND has-data).
    visual = next((v for v in displayable_visuals(current_user, scope)
                   if v.slug == entry_slug and v.level == "1"), None)
    if visual is None:
        flash("That visual isn't available.", "danger")
        if request.headers.get("HX-Request") == "true":
            return ("", 204, {"HX-Redirect": url_for("main.index")})
        return redirect(url_for("main.index"))
    return _render_province(
        scope,
        initial_visual=visual.name,
        initial_location_slug=segs[1] if len(segs) > 1 else None,
        initial_category_slug=segs[2] if len(segs) > 2 else None,
        initial_year=request.args.get("y"),
        **extra,
    )


@main_blueprint.route("/v1/province/<province>/<path:rest>")
def v1_province_visual(province, rest):
    if province not in active_provinces:
        return redirect(url_for("main.page_not_found"))
    return _render_scope_visual(province, rest)


# DAS Explorer: a row-level data table + pivot builder over its own das_* tables, not the visuals
# chart machinery -- so it gets its own template and JSON endpoints instead of provincial_vis.jinja
# + /api/v1/province/<...>/data. Access is still the standard per-visual model: the metric-less
# "canada-das"/das_explorer Visuals row (app_config/visuals/nationalDAS.json) carries the visibility
# + group grants, checked server-side here AND on both APIs below. A static path segment outranks
# the /v1/national/<dashboard> converter, so this coexists with the dashboards above.
@main_blueprint.route("/v1/national/das-explorer")
def das_explorer_page():
    from flask_login import current_user
    from .das_explorer import das_access_allowed, explorer_config
    from .database.models import DataSources
    from .das_ingest import DAS_SOURCE_NAME
    if not das_access_allowed(current_user):
        flash("That page isn't available.", "danger")
        if request.headers.get("HX-Request") == "true":
            return ("", 204, {"HX-Redirect": url_for("main.index")})
        return redirect(url_for("main.index"))
    source = DataSources.query.filter_by(name=DAS_SOURCE_NAME).first()
    context = {"das_config": explorer_config(), "das_source": source}
    if request.headers.get("HX-Request") == "true":
        return render_template("v1/das_explorer.jinja", **context)
    return render_template("base.jinja", include_partials="index",
                           dash_template="v1/das_explorer.jinja", **context)


# JSON APIs the DAS Explorer fetches: one page of table rows (Tabulator's remote pagination/sort/
# filter contract) and one pivot aggregation. Both enforce the same per-visual access check as the
# page -- hiding the nav link alone wouldn't protect the data.
@main_blueprint.route("/api/v1/das/<dataset>/rows")
@limiter.limit(lambda: current_app.config["RATELIMIT_API"])
def das_rows(dataset):
    from flask_login import current_user
    from .das_explorer import DATASETS, das_access_allowed, parse_filters, query_rows
    if dataset not in DATASETS:
        return jsonify({"error": "unknown dataset"}), 404
    if not das_access_allowed(current_user):
        return jsonify({"error": "forbidden"}), 403
    try:
        page = int(request.args.get("page", 1))
        size = int(request.args.get("size", 50))
    except ValueError:
        return jsonify({"error": "bad paging params"}), 400
    from .das_filter_expr import FilterSyntaxError
    try:
        return jsonify(query_rows(dataset, page, size,
                                  request.args.getlist("sort"),
                                  parse_filters(request.args, dataset)))
    except FilterSyntaxError as err:
        # A malformed filter expression must fail loudly, never degrade into a wrong result.
        return jsonify({"error": f"Invalid filter: {err}"}), 400


@main_blueprint.route("/api/v1/das/<dataset>/pivot")
@limiter.limit(lambda: current_app.config["RATELIMIT_API"])
def das_pivot(dataset):
    from flask_login import current_user
    from .das_explorer import (DATASETS, PIVOT_MAX_ROWS, PIVOT_MAX_ROWS_GEO,
                               das_access_allowed, parse_filters, query_pivot)
    if dataset not in DATASETS:
        return jsonify({"error": "unknown dataset"}), 404
    if not das_access_allowed(current_user):
        return jsonify({"error": "forbidden"}), 403
    spec = DATASETS[dataset]
    rows_field = request.args.get("rows")
    cols_field = request.args.get("cols") or None
    measure = request.args.get("measure") or next(iter(spec["measures"]))
    if (rows_field not in spec["pivot_dims"]
            or (cols_field is not None and cols_field not in spec["pivot_dims"])
            or measure not in spec["measures"]):
        return jsonify({"error": "bad pivot params"}), 400
    # Optional opt-up for the map charts, which plot every place rather than a top-N;
    # clamped so the param can't be abused into an unbounded payload.
    try:
        rows_limit = request.args.get("rows_limit")
        rows_cap = max(1, min(int(rows_limit), PIVOT_MAX_ROWS_GEO)) if rows_limit else PIVOT_MAX_ROWS
    except ValueError:
        return jsonify({"error": "bad pivot params"}), 400
    from .das_filter_expr import FilterSyntaxError
    try:
        return jsonify(query_pivot(dataset, rows_field, cols_field,
                                   parse_filters(request.args, dataset), measure,
                                   rows_cap=rows_cap))
    except FilterSyntaxError as err:
        return jsonify({"error": f"Invalid filter: {err}"}), 400


# National dashboards: render like a province page but under their own URL space (canada is a data
# scope, not a place). `page_title` overrides the breadcrumb/title so the page reads as the dashboard
# rather than "Canada".
@main_blueprint.route("/v1/national/<dashboard>")
def v1_national(dashboard):
    entry = NATIONAL_DASHBOARDS.get(dashboard)
    if entry is None:
        return redirect(url_for("main.page_not_found"))
    return _render_province(entry["scope"], page_title=entry["title"], url_base=f"/v1/national/{dashboard}")


@main_blueprint.route("/v1/national/<dashboard>/<path:rest>")
def v1_national_visual(dashboard, rest):
    entry = NATIONAL_DASHBOARDS.get(dashboard)
    if entry is None:
        return redirect(url_for("main.page_not_found"))
    return _render_scope_visual(entry["scope"], rest,
                                page_title=entry["title"], url_base=f"/v1/national/{dashboard}")

# JSON API the V1 frontend fetches its visual data from (DB-backed normalized facts).
# Serves both province scopes and national-dashboard scopes (e.g. "canada"); the user-facing national
# page lives at /v1/national/<dashboard> -- this is the internal data endpoint it fetches.
@main_blueprint.route("/api/v1/province/<province>/data")
@limiter.limit(lambda: current_app.config["RATELIMIT_API"])
def v1_province_data(province):
    if province not in active_provinces and province not in national_scopes:
        return jsonify({"error": "unknown province"}), 404
    from flask_login import current_user
    from .visual_query import build_province_menu
    from .visual_generic import build_province_generic
    # `data` is the normalized fact-based contract the frontend adapts client-side; config/default
    # drive the menu. Both are filtered to what the current user may see.
    return jsonify({
        "data": build_province_generic(province, current_user),
        **build_province_menu(province, current_user),
    })


################################# Test Code Below ######################################
if __name__ == '__main__':
    #Test update
    pass
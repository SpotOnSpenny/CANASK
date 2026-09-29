# The nightly Data Updates email -- the pipeline's ONLY email. Sent every morning even when nothing
# ran, so its absence is itself the "beat/workers are down" alarm.

import datetime

from flask import current_app

from data_scraping import registry
from data_scraping.orchestrator import _now, settings_for
from data_viz.database.models import ScrapeRun, User
from data_viz.email import send_ses_email

WINDOW = datetime.timedelta(hours=24)
ISSUE_STATUSES = (ScrapeRun.STATUS_FAILED, ScrapeRun.STATUS_REJECTED, ScrapeRun.STATUS_HELD,
                  ScrapeRun.STATUS_PUBLISH_FAILED)


def _label(key):
    spec = registry.SOURCES.get(key)
    return spec.label if spec else key


def collect_report(now):
    start = now - WINDOW
    runs = (ScrapeRun.query.filter(ScrapeRun.started_at >= start, ScrapeRun.started_at < now)
            .order_by(ScrapeRun.started_at).all())
    ran = [{"source": _label(r.source_key), "trigger": r.trigger, "status": r.status,
            "duration": (r.finished_at - r.started_at) if r.finished_at else None} for r in runs]
    updated = [{"source": _label(r.source_key), "from": r.previous_data_until, "to": r.data_until,
                "trigger": r.trigger} for r in runs if r.status == ScrapeRun.STATUS_PUBLISHED]
    issues = [{"source": _label(r.source_key), "status": r.status,
               "checks": [c for c in (r.check_results or []) if c["level"] in ("fail", "warn")],
               "error": (r.error or "").strip().splitlines()[-1:]}
              for r in runs if r.status in ISSUE_STATUSES
              or any(c["level"] == "warn" for c in (r.check_results or []))]
    new_data = [{"source": _label(r.source_key), "notices": r.notices} for r in runs if r.notices]
    status = []
    for key, spec in registry.SOURCES.items():
        settings = settings_for(key)
        last_published = (ScrapeRun.query.filter_by(source_key=key, status=ScrapeRun.STATUS_PUBLISHED)
                          .order_by(ScrapeRun.finished_at.desc()).first())
        stale_days = (now - last_published.finished_at).days if last_published and last_published.finished_at else None
        status.append({
            "key": key, "source": spec.label, "onboarded": registry.onboarded(spec),
            "schedule_enabled": settings.schedule_enabled, "auto_publish": settings.auto_publish,
            "paused_reason": settings.paused_reason,
            "held": ScrapeRun.query.filter_by(source_key=key, status=ScrapeRun.STATUS_HELD).count(),
            "running": ScrapeRun.query.filter(ScrapeRun.source_key == key,
                                              ScrapeRun.status.in_(ScrapeRun.IN_FLIGHT)).count(),
            "stale_days": stale_days,
            "stale": stale_days is not None and stale_days > spec.expected_cycle_days,
        })
    return {"window_start": start, "window_end": now, "ran": ran, "updated": updated,
            "issues": issues, "new_data": new_data, "status": status}


def report_recipients():
    users = User.query.filter_by(data_report_subscribed=True, site_admin=True,
                                 status=User.STATUS_ACTIVE).order_by(User.email).all()
    return [u.email for u in users]


def render_report(data):
    problems = len(data["issues"])
    subject = (f"CANASK data updates: {len(data['updated'])} updated, "
               f"{problems} issue{'s' if problems != 1 else ''}")
    # get_template().render() instead of Flask's render_template(): this runs from the
    # nightly_report Celery task, which only pushes an app context (celery_worker/celery.py's
    # ContextTask), never a request context. render_template() would also run every app-wide
    # @app.context_processor (inject_nav_permissions/inject_page_title), which this template
    # doesn't need and which touch request/current_user -- reaching for a request context to
    # satisfy them would leak into a real request's `g` when render_report is ever called from
    # inside one. Going straight through the Jinja environment skips context processors entirely
    # (the `mt` filter is on app.jinja_env, so it still resolves) and needs no request context.
    page_url = current_app.config["PUBLIC_BASE_URL"] + "/v1/admin/data-updates"
    html = current_app.jinja_env.get_template("email/nightly_report.jinja").render(
        data=data, page_url=page_url)
    return subject, html


def send_nightly_report(now=None):
    recipients = report_recipients()
    if not recipients:
        return "no subscribers"
    subject, html = render_report(collect_report(now or _now()))
    ok = send_ses_email(recipients, subject, html)
    return f"sent to {len(recipients)}" if ok else "SES send failed (see logs)"

# Thin Celery wrappers around data_scraping.orchestrator (which owns every ScrapeRun transition) and
# the nightly report. Scrape-queue tasks are routed in celery_worker/celery.py.

# External Imports
from celery import shared_task

# Internal Imports
from data_scraping import orchestrator, registry


def enqueue_scrape(key, trigger, user_id=None):
    limit = registry.get_source(key).time_limit
    run_source_task.apply_async(args=(key, trigger, user_id), soft_time_limit=limit, time_limit=limit + 60)


def enqueue_upload_processing(run_id, key):
    limit = registry.get_source(key).time_limit
    process_upload_task.apply_async(args=(run_id,), soft_time_limit=limit, time_limit=limit + 60)


def enqueue_publish(run_id, user_id, key):
    limit = registry.get_source(key).time_limit
    publish_run_task.apply_async(args=(run_id, user_id), soft_time_limit=limit, time_limit=limit + 60)


def enqueue_rollback(key, run_id, user_id):
    limit = registry.get_source(key).time_limit
    rollback_task.apply_async(args=(key, run_id, user_id), soft_time_limit=limit, time_limit=limit + 60)


@shared_task
def run_source_task(key, trigger, user_id=None):
    run = orchestrator.run_source(key, trigger, user_id)
    return f"{key}: {run.status if run else 'skipped'}"


@shared_task
def process_upload_task(run_id):
    return f"upload {run_id}: {orchestrator.process_upload(run_id).status}"


@shared_task
def publish_run_task(run_id, user_id):
    try:
        return f"publish {run_id}: {orchestrator.publish_run(run_id, user_id).status}"
    except ValueError as exc:
        return f"publish {run_id} refused: {exc}"


@shared_task
def rollback_task(key, run_id, user_id):
    try:
        return f"rollback {key} -> {run_id}: {orchestrator.rollback_source(key, run_id, user_id).status}"
    except ValueError as exc:
        return f"rollback {key} -> {run_id} refused: {exc}"


@shared_task
def nightly_refresh():
    queued = []
    for spec in registry.automated_sources():
        if orchestrator.settings_for(spec.key).schedule_enabled:
            enqueue_scrape(spec.key, "schedule")
            queued.append(spec.key)
    return f"queued: {', '.join(queued) or 'none'}"


@shared_task
def sweep_stuck_runs_task():
    return f"swept {orchestrator.sweep_stuck_runs()} run(s)"


@shared_task
def nightly_report():
    from data_scraping.report import send_nightly_report
    return send_nightly_report()

# Standard Library Imports
import os

# External Imports
from celery import Celery
from celery.schedules import crontab
from flask import Flask


def init_celery(app: Flask) -> Celery:
    celery = Celery(app.name)
    # Redis redelivers any message left unacked past visibility_timeout, and an eta task stays
    # unacked until it fires -- so the timeout must exceed the longest eta we schedule (invite
    # expiry, INVITE_EXPIRY_HOURS) or each invite's task is redelivered hourly until it runs.
    invite_expiry_seconds = int(os.environ.get("INVITE_EXPIRY_HOURS", "72")) * 3600
    celery.conf.update(
        broker_url = os.environ.get("CELERY_BROKER_URL"),
        result_backend = os.environ.get("CELERY_RESULT_BACKEND"),
        broker_transport_options = {"visibility_timeout": invite_expiry_seconds + 3600},
        imports=["celery_worker.tasks.invite_jwt_expiry", "celery_worker.tasks.data_collection"],
        timezone="America/Edmonton",
        # Scrapes run one at a time on their own worker (scrape-worker: -Q scrape --concurrency=1) so a
        # 20-minute PowerBI scrape never delays invite expiry on the default queue.
        task_routes={f"celery_worker.tasks.data_collection.{name}": {"queue": "scrape"}
                     for name in ("run_source_task", "process_upload_task", "publish_run_task", "rollback_task")},
        beat_schedule={
            "nightly-refresh": {"task": "celery_worker.tasks.data_collection.nightly_refresh",
                                "schedule": crontab(hour=1, minute=0)},
            "nightly-report": {"task": "celery_worker.tasks.data_collection.nightly_report",
                               "schedule": crontab(hour=6, minute=0)},
            "sweep-stuck-runs": {"task": "celery_worker.tasks.data_collection.sweep_stuck_runs_task",
                                 "schedule": crontab(minute=30)},
        },
    )

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery
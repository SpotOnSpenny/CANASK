# Standard Library Imports
import os

# External Imports
from celery import Celery
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
        imports=["celery_worker.tasks.invite_jwt_expiry"]
    )

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery
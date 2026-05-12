# Standard Library Imports
import os

# External Imports
from celery import Celery
from flask import Flask


def init_celery(app: Flask) -> Celery:
    celery = Celery(app.name)
    celery.conf.update(
        broker_url = os.environ.get("CELERY_BROKER_URL"),
        result_backend = os.environ.get("CELERY_RESULT_BACKEND"),
        imports=["celery_worker.tasks.invite_jwt_expiry"]
    )

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery
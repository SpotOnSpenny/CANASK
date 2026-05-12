# Standard Library Imports
from datetime import datetime, timezone

# External Imports
from celery import shared_task

# Internal Imports
from data_viz.database import db
from data_viz.database.models import Invites

@shared_task(bind=True)
def expire_invite(self, invite_id: int) -> str:
    invite = Invites.query.get(invite_id)

    if not invite:
        return f"Invite {invite_id} not found"

    if invite.status != "pending":
        return f"Invite {invite_id} is already {invite.status}, and cannot be expired."
    
    invite.status = "expired"
    db.session.commit()

    return f"Invite {invite_id} expired successfully."
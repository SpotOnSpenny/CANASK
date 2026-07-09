# Outbound email via AWS SES. Both the feedback form and the invite flow send through here so the SES
# client setup lives in one place. Credentials/sender come from the environment (AWS_*, SES_SENDER_EMAIL)
# and are read per-send so a missing config fails at send time (logged), not at import.

import os

import boto3
from botocore.exceptions import ClientError
from flask import current_app


def send_ses_email(to_addresses, subject, html_body):
    """Send an HTML email via SES to one or more recipients.

    Returns True on success, False on failure (failures are logged, never raised) so callers can degrade
    gracefully -- e.g. still create an invite even if the notification email couldn't be sent.
    """
    sender = os.environ.get("SES_SENDER_EMAIL")
    if not sender:
        current_app.logger.error("SES_SENDER_EMAIL is not set; cannot send email %r", subject)
        return False
    try:
        client = boto3.client(
            "ses",
            region_name=os.environ.get("AWS_REGION"),
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )
        client.send_email(
            Source=sender,
            Destination={"ToAddresses": to_addresses},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Html": {"Data": html_body}},
            },
        )
        return True
    except ClientError as e:
        current_app.logger.error("SES send_email failed: %s", e.response["Error"]["Message"])
        return False
    except Exception as e:
        current_app.logger.exception("Unexpected error sending email: %s", e)
        return False

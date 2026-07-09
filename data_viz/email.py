# Outbound email via AWS SES. Both the feedback form and the invite flow send through here so the SES
# client setup lives in one place. Credentials/sender come from the environment (AWS_*, SES_SENDER_EMAIL,
# optional SES_REPLY_TO_EMAIL) and are read per-send so a missing config fails at send time (logged),
# not at import.

import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from flask import current_app


def send_ses_email(to_addresses, subject, html_body):
    """Send an HTML email via SES to one or more recipients.

    Returns True on success, False on SES/AWS failure (logged, not raised) so callers can degrade
    gracefully -- e.g. still create an invite even if the notification email couldn't be sent. Only
    AWS-side failures (ClientError, BotoCoreError -- bad credentials, unreachable endpoint, rejected
    send) are converted to False; anything else is a programming bug and propagates, so it can't
    masquerade as an email-config problem.
    """
    sender = os.environ.get("SES_SENDER_EMAIL")
    if not sender:
        current_app.logger.error("SES_SENDER_EMAIL is not set; cannot send email %r", subject)
        return False
    reply_to = os.environ.get("SES_REPLY_TO_EMAIL")
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
            **({"ReplyToAddresses": [reply_to]} if reply_to else {}),
        )
        return True
    except ClientError as e:
        # The response dict's shape isn't guaranteed -- .get() so the handler itself can't KeyError.
        error = e.response.get("Error", {})
        current_app.logger.error(
            "SES send_email to %s (subject %r) failed: %s %s",
            to_addresses, subject, error.get("Code", "unknown"), error.get("Message", str(e)))
        return False
    except BotoCoreError as e:
        # Covers NoCredentialsError, EndpointConnectionError, etc. -- config/connectivity problems.
        current_app.logger.error(
            "SES send_email to %s (subject %r) failed: %s", to_addresses, subject, e)
        return False

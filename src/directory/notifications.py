"""Emails to whoever runs the directory, when something needs their attention.

The question here isn't how to send email, which Django does. It's how to send
it without the sending ever becoming the visitor's problem. A provider who has
just filled in a forty-question form should land on the thank-you page whether
or not Resend is having a good day. So every notification is sent after the
database has saved what it's about, and a failure is logged, not raised. Errors
are read in Fly's logs (see docs/hosting-setup.md), so a lost email is still
noticed.

Who receives these is the `NOTIFICATION_EMAILS` setting, a Fly secret so the
addresses never appear in the repository. Empty means nothing is sent, which is
what development and the tests get by default.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse

logger = logging.getLogger(__name__)


def notify(subject, body):
    """Email the directory's owners, once the current transaction commits.

    Outside a transaction it sends straight away. Inside one, it waits, so an
    email never announces a record that was then rolled back.
    """
    recipients = list(settings.NOTIFICATION_EMAILS)
    if not recipients:
        return

    def send():
        try:
            send_mail(subject, body, None, recipients)
        except Exception:
            # Broad on purpose: SMTP, DNS and TLS failures all arrive as
            # different exceptions, and none of them should reach the visitor.
            logger.exception("Could not send the notification %r", subject)

    transaction.on_commit(send)


def admin_link(view_name, *args):
    """An absolute link into the admin, for an email to point at."""
    return settings.SITE_BASE_URL.rstrip("/") + reverse(view_name, args=args)


def submission_received(submission):
    """A program was registered or suggested (#28), with any tags it suggested (#25).

    Tag suggestions only ever arrive with a registration, so they're announced
    in that registration's email rather than in one of their own: one email
    per thing somebody sent, not one per field they filled in.
    """
    registration = submission.is_registration
    kind = "registration" if registration else "suggestion"
    category = submission.category.name if submission.category else "No category chosen"
    sender = submission.submitter_name or "(no name given)"
    if submission.submitter_email:
        sender += f" <{submission.submitter_email}>"
    suggested = list(submission.tag_suggestions.order_by("pk").values_list("name", flat=True))

    lines = [
        f"A new program {kind} is waiting in the directory.",
        "",
        f"Program:   {submission.program_name}",
        f"Category:  {category}",
        f"Sent by:   {sender}",
    ]
    if suggested:
        lines.append(f"Suggested tags: {', '.join(suggested)}")
    lines += ["", "Review it here:", admin_link("admin:directory_submission_change", submission.pk)]
    if suggested:
        lines += [
            "",
            "Decide the tag suggestions here (the program doesn't have to wait):",
            admin_link("admin:directory_tagsuggestion_changelist") + "?status__exact=pending",
        ]

    subject = f"New {kind}: {submission.program_name}"
    if suggested:
        count = len(suggested)
        subject += f" ({count} tag suggestion{'s' if count != 1 else ''})"
    notify(subject, "\n".join(lines))

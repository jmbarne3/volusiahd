"""The two views behind the "Sign in with Google" button on the admin login.

The rule this file exists to enforce is that a Google account, however valid,
is not an admin account. Nothing here creates a `User`; the account has to have
been made in the admin first, by somebody who already had access. If you read
one function in this app, read `authorized_user` — it is the entire policy, and
it is a lookup that returns `None` and turns the person away.
"""

import logging
import secrets
import time
from urllib.parse import urljoin

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME, get_user_model
from django.contrib.auth import login as start_session
from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import iri_to_uri
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache

from . import google
from .models import GoogleAccountLink

logger = logging.getLogger(__name__)
User = get_user_model()

# Where the in-progress flow is parked between the two views.
SESSION_KEY = "google_oauth_flow"

# Long enough to pick an account and type a password; short enough that a
# stale tab cannot be completed days later.
FLOW_LIFETIME_SECONDS = 10 * 60

# Django's ModelBackend is the only backend installed. `start_session` needs it
# named explicitly because we authenticated the person ourselves rather than
# going through `django.contrib.auth.authenticate`.
BACKEND = "django.contrib.auth.backends.ModelBackend"


@never_cache
def google_login(request):
    """Start the flow: mint the one-time values and hand off to Google."""
    if not google.is_configured():
        return _refuse(request, "Google sign-in is not set up on this site.")

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = google.new_code_verifier()

    request.session[SESSION_KEY] = {
        "state": state,
        "nonce": nonce,
        "code_verifier": code_verifier,
        "started_at": time.time(),
        "next": _safe_next(request),
    }

    return HttpResponseRedirect(
        google.authorization_url(
            redirect_uri=_redirect_uri(),
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
        )
    )


@never_cache
def google_callback(request):
    """Finish the flow: verify, look up a pre-approved account, or refuse."""
    # Popped rather than read: a flow is good for exactly one callback, so a
    # replayed URL finds nothing waiting for it.
    flow = request.session.pop(SESSION_KEY, None)
    if not isinstance(flow, dict):
        return _refuse(request, "That sign-in link has expired. Please try again.")

    if time.time() - float(flow.get("started_at") or 0) > FLOW_LIFETIME_SECONDS:
        return _refuse(request, "That sign-in took too long. Please try again.")

    if not secrets.compare_digest(str(flow.get("state") or ""), request.GET.get("state", "")):
        logger.warning("Rejected a Google callback whose state did not match the session.")
        return _refuse(request, "That sign-in could not be verified. Please try again.")

    if error := request.GET.get("error"):
        # Ordinarily this is someone pressing Cancel on Google's screen.
        logger.info("Google returned an error for a sign-in attempt: %s", error)
        return _refuse(request, "Google did not complete that sign-in.")

    code = request.GET.get("code", "")
    if not code:
        return _refuse(request, "Google did not return an authorization code.")

    try:
        identity = google.identity_from_code(
            code=code,
            redirect_uri=_redirect_uri(),
            nonce=str(flow.get("nonce") or ""),
            code_verifier=str(flow.get("code_verifier") or ""),
        )
    except google.GoogleAuthError as exc:
        logger.warning("Google sign-in failed: %s", exc)
        return _refuse(request, str(exc))

    user = authorized_user(identity)
    if user is None:
        logger.warning("Refused admin access to unapproved Google account %r", identity.email)
        return _refuse(
            request,
            f"{identity.email} is not approved to use this site. "
            "Ask an administrator to add the account first.",
        )

    start_session(request, user, backend=BACKEND)
    logger.info("Admin sign-in by %r via Google", user.get_username())
    return HttpResponseRedirect(flow.get("next") or reverse("admin:index"))


def authorized_user(identity: google.GoogleIdentity):
    """Return the pre-approved account for this Google identity, or `None`.

    This is the whole access-control policy. It is a lookup and not a creation
    on purpose: there is no branch here, and no code path anywhere else in this
    app, that constructs a `User`. Somebody holding a perfectly valid Google
    account who has not been added in the admin first gets `None`.

    Matching happens in two stages. Once an account is linked we match on
    Google's subject ID, which is permanent. Before that — the first sign-in —
    we match on the verified email address, which has to name exactly one
    active staff account.
    """
    link = GoogleAccountLink.objects.filter(subject=identity.subject).select_related("user").first()
    if link is not None:
        if not _may_use_admin(link.user):
            return None
        GoogleAccountLink.objects.filter(pk=link.pk).update(
            email=identity.email, last_used_on=timezone.now()
        )
        return link.user

    # First sign-in for this Google account. Two rows matching the address is
    # as disqualifying as none: `User.email` carries no uniqueness constraint
    # in Django, so an ambiguous match has to fail rather than pick one.
    candidates = list(User.objects.filter(email__iexact=identity.email)[:2])
    if len(candidates) != 1:
        return None

    user = candidates[0]
    if not _may_use_admin(user):
        return None

    if GoogleAccountLink.objects.filter(user=user).exists():
        # The account is already bound to a different Google identity, which
        # means the address has been reassigned. Transferring someone's admin
        # access should take a deliberate act by a human, not a login.
        logger.warning(
            "Refused admin access: %r is already linked to a different Google account.",
            identity.email,
        )
        return None

    try:
        with transaction.atomic():
            GoogleAccountLink.objects.create(
                user=user,
                subject=identity.subject,
                email=identity.email,
                last_used_on=timezone.now(),
            )
    except IntegrityError:
        # Two callbacks racing — a double-clicked button, or a reloaded tab.
        # Whoever lost is still the same person, so defer to the row that won
        # rather than turning a double click into a 500 on the login page.
        if not GoogleAccountLink.objects.filter(subject=identity.subject, user=user).exists():
            return None

    # A pre-created account usually has no name on it; borrow Google's once.
    if identity.full_name and not (user.first_name or user.last_name):
        first, _, last = identity.full_name.partition(" ")
        user.first_name, user.last_name = first[:150], last[:150]
        user.save(update_fields=["first_name", "last_name"])

    return user


def _may_use_admin(user) -> bool:
    return bool(user.is_active and user.is_staff)


def _refuse(request, message):
    """Send the person back to the login page with the reason."""
    messages.error(request, message)
    return HttpResponseRedirect(reverse("admin:login"))


def _redirect_uri() -> str:
    """The callback URL, built from `SITE_BASE_URL` rather than the request.

    Google compares this string character for character against the one
    registered in the Cloud Console, so it has to be the same every time. The
    incoming request's host is not a safe source: behind Fly's proxy the site
    answers on three hostnames, and only one of them is registered.
    """
    return urljoin(settings.SITE_BASE_URL, reverse("accounts:google_callback"))


def _safe_next(request) -> str:
    """The `?next=` the admin passed through, if it points back at this site."""
    candidate = request.GET.get(REDIRECT_FIELD_NAME, "")
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return iri_to_uri(candidate)
    return ""

"""Everything this project knows about signing in with Google.

This module is deliberately sealed off from the rest of the application: it
does not import Django's auth system, does not touch the database, and does
not decide who is allowed in. It turns a browser round trip into a
`GoogleIdentity` — a verified claim about *who* just signed in — or raises
`GoogleAuthError`. Whether that person may use the admin is `views.py`'s
question, and keeping the two apart is what makes the access-control rule
short enough to read in one sitting.

The endpoints below are hardcoded rather than read from Google's discovery
document on every sign-in, because a network round trip per login buys nothing
while the values are unchanged. That is an assumption with an expiry date, so
`tests/test_contract.py` checks these constants against the live discovery
document and fails loudly if Google ever moves them.
"""

import base64
import binascii
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
DISCOVERY_DOCUMENT = "https://accounts.google.com/.well-known/openid-configuration"

# Google uses both spellings of its own issuer claim, depending on the flow.
ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})

# `openid` and `email` are what the access decision needs; `profile` only
# supplies a display name for a blank account record.
SCOPES = ("openid", "email", "profile")

# Clock skew tolerated when checking the token's expiry.
LEEWAY_SECONDS = 60

HTTP_TIMEOUT_SECONDS = 10


class GoogleAuthError(Exception):
    """A sign-in could not be completed. The message is shown to the person."""


@dataclass(frozen=True)
class GoogleIdentity:
    """Who Google says just signed in. Says nothing about whether they may."""

    subject: str
    email: str
    full_name: str = ""


def is_configured() -> bool:
    """Whether this deployment has Google credentials at all."""
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)


# --- Step one: send the browser to Google -----------------------------------


def new_code_verifier() -> str:
    """A fresh PKCE verifier. Held in the session until the code comes back."""
    return secrets.token_urlsafe(64)


def authorization_url(*, redirect_uri: str, state: str, nonce: str, code_verifier: str) -> str:
    """The Google URL to send the browser to.

    Three separate anti-forgery measures ride along, and they guard different
    things. `state` proves the callback belongs to a flow this browser started.
    `nonce` is echoed inside the ID token, tying the token to that same flow.
    The PKCE challenge proves that whoever redeems the authorization code is
    whoever requested it — belt and braces for a confidential client like this
    one, but it is eight lines and OAuth 2.1 expects it.
    """
    challenge = _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Always offer the account chooser. Editors who are signed into a
        # personal Google account in the same browser would otherwise be
        # bounced straight back with the wrong address and no way to correct it.
        "prompt": "select_account",
    }
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"


# --- Step two: turn the code Google hands back into an identity -------------


def identity_from_code(
    *, code: str, redirect_uri: str, nonce: str, code_verifier: str, post=None
) -> GoogleIdentity:
    """Redeem an authorization code and return the identity it stands for.

    `post` is the seam the tests substitute; in production it is the real
    HTTPS call to Google's token endpoint.
    """
    tokens = exchange_code(
        code=code, redirect_uri=redirect_uri, code_verifier=code_verifier, post=post
    )
    id_token = tokens.get("id_token")
    if not id_token:
        raise GoogleAuthError("Google's response did not include an ID token.")
    return identity_from_claims(verify_id_token(id_token, nonce=nonce))


def exchange_code(*, code: str, redirect_uri: str, code_verifier: str, post=None) -> dict:
    """Swap the authorization code for tokens, server to server over TLS."""
    post = post or _post_form
    return post(
        TOKEN_ENDPOINT,
        {
            "code": code,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        },
    )


def verify_id_token(id_token: str, *, nonce: str, now: float | None = None) -> dict:
    """Check the claims in an ID token that arrived straight from Google.

    We do not verify the JWT signature, and that is a decision rather than an
    oversight. This token did not pass through the browser: `exchange_code`
    fetched it over TLS directly from Google's token endpoint, authenticating
    with our client secret. OpenID Connect Core section 3.1.3.7 says a client
    MAY skip signature validation in exactly that case, because TLS has already
    established that the bytes came from Google. Checking the signature would
    mean fetching and caching Google's rotating JWKS and taking on a crypto
    dependency to re-establish something we already know.

    The claims themselves still have to be checked, because they are what we
    act on. The token must be addressed to us, issued by Google, unexpired, and
    carry the nonce we minted for this particular browser round trip.
    """
    claims = _decode_payload(id_token)
    now = time.time() if now is None else now

    if claims.get("iss") not in ISSUERS:
        raise GoogleAuthError("That sign-in token was not issued by Google.")
    if claims.get("aud") != settings.GOOGLE_OAUTH_CLIENT_ID:
        raise GoogleAuthError("That sign-in token was issued for a different application.")

    try:
        expires_at = float(claims["exp"])
    except (KeyError, TypeError, ValueError):
        raise GoogleAuthError("That sign-in token did not say when it expires.") from None
    if expires_at + LEEWAY_SECONDS < now:
        raise GoogleAuthError("That sign-in took too long. Please try again.")

    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise GoogleAuthError("That sign-in did not match the request that started it.")

    return claims


def identity_from_claims(claims: dict) -> GoogleIdentity:
    """Reduce a verified claim set to the three things we care about."""
    subject = str(claims.get("sub") or "")
    email = str(claims.get("email") or "").strip()
    if not subject or not email:
        raise GoogleAuthError("Google did not say which account signed in.")

    # Google sends a JSON boolean here, but has historically sent the string
    # too. An unverified address must never satisfy the email match in
    # views.authorized_user, so anything else is a refusal. Note the identity
    # check: `1 in (True,)` is true in Python, and 1 is not a verified email.
    verified = claims.get("email_verified")
    if verified is not True and verified != "true":
        raise GoogleAuthError("Google has not verified the email address on that account.")

    return GoogleIdentity(
        subject=subject, email=email, full_name=str(claims.get("name") or "").strip()
    )


# --- Plumbing ---------------------------------------------------------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_payload(id_token: str) -> dict:
    """Read a JWT's payload without validating its signature. See above."""
    parts = id_token.split(".")
    if len(parts) != 3:
        raise GoogleAuthError("That sign-in token was not a well-formed JWT.")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, binascii.Error):
        raise GoogleAuthError("That sign-in token could not be read.") from None
    if not isinstance(claims, dict):
        raise GoogleAuthError("That sign-in token could not be read.")
    return claims


def _post_form(url: str, payload: dict) -> dict:
    """POST a form to Google and return the parsed JSON body."""
    request = Request(
        url,
        data=urlencode(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            body = response.read()
    except HTTPError as exc:
        # Google puts a machine-readable reason in the body of a 4xx. It is
        # worth logging, and never worth showing to the person signing in.
        detail = exc.read().decode("utf-8", "replace")[:500]
        logger.warning("Google token endpoint returned HTTP %s: %s", exc.code, detail)
        raise GoogleAuthError("Google refused that sign-in. Please try again.") from None
    except (URLError, TimeoutError) as exc:
        logger.warning("Could not reach Google's token endpoint: %s", exc)
        raise GoogleAuthError("Could not reach Google. Please try again.") from None

    try:
        parsed = json.loads(body)
    except ValueError:
        raise GoogleAuthError("Google's response could not be read.") from None
    if not isinstance(parsed, dict):
        raise GoogleAuthError("Google's response could not be read.")
    return parsed

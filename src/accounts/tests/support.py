"""Shared fixtures for the sign-in tests."""

import base64
import json
import time

from django.test import override_settings

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
CLIENT_SECRET = "test-client-secret"

with_google_configured = override_settings(
    GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET=CLIENT_SECRET,
    SITE_BASE_URL="https://example.test",
)


def _segment(data: dict) -> str:
    raw = json.dumps(data).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def make_id_token(drop=(), **claims) -> str:
    """A JWT shaped like Google's, with whatever claims the test needs.

    The signature is junk on purpose: `verify_id_token` does not check it, and
    a test that pretended otherwise would be testing a fiction. What the tests
    exercise is the claim checking, which is the part that guards anything.

    `drop` removes a claim entirely, which is a different case from setting it
    to something wrong.
    """
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-subject-1",
        "email": "editor@example.org",
        "email_verified": True,
        "name": "Ada Editor",
        "exp": time.time() + 300,
        "iat": time.time(),
        **claims,
    }
    for claim in drop:
        payload.pop(claim, None)
    return f"{_segment({'alg': 'RS256', 'kid': 'x'})}.{_segment(payload)}.not-a-signature"

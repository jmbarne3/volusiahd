"""Checks our assumptions about Google against Google.

Every other test in this app fakes Google's side of the conversation, which
means they will all keep passing on the day Google changes something. This
file is the one that will not. It fetches the live OpenID Connect discovery
document — the machine-readable statement of how Google's sign-in works — and
compares it with the constants and choices hardcoded in `accounts/google.py`.

It talks to the internet, so it is tagged `network` and excluded from the
ordinary run:

    python manage.py test --exclude-tag=network      # the everyday suite
    python manage.py test accounts.tests.test_contract   # this file

Run it before a deploy, and on a schedule if you want warning rather than a
support call. A failure here is not a bug in this code — it means Google moved
and `accounts/google.py` has to follow.

When the network is unreachable these tests skip rather than fail, so a green
run offline says nothing. Check for the skip if you are relying on this in CI.
"""

import json
from urllib.error import URLError
from urllib.request import urlopen

from django.test import SimpleTestCase, tag

from accounts import google


@tag("network")
class GoogleDiscoveryDocumentTests(SimpleTestCase):
    """Google publishes how its sign-in works; this asserts we still match."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            with urlopen(google.DISCOVERY_DOCUMENT, timeout=15) as response:
                cls.doc = json.loads(response.read())
        except (URLError, TimeoutError, ValueError) as exc:
            cls.doc = None
            cls.reason = exc

    def setUp(self):
        if self.doc is None:
            self.skipTest(
                f"Could not read {google.DISCOVERY_DOCUMENT} ({self.reason}). "
                "This test only means something when it actually runs."
            )

    def test_the_authorization_endpoint_is_where_we_send_people(self):
        self.assertEqual(self.doc["authorization_endpoint"], google.AUTHORIZATION_ENDPOINT)

    def test_the_token_endpoint_is_where_we_redeem_the_code(self):
        self.assertEqual(self.doc["token_endpoint"], google.TOKEN_ENDPOINT)

    def test_the_issuer_is_one_we_accept(self):
        """`verify_id_token` refuses any token whose `iss` is not in this set,
        so an issuer we do not know about locks everybody out."""
        self.assertIn(self.doc["issuer"], google.ISSUERS)

    def test_the_authorization_code_flow_is_still_supported(self):
        self.assertIn("code", self.doc["response_types_supported"])

    def test_the_scopes_we_ask_for_are_still_offered(self):
        self.assertLessEqual(set(google.SCOPES), set(self.doc["scopes_supported"]))

    def test_pkce_with_sha256_is_still_supported(self):
        """We always send a code challenge. If Google stopped accepting S256,
        every sign-in would fail at the token endpoint."""
        self.assertIn("S256", self.doc["code_challenge_methods_supported"])

    def test_we_may_still_authenticate_with_the_secret_in_the_form_body(self):
        """`_post_form` puts client_secret in the POST body rather than in a
        Basic auth header."""
        self.assertIn("client_secret_post", self.doc["token_endpoint_auth_methods_supported"])

    def test_every_claim_the_access_decision_reads_is_still_published(self):
        """These are the claims `verify_id_token` and `identity_from_claims`
        act on. One of them disappearing is the quiet failure this whole file
        exists to catch.

        `nonce` is deliberately not in this list. It is a request parameter
        Google echoes back into the token rather than a claim it advertises,
        so discovery says nothing about it; OpenID Connect Core section 3.1.3.7
        requires the echo, and the unit tests check that we enforce it.
        """
        required = {"sub", "email", "email_verified", "aud", "iss", "exp"}
        self.assertLessEqual(required, set(self.doc["claims_supported"]))

    def test_id_tokens_are_still_signed_the_way_we_assume(self):
        """We skip signature verification because the token comes to us over
        TLS from the token endpoint, not through the browser. If Google ever
        offered an unsigned or symmetric alternative, that reasoning would
        need revisiting."""
        algorithms = set(self.doc["id_token_signing_alg_values_supported"])
        self.assertIn("RS256", algorithms)
        self.assertNotIn("none", algorithms)

"""Tests for the module that talks to Google.

These are hermetic: no network, and every Google response is fabricated. They
cover the checks that stand between a forged token and an admin session. The
question of whether Google still behaves the way this module assumes is a
different question, and test_contract.py answers it.
"""

import time
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from django.test import SimpleTestCase

from accounts import google

from .support import CLIENT_ID, make_id_token, with_google_configured


@with_google_configured
class AuthorizationUrlTests(SimpleTestCase):
    def build(self, **overrides):
        params = {
            "redirect_uri": "https://example.test/admin/google/callback/",
            "state": "state-value",
            "nonce": "nonce-value",
            "code_verifier": "verifier-value",
            **overrides,
        }
        url = google.authorization_url(**params)
        parsed = urlparse(url)
        return parsed, {k: v[0] for k, v in parse_qs(parsed.query).items()}

    def test_it_points_at_googles_authorization_endpoint(self):
        parsed, _ = self.build()
        endpoint = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        self.assertEqual(endpoint, google.AUTHORIZATION_ENDPOINT)
        self.assertEqual(parsed.scheme, "https")

    def test_it_asks_for_a_code_and_carries_the_one_time_values(self):
        """If any of these three go missing the flow still works, which is
        exactly why a test has to notice: state, nonce and the PKCE challenge
        are the parts that fail silently when they are dropped."""
        _, params = self.build()
        self.assertEqual(params["response_type"], "code")
        self.assertEqual(params["client_id"], CLIENT_ID)
        self.assertEqual(params["state"], "state-value")
        self.assertEqual(params["nonce"], "nonce-value")
        self.assertEqual(params["code_challenge_method"], "S256")
        self.assertTrue(params["code_challenge"])
        self.assertNotIn("verifier-value", params["code_challenge"])

    def test_it_requests_the_scopes_the_access_decision_needs(self):
        _, params = self.build()
        self.assertEqual(set(params["scope"].split()), set(google.SCOPES))
        self.assertIn("email", params["scope"].split())

    def test_the_code_challenge_is_the_sha256_of_the_verifier(self):
        import base64
        import hashlib

        verifier = google.new_code_verifier()
        _, params = self.build(code_verifier=verifier)
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        self.assertEqual(params["code_challenge"], expected)


@with_google_configured
class IdTokenVerificationTests(SimpleTestCase):
    def verify(self, *, nonce="n", **claims):
        return google.verify_id_token(make_id_token(nonce=nonce, **claims), nonce=nonce)

    def test_a_good_token_verifies(self):
        claims = self.verify()
        self.assertEqual(claims["email"], "editor@example.org")

    def test_both_spellings_of_googles_issuer_are_accepted(self):
        for issuer in ["https://accounts.google.com", "accounts.google.com"]:
            with self.subTest(issuer=issuer):
                self.assertTrue(self.verify(iss=issuer))

    def test_a_token_from_somewhere_else_is_refused(self):
        with self.assertRaises(google.GoogleAuthError):
            self.verify(iss="https://accounts.evil.example")

    def test_a_token_for_another_application_is_refused(self):
        """Somebody else's valid Google token is still somebody else's."""
        with self.assertRaises(google.GoogleAuthError):
            self.verify(aud="someone-elses-client-id.apps.googleusercontent.com")

    def test_an_expired_token_is_refused(self):
        with self.assertRaises(google.GoogleAuthError):
            self.verify(exp=time.time() - 3600)

    def test_a_little_clock_skew_is_tolerated(self):
        self.assertTrue(self.verify(exp=time.time() - 5))

    def test_a_token_missing_or_mangling_its_expiry_is_refused(self):
        for token in [
            make_id_token(nonce="n", drop=["exp"]),
            make_id_token(nonce="n", exp=None),
            make_id_token(nonce="n", exp="whenever"),
        ]:
            with self.subTest(token=token):
                with self.assertRaises(google.GoogleAuthError):
                    google.verify_id_token(token, nonce="n")

    def test_a_token_from_a_different_flow_is_refused(self):
        """The nonce is what stops a token captured from one sign-in being
        replayed into another browser's pending sign-in."""
        with self.assertRaises(google.GoogleAuthError):
            google.verify_id_token(make_id_token(nonce="theirs"), nonce="ours")

    def test_a_token_with_no_nonce_at_all_is_refused(self):
        with self.assertRaises(google.GoogleAuthError):
            google.verify_id_token(make_id_token(), nonce="ours")

    def test_malformed_tokens_are_refused_rather_than_crashing(self):
        for token in ["", "not-a-jwt", "a.b", "a.b.c.d", "a.!!!.c"]:
            with self.subTest(token=token):
                with self.assertRaises(google.GoogleAuthError):
                    google.verify_id_token(token, nonce="n")


@with_google_configured
class IdentityTests(SimpleTestCase):
    def test_claims_become_an_identity(self):
        identity = google.identity_from_claims(
            {"sub": "s", "email": "a@b.test", "email_verified": True, "name": "A B"}
        )
        self.assertEqual(identity.subject, "s")
        self.assertEqual(identity.email, "a@b.test")
        self.assertEqual(identity.full_name, "A B")

    def test_an_unverified_address_is_refused(self):
        """An unverified address proves nothing about who holds it, and the
        access decision downstream is an email match."""
        for value in [False, "false", None, "yes", 1]:
            with self.subTest(value=value):
                with self.assertRaises(google.GoogleAuthError):
                    google.identity_from_claims(
                        {"sub": "s", "email": "a@b.test", "email_verified": value}
                    )

    def test_google_sending_a_string_boolean_still_works(self):
        identity = google.identity_from_claims(
            {"sub": "s", "email": "a@b.test", "email_verified": "true"}
        )
        self.assertEqual(identity.email, "a@b.test")

    def test_a_token_with_no_email_is_refused(self):
        with self.assertRaises(google.GoogleAuthError):
            google.identity_from_claims({"sub": "s", "email_verified": True})

    def test_a_token_with_no_subject_is_refused(self):
        with self.assertRaises(google.GoogleAuthError):
            google.identity_from_claims({"email": "a@b.test", "email_verified": True})


@with_google_configured
class CodeExchangeTests(SimpleTestCase):
    def test_the_exchange_sends_what_google_requires(self):
        post = mock.Mock(return_value={"id_token": "x"})
        google.exchange_code(
            code="the-code",
            redirect_uri="https://example.test/cb/",
            code_verifier="the-verifier",
            post=post,
        )
        url, payload = post.call_args.args
        self.assertEqual(url, google.TOKEN_ENDPOINT)
        self.assertEqual(payload["grant_type"], "authorization_code")
        self.assertEqual(payload["code"], "the-code")
        self.assertEqual(payload["code_verifier"], "the-verifier")
        self.assertEqual(payload["redirect_uri"], "https://example.test/cb/")
        self.assertEqual(payload["client_secret"], "test-client-secret")

    def test_a_response_without_an_id_token_is_refused(self):
        with self.assertRaises(google.GoogleAuthError):
            google.identity_from_code(
                code="c",
                redirect_uri="https://example.test/cb/",
                nonce="n",
                code_verifier="v",
                post=lambda url, payload: {"access_token": "only-this"},
            )

    def test_google_saying_no_becomes_a_clean_error(self):
        """A 400 from the token endpoint carries a reason we want in the log
        and never on the screen."""
        error = HTTPError("url", 400, "Bad Request", {}, None)
        error.read = lambda: b'{"error":"invalid_grant"}'
        with mock.patch("accounts.google.urlopen", side_effect=error):
            with self.assertRaises(google.GoogleAuthError) as caught:
                google._post_form(google.TOKEN_ENDPOINT, {})
        self.assertNotIn("invalid_grant", str(caught.exception))

    def test_google_being_unreachable_becomes_a_clean_error(self):
        with mock.patch("accounts.google.urlopen", side_effect=URLError("down")):
            with self.assertRaises(google.GoogleAuthError):
                google._post_form(google.TOKEN_ENDPOINT, {})

    def test_a_response_that_is_not_json_becomes_a_clean_error(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"<html>oops</html>"
        with mock.patch("accounts.google.urlopen", return_value=response):
            with self.assertRaises(google.GoogleAuthError):
                google._post_form(google.TOKEN_ENDPOINT, {})


class ConfigurationTests(SimpleTestCase):
    def test_no_credentials_means_not_configured(self):
        with self.settings(GOOGLE_OAUTH_CLIENT_ID="", GOOGLE_OAUTH_CLIENT_SECRET=""):
            self.assertFalse(google.is_configured())

    def test_half_configured_is_not_configured(self):
        with self.settings(GOOGLE_OAUTH_CLIENT_ID="id", GOOGLE_OAUTH_CLIENT_SECRET=""):
            self.assertFalse(google.is_configured())

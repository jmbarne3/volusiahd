"""Tests for the sign-in flow and, mostly, for who it turns away.

The promise this app makes is that holding a Google account is not the same as
having an admin account. Every test in `RefusalTests` is a way that promise
could quietly stop holding, and each of them asserts the same two things: no
session was started, and `User.objects.count()` did not move.
"""

import time
from unittest import mock

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from accounts import google
from accounts.models import GoogleAccountLink
from accounts.views import SESSION_KEY

from .support import make_id_token, with_google_configured

LOGIN_URL = reverse("accounts:google_login")
CALLBACK_URL = reverse("accounts:google_callback")
ADMIN_LOGIN = reverse("admin:login")


class FlowMixin:
    def start_flow(self, **query):
        self.client.get(LOGIN_URL, query)
        return self.client.session[SESSION_KEY]

    def finish_flow(self, flow=None, *, state=None, token=None, query=None, **claims):
        """Play Google's part: hand the callback a code and an ID token."""
        flow = self.start_flow() if flow is None else flow
        if token is None:
            token = make_id_token(nonce=flow["nonce"], **claims)
        params = {"code": "an-authorization-code", "state": state or flow["state"]}
        params.update(query or {})
        with mock.patch.object(google, "_post_form", return_value={"id_token": token}):
            return self.client.get(CALLBACK_URL, params)

    def assertNotSignedIn(self, response):
        self.assertRedirects(response, ADMIN_LOGIN, fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def messages_from(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


@with_google_configured
class StartingTheFlowTests(FlowMixin, TestCase):
    def test_it_redirects_to_google(self):
        response = self.client.get(LOGIN_URL)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(google.AUTHORIZATION_ENDPOINT))

    def test_it_parks_one_time_values_in_the_session(self):
        flow = self.start_flow()
        self.assertTrue(flow["state"])
        self.assertTrue(flow["nonce"])
        self.assertTrue(flow["code_verifier"])
        self.assertNotEqual(flow["state"], flow["nonce"])

    def test_every_attempt_gets_fresh_values(self):
        self.assertNotEqual(self.start_flow()["state"], self.start_flow()["state"])

    def test_the_redirect_uri_comes_from_site_base_url_not_the_request(self):
        """Fly answers on three hostnames and only one is registered with
        Google, so the request's host is not a safe source for this."""
        response = self.client.get(LOGIN_URL, headers={"host": "testserver"})
        self.assertIn(
            "redirect_uri=https%3A%2F%2Fexample.test%2Fadmin%2Fgoogle%2Fcallback%2F",
            response["Location"],
        )

    def test_an_internal_next_is_kept(self):
        flow = self.start_flow(next="/admin/directory/program/")
        self.assertEqual(flow["next"], "/admin/directory/program/")

    def test_a_next_pointing_off_site_is_dropped(self):
        for candidate in ["https://evil.example/", "//evil.example/", "http://evil.example"]:
            with self.subTest(candidate=candidate):
                self.assertEqual(self.start_flow(next=candidate)["next"], "")


class UnconfiguredTests(FlowMixin, TestCase):
    def test_without_credentials_the_button_is_absent_and_the_view_refuses(self):
        with self.settings(GOOGLE_OAUTH_CLIENT_ID="", GOOGLE_OAUTH_CLIENT_SECRET=""):
            self.assertNotContains(self.client.get(ADMIN_LOGIN), "Sign in with Google")
            self.assertNotSignedIn(self.client.get(LOGIN_URL))


@with_google_configured
class ButtonTests(TestCase):
    def test_the_login_page_offers_google_and_still_offers_a_password(self):
        """Password sign-in stays as the way back in if the OAuth client is
        ever misconfigured. Removing it would make a typo in a secret into a
        lockout."""
        response = self.client.get(ADMIN_LOGIN)
        self.assertContains(response, "Sign in with Google")
        self.assertContains(response, LOGIN_URL)
        self.assertContains(response, 'name="password"')

    def test_the_button_carries_the_page_the_admin_was_heading_for(self):
        """Asserted against the button's own href. Matching on `next=...`
        alone passes on the password form's action attribute, which carries
        the same value and would hide the button losing it entirely."""
        response = self.client.get(ADMIN_LOGIN, {"next": "/admin/directory/program/"})
        self.assertContains(
            response, f'<a href="{LOGIN_URL}?next=/admin/directory/program/"', html=False
        )

    def test_a_next_with_its_own_query_string_survives_intact(self):
        """The `&` has to be escaped or it would read as a second parameter
        on the sign-in URL and the filter would be silently dropped."""
        response = self.client.get(
            ADMIN_LOGIN, {"next": "/admin/directory/program/?status=draft&q=co-op"}
        )
        self.assertContains(
            response,
            f'<a href="{LOGIN_URL}?next=/admin/directory/program/%3Fstatus%3Ddraft%26q%3Dco-op"',
        )
        flow = self.client.get(
            LOGIN_URL, {"next": "/admin/directory/program/?status=draft&q=co-op"}
        )
        self.assertEqual(flow.status_code, 302)


@with_google_configured
class SuccessfulSignInTests(FlowMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="editor@example.org", email="editor@example.org", is_staff=True
        )
        cls.user.set_unusable_password()
        cls.user.save()

    def test_a_pre_approved_account_gets_in(self):
        response = self.finish_flow()
        self.assertRedirects(response, reverse("admin:index"), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_signing_in_without_a_usable_password_still_works(self):
        """Pre-created accounts have no password at all. This is the case the
        whole feature exists for, so it gets its own test."""
        self.assertFalse(self.user.has_usable_password())
        self.finish_flow()
        self.assertIn("_auth_user_id", self.client.session)

    def test_the_address_matches_regardless_of_case(self):
        self.finish_flow(email="Editor@Example.ORG")
        self.assertIn("_auth_user_id", self.client.session)

    def test_the_first_sign_in_records_the_google_subject(self):
        self.finish_flow()
        link = GoogleAccountLink.objects.get()
        self.assertEqual(link.user, self.user)
        self.assertEqual(link.subject, "google-subject-1")
        self.assertIsNotNone(link.last_used_on)

    def test_a_blank_account_borrows_the_name_from_google(self):
        self.finish_flow()
        self.user.refresh_from_db()
        self.assertEqual(self.user.get_full_name(), "Ada Editor")

    def test_a_name_already_entered_is_not_overwritten(self):
        User.objects.filter(pk=self.user.pk).update(first_name="Adelaide", last_name="Lovelace")
        self.finish_flow()
        self.user.refresh_from_db()
        self.assertEqual(self.user.get_full_name(), "Adelaide Lovelace")

    def test_later_sign_ins_match_on_the_subject_not_the_address(self):
        """Google addresses change; the subject does not. After the link
        exists, a renamed address must still reach the same account — and must
        not need a second matching User row to do it."""
        self.finish_flow()
        self.client.logout()
        self.finish_flow(email="ada.editor@example.org")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)
        self.assertEqual(GoogleAccountLink.objects.count(), 1)
        self.assertEqual(GoogleAccountLink.objects.get().email, "ada.editor@example.org")

    def test_the_page_the_admin_was_heading_for_is_honoured(self):
        flow = self.start_flow(next="/admin/directory/program/")
        response = self.finish_flow(flow)
        self.assertRedirects(response, "/admin/directory/program/", fetch_redirect_response=False)

    def test_the_admin_actually_opens_afterwards(self):
        self.finish_flow()
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)


@with_google_configured
class RefusalTests(FlowMixin, TestCase):
    """Each of these is a way somebody could get in who should not."""

    def assertRefused(self, response):
        self.assertNotSignedIn(response)
        self.assertEqual(User.objects.count(), self.user_count)
        self.assertEqual(GoogleAccountLink.objects.count(), self.link_count)

    def setUp(self):
        self.user_count = User.objects.count()
        self.link_count = GoogleAccountLink.objects.count()

    def test_an_unknown_google_account_creates_nothing(self):
        """The whole point of the feature. A valid Google sign-in by somebody
        nobody has approved must not become an account, a link, or a session."""
        response = self.finish_flow(email="stranger@gmail.com", sub="stranger-subject")
        self.assertRefused(response)
        self.assertFalse(User.objects.filter(email="stranger@gmail.com").exists())
        self.assertIn("not approved", " ".join(self.messages_from(response)))

    def test_a_deactivated_account_is_refused(self):
        User.objects.create_user(
            username="gone@example.org", email="gone@example.org", is_staff=True, is_active=False
        )
        self.setUp()
        self.assertRefused(self.finish_flow(email="gone@example.org"))

    def test_a_deactivated_account_that_had_already_linked_is_refused(self):
        """Clearing Active has to shut the door for somebody who signed in
        successfully yesterday, not just for a first-timer."""
        user = User.objects.create_user(
            username="gone@example.org", email="gone@example.org", is_staff=True
        )
        self.finish_flow(email="gone@example.org")
        self.client.logout()
        User.objects.filter(pk=user.pk).update(is_active=False)
        self.setUp()
        self.assertRefused(self.finish_flow(email="gone@example.org"))

    def test_a_non_staff_account_is_refused(self):
        User.objects.create_user(username="reader@example.org", email="reader@example.org")
        self.setUp()
        self.assertRefused(self.finish_flow(email="reader@example.org"))

    def test_two_accounts_sharing_an_address_is_refused_rather_than_guessed(self):
        """Django puts no uniqueness constraint on User.email. An ambiguous
        match must fail closed instead of picking whichever row came first."""
        for name in ["a", "b"]:
            User.objects.create_user(username=name, email="shared@example.org", is_staff=True)
        self.setUp()
        self.assertRefused(self.finish_flow(email="shared@example.org"))

    def test_a_reassigned_address_does_not_inherit_the_previous_holders_access(self):
        """A Workspace address handed to a new hire arrives with the same
        email and a different Google subject. That should need a human."""
        User.objects.create_user(
            username="role@example.org", email="role@example.org", is_staff=True
        )
        self.finish_flow(email="role@example.org", sub="first-person")
        self.client.logout()
        self.setUp()
        self.assertRefused(self.finish_flow(email="role@example.org", sub="second-person"))

    def test_an_unverified_address_is_refused(self):
        """Otherwise anyone able to set an unverified address on a throwaway
        Google account could match an approved one."""
        User.objects.create_user(
            username="editor@example.org", email="editor@example.org", is_staff=True
        )
        self.setUp()
        self.assertRefused(self.finish_flow(email="editor@example.org", email_verified=False))

    def test_an_account_with_no_email_address_cannot_be_matched(self):
        """`createsuperuser` will happily leave the email blank, and a blank
        column must never become a wildcard that anything matches."""
        User.objects.create_user(username="nobody", email="", is_staff=True)
        self.setUp()
        self.assertRefused(self.finish_flow(email=""))
        self.assertRefused(self.finish_flow(email="   "))

    def test_a_token_for_another_application_is_refused(self):
        User.objects.create_user(
            username="editor@example.org", email="editor@example.org", is_staff=True
        )
        self.setUp()
        self.assertRefused(self.finish_flow(aud="another-client-id"))

    def test_a_callback_with_no_flow_in_the_session_is_refused(self):
        response = self.client.get(CALLBACK_URL, {"code": "c", "state": "s"})
        self.assertRefused(response)

    def test_a_mismatched_state_is_refused(self):
        self.assertRefused(self.finish_flow(state="not-the-state-we-issued"))

    def test_a_missing_state_is_refused(self):
        flow = self.start_flow()
        with mock.patch.object(
            google, "_post_form", return_value={"id_token": make_id_token(nonce=flow["nonce"])}
        ):
            response = self.client.get(CALLBACK_URL, {"code": "c"})
        self.assertRefused(response)

    def test_a_flow_can_only_be_completed_once(self):
        """A replayed callback URL, from a browser history or a shared link,
        finds nothing waiting for it."""
        User.objects.create_user(
            username="editor@example.org", email="editor@example.org", is_staff=True
        )
        flow = self.start_flow()
        self.finish_flow(flow)
        self.client.logout()
        self.setUp()
        self.assertRefused(self.finish_flow(flow))

    def test_a_stale_flow_is_refused(self):
        flow = self.start_flow()
        session = self.client.session
        session[SESSION_KEY] = {**flow, "started_at": time.time() - 3600}
        session.save()
        self.assertRefused(self.finish_flow(flow))

    def test_google_reporting_an_error_is_refused(self):
        flow = self.start_flow()
        response = self.client.get(CALLBACK_URL, {"state": flow["state"], "error": "access_denied"})
        self.assertRefused(response)

    def test_a_callback_with_no_code_is_refused(self):
        flow = self.start_flow()
        self.assertRefused(self.client.get(CALLBACK_URL, {"state": flow["state"]}))

    def test_google_being_unreachable_is_refused_not_a_500(self):
        flow = self.start_flow()
        with mock.patch.object(
            google, "_post_form", side_effect=google.GoogleAuthError("Could not reach Google.")
        ):
            response = self.client.get(CALLBACK_URL, {"code": "c", "state": flow["state"]})
        self.assertRefused(response)

    def test_an_off_site_next_cannot_be_smuggled_through_the_callback(self):
        User.objects.create_user(
            username="editor@example.org", email="editor@example.org", is_staff=True
        )
        flow = self.start_flow(next="https://evil.example/")
        response = self.finish_flow(flow)
        self.assertRedirects(response, reverse("admin:index"), fetch_redirect_response=False)

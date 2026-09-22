"""Tests for the screens that grant access.

Adding a row through this form *is* the authorization, so the two things that
matter are that the row it writes actually matches at sign-in time, and that
only a superuser can write one.
"""

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from accounts.models import GoogleAccountLink

ADD_URL = reverse("admin:auth_user_add")
LIST_URL = reverse("admin:auth_user_changelist")


class PreApprovalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            "owner", "owner@example.org", "pw-for-tests-only"
        )

    def setUp(self):
        self.client.force_login(self.superuser)

    def add(self, **overrides):
        return self.client.post(
            ADD_URL,
            {
                "email": "new.editor@example.org",
                "first_name": "",
                "last_name": "",
                **overrides,
            },
        )

    def test_the_screens_render(self):
        for url in [LIST_URL, ADD_URL, reverse("admin:auth_user_change", args=[self.superuser.pk])]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_adding_a_person_makes_a_staff_account_with_no_password(self):
        """No password is collected, and the account is saved with an unusable
        hash — so the username-and-password form cannot let this person in
        even if somebody guesses the username. Google is the only door."""
        self.add()
        user = User.objects.get(email="new.editor@example.org")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_usable_password())

    def test_the_username_is_the_address_so_the_match_is_unambiguous(self):
        self.add()
        self.assertTrue(User.objects.filter(username="new.editor@example.org").exists())

    def test_a_duplicate_address_is_refused_rather_than_created(self):
        """Two rows with the same address make the first-sign-in lookup
        ambiguous, which fails closed and locks out both people. Better to
        say so here, where it can be explained."""
        self.add()
        response = self.add(email="New.Editor@example.org")
        self.assertContains(response, "already exists")
        self.assertEqual(User.objects.filter(email__iexact="new.editor@example.org").count(), 1)

    def test_an_address_can_be_promoted_to_superuser_on_the_way_in(self):
        self.add(is_superuser="on")
        self.assertTrue(User.objects.get(email="new.editor@example.org").is_superuser)

    def test_the_changelist_says_who_has_signed_in_yet(self):
        self.add()
        response = self.client.get(LIST_URL)
        self.assertContains(response, "Not yet signed in")

        user = User.objects.get(email="new.editor@example.org")
        GoogleAccountLink.objects.create(user=user, subject="s", email=user.email)
        self.assertContains(self.client.get(LIST_URL), "Linked")


class WhoCanGrantAccessTests(TestCase):
    """An editor must not be able to add themselves a second account, or
    promote themselves. The stock Django permissions would allow it if
    somebody ever ticked the wrong box, so the admin refuses outright."""

    @classmethod
    def setUpTestData(cls):
        cls.editor = User.objects.create_user(
            "editor@example.org", "editor@example.org", "pw-for-tests-only", is_staff=True
        )
        # Deliberately over-permissioned: the point is that the admin still
        # refuses, so a mis-ticked box cannot become a privilege escalation.
        cls.editor.user_permissions.add(
            *Permission.objects.filter(content_type__app_label="auth", content_type__model="user")
        )

    def setUp(self):
        self.client.force_login(self.editor)

    def test_an_editor_cannot_reach_the_people_screens_even_with_the_permission(self):
        for url in [LIST_URL, ADD_URL]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_an_editor_cannot_post_a_new_account_into_existence(self):
        before = User.objects.count()
        self.client.post(ADD_URL, {"email": "sneaky@example.org"})
        self.assertEqual(User.objects.count(), before)

    def test_the_people_section_is_hidden_from_the_sidebar(self):
        from accounts.navigation import is_superuser

        response = self.client.get(reverse("admin:index"))
        self.assertNotContains(response, LIST_URL)
        self.assertFalse(is_superuser(response.wsgi_request))

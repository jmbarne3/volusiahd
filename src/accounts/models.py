"""The record that binds a Django account to a Google account."""

from django.conf import settings
from django.db import models


class GoogleAccountLink(models.Model):
    """One Django account, bound to one Google account by its subject ID.

    Email addresses move; Google's `sub` claim does not. Matching on the
    address alone would mean that a Workspace address reassigned to a new hire
    quietly hands them the previous holder's admin access. So the first
    successful sign-in records the subject, and every sign-in after that
    matches on the subject instead — the address is then only a display value.

    Deleting a row here does not remove anyone's access; it unbinds the Google
    account, and the next sign-in re-matches by email. To actually revoke
    access, deactivate or delete the user.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="google_link",
        verbose_name="account",
    )
    subject = models.CharField(
        "Google subject ID",
        max_length=255,
        unique=True,
        editable=False,
        help_text="Google's permanent identifier for this person. Never reused.",
    )
    email = models.EmailField(
        "Google email address",
        editable=False,
        help_text="The address Google reported at the last sign-in.",
    )
    linked_on = models.DateTimeField(auto_now_add=True)
    last_used_on = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "linked Google account"
        verbose_name_plural = "linked Google accounts"

    def __str__(self):
        return f"{self.email} -> {self.user}"

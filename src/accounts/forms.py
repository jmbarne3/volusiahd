"""The form that pre-approves somebody for Google sign-in.

Adding a user here is the act of authorization — `views.authorized_user` only
ever matches against rows this form creates. So the form asks for the one
thing the match depends on, the Google address, and refuses anything that
would make the match ambiguous later.
"""

from django import forms
from django.contrib.auth import get_user_model

User = get_user_model()


class ApprovedUserCreationForm(forms.ModelForm):
    """Create an account that can only be used through Google sign-in.

    No password is collected. The account is saved with an unusable password
    hash, which means the username-and-password form cannot let this person in
    even if they guess the username — Google is the only door.
    """

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "is_superuser"]
        labels = {"email": "Google email address"}
        help_texts = {
            "email": (
                "The exact address this person signs into Google with. "
                "Sign-in is refused unless it matches an account here."
            ),
            "is_superuser": (
                "Leave this off for editors. Superusers can add and remove other accounts."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True

    def clean_email(self):
        email = self.cleaned_data["email"].strip()

        # `User.email` has no uniqueness constraint in Django, and a duplicate
        # would make the first-sign-in lookup ambiguous — which fails closed
        # and locks out both people. Catch it here, where it can be explained.
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with that email address already exists.")

        # The username doubles as the address, so it inherits the 150-char cap.
        if len(email) > 150:
            raise forms.ValidationError("That address is too long to use as a username.")

        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["email"]
        user.is_staff = True
        user.set_unusable_password()
        if commit:
            user.save()
        return user

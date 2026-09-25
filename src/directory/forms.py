"""The two public doors into the directory.

Registration is the one that matters: a provider describes their own program in
full, and approving it is the only action left for anyone here. Referral is the
thin one — a lead, so we can invite the people who actually run a program to
register it themselves.

Neither form gets a rich text editor. Handing a stranger a ProseMirror instance
buys inconsistent typography and a wider sanitization surface in exchange for
formatting nobody asked for; plain text becomes paragraphs on approval.
"""

from django import forms

from .addresses import LocationsField
from .models import Category, Submission, SubmissionLocation, Tag

# Bots fill every field they find; people never see this one.
HONEYPOT_FIELD = "website_url"


class ScopedTagSelect(forms.SelectMultiple):
    """A <select multiple> whose options say which headings they belong to.

    Which tags go with which heading is `Category.tags`, and the check that
    matters happens in `clean()` — this widget only writes that pairing into the
    markup as `data-categories`, so the page's JavaScript can narrow the list
    the moment someone picks a heading rather than after they submit. Without
    JavaScript the full list renders and the server does the rejecting.
    """

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        tag = getattr(value, "instance", None)
        if tag is not None:
            option["attrs"]["data-categories"] = " ".join(
                str(pk) for pk in sorted(category.pk for category in tag.categories.all())
            )
        return option


class BaseSubmissionForm(forms.ModelForm):
    """Shared honeypot and category widget.

    Spam control is a hidden field plus the moderation queue, not a captcha. A
    captcha costs every legitimate submitter something in exchange for stopping
    bots that an unrendered input already stops.
    """

    website_url = forms.CharField(required=False, widget=forms.HiddenInput)

    # One category, so a dropdown rather than a list of checkboxes. There are
    # fewer than ten and they are mutually exclusive by design; anything finer
    # than a heading is a tag.
    category = forms.ModelChoiceField(
        queryset=Category.objects.all(),
        required=False,
        widget=forms.Select,
        empty_label="Choose the closest one",
        help_text="The one heading that fits best. Everything else is a tag.",
    )

    # Not a model field any more. A place is a row with coordinates on it, so
    # the form collects them through the picker and writes them after the
    # submission itself exists.
    locations = LocationsField(required=False)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get(HONEYPOT_FIELD):
            raise forms.ValidationError("This submission could not be accepted.")
        return cleaned

    def save(self, commit=True):
        submission = super().save(commit=commit)
        if commit:
            self._write_locations(submission)
        else:
            # `save(commit=False)` defers related objects to `save_m2m()`, and
            # these are related objects like any other.
            deferred = self.save_m2m

            def save_m2m():
                deferred()
                self._write_locations(submission)

            self.save_m2m = save_m2m
        return submission

    def _write_locations(self, submission):
        """One row per place, in the order they were added."""
        submission.locations.all().delete()
        SubmissionLocation.objects.bulk_create(
            [
                SubmissionLocation(submission=submission, sort_order=order, **place)
                for order, place in enumerate(self.cleaned_data.get("locations") or [])
            ]
        )


class ProgramRegistrationForm(BaseSubmissionForm):
    """For the people who run the program.

    Every field `Program` publishes is collected here, so that approval copies
    a complete record rather than starting a conversation.
    """

    is_authorized = forms.BooleanField(
        required=True,
        label="I run this program, or I am authorized to list it",
    )

    # Pick from the vocabulary; never add to it. A free-text tag field would
    # give us "co-op", "Co-Op" and "coop" inside a week, and the whole value of
    # a tag is that the same idea always carries the same word. The queryset is
    # evaluated per request rather than at import, so a tag added in the admin
    # this morning is on the form this afternoon.
    #
    # Only tags that belong to at least one heading are offered at all. The rest
    # are ours to apply in the admin, and offering one here that no heading
    # accepts would be a choice that always fails validation.
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.filter(categories__isnull=False)
        .distinct()
        .prefetch_related("categories"),
        required=False,
        # A plain <select multiple>, which Select2 turns into a searchable
        # multiselect on the registration page. Without JavaScript it stays a
        # usable native control rather than nothing at all.
        widget=ScopedTagSelect(
            attrs={
                "data-tag-select": "",
                "data-placeholder": "Start typing to find a tag",
                "size": "8",
            }
        ),
        label="Tags",
        help_text="These follow from the heading you chose above. We keep this list, "
        "so if the word you want is missing, say so in your description and we will "
        "look at adding it.",
    )

    class Meta:
        model = Submission
        fields = [
            "program_name",
            "short_description",
            "description",
            "category",
            "tags",
            "website",
            "facebook",
            "email",
            "phone",
            "locations",
            "serves_grades",
            "age_min",
            "age_max",
            "cost_notes",
            "step_up_direct_pay",
            "step_up_pep",
            "step_up_fes_ua",
            "meeting_schedule",
            "logo",
            "submitter_name",
            "submitter_email",
            "submitter_role",
            "is_authorized",
        ]
        labels = {
            "program_name": "Program name",
            "short_description": "One-line description",
            "description": "Full description",
            "website": "Website",
            "facebook": "Facebook page URL",
            "email": "Public email address",
            "phone": "Public phone number",
            "serves_grades": "Grades served",
            "age_min": "Youngest age",
            "age_max": "Oldest age",
            "cost_notes": "Cost",
            "step_up_direct_pay": "We are a Step Up For Students direct pay provider",
            "step_up_pep": "Direct pay for PEP",
            "step_up_fes_ua": "Direct pay for FES-UA",
            "meeting_schedule": "When you meet",
            "logo": "Logo",
            "submitter_name": "Your name",
            "submitter_email": "Your email",
            "submitter_role": "Your role at the program",
        }
        help_texts = {
            "short_description": "This is what people read in the directory listing "
            "before they click to your profile page. One sentence.",
            "description": "Tell families what you do, who it is for, and how to join. "
            "Leave a blank line between paragraphs.",
            "facebook": "The whole address, starting with https://.",
            "email": "Published on your page. Leave blank if you would rather not.",
            "serves_grades": "Free text, e.g. 'K–8' or 'high school only'. "
            "If it varies, say so — 'varies by class' is a useful answer too.",
            "cost_notes": "e.g. '$45/semester per family, plus a $20 materials fee'.",
            "step_up_direct_pay": "Tick this if families can pay you directly through "
            "Step Up For Students' EMA marketplace, rather than paying you and claiming "
            "it back.",
            "step_up_pep": "Personalized Education Program.",
            "step_up_fes_ua": "Family Empowerment Scholarship for Students with Unique Abilities.",
            "meeting_schedule": "e.g. 'Tuesdays 9am–noon, September through May'.",
            "logo": "Optional. PNG or JPEG, up to 2 MB.",
            "submitter_email": "So we can ask you a question if something is unclear. "
            "It is never published.",
        }
        widgets = {
            "short_description": forms.TextInput,
            "description": forms.Textarea(attrs={"rows": 8}),
            "cost_notes": forms.Textarea(attrs={"rows": 3}),
            "meeting_schedule": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A registration that cannot be published on approval defeats the point.
        for name in [
            "short_description",
            "description",
            "locations",
            "serves_grades",
            "submitter_name",
            "submitter_email",
        ]:
            self.fields[name].required = True
        self.fields["locations"].label = "Where you meet"
        self.fields["locations"].help_text = (
            "Start typing an address and pick the match. If you meet in more than one "
            "place, add each of them. No fixed address? A city is a perfectly good "
            "answer — type it and press +."
        )

    def clean(self):
        cleaned = super().clean()

        # Someone who ticks PEP but not the box above it has answered the
        # question; rejecting the form over a checkbox they already implied
        # would lose us a registration for nothing.
        if cleaned.get("step_up_pep") or cleaned.get("step_up_fes_ua"):
            cleaned["step_up_direct_pay"] = True
        elif not cleaned.get("step_up_direct_pay"):
            cleaned["step_up_pep"] = False
            cleaned["step_up_fes_ua"] = False

        # Tags are scoped to the heading, so a tag without a heading has
        # nothing to be scoped by. Both of these are unreachable with the
        # JavaScript running — the list narrows as soon as a heading is picked —
        # and both are reachable by anyone posting the form directly.
        category, tags = cleaned.get("category"), cleaned.get("tags")
        if tags:
            if not category:
                self.add_error(
                    "category",
                    "Please choose a heading. The tags you picked depend on it.",
                )
            else:
                allowed = set(category.tags.values_list("pk", flat=True))
                stray = [tag.name for tag in tags if tag.pk not in allowed]
                if stray:
                    self.add_error(
                        "tags",
                        f"We do not use {', '.join(stray)} under {category.name}. "
                        "Please pick from the list, or tell us in your description.",
                    )

        age_min, age_max = cleaned.get("age_min"), cleaned.get("age_max")
        if age_min and age_max and age_min > age_max:
            self.add_error("age_max", "The oldest age cannot be younger than the youngest age.")
        reachable = ["website", "facebook", "email", "phone"]
        if not any(cleaned.get(f) for f in reachable):
            raise forms.ValidationError(
                "Please give at least one way for families to reach you — "
                "a website, a Facebook page, an email address, or a phone number."
            )
        return cleaned

    def save(self, commit=True):
        self.instance.kind = Submission.Kind.REGISTRATION
        return super().save(commit=commit)


class ProgramReferralForm(BaseSubmissionForm):
    """For someone pointing us at a program they do not run.

    Deliberately short. We are collecting a lead, not a listing — the goal is
    enough to reach whoever runs it and invite them to register it themselves.
    """

    class Meta:
        model = Submission
        fields = [
            "program_name",
            "website",
            "facebook",
            "email",
            "phone",
            "locations",
            "category",
            "description",
            "submitter_name",
            "submitter_email",
        ]
        labels = {
            "program_name": "Program name",
            "website": "Website",
            "facebook": "Facebook page URL",
            "email": "Their email, if you know it",
            "phone": "Their phone, if you know it",
            "description": "What do you know about it?",
            "submitter_name": "Your name",
            "submitter_email": "Your email",
        }
        help_texts = {
            "description": "Anything helps — what they do, roughly when they meet, who to ask for.",
            "submitter_email": "So we can follow up if we have a question. It is never published.",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["locations"].label = "Where they meet"
        self.fields[
            "locations"
        ].help_text = "A city is plenty. Start typing and pick the match, or type it and press +."

    def save(self, commit=True):
        self.instance.kind = Submission.Kind.REFERRAL
        return super().save(commit=commit)

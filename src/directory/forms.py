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

from .models import Category, Submission

# Bots fill every field they find; people never see this one.
HONEYPOT_FIELD = "website_url"


class BaseSubmissionForm(forms.ModelForm):
    """Shared honeypot and category widget.

    Spam control is a hidden field plus the moderation queue, not a captcha. A
    captcha costs every legitimate submitter something in exchange for stopping
    bots that an unrendered input already stops.
    """

    website_url = forms.CharField(required=False, widget=forms.HiddenInput)

    categories = forms.ModelMultipleChoiceField(
        queryset=Category.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Tick any that apply.",
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get(HONEYPOT_FIELD):
            raise forms.ValidationError("This submission could not be accepted.")
        return cleaned


class ProgramRegistrationForm(BaseSubmissionForm):
    """For the people who run the program.

    Every field `Program` publishes is collected here, so that approval copies
    a complete record rather than starting a conversation.
    """

    is_authorized = forms.BooleanField(
        required=True,
        label="I run this program, or I am authorized to list it",
    )

    class Meta:
        model = Submission
        fields = [
            "program_name",
            "short_description",
            "description",
            "categories",
            "website",
            "email",
            "phone",
            "locations",
            "serves_grades",
            "age_min",
            "age_max",
            "cost_notes",
            "meeting_schedule",
            "logo",
            "contact_name",
            "contact_role",
            "contact_email",
            "contact_phone",
            "contact_is_public",
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
            "email": "Public email address",
            "phone": "Public phone number",
            "locations": "Where you meet",
            "serves_grades": "Grades served",
            "age_min": "Youngest age",
            "age_max": "Oldest age",
            "cost_notes": "Cost",
            "meeting_schedule": "When you meet",
            "logo": "Logo",
            "contact_name": "Contact name",
            "contact_role": "Their role",
            "contact_email": "Contact email",
            "contact_phone": "Contact phone",
            "contact_is_public": "Show this contact on our public page",
            "submitter_name": "Your name",
            "submitter_email": "Your email",
            "submitter_role": "Your role at the program",
        }
        help_texts = {
            "short_description": "This is what people read in the directory listing "
            "before they click to your profile page. One sentence.",
            "description": "Tell families what you do, who it is for, and how to join. "
            "Leave a blank line between paragraphs.",
            "email": "Published on your page. Leave blank if you would rather not.",
            "locations": "One address per line. If you meet in more than one place, "
            "add a line for each. No fixed address? Indicate which city or cities you"
            " meet in if your meeting location varies.",
            "serves_grades": "Free text, e.g. 'K–8' or 'high school only'. "
            "If it varies, say so — 'varies by class' is a useful answer too.",
            "cost_notes": "e.g. '$45/semester per family, plus a $20 materials fee'.",
            "meeting_schedule": "e.g. 'Tuesdays 9am–noon, September through May'.",
            "logo": "Optional. PNG or JPEG, up to 2 MB.",
            "contact_is_public": "Untick and we will keep these details for our records only.",
            "submitter_email": "So we can ask you a question if something is unclear. "
            "It is never published.",
        }
        widgets = {
            "short_description": forms.TextInput,
            "description": forms.Textarea(attrs={"rows": 8}),
            "locations": forms.Textarea(attrs={"rows": 3}),
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

    def clean(self):
        cleaned = super().clean()
        age_min, age_max = cleaned.get("age_min"), cleaned.get("age_max")
        if age_min and age_max and age_min > age_max:
            self.add_error("age_max", "The oldest age cannot be younger than the youngest age.")
        if not any(cleaned.get(f) for f in ["website", "email", "phone", "contact_email"]):
            raise forms.ValidationError(
                "Please give at least one way for families to reach you — "
                "a website, an email address, or a phone number."
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
            "email",
            "phone",
            "locations",
            "categories",
            "description",
            "submitter_name",
            "submitter_email",
        ]
        labels = {
            "program_name": "Program name",
            "website": "Website, or a Facebook page",
            "email": "Their email, if you know it",
            "phone": "Their phone, if you know it",
            "locations": "Where they meet",
            "description": "What do you know about it?",
            "submitter_name": "Your name",
            "submitter_email": "Your email",
        }
        help_texts = {
            "locations": "A city is plenty. An address if you have one.",
            "description": "Anything helps — what they do, roughly when they meet, who to ask for.",
            "submitter_email": "So we can follow up if we have a question. It is never published.",
        }
        widgets = {
            "locations": forms.TextInput,
            "description": forms.Textarea(attrs={"rows": 5}),
        }

    def save(self, commit=True):
        self.instance.kind = Submission.Kind.REFERRAL
        return super().save(commit=commit)

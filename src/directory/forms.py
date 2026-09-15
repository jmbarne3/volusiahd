from django import forms

from .models import Category, Submission


class SubmissionForm(forms.ModelForm):
    """The public "suggest a program" form.

    Spam control is a honeypot plus the moderation queue, not a captcha. A
    captcha costs every legitimate submitter something in exchange for stopping
    bots that a hidden field already stops.
    """

    # Bots fill every field they find; people never see this one.
    website_url = forms.CharField(required=False, widget=forms.HiddenInput)

    categories = forms.ModelMultipleChoiceField(
        queryset=Category.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Tick any that apply.",
    )

    class Meta:
        model = Submission
        fields = [
            "program_name",
            "website",
            "email",
            "phone",
            "city",
            "categories",
            "description",
            "submitter_name",
            "submitter_email",
        ]
        labels = {
            "program_name": "Program name",
            "website": "Website",
            "email": "Program email",
            "phone": "Program phone",
            "city": "City",
            "description": "Tell us about it",
            "submitter_name": "Your name",
            "submitter_email": "Your email",
        }
        help_texts = {
            "description": "What it is, who it serves, when it meets — a paragraph is plenty.",
            "submitter_email": "So we can ask you a question if something is unclear. "
            "It is never published.",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website_url"):
            raise forms.ValidationError("This submission could not be accepted.")
        return cleaned

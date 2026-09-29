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
from django.core.exceptions import FieldDoesNotExist
from django.db import models

from . import questions as q
from .addresses import LocationsField
from .models import Category, Submission, SubmissionLocation, Tag, TagGroup

# Bots fill every field they find; people never see this one.
HONEYPOT_FIELD = "website_url"


class ScopedChoices:
    """Marks each option with the headings it is offered under, as slugs.

    Which tags go with which heading is `Category.tags`, and the check that
    matters happens in `clean()`. This only writes the pairing into the markup,
    so the page's JavaScript can narrow a list the moment somebody picks a
    heading rather than after they submit. Without JavaScript the whole list
    renders and the server does the rejecting.

    Slugs rather than primary keys, because the other half of the same mechanism
    — `data-asked-by`, which decides whether a whole question is shown — comes
    from `questions.py` and can only speak in slugs. One vocabulary in the markup
    means one code path in the script.
    """

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        tag = getattr(value, "instance", None)
        if tag is not None:
            option["attrs"]["data-categories"] = " ".join(
                sorted(category.slug for category in tag.categories.all())
            )
        return option


class ScopedTagSelect(ScopedChoices, forms.SelectMultiple):
    """The subject tags: a <select multiple>, dressed as a Select2 on the page."""


class ScopedCheckboxes(ScopedChoices, forms.CheckboxSelectMultiple):
    """A tag question that takes several answers."""


class ScopedRadios(ScopedChoices, forms.RadioSelect):
    """A tag question whose answers are alternatives."""


class AnswerChoiceField(forms.ModelChoiceField):
    """One answer to a tag question.

    `Tag.__str__` qualifies an answer with its question, which is right in an
    admin picker and redundant here: the question is already the field's label,
    so "Ability level: Beginner" under a heading reading "Ability level" would say
    it twice.
    """

    def label_from_instance(self, obj):
        return obj.name


class AnswerMultipleChoiceField(forms.ModelMultipleChoiceField):
    """Several answers to one tag question. See `AnswerChoiceField`."""

    def label_from_instance(self, obj):
        return obj.name


class CategorySelect(forms.Select):
    """The heading dropdown, with each option carrying its slug.

    The value stays the primary key — that is what a `ModelChoiceField` resolves
    and what every existing form post sends — but everything that reacts to the
    choice reasons in slugs, so the slug travels alongside rather than being
    looked up.
    """

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        category = getattr(value, "instance", None)
        if category is not None:
            option["attrs"]["data-slug"] = category.slug
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
        widget=CategorySelect,
        empty_label="Choose the closest one",
        help_text="The one heading that fits best. It also decides which of the "
        "questions below we bother you with.",
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
        queryset=Tag.objects.filter(categories__isnull=False, group__isnull=True)
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
        "look at adding it. Answers to the questions above are tags too — they are "
        "asked separately because they have a fixed set of answers.",
    )

    class Meta:
        model = Submission
        # In the order the page asks them. Which of these a registrant actually
        # sees depends on the heading they pick — see `questions.py` and
        # `_scope_fields` below.
        fields = [
            "program_name",
            "host_name",
            "short_description",
            "description",
            "category",
            "tags",
            "class_names",
            "faith_basis",
            "instructor_info",
            "highlights",
            "website",
            "facebook",
            "email",
            "phone",
            "locations",
            "delivery",
            "travels_to_student_home",
            "service_area",
            "serves_grades",
            "age_min",
            "age_max",
            "meeting_days",
            "meeting_time_start",
            "meeting_time_end",
            "season_start",
            "season_end",
            "meeting_schedule",
            "enrollment_opens",
            "enrollment_closes",
            "enrollment_notes",
            "cost_basis",
            "cost_notes",
            "step_up_direct_pay",
            "step_up_pep",
            "step_up_fes_ua",
            "logo",
            "submitter_name",
            "submitter_email",
            "submitter_role",
            "is_authorized",
        ]
        labels = {
            "program_name": "Program name",
            "host_name": "Host or group name",
            "short_description": "One-line description",
            "description": "Full description",
            "class_names": "Classes offered",
            "faith_basis": "Faith affiliation",
            "instructor_info": "Who teaches, and what qualifies them",
            "highlights": "What makes your program special",
            "website": "Website",
            "facebook": "Facebook page URL",
            "email": "Public email address",
            "phone": "Public phone number",
            "delivery": "In person or online",
            "travels_to_student_home": "We travel to the student's home",
            "service_area": "How far you travel",
            "serves_grades": "Grades served",
            "age_min": "Youngest age",
            "age_max": "Oldest age",
            "meeting_days": "Days you meet",
            "meeting_time_start": "Starts at",
            "meeting_time_end": "Ends at",
            "season_start": "First meeting",
            "season_end": "Last meeting",
            "meeting_schedule": "Anything else about when you meet",
            "enrollment_opens": "Enrollment opens",
            "enrollment_closes": "Enrollment closes",
            "enrollment_notes": "Enrollment, in words",
            "cost_basis": "Cost",
            "cost_notes": "Cost, in detail",
            "step_up_direct_pay": "We are a Step Up For Students direct pay provider",
            "step_up_pep": "Direct pay for PEP",
            "step_up_fes_ua": "Direct pay for FES-UA",
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
            "host_name": "Only if your listing is a class or a team run under a larger "
            "group. Leave it blank if they are the same thing.",
            "class_names": "Separated by commas — e.g. 'Algebra I, Geometry, Chemistry'.",
            "faith_basis": "Families ask, so we ask. Leave it blank if you would rather "
            "not answer — that is not the same as saying no, and we will publish neither.",
            "instructor_info": "Families ask this before they ask almost anything else. "
            "Names are optional; qualifications and experience are what they are after.",
            "highlights": "What a family gets with you that they would not get anywhere "
            "else. Two or three sentences is plenty.",
            "facebook": "The whole address, starting with https://.",
            "email": "Published on your page. Leave blank if you would rather not.",
            "service_area": "e.g. 'west Volusia only' or 'up to 30 miles'.",
            "serves_grades": "Free text, e.g. 'K–8' or 'high school only'. "
            "If it varies, say so — 'varies by class' is a useful answer too.",
            "meeting_days": "Tick every day you normally meet. Families filter by this.",
            "meeting_time_start": "Leave blank if it varies.",
            "season_start": "The date your term or season starts. Leave blank if you run "
            "year round.",
            "enrollment_notes": "e.g. 'rolling admission' or 'waiting list only'.",
            "cost_basis": "The headline answer. The detail goes in the next box.",
            "cost_notes": "e.g. '$45/semester per family, plus a $20 materials fee'.",
            "step_up_direct_pay": "Tick this if families can pay you directly through "
            "Step Up For Students' EMA marketplace, rather than paying you and claiming "
            "it back.",
            "step_up_pep": "Personalized Education Program.",
            "step_up_fes_ua": "Family Empowerment Scholarship for Students with Unique Abilities.",
            "meeting_schedule": "For what the boxes above cannot say — e.g. 'alternate "
            "Tuesdays' or 'times vary by class'.",
            "logo": "Optional. PNG or JPEG, up to 2 MB.",
            "submitter_email": "So we can ask you a question if something is unclear. "
            "It is never published.",
        }
        widgets = {
            "short_description": forms.TextInput,
            "description": forms.Textarea(attrs={"rows": 8}),
            "instructor_info": forms.Textarea(attrs={"rows": 4}),
            "highlights": forms.Textarea(attrs={"rows": 4}),
            "cost_notes": forms.Textarea(attrs={"rows": 3}),
            "meeting_schedule": forms.Textarea(attrs={"rows": 2}),
            # `type=date` and `type=time` rather than a date picker of our own:
            # every browser and phone that matters has one, and theirs is the one
            # the person filling this in already knows how to use.
            "season_start": forms.DateInput(attrs={"type": "date"}),
            "season_end": forms.DateInput(attrs={"type": "date"}),
            "enrollment_opens": forms.DateInput(attrs={"type": "date"}),
            "enrollment_closes": forms.DateInput(attrs={"type": "date"}),
            "meeting_time_start": forms.TimeInput(attrs={"type": "time"}),
            "meeting_time_end": forms.TimeInput(attrs={"type": "time"}),
        }

    # Blank is a real answer to each of these, and "---------" says so badly.
    BLANK_LABELS = {
        "faith_basis": "Prefer not to say",
        "cost_basis": "Choose one",
        "delivery": "Choose one",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A registration that cannot be published on approval defeats the point.
        # Only the questions every heading asks can be required up front; the rest
        # are enforced in `clean()`, once we know which heading was picked.
        for name in q.REQUIRED & q.CORE:
            if name in self.fields:
                self.fields[name].required = True
        self.fields["locations"].label = "Where you meet"
        self.fields["locations"].help_text = (
            "Start typing an address and pick the match. If you meet in more than one "
            "place, add each of them. No fixed address? A city is a perfectly good "
            "answer — type it and press +."
        )
        for name, label in self.BLANK_LABELS.items():
            field = self.fields[name]
            field.choices = [("", label), *[pair for pair in field.choices if pair[0]]]

        self._add_question_fields()
        self._scope_fields()

    # --- The questions that are tags ----------------------------------------

    def _add_question_fields(self):
        """One field per tag question, built from what is in the database.

        The answers are `Tag` rows in a `TagGroup`, so a new question — or a
        reworded answer — is a change she makes in the admin and sees on this form
        the next time somebody loads it. What she cannot do from there is invent a
        question with nowhere to put the answer, which is the trade this design is
        making.

        Every question is rendered, not just the ones the chosen heading asks: the
        heading is picked on this same page, so the narrowing is the script's job
        and the scoping below is what it reads.
        """
        self.question_groups = []
        groups = TagGroup.objects.prefetch_related("tags__categories")
        for group in groups:
            answers = [tag for tag in group.tags.all() if tag.categories.exists()]
            if not answers:
                # A question nobody is offered an answer to is not a question.
                continue
            self.question_groups.append(group)
            field_class = AnswerMultipleChoiceField if group.allows_several else AnswerChoiceField
            widget = ScopedCheckboxes if group.allows_several else ScopedRadios
            field = field_class(
                queryset=Tag.objects.filter(pk__in=[tag.pk for tag in answers]).prefetch_related(
                    "categories"
                ),
                required=False,
                widget=widget,
                label=group.name,
                help_text=group.prompt,
            )
            if not group.allows_several:
                field.empty_label = None
            self.fields[self.question_field_name(group)] = field

    @staticmethod
    def question_field_name(group):
        return f"question_{group.slug.replace('-', '_')}"

    @property
    def question_fields(self):
        """The tag questions as bound fields, for the template to loop over."""
        return [self[self.question_field_name(group)] for group in self.question_groups]

    # --- Which questions this heading asks ----------------------------------

    def _scope_fields(self):
        """Write each field's headings into the markup as `data-asked-by`.

        The script hides a field whose headings do not include the chosen one, and
        `clean()` forgets anything answered in a field that is not asked. Neither
        is load-bearing: with no script running every question is shown, and
        answering one that does not apply to you is not an error we should make
        somebody fix.
        """
        for name, field in self.fields.items():
            headings = self._headings_for(name)
            if headings is not None and headings != q.EVERY:
                field.widget.attrs["data-asked-by"] = " ".join(sorted(headings))

    def _headings_for(self, name):
        """Which headings ask about a field, or None if nothing scopes it.

        A tag question is scoped by which headings offer its answers, which is
        `Category.tags` and not this module — the same single source of truth that
        decides whether the question has any answers at all.
        """
        if name in q.ASKED_BY:
            return q.ASKED_BY[name]
        for group in self.question_groups:
            if name == self.question_field_name(group):
                return frozenset(
                    category.slug for tag in group.tags.all() for category in tag.categories.all()
                )
        return None

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get("category")
        # No heading chosen, and the heading is what decides which questions were
        # asked. So nothing is treated as unasked: every answer is kept and
        # everything on the required list is still required. Narrowing a form on
        # the strength of a question nobody answered would throw away work.
        asked = q.fields_for(category.slug) if category else None
        if asked is not None:
            self._forget_unasked(cleaned, asked)
        self._require_asked(cleaned, asked)

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

        self._fold_in_answers(cleaned, category)

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

    def _forget_unasked(self, cleaned, asked):
        """Throw away answers to questions this heading does not ask.

        Silently, and that is the point. Somebody who fills in the meeting days,
        changes their mind about the heading and submits has not lied to us; they
        have answered a question we then stopped asking. Erroring would be the form
        blaming them for our own narrowing, so the answer is dropped and the
        registration goes through.

        Only reachable with the script switched off or a direct post — the script
        clears a field as it hides it.
        """
        for name in self.fields:
            if name in q.ASKED_BY and name not in asked:
                cleaned[name] = self._blank_for(name)

    def _require_asked(self, cleaned, asked):
        """Enforce the required answers that depend on the heading.

        Everything asked of all thirteen headings is already `required` on the
        field. What is left is the handful that are required where they are asked
        and meaningless where they are not — grades, of a parent support group.

        `asked` of None means no heading was chosen, so every one of them applies.
        """
        for name in q.REQUIRED - q.CORE:
            if (asked is None or name in asked) and name in self.fields and not cleaned.get(name):
                self.add_error(name, self.fields[name].error_messages["required"])

    def _fold_in_answers(self, cleaned, category):
        """Add the tag questions' answers to `tags`, where they are stored.

        An answer is a tag, so it needs no column and no second place to look: the
        program page, the tag page and every filter treat "Varsity" exactly as they
        treat "Lego". Answers the chosen heading is not offered are dropped rather
        than refused, for the same reason as `_forget_unasked`.
        """
        offered = set(category.tags.values_list("pk", flat=True)) if category else set()
        chosen = list(cleaned.get("tags") or [])
        seen = {tag.pk for tag in chosen}
        for group in self.question_groups:
            answer = cleaned.get(self.question_field_name(group))
            answers = list(answer) if isinstance(answer, (list, models.QuerySet)) else [answer]
            for tag in answers:
                if tag is not None and tag.pk in offered and tag.pk not in seen:
                    seen.add(tag.pk)
                    chosen.append(tag)
        cleaned["tags"] = chosen

    def _blank_for(self, name):
        """The empty value for a field, as its column would understand it."""
        try:
            field = Submission._meta.get_field(name)
        except FieldDoesNotExist:
            return None
        if isinstance(field, models.BooleanField):
            return False
        return None if field.null else ""

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

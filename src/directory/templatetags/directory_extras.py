"""Template helpers for the public forms and the category colour code.

The registration form is long enough that laying it out field by field is the
only way to group it into fieldsets a human can read. `{% field form.x %}`
keeps that readable without repeating the markup twenty-five times.
"""

from django import forms, template

register = template.Library()

# How many colours the category code rotates through — one per heading, and there
# are thirteen headings. The colours themselves live in the --code-* block in
# site.css.
#
# This was nine, on the stated assumption that the taxonomy was collapsing to
# fewer than ten. It did the opposite: the site owner's programme types number
# thirteen, so two pairs of headings were sharing a chip colour on the filter bar.
#
# Thirteen hues around the wheel are 27 degrees apart rather than 40, which is
# less distinct than before and about as far as a generated ring can be pushed.
# The note the nine-colour version left still stands and now stands more firmly:
# a fourteenth heading should be the moment this becomes a colour field on
# `Category`, chosen by somebody with an opinion, rather than a thinner slice.
# Tags have no colour at all, by design.
CATEGORY_COLOUR_COUNT = 13


@register.filter
def colour_code(category):
    """Return a stable 1-9 colour code for a category.

    Keyed on the primary key so a category keeps its colour everywhere it
    appears and across page loads, rather than depending on its position in
    whatever list happens to be rendering. Editors do not choose the colour.
    If they ever need to, the fix is a colour field on Category rather than
    more cleverness here.
    """
    key = getattr(category, "pk", None) or 0
    return (key - 1) % CATEGORY_COLOUR_COUNT + 1


@register.inclusion_tag("directory/_field.html")
def field(bound_field, is_answer=False):
    """One form row.

    `is_answer` marks a row as the answers to a tag question, which the
    registration form's script needs to know: those rows hide themselves when
    every answer inside them has been narrowed away, and no other row does.
    """
    widget = bound_field.field.widget
    return {
        "field": bound_field,
        "is_checkbox": isinstance(widget, forms.CheckboxInput),
        # A group of radios or checkboxes has no single input for a <label for>
        # to point at, so it gets a heading of its own instead.
        "is_group": isinstance(widget, (forms.CheckboxSelectMultiple, forms.RadioSelect)),
        "is_answer": is_answer,
    }

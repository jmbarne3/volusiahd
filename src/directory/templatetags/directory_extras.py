"""Template helpers for the public forms and the category colour code.

The registration form is long enough that laying it out field by field is the
only way to group it into fieldsets a human can read. `{% field form.x %}`
keeps that readable without repeating the markup twenty-five times.
"""

from django import template

register = template.Library()

# How many colours the category code rotates through. Nine — one per category,
# and the taxonomy is collapsing to fewer than ten, so there is deliberately no
# headroom here. The colours themselves live in the --code-* block in site.css.
#
# This used to be thirty, which bought room to grow at the cost of hues twelve
# degrees apart that nobody could tell apart. Fewer categories means the budget
# can go into distinctness instead: nine hues forty degrees apart, each with its
# own lightness and chroma.
#
# A tenth category wraps back to the first and two categories share a colour.
# If the taxonomy ever grows past nine, the fix is a colour field on `Category`
# rather than a wider ring — at that point the colours are editorial, not
# generated. Tags have no colour at all, by design.
CATEGORY_COLOUR_COUNT = 9


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
def field(bound_field):
    widget = bound_field.field.widget
    return {
        "field": bound_field,
        "is_checkbox": widget.__class__.__name__ == "CheckboxInput",
        "is_multi_checkbox": widget.__class__.__name__ == "CheckboxSelectMultiple",
    }

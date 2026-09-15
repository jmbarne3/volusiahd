"""Template helpers for the public forms and the category colour code.

The registration form is long enough that laying it out field by field is the
only way to group it into fieldsets a human can read. `{% field form.x %}`
keeps that readable without repeating the markup twenty-five times.
"""

from django import template

register = template.Library()

# How many colours the category code rotates through. The ring is nine muted
# hues evenly spaced around the colour circle, anchored on terracotta's own
# hue; see the --code-* block in site.css. Nine is the current category count,
# so today every category has its own. Adding a tenth wraps it back to the
# first, which is the point at which the ring should be regenerated rather
# than stretched: the hues are already as close together as stays legible.
CATEGORY_COLOUR_COUNT = 9


@register.filter
def colour_code(category):
    """Return a stable 1-4 colour code for a category.

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

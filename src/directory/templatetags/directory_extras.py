"""Template helpers for the public forms and the category colour code.

The registration form is long enough that laying it out field by field is the
only way to group it into fieldsets a human can read. `{% field form.x %}`
keeps that readable without repeating the markup twenty-five times.
"""

from django import template

register = template.Library()

# How many colours the category code rotates through. Thirty: ten muted hues
# evenly spaced around the colour circle and anchored on terracotta, in three
# tones. See the --code-* block in site.css, which is where the colours
# actually live.
#
# Ten hues would have been the natural stopping point — below about 30 degrees
# apart two dots stop being tellable apart — so the extra headroom comes from
# tone rather than from crowding the circle. Codes run through all ten hues
# before the tone changes, which means the first ten categories are the most
# distinct set available and growth degrades gently from there.
#
# Thirty-one wraps back to the first. At that point regenerate the ring rather
# than stretch it further, or give `Category` a colour field and let editors
# choose.
CATEGORY_COLOUR_COUNT = 30


@register.filter
def colour_code(category):
    """Return a stable 1-30 colour code for a category.

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

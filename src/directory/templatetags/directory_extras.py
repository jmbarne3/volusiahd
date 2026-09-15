"""Template helpers for the public forms.

The registration form is long enough that laying it out field by field is the
only way to group it into fieldsets a human can read. `{% field form.x %}`
keeps that readable without repeating the markup twenty-five times.
"""

from django import template

register = template.Library()


@register.inclusion_tag("directory/_field.html")
def field(bound_field):
    widget = bound_field.field.widget
    return {
        "field": bound_field,
        "is_checkbox": widget.__class__.__name__ == "CheckboxInput",
        "is_multi_checkbox": widget.__class__.__name__ == "CheckboxSelectMultiple",
    }

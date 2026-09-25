"""The address picker: one input, a list of suggestions, a list of chosen places.

Shared by the public forms and the admin, because the problem is the same in
both places. Somebody types part of an address, the browser asks
`views.address_search` what that might be, and they pick. What gets submitted is
the place they picked — its wording and its coordinates — rather than a line of
text for somebody else to interpret later.

That is the whole argument for doing it this way. A geocoder run after the fact
returns its best guess and sounds certain about it; asked live, it offers five
and lets the person who actually knows where they meet choose. The one who knows
is the one choosing.

Three things follow from accepting coordinates from a browser. They are
validated here rather than trusted — a number that is not a number, or a
latitude past the pole, is a bad request and not a location. Nothing a submitter
sends is published before somebody approves it, which is what makes accepting
them reasonable at all. And a place with no coordinates stays perfectly valid:
the + button exists so that "at members' homes" can be entered by someone who
was never going to find it on a map.

Without JavaScript the widget falls back to a textarea, one address per line,
which is exactly what the form collected before any of this existed.
"""

import json

from django import forms
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.html import format_html, format_html_join

# Enough places for a program that really does meet in several, few enough that
# a runaway script cannot write a thousand rows through the public form.
MAX_LOCATIONS = 10

# Below this, a search is mostly noise: "de" matches half of Florida. The number
# is rendered into the markup so the script and the server agree on it.
MIN_QUERY_CHARS = 3

# How long the typing has to stop before anything is asked. Deliberately longer
# than the usual autocomplete twitch: somebody typing an address types the whole
# thing, and a request per keystroke would mean twenty lookups to find one place.
# At 450ms an ordinary typist produces one request per address, not per letter.
DEBOUNCE_MS = 450

FIELD_LIMITS = {"query": 255, "label": 255, "city": 80, "postcode": 16, "osm_type": 1}


def parse_place(raw):
    """One submitted value as a dict of model fields, or None if it is empty.

    Two shapes arrive. A pick from the suggestion list is a JSON object carrying
    the coordinates that were chosen. Anything else is plain text — typed into
    the no-JavaScript fallback, or added with the + button — and stays a query
    with no point until something looks it up.

    Raises ValidationError on a payload that cannot be read, which is a bug or a
    tampered form rather than a mistake a person made, so the message only has
    to be honest rather than helpful.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.startswith("{"):
        return _place(query=raw)

    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise forms.ValidationError("That address could not be read. Please add it again.")

    query = str(payload.get("query") or payload.get("label") or "").strip()
    if not query:
        raise forms.ValidationError("That address had nothing in it. Please add it again.")

    latitude = _coordinate(payload.get("latitude"), 90)
    longitude = _coordinate(payload.get("longitude"), 180)
    return _place(
        query=query,
        label=str(payload.get("label") or "").strip(),
        # Half a point is no point. Storing one of the two would put a program on
        # the prime meridian and look like data.
        latitude=latitude if longitude is not None else None,
        longitude=longitude if latitude is not None else None,
        city=str(payload.get("city") or "").strip(),
        postcode=str(payload.get("postcode") or "").strip(),
        osm_type=str(payload.get("osm_type") or "").strip(),
        osm_id=payload.get("osm_id") if isinstance(payload.get("osm_id"), int) else None,
    )


def _place(**values):
    """Trim every string to what the column holds, and set the status to match."""
    place = {
        "query": "",
        "label": "",
        "latitude": None,
        "longitude": None,
        "city": "",
        "postcode": "",
        "osm_type": "",
        "osm_id": None,
        **values,
    }
    for field, limit in FIELD_LIMITS.items():
        place[field] = place[field][:limit]

    resolved = place["latitude"] is not None and place["longitude"] is not None
    place["status"] = "resolved" if resolved else "pending"
    place["resolved_at"] = timezone.now() if resolved else None
    return place


def _coordinate(value, limit):
    """A float inside ±limit, or None. Never an exception and never a wrong number."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or abs(number) > limit:  # NaN fails its own comparison
        return None
    return number


def display_name(place):
    """What to show for a chosen place: the resolved wording, or what was typed."""
    return place.get("label") or place.get("query") or ""


class AddressPickerWidget(forms.Widget):
    """A search box, a list of what was chosen, and a textarea for when no script runs.

    Each chosen place is a hidden input holding the JSON the server parses, so
    submitting the form sends every place at once with no round trip in between.

    The search box submits too, under `<field>_typed`, and that is not an
    oversight. Somebody who types an address and presses Send without pressing +
    has told us where they meet; throwing it away and answering "please add at
    least one place" would be a form arguing with someone who just answered the
    question. Whatever is left in the box arrives as a place waiting to be looked
    up, after the ones that were picked deliberately.
    """

    template_name = None  # rendered in Python; there is no partial worth reusing

    def value_from_datadict(self, data, files, name):
        typed = f"{name}_typed"
        if hasattr(data, "getlist"):
            return data.getlist(name) + data.getlist(typed)
        return [value for value in [data.get(name), data.get(typed)] if value]

    def value_omitted_from_data(self, data, files, name):
        return name not in data and f"{name}_typed" not in data

    def use_required_attribute(self, initial):
        # The visible input is not the field. Marking it required would stop a
        # form that has three chosen addresses and an empty search box.
        return False

    def render(self, name, value, attrs=None, renderer=None):
        chosen = [(raw, display_name(place)) for raw, place in _values(value)]
        element_id = (attrs or {}).get("id") or f"id_{name}"

        return format_html(
            '<div class="address-picker" data-address-picker '
            'data-field="{field}" data-endpoint="{endpoint}" data-min-chars="{min_chars}" '
            'data-max="{max_places}" data-debounce="{debounce}">'
            '<div class="address-picker__row">'
            '<input type="search" id="{id}" name="{field}_typed"'
            ' class="address-picker__input" autocomplete="off"'
            ' role="combobox" aria-expanded="false" aria-autocomplete="list"'
            ' aria-controls="{id}_results" placeholder="Start typing an address"'
            " data-address-input>"
            '<button type="button" class="address-picker__add" data-address-add'
            ' aria-label="Add what you typed">+</button>'
            "</div>"
            '<ul id="{id}_results" class="address-picker__results" role="listbox" hidden'
            " data-address-results></ul>"
            '<ul class="address-picker__chosen" data-address-chosen>{chosen}</ul>'
            '<p class="address-picker__status" data-address-status aria-live="polite"></p>'
            "<noscript>"
            '<textarea name="{field}" rows="3" class="address-picker__fallback">{typed}</textarea>'
            '<span class="hint">One address per line.</span>'
            "</noscript>"
            "</div>",
            field=name,
            id=element_id,
            endpoint=str(reverse_lazy("directory:address_search")),
            min_chars=MIN_QUERY_CHARS,
            max_places=MAX_LOCATIONS,
            debounce=DEBOUNCE_MS,
            chosen=self.render_chosen(name, chosen),
            typed="\n".join(label for _, label in chosen),
        )

    def render_chosen(self, name, chosen):
        """The places already picked, as removable rows.

        Rendered server side as well as by the script, so that a form coming back
        with an error still shows what somebody had already chosen. Losing five
        addresses because the phone number was wrong is how a registration turns
        into an abandoned registration.
        """
        return format_html_join(
            "",
            '<li class="address-picker__place" data-address-place>'
            '<span class="address-picker__label">{1}</span>'
            '<input type="hidden" name="{0}" value="{2}">'
            '<button type="button" class="address-picker__remove" data-address-remove'
            ' aria-label="Remove {1}">×</button>'
            "</li>",
            ((name, label, raw) for raw, label in chosen),
        )


class LocationsField(forms.Field):
    """Every place somebody chose, as a list of dicts ready to become rows."""

    widget = AddressPickerWidget
    default_error_messages = {
        "required": "Please add at least one place, even if it is only a city.",
        "too_many": "That is more than %(limit)s places. Please describe the rest in words.",
    }

    def __init__(self, *, max_locations=MAX_LOCATIONS, **kwargs):
        self.max_locations = max_locations
        super().__init__(**kwargs)

    def clean(self, value):
        places, seen = [], set()
        for raw in value or []:
            for part in _split(raw):
                if not (place := parse_place(part)):
                    continue
                # The same address twice is a slip, not an error worth a message.
                key = (place["query"].casefold(), place["latitude"], place["longitude"])
                if key not in seen:
                    seen.add(key)
                    places.append(place)

        if not places and self.required:
            raise forms.ValidationError(self.error_messages["required"], code="required")
        if len(places) > self.max_locations:
            raise forms.ValidationError(
                self.error_messages["too_many"],
                code="too_many",
                params={"limit": self.max_locations},
            )
        return places

    def has_changed(self, initial, data):
        return bool(data)


def _split(raw):
    """One submitted value, as the one or several places it stands for."""
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("{"):
        return [raw]
    # The no-JavaScript textarea: one address per line, as it always was.
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _values(value):
    """Whatever the field holds, as (raw submitted string, parsed place) pairs.

    Redisplay after a validation error hands back the raw strings somebody
    submitted; an unbound form may hand back rows or dicts. Both have to render.
    """
    pairs = []
    for item in value or []:
        if isinstance(item, dict):
            pairs.append((json.dumps(_payload(item)), item))
        elif hasattr(item, "query"):
            place = {"query": item.query, "label": item.label}
            pairs.append((json.dumps(_payload(item.__dict__)), place))
        else:
            for part in _split(item):
                try:
                    place = parse_place(part)
                except forms.ValidationError:
                    continue
                if place:
                    pairs.append((part, place))
    return pairs


def _payload(place):
    """A place as the JSON the widget submits and `parse_place` reads back."""
    keys = ["query", "label", "latitude", "longitude", "city", "postcode", "osm_type", "osm_id"]
    return {key: place.get(key) for key in keys if place.get(key) not in (None, "")}

"""Turning an address somebody typed into a point on a map.

Photon is an OpenStreetMap geocoder with no API key, no account and no
per-request charge, which is why it is here rather than Google or Mapbox: this
directory geocodes a few hundred addresses once and then rarely again, and
paying for that — in money, or in a billing account somebody has to still own in
five years — buys nothing a volunteer-run county directory needs.

This module is sealed off the way `accounts/google.py` is. It does not import
models, does not touch the database, and does not decide when a lookup should
happen. It turns a string into `Place` objects or raises `GeocoderUnavailable`.
When to look an address up, what to do with a miss, and who may trigger one are
questions for `models.py` and `admin.py`.

Three things are worth knowing about the service. The public instance is run as
a courtesy by komoot and asks callers not to use it for bulk work, which is why
batch resolution pauses between requests and why nothing geocodes inside a
public page request. It is a dependency we do not control, so `GEOCODER_URL` is
a setting and a self-hosted Photon is a drop-in replacement. And it is
best-effort: a miss is a normal outcome, not an error, because people write
"the Presbyterian church on Ocean Ave" and mean it.
"""

import json
import logging
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 5.0

# How long to wait between requests in a batch. Slower than the service can
# answer, on purpose: see the note about bulk use above.
BATCH_PAUSE_SECONDS = 1.0

# Every program in this directory is in Florida or a few miles outside it, so
# the state name in a label is noise until it is not Florida — at which point it
# is the most important word in the label.
LOCAL_STATE = "Florida"


class GeocoderUnavailable(Exception):
    """The lookup could not be attempted. Distinct from finding no match.

    A caller that gets this should leave the address alone and try later; a
    caller that gets an empty result has an answer, and the answer is no.
    """


@dataclass(frozen=True)
class Place:
    """One candidate match: somewhere to put on a map, and what to call it."""

    label: str
    latitude: float
    longitude: float
    city: str = ""
    postcode: str = ""
    osm_type: str = ""
    osm_id: int | None = None


def search(query, *, limit=5, timeout=TIMEOUT_SECONDS):
    """Candidate matches for `query`, best first. Empty list means no match."""
    query = (query or "").strip()
    if not query:
        return []
    if not settings.GEOCODER_URL:
        raise GeocoderUnavailable("No geocoder is configured.")

    latitude, longitude = settings.GEOCODER_BIAS
    url = "{}?{}".format(
        settings.GEOCODER_URL,
        urlencode({"q": query, "limit": limit, "lang": "en", "lat": latitude, "lon": longitude}),
    )
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            # Photon's operators ask to be able to tell callers apart, and a
            # nameless client is the one they block first.
            "User-Agent": f"volusiahd ({settings.SITE_BASE_URL})",
        },
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as exc:
        logger.warning("Geocoder returned HTTP %s for %r", exc.code, query)
        raise GeocoderUnavailable(f"The geocoder returned HTTP {exc.code}.") from None
    except (URLError, TimeoutError) as exc:
        logger.warning("Could not reach the geocoder: %s", exc)
        raise GeocoderUnavailable("The geocoder could not be reached.") from None

    try:
        parsed = json.loads(body)
        features = parsed["features"]
    except (ValueError, KeyError, TypeError):
        logger.warning("Geocoder response could not be read for %r", query)
        raise GeocoderUnavailable("The geocoder's response could not be read.") from None

    places = [_place(feature) for feature in features if isinstance(feature, dict)]
    return [place for place in places if place is not None]


def resolve(query, *, timeout=TIMEOUT_SECONDS):
    """The single best match for `query`, or None if there is not one.

    Photon orders its results, and second-guessing that order without a map in
    front of a human is guesswork dressed up as logic. An editor who disagrees
    with the answer can overwrite it.
    """
    matches = search(query, limit=1, timeout=timeout)
    return matches[0] if matches else None


def _place(feature):
    """One GeoJSON feature as a `Place`, or None if it is not usable."""
    properties = feature.get("properties") or {}
    coordinates = (feature.get("geometry") or {}).get("coordinates") or []
    if len(coordinates) < 2:
        return None
    try:
        # GeoJSON is longitude first. Getting this backwards puts Volusia
        # County in the Indian Ocean, which is the one comforting thing about
        # the mistake: it is never subtle.
        longitude, latitude = float(coordinates[0]), float(coordinates[1])
    except (TypeError, ValueError):
        return None

    osm_id = properties.get("osm_id")
    return Place(
        label=_label(properties),
        latitude=latitude,
        longitude=longitude,
        city=(properties.get("city") or properties.get("locality") or "")[:80],
        postcode=(properties.get("postcode") or "")[:16],
        osm_type=(properties.get("osm_type") or "")[:1],
        osm_id=osm_id if isinstance(osm_id, int) else None,
    )


def _label(properties):
    """A one-line address a family would recognise.

    Photon fills in whatever it knows, and which keys it uses depends on what
    matched: a church comes back with a `name` and no house number, a street
    address with a house number and no name, a town with a name that repeats as
    the city. So the parts are assembled in reading order and de-duplicated
    rather than formatted from a fixed template.
    """
    street = " ".join(
        part for part in [properties.get("housenumber"), properties.get("street")] if part
    )
    state = properties.get("state") or ""
    parts = [
        properties.get("name") or "",
        street,
        properties.get("city") or properties.get("locality") or "",
        properties.get("district") if not properties.get("city") else "",
        "" if state == LOCAL_STATE else state,
        properties.get("postcode") or "",
    ]

    seen, label = set(), []
    for part in parts:
        part = (part or "").strip()
        if part and part.casefold() not in seen:
            seen.add(part.casefold())
            label.append(part)
    return ", ".join(label)[:255]

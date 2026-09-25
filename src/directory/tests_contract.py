"""Checks our assumptions about Photon against Photon.

Every other geocoding test in this app fakes the service's side of the
conversation, which means they will all keep passing on the day Photon changes
the shape of an answer. This file is the one that will not. It asks the live
service for a real Volusia County address and checks that what comes back still
has the keys `geocoding._place()` reads and still lands where it should.

It talks to the internet, so it is tagged `network` and excluded from the
ordinary run — the same arrangement as `accounts/tests/test_contract.py`:

    python manage.py test directory --exclude-tag=network   # the everyday suite
    python manage.py test directory.tests_contract          # this file

Run it before a deploy. A failure here is not a bug in this code; it means
Photon moved and `geocoding.py` has to follow. When the service is unreachable
these tests skip rather than fail, so a green run offline says nothing at all.

Two requests, once, on purpose. The public instance is a courtesy and this file
is not a load test.
"""

from django.test import SimpleTestCase, override_settings, tag

from . import geocoding

# The county library's headquarters in DeLand: a real address, unlikely to move,
# and central enough that a wrong answer is obvious.
KNOWN_ADDRESS = "101 East Rich Avenue, DeLand, Florida"
KNOWN_POINT = (29.0289, -81.3031)

# Generous enough that a geocoder splitting hairs over which side of the street
# a building sits on passes, tight enough that the wrong DeLand fails.
TOLERANCE_DEGREES = 0.05


@tag("network")
@override_settings(GEOCODER_URL="https://photon.komoot.io/api")
class PhotonContractTests(SimpleTestCase):
    """What the live service returns, against what this code expects of it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            cls.matches = geocoding.search(KNOWN_ADDRESS, limit=3)
        except geocoding.GeocoderUnavailable as exc:
            cls.matches = None
            cls.reason = str(exc)

    def setUp(self):
        if self.matches is None:
            self.skipTest(f"Photon was not reachable: {self.reason}")

    def test_a_known_address_still_resolves_to_where_it_is(self):
        """The whole contract in one assertion: right keys, right hemisphere."""
        best = self.matches[0]
        latitude, longitude = KNOWN_POINT
        self.assertAlmostEqual(best.latitude, latitude, delta=TOLERANCE_DEGREES)
        self.assertAlmostEqual(best.longitude, longitude, delta=TOLERANCE_DEGREES)

    def test_the_label_is_something_a_family_would_recognise(self):
        best = self.matches[0]
        self.assertIn("DeLand", best.label)
        self.assertTrue(best.label.strip())
        self.assertLessEqual(len(best.label), 255)

    def test_the_fields_we_store_alongside_the_point_are_still_there(self):
        """`city` and `postcode` are what a text search leans on, so they matter."""
        best = self.matches[0]
        self.assertEqual(best.city, "DeLand")
        self.assertTrue(best.postcode.startswith("327"))

    def test_asking_for_nowhere_returns_nothing_rather_than_failing(self):
        """A miss has to stay distinguishable from an outage against the real service."""
        self.assertEqual(geocoding.search("zzzqqq no such place at all zzzqqq"), [])

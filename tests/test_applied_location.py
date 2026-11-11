"""Applied-location bubbles: Germany + Remote only (no Mongo)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.applied_location import (  # noqa: E402
    _city_in_text,
    is_germany_or_remote,
    normalize_applied_location,
)
from core.db_mongo import (  # noqa: E402
    is_germany_or_remote as facade_is_germany_or_remote,
    normalize_applied_location as facade_normalize,
)


class NormalizeAppliedLocationTests(unittest.TestCase):
    def test_blank_and_pure_remote(self):
        self.assertEqual(normalize_applied_location(""), "Remote")
        self.assertEqual(normalize_applied_location("Remote"), "Remote")
        self.assertEqual(normalize_applied_location("100% remote"), "Remote")
        self.assertEqual(normalize_applied_location("European Union"), "Remote")

    def test_city_beats_work_mode_tag(self):
        self.assertEqual(normalize_applied_location("Berlin (hybrid)"), "Berlin")
        self.assertEqual(normalize_applied_location("München (Remote)"), "Munich")
        self.assertEqual(normalize_applied_location("Köln"), "Cologne")

    def test_street_and_postal_line(self):
        self.assertEqual(
            normalize_applied_location("Kastanienallee 97, 10435 Berlin"),
            "Berlin",
        )
        self.assertEqual(normalize_applied_location("10407 Berlin"), "Berlin")

    def test_near_keeps_the_specific_place(self):
        self.assertEqual(normalize_applied_location("Pullach near Munich"), "Pullach")

    def test_greater_metro_folds_to_the_city(self):
        self.assertEqual(
            normalize_applied_location("Greater Munich Metropolitan Area"),
            "Munich",
        )

    def test_unknown_german_town_stays_its_own_bubble(self):
        self.assertEqual(normalize_applied_location("Manching, Bavaria, Germany"), "Manching")


class GermanyOrRemoteTests(unittest.TestCase):
    def test_known_german_city_and_unknown_town(self):
        self.assertTrue(is_germany_or_remote("Stuttgart, Ulm or Berlin"))
        self.assertTrue(is_germany_or_remote("Manching, Bavaria, Germany"))
        self.assertTrue(is_germany_or_remote("EMEA (Remote)"))

    def test_foreign_and_country_scoped_remote(self):
        self.assertFalse(is_germany_or_remote("The Hague, Netherlands"))
        self.assertFalse(is_germany_or_remote("Remote, UK"))
        self.assertFalse(is_germany_or_remote("London"))
        self.assertTrue(is_germany_or_remote("Remote (UK, Ireland, Spain, Germany)"))

    def test_city_in_text_is_whole_word(self):
        self.assertEqual(_city_in_text("10435 berlin"), "Berlin")
        self.assertIsNone(_city_in_text("berliner"))

    def test_facade_reexports_the_same_functions(self):
        self.assertIs(facade_normalize, normalize_applied_location)
        self.assertIs(facade_is_germany_or_remote, is_germany_or_remote)


if __name__ == "__main__":
    unittest.main()

"""German language gate: mandatory vs OR-English / nice-to-have / basic."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from matching.german_gate import find_mandatory_german_requirement  # noqa: E402


def _job(description: str, title: str = "Software Engineer", location: str = "Berlin") -> dict:
    return {"title": title, "location": location, "description": description}


class GermanOrEnglishPassTests(unittest.TestCase):
    def test_very_good_german_or_fluent_english(self):
        reason = find_mandatory_german_requirement(
            _job("We need very good German skills or fluent English.")
        )
        self.assertIsNone(reason)

    def test_german_or_english(self):
        self.assertIsNone(
            find_mandatory_german_requirement(_job("Languages: German or English."))
        )

    def test_german_and_or_english(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("German and/or English is required for this role.")
            )
        )

    def test_english_or_german(self):
        self.assertIsNone(
            find_mandatory_german_requirement(_job("English or German fluency is fine."))
        )

    def test_de_sehr_gute_deutsch_oder_englischkenntnisse(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("Voraussetzung: sehr gute Deutsch- oder Englischkenntnisse.")
            )
        )

    def test_de_deutschkenntnisse_oder_englisch(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("Sehr gute Deutschkenntnisse oder Englischkenntnisse.")
            )
        )


class GermanOptionalPassTests(unittest.TestCase):
    def test_working_proficiency_is_a_plus_with_since_clause(self):
        reason = find_mandatory_german_requirement(
            _job(
                "Working proficiency in German is a plus, since many of "
                "the businesses you build for speak German"
            )
        )
        self.assertIsNone(reason)

    def test_german_is_a_plus(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("Excellent English skills, German is a plus.")
            )
        )

    def test_german_is_a_bonus(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("German is a bonus for working with local teams.")
            )
        )

    def test_preferred_german(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("German preferred; English is the working language.")
            )
        )

    def test_advantageous_and_helpful(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("German is advantageous and helpful for client visits.")
            )
        )

    def test_would_be_great_and_desirable(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("German would be great. It is desirable but optional.")
            )
        )

    def test_no_german_required(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("No German required. English is enough.")
            )
        )

    def test_german_skills_are_welcome(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("German skills are welcome on the team.")
            )
        )

    def test_de_ein_plus_gerne_idealerweise(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("Deutschkenntnisse sind ein Plus, gerne vorhanden, idealerweise.")
            )
        )

    def test_de_wuenschenswert(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("Deutschkenntnisse wünschenswert.")
            )
        )

    def test_nice_to_have_german(self):
        self.assertIsNone(
            find_mandatory_german_requirement(_job("Nice to have: German."))
        )

    def test_beneficial_german(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("German is beneficial but not required.")
            )
        )

    def test_basic_german(self):
        self.assertIsNone(
            find_mandatory_german_requirement(_job("Basic German is enough."))
        )

    def test_de_von_vorteil(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("Deutschkenntnisse von Vorteil.")
            )
        )

    def test_de_grundkenntnisse(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("Grundkenntnisse Deutsch erwünscht.")
            )
        )

    def test_location_germany_is_not_language(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("Join our Berlin office.", location="Berlin, Germany")
            )
        )


class GermanPlusAfterEnglishRequiredTests(unittest.TestCase):
    """'required' attached to English must not reach a later 'German is a plus'."""

    def test_semicolon_german_is_a_plus(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job(
                    "Fluent English required; German is a plus.",
                    title="Senior Full-Stack Engineer (m/f/d)",
                    location="Hamburg",
                )
            )
        )

    def test_comma_german_is_a_plus(self):
        self.assertIsNone(
            find_mandatory_german_requirement(
                _job("Fluent English required, German is a plus.")
            )
        )

    def test_german_required_english_plus_still_fails(self):
        self.assertIsNotNone(
            find_mandatory_german_requirement(
                _job("Fluent German required; English is a plus.")
            )
        )
        self.assertIsNotNone(
            find_mandatory_german_requirement(
                _job("Fluent German required, English is a plus.")
            )
        )

    def test_required_list_with_fluent_german_still_fails(self):
        self.assertIsNotNone(
            find_mandatory_german_requirement(
                _job("Required: Python, Docker, fluent German.")
            )
        )


class GermanMandatoryFailTests(unittest.TestCase):
    def test_fluent_german_required(self):
        reason = find_mandatory_german_requirement(
            _job("Fluent German required. English is a plus.")
        )
        self.assertIsNotNone(reason)
        self.assertIn("Mandatory German", reason)

    def test_german_c1_required(self):
        self.assertIsNotNone(
            find_mandatory_german_requirement(_job("German C1 required."))
        )

    def test_c1_german_required(self):
        self.assertIsNotNone(
            find_mandatory_german_requirement(_job("C1 German required for the role."))
        )

    def test_german_and_english_required(self):
        self.assertIsNotNone(
            find_mandatory_german_requirement(
                _job("German and English required.")
            )
        )

    def test_de_fliessend_erforderlich(self):
        self.assertIsNotNone(
            find_mandatory_german_requirement(
                _job("Fließend Deutsch erforderlich.")
            )
        )

    def test_de_deutsch_und_englisch_erforderlich(self):
        self.assertIsNotNone(
            find_mandatory_german_requirement(
                _job("Deutsch und Englisch erforderlich.")
            )
        )

    def test_must_speak_german(self):
        self.assertIsNotNone(
            find_mandatory_german_requirement(_job("You must speak German."))
        )

    def test_german_c1_is_a_must(self):
        self.assertIsNotNone(
            find_mandatory_german_requirement(_job("German C1 is a must."))
        )

    def test_de_sehr_gute_deutschkenntnisse_erforderlich(self):
        self.assertIsNotNone(
            find_mandatory_german_requirement(
                _job("Sehr gute Deutschkenntnisse erforderlich.")
            )
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import pathlib
import unittest

from drama_identity import normalize_title, same_title, strip_release_markers
from collector_import import _norm_title


ROOT = pathlib.Path(__file__).resolve().parent


class DramaIdentityContractV1Tests(unittest.TestCase):
    def test_case_and_punctuation_variation(self):
        self.assertEqual(normalize_title("The Hidden Tyrant"), "thehiddentyrant")
        self.assertEqual(normalize_title("THE-HIDDEN: TYRANT!"), "thehiddentyrant")

    def test_bracketed_dub_markers_do_not_create_new_identity(self):
        base = normalize_title("Ruling Over All I See")
        self.assertEqual(normalize_title("Ruling Over All I See (DUBBED)"), base)
        self.assertEqual(normalize_title("(DUBBED) Ruling Over All I See"), base)
        self.assertEqual(normalize_title("[ENG DUB] Ruling Over All I See"), base)
        self.assertEqual(normalize_title("[English Dubbed] Ruling Over All I See"), base)

    def test_explicit_unbracketed_release_markers(self):
        base = normalize_title("Justice in Blood")
        self.assertEqual(normalize_title("English Dubbed: Justice in Blood"), base)
        self.assertEqual(normalize_title("Justice in Blood - Dubbed"), base)
        self.assertEqual(normalize_title("Justice in Blood English Dubbed"), base)

    def test_title_content_is_not_over_stripped(self):
        self.assertEqual(strip_release_markers("The Dubbed Wife"), "the dubbed wife")
        self.assertEqual(normalize_title("The Dubbed Wife"), "thedubbedwife")
        self.assertEqual(normalize_title("Dubbed in Blood"), "dubbedinblood")

    def test_empty_or_non_latin_only_title_does_not_create_identity(self):
        self.assertEqual(normalize_title(""), "")
        self.assertEqual(normalize_title("   "), "")
        self.assertEqual(normalize_title("中文剧名"), "")
        self.assertFalse(same_title("", ""))

    def test_shared_collector_alias_uses_contract_normalizer(self):
        self.assertIs(_norm_title, normalize_title)

    def test_app_does_not_define_a_second_title_normalizer(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("from drama_identity import normalize_title", source)
        self.assertNotIn("def normalize_title(", source)

    def test_live_publication_does_not_define_a_second_identity_regex(self):
        source = (ROOT / "live_observations.py").read_text(encoding="utf-8")
        self.assertIn("from drama_identity import normalize_title", source)
        self.assertNotIn("def _norm_title(", source)

        source_v2 = (ROOT / "live_observations_v2.py").read_text(encoding="utf-8")
        self.assertIn("from drama_identity import normalize_title", source_v2)
        self.assertNotIn("def _audit_norm_title(", source_v2)


if __name__ == "__main__":
    unittest.main()

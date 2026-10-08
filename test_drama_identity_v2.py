"""Synthetic isolated B1 cases, not production incident-data replay."""
import importlib.util
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("drama_identity_v2", ROOT / "drama_identity_v2.py")
identity = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = identity
spec.loader.exec_module(identity)
Proof = identity.ReleaseMarkerProof
Review = identity.IdentityReviewRequired
key = identity.canonical_title_key
publication = identity.publication_identity_key


class IdentityV2Tests(unittest.TestCase):
    def test_evidenced_prefix_and_suffix_variants(self):
        for label in ("(Dubbed)", "[DUBBED]", "English Dub", "ENG DUB", "DUBBED", "【DUB】"):
            for edge in ("prefix", "suffix"):
                with self.subTest(label=label, edge=edge):
                    title = label + " - Justice in Blood" if edge == "prefix" else "Justice in Blood - " + label
                    result = key(title, release_proofs=[Proof(label, edge, "fixture:release-label")])
                    self.assertEqual(result.value, "justiceinblood")
                    self.assertEqual(result.version, identity.TITLE_KEY_VERSION)
                    self.assertEqual(result.removed_marker_evidence, ("fixture:release-label",))

    def test_missing_proof_preserves_even_bracketed_labels(self):
        self.assertEqual(key("(DUBBED) Justice in Blood").value, "dubbedjusticeinblood")
        self.assertNotEqual(key("Justice in Blood"), key("Justice in Blood English Dub"))

    def test_real_title_words_preserved(self):
        for title, expected in (("Dubbed a Queen", "dubbedaqueen"), ("Dublin Romance", "dublinromance"), ("The English Dub Mystery", "theenglishdubmystery"), ("A Dubbed Promise", "adubbedpromise")):
            with self.subTest(title=title):
                self.assertEqual(key(title).value, expected)

    def test_wrong_edge_and_interior_evidence_rejected(self):
        for title, proof in (("The Dubbed Queen", Proof("Dubbed", "prefix", "fixture:x")), ("Dublin Romance", Proof("Dub", "prefix", "fixture:x")), ("QueenDub", Proof("Dub", "suffix", "fixture:x")), ("(Dubbed) Queen", Proof("(Dubbed)", "suffix", "fixture:x"))):
            with self.subTest(title=title):
                with self.assertRaises(Review):
                    key(title, release_proofs=[proof])

    def test_malformed_proofs_rejected(self):
        for label, edge, evidence in (("(Dubbed]", "prefix", "fixture:x"), ("Dubbed", "middle", "fixture:x"), ("Dubbed", "prefix", ""), ("Queen", "prefix", "fixture:x"), (None, "prefix", "fixture:x")):
            with self.subTest(label=label, edge=edge):
                with self.assertRaises(Review):
                    Proof(label, edge, evidence)
        with self.assertRaises(Review):
            key("Dubbed Queen", release_proofs=[{"label": "Dubbed"}])

    def test_empty_or_non_text_inputs_rejected(self):
        for value in (None, False, 17, [], {}, "", "   ", "---", "hello\x00world", "line\nbreak"):
            with self.subTest(value=value):
                with self.assertRaises(Review):
                    key(value)
        with self.assertRaises(Review):
            key("[Dub]", release_proofs=[Proof("[Dub]", "prefix", "fixture:x")])

    def test_unicode_letters_not_silently_lost(self):
        for title in ("女王", "Café Romance", "Queen女王", "Straße", "ＡＢＣ", "Queen١"):
            with self.subTest(title=title):
                with self.assertRaises(Review):
                    key(title)
        self.assertEqual(key("Queen’s Promise — Part 2").value, "queenspromisepart2")

    def test_key_idempotency_without_reapplying_proofs(self):
        for title in ("Justice in Blood", "CEO's Promise", "A-B", "Part 2"):
            self.assertEqual(key(key(title).value).value, key(title).value)

    def test_title_collisions_are_not_global_identity(self):
        self.assertEqual(key("A-B").value, key("AB").value)
        first = publication(canonical_drama_id="content:1", platform="DramaBox", record_id="10")
        self.assertEqual(first, ("content:1", "DramaBox", "10"))
        second = publication(canonical_drama_id="content:2", platform="DramaBox", record_id="11")
        self.assertNotEqual(first, second)

    def test_same_content_cannot_collapse_platform_records(self):
        first = publication(canonical_drama_id="content:1", platform="DramaBox", record_id="10")
        other = publication(canonical_drama_id="content:1", platform="NetShort", record_id="10")
        self.assertNotEqual(first, other)
        self.assertNotEqual(first, publication(canonical_drama_id="content:1", platform="DramaBox", record_id="11"))
        self.assertEqual(first, publication(canonical_drama_id="content:1", platform="DramaBox", record_id="10"))

    def test_publication_tuple_does_not_use_ambiguous_delimiters(self):
        self.assertNotEqual(publication(canonical_drama_id="a:b", platform="c", record_id="d"), publication(canonical_drama_id="a", platform="b:c", record_id="d"))
        for field in ("canonical_drama_id", "platform", "record_id"):
            for invalid in ("", None, False, 10, " padded ", "bad\x00id"):
                with self.subTest(field=field, invalid=invalid):
                    args = dict(canonical_drama_id="content:1", platform="DramaBox", record_id="10")
                    args[field] = invalid
                    with self.assertRaises(Review):
                        publication(**args)


if __name__ == "__main__":
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")), patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
        unittest.main()

"""Synthetic-only adversarial checks; never contacts external services."""
import copy
import unittest
from pathlib import Path

import web_continuity_gate as gate

DATE = "2026-10-10"
URLS = {
    "DramaBox": "https://www.dramaboxdb.com/channel/trending",
    "FlexTV": "https://www.flextv.cc/drama/Top-in-FlexTV",
    "GoodShort": "https://www.goodshort.com/channel/Top-in-GoodShort",
    "MoboReels": "https://www.moboreels.com/",
    "NetShort": "https://netshort.com/",
    "ReelShort": "https://www.reelshort.com/shelf/top-short-movies-dramas-51001122",
}


def scope():
    return {
        "production_write": False, "research_auto_trigger": False,
        "research_queue_write": False, "source_type": "OFFICIAL_WEB",
        "locale": "en-US", "region": "US", "language": "English",
        "platforms": [
            {"platform": p, "url": URLS[p], "ranking_type": rank, "target_key": target}
            for p, (rank, target, _) in gate.EXPECTED.items()
        ],
    }


def payload(platform):
    rank, target, _ = gate.EXPECTED[platform]
    evidence = {
        "DramaBox": {"sourceEvidence": {"http_status": 200, "raw_html_sha256": "f" * 64}},
        "FlexTV": {"pageUrl": URLS[platform], "rawHtmlSha256": "f" * 64, "structuredData": "JSON-LD ItemList", "itemListName": ""},
        "GoodShort": {"payloadEvidence": {"section": "Top in GoodShort", "row_count": 10}},
        "MoboReels": {"pageUrl": URLS[platform], "rawHtmlSha256": "f" * 64, "structuredData": "MoboReels Popular Series DOM"},
        "NetShort": {"pageUrl": URLS[platform], "rawHtmlSha256": "f" * 64, "structuredData": "JSON-LD ItemList", "itemListName": "Trending Now"},
        "ReelShort": {"pageUrl": URLS[platform], "rawHtmlSha256": "f" * 64, "structuredData": "Next.js __NEXT_DATA__ pageProps.list", "shelfName": "TOP"},
    }[platform]
    return {
        "platform": platform, "collection_date": DATE, "source_type": "OFFICIAL_WEB",
        "target_key": target, "ranking_type": rank, "top_n": 10,
        "production_write": False, "source_url": URLS[platform],
        "status": "PASS_CANDIDATE", "row_count": 10,
        "rows": [{"rank": i, "title": f"Drama {i} in {platform}"} for i in range(1, 11)],
        "audit": {"batchComplete": True}, "evidence": evidence,
    }


class GateTests(unittest.TestCase):
    def test_exact_scope(self):
        self.assertEqual(len(gate.preflight(scope())), 6)
        for field, bad in [("production_write", True), ("research_queue_write", True),
                           ("research_auto_trigger", True), ("locale", "zh-CN"),
                           ("region", "CN"), ("source_type", "SHORT_DRAMA_APP")]:
            item = scope()
            item[field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                gate.preflight(item)

    def test_platform_expansion_and_rank_label_blocked(self):
        item = scope()
        item["platforms"].append({"platform": "ShortMax"})
        with self.assertRaises(ValueError):
            gate.preflight(item)
        item = scope()
        item["platforms"][0]["ranking_type"] = "Popular"
        with self.assertRaises(ValueError):
            gate.preflight(item)

    def test_normal_six_captured_but_not_promoted(self):
        item = scope()
        data = {p: payload(p) for p in gate.EXPECTED}
        report = gate.build_report(DATE, item, read=lambda path: copy.deepcopy(data[Path(path).stem]))
        self.assertEqual(report["coverage"], "6/6")
        self.assertEqual(report["promotionState"], "BLOCK_PROMOTION")
        self.assertEqual(report["businessAcceptance"], "NOT_RUN")
        self.assertEqual(report["platforms"]["FlexTV"]["status"], "STRUCTURE_PASS_TRUTH_PENDING")

    def test_partial_does_not_block_five(self):
        data = {p: payload(p) for p in gate.EXPECTED}
        data["MoboReels"]["rows"] = data["MoboReels"]["rows"][:6]
        report = gate.build_report(DATE, scope(), read=lambda path: copy.deepcopy(data[Path(path).stem]))
        self.assertEqual(report["coverage"], "5/6")
        self.assertEqual(report["platforms"]["MoboReels"]["status"], "REJECTED_OR_PARTIAL")

    def test_forged_pass_missing_rank(self):
        x = payload("NetShort")
        x["rows"][8]["rank"] = 6
        self.assertEqual(gate.check("NetShort", x, scope()["platforms"][4], DATE)["status"], "REJECTED_OR_PARTIAL")

    def test_duplicate_titles_and_wrong_date(self):
        x = payload("ReelShort")
        x["rows"][2]["title"] = x["rows"][1]["title"]
        x["collection_date"] = "2026-09-17"
        result = gate.check("ReelShort", x, scope()["platforms"][5], DATE)
        self.assertIn("INVALID_TITLES", result["errors"])
        self.assertIn("CONTRACT_COLLECTION_DATE", result["errors"])

    def test_cross_domain_and_semantic_substitution(self):
        x = payload("GoodShort")
        x["rows"][0]["source_url"] = "https://www.other-site.com/movie"
        self.assertIn("CROSS_DOMAIN_TITLE_URL", gate.check("GoodShort", x, scope()["platforms"][2], DATE)["errors"])
        x = payload("ReelShort")
        x["evidence"]["shelfName"] = "NEW"
        self.assertIn("WRONG_SHELF_NAME", gate.check("ReelShort", x, scope()["platforms"][5], DATE)["errors"])

    def test_missing_source_witness(self):
        x = payload("DramaBox")
        x["evidence"] = {}
        self.assertIn("NO_SOURCE_WITNESS", gate.check("DramaBox", x, scope()["platforms"][0], DATE)["errors"])

    def test_missing_platform_read_is_explicit(self):
        def reader(path):
            if Path(path).stem == "MoboReels":
                raise FileNotFoundError(path)
            return payload(Path(path).stem)
        report = gate.build_report(DATE, scope(), read=reader)
        self.assertEqual(report["coverage"], "5/6")
        self.assertEqual(report["platforms"]["MoboReels"]["status"], "MISSING")


if __name__ == "__main__":
    unittest.main()

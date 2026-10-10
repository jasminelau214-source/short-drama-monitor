"""Offline real-evidence + adversarial fail-closed checks. Never contacts external systems."""
import gzip
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import unittest
from isolated_web_semantic_audit_v2 import verify, PLATFORMS

ROOT = Path(__file__).resolve().parent
DATE = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()

def fixture(name):
    payload = json.loads((ROOT/"pilot_data"/DATE/(name+".json")).read_text(encoding="utf-8"))
    html = gzip.decompress((ROOT/"pilot_evidence"/DATE/(name+".html.gz")).read_bytes())
    png = (ROOT/"pilot_evidence"/DATE/(name+".png")).read_bytes()
    return payload,html,png

class WitnessFaultTests(unittest.TestCase):
    def test_four_real_snapshots_source_order(self):
        for name in PLATFORMS:
            with self.subTest(platform=name):
                result = verify(name, *fixture(name))
                self.assertEqual(result["status"], "WEB_SHELF_ORDER_EVIDENCE_MATCHED", result["errors"])
                self.assertNotEqual(result["rankOrigin"], "APP_AUTHORITY")

    def test_bad_html_checksum_rejected(self):
        payload, html, png = fixture("FlexTV")
        result = verify("FlexTV", payload, html+b"tampered", png)
        self.assertIn("RAW_HTML_HASH_MISMATCH_OR_MISSING", result["errors"])

    def test_missing_png_rejected(self):
        payload, html, png = fixture("NetShort")
        result = verify("NetShort", payload, html, b"")
        self.assertIn("SCREENSHOT_HASH_MISMATCH_OR_MISSING", result["errors"])

    def test_flextv_ranked_list_mismatch_rejected(self):
        payload, html, png = fixture("FlexTV")
        tampered = html.decode("utf-8","replace").replace(payload["rows"][0]["title"], "Wrong Ranking Source")
        result = verify("FlexTV", payload, tampered.encode("utf-8"), png)
        self.assertIn("FLEXTV_AMBIGUOUS_OR_WRONG_ITEMLIST", result["errors"])

    def test_moboreels_wrong_title_rejected(self):
        payload, html, png = fixture("MoboReels")
        payload["rows"][0]["title"] = "Injected Fake Title"
        result = verify("MoboReels", payload, html, png)
        self.assertIn("MOBORREELS_DISPLAY_ORDER_MISMATCH", result["errors"])

    def test_netshort_wrong_order_rejected(self):
        payload, html, png = fixture("NetShort")
        payload["rows"][0]["title"], payload["rows"][1]["title"] = payload["rows"][1]["title"], payload["rows"][0]["title"]
        result = verify("NetShort", payload, html, png)
        self.assertIn("NETSHORT_NAMED_ITEMLIST_MISMATCH", result["errors"])

    def test_reelshort_other_shelf_rejected(self):
        payload, html, png = fixture("ReelShort")
        tampered = html.decode("utf-8","replace").replace('"shelfName":"TOP"', '"shelfName":"NEW"')
        result = verify("ReelShort", payload, tampered.encode("utf-8"), png)
        self.assertIn("REELSHORT_TOP_SHELF_ID_OR_LOCALE_MISMATCH", result["errors"])

    def test_app_source_substitution_rejected(self):
        payload, html, png = fixture("ReelShort")
        payload["source_type"] = "SHORT_DRAMA_APP"
        result = verify("ReelShort", payload, html, png)
        self.assertIn("ROW_OR_SOURCE_CONTRACT_FAIL", result["errors"])

    def test_display_order_not_promoted_to_explicit_rank(self):
        for name in ("MoboReels", "ReelShort"):
            result = verify(name, *fixture(name))
            self.assertEqual(result["rankOrigin"], "DISPLAY_ORDER_ONLY")

if __name__ == "__main__":
    unittest.main()

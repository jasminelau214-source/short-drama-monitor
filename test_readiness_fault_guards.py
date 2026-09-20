import copy
import unittest

import pilot_web_top10 as base


class FaultGateStructureTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"rank": i, "title": f"Title {i}"}
            for i in range(1, 11)
        ]

    def assertBlocked(self, rows):
        audit = base.audit_rows(rows)
        self.assertFalse(audit["batchComplete"])
        status = base.status_from_audit(
            audit,
            verified_parser=False,
            adapter_found=True,
        )
        self.assertNotIn(status, {"PASS_VERIFIED", "PASS_CANDIDATE"})

    def test_baseline_is_complete(self):
        audit = base.audit_rows(self.rows)
        self.assertTrue(audit["batchComplete"])

    def test_missing_row_blocks(self):
        self.assertBlocked(self.rows[:-1])

    def test_duplicate_rank_blocks(self):
        rows = copy.deepcopy(self.rows)
        rows[1]["rank"] = 1
        self.assertBlocked(rows)

    def test_duplicate_title_blocks(self):
        rows = copy.deepcopy(self.rows)
        rows[1]["title"] = rows[0]["title"]
        self.assertBlocked(rows)

    def test_empty_title_blocks(self):
        rows = copy.deepcopy(self.rows)
        rows[0]["title"] = ""
        self.assertBlocked(rows)

    def test_extra_row_blocks(self):
        rows = copy.deepcopy(self.rows)
        rows.append({"rank": 11, "title": "Extra"})
        self.assertBlocked(rows)




class SourceControlTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "platform": "FlexTV",
            "url": "https://www.flextv.cc/drama/Top-in-FlexTV",
        }
        self.now = datetime.now(timezone.utc)
        self.valid = {
            "httpStatus": 200,
            "pageUrl": self.cfg["url"],
            "semanticVerified": True,
            "fetchedAt": self.now.isoformat(),
        }

    def test_valid_source_evidence_passes(self):
        result = base.audit_source_evidence(self.cfg, self.valid, now=self.now)
        self.assertTrue(result["pass"])

    def test_http_error_fails_closed(self):
        result = base.audit_source_evidence(
            self.cfg,
            {**self.valid, "httpStatus": 503},
            now=self.now,
        )
        self.assertFalse(result["pass"])
        self.assertIn("HTTP_STATUS_INVALID", result["errors"])

    def test_cross_host_redirect_fails_closed(self):
        result = base.audit_source_evidence(
            self.cfg,
            {**self.valid, "pageUrl": "https://example.invalid/control"},
            now=self.now,
        )
        self.assertFalse(result["pass"])
        self.assertIn("OFFICIAL_HOST_MISMATCH", result["errors"])

    def test_stale_evidence_fails_closed(self):
        result = base.audit_source_evidence(
            self.cfg,
            {
                **self.valid,
                "fetchedAt": (self.now - timedelta(hours=1)).isoformat(),
            },
            now=self.now,
        )
        self.assertFalse(result["pass"])
        self.assertIn("FETCH_EVIDENCE_STALE", result["errors"])

    def test_unverified_semantics_fail_closed(self):
        result = base.audit_source_evidence(
            self.cfg,
            {**self.valid, "semanticVerified": False},
            now=self.now,
        )
        self.assertFalse(result["pass"])
        self.assertIn("TARGET_SEMANTIC_UNVERIFIED", result["errors"])
\n\nif __name__ == "__main__":
    unittest.main()

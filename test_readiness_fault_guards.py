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


if __name__ == "__main__":
    unittest.main()

import unittest

import pilot_exact_web_adapters as exact

from readiness_path_optimization import decide


class PathDecisionTests(unittest.TestCase):
    def rows(self):
        return [{"rank": i, "title": f"Title {i}"} for i in range(1, 11)]

    def incumbent(self, strategy="browser_probe"):
        return {
            "ok": True,
            "status": "PASS_CANDIDATE",
            "rows": self.rows(),
            "durationSeconds": 5.0,
            "strategy": strategy,
        }

    def direct(self, ok=True, suffix=""):
        rows = self.rows()
        if suffix:
            rows[-1] = {"rank": 10, "title": "Different " + suffix}
        return {
            "ok": ok,
            "status": "PASS_CANDIDATE" if ok else "FAIL",
            "rows": rows,
            "durationSeconds": 0.3,
        }

    def test_browser_can_become_direct_candidate_only_after_three_exact_passes(self):
        decision, _ = decide(
            {"strategy": "browser_probe"},
            self.incumbent(),
            [self.direct(), self.direct(), self.direct()],
        )
        self.assertEqual(decision, "PREFER_DIRECT_HTTP_CANDIDATE")

    def test_any_direct_failure_keeps_browser(self):
        decision, _ = decide(
            {"strategy": "browser_probe"},
            self.incumbent(),
            [self.direct(), self.direct(ok=False), self.direct()],
        )
        self.assertEqual(decision, "KEEP_CURRENT_BROWSER")

    def test_direct_instability_keeps_browser(self):
        decision, _ = decide(
            {"strategy": "browser_probe"},
            self.incumbent(),
            [self.direct(), self.direct(suffix="x"), self.direct()],
        )
        self.assertEqual(decision, "KEEP_CURRENT_BROWSER")

    def test_existing_direct_path_is_kept(self):
        decision, _ = decide(
            {"strategy": "verified_parser"},
            self.incumbent("verified_parser"),
            [self.direct(), self.direct(), self.direct()],
        )
        self.assertEqual(decision, "KEEP_CURRENT_DIRECT")

    def test_invalid_incumbent_blocks_c(self):
        incumbent = self.incumbent()
        incumbent["ok"] = False
        decision, _ = decide(
            {"strategy": "browser_probe"},
            incumbent,
            [self.direct(), self.direct(), self.direct()],
        )
        self.assertEqual(decision, "BLOCKED_INCUMBENT_INVALID")




class MoboReelsDirectVariantTests(unittest.TestCase):
    def test_new_released_ssr_variant(self):
        cards = "".join(
            f'<a><h3 class="new-released-item-title">Drama {i}</h3></a>'
            for i in range(1, 13)
        )
        html = (
            '<div class="new-released">'
            '<h2 class="new-released-title">Popular Series</h2>'
            '<div class="new-released-list">' + cards + '</div>'
            '</div><div class="new-released"><h2 class="new-released-title">Other</h2></div>'
        )
        rows, evidence, found = exact._rows_from_moboreels(html)
        self.assertTrue(found)
        self.assertEqual(len(rows), 10)
        self.assertEqual(evidence["domVariant"], "new-released")
        self.assertTrue(evidence["semanticVerified"])

    def test_wrong_heading_still_fails_closed(self):
        html = (
            '<div class="new-released">'
            '<h2 class="new-released-title">Control Shelf</h2>'
            + "".join(
                f'<h3 class="new-released-item-title">Drama {i}</h3>'
                for i in range(1, 13)
            )
            + '</div>'
        )
        rows, evidence, found = exact._rows_from_moboreels(html)
        self.assertFalse(found)
        self.assertEqual(rows, [])
        self.assertFalse(evidence["semanticVerified"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import pilot_web_top10 as base
import pilot_exact_web_adapters as exact

# Replace only the isolated pilot adapters. Production collectors remain unchanged.
base.run_verified_parser = exact.run_verified_parser


def _exact_browser_probe(browser, cfg, evidence_dir):
    return exact.browser_probe(browser, cfg, evidence_dir, base.local_today())


base.browser_probe = _exact_browser_probe

if __name__ == "__main__":
    raise SystemExit(base.main())

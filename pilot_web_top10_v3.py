from __future__ import annotations

import pilot_web_top10 as base

# Capture the original verified-parser dispatcher before the exact-adapter module is
# allowed to replace anything. This prevents recursive delegation for DramaBox and
# GoodShort while keeping the production collector modules untouched.
_original_verified_parser = base.run_verified_parser

import pilot_exact_web_adapters as exact


def _verified_parser(cfg, collection_date):
    if cfg.get("platform") == "ShortMax":
        rows, evidence = exact.run_verified_parser(cfg, collection_date)
        # The stdlib collector can see only the server-rendered slice. If it is
        # incomplete, deliberately fall back to the rendered browser adapter
        # rather than treating a partial shelf as final.
        if len(rows) < base.TOP_N:
            raise RuntimeError(f"SHORTMAX_VERIFIED_PARTIAL:{len(rows)}")
        return rows, evidence
    return _original_verified_parser(cfg, collection_date)


def _exact_browser_probe(browser, cfg, evidence_dir):
    return exact.browser_probe(browser, cfg, evidence_dir, base.local_today())


base.run_verified_parser = _verified_parser
base.browser_probe = _exact_browser_probe

if __name__ == "__main__":
    raise SystemExit(base.main())

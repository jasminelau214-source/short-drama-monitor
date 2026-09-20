from __future__ import annotations

import json
import urllib.request

import pilot_web_top10 as base

# Capture the original verified-parser dispatcher before the exact-adapter module is
# allowed to replace anything. This prevents recursive delegation for DramaBox and
# GoodShort while keeping the production collector modules untouched.
_original_verified_parser = base.run_verified_parser

import pilot_exact_web_adapters as exact

DRAMABOX_RENDER_FALLBACK = "https://jsm-dramabox-pilot-probe.onrender.com/refresh"


def _dramabox_render_fallback(primary_error: Exception):
    req = urllib.request.Request(
        DRAMABOX_RENDER_FALLBACK,
        headers={
            "User-Agent": "JSM-WebTop10-Pilot/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    top = payload.get("dramabox_top10") or {}
    rows = top.get("rows") or []
    if int(top.get("row_count") or 0) != base.TOP_N or len(rows) != base.TOP_N:
        raise RuntimeError(
            f"DRAMABOX_RENDER_FALLBACK_INCOMPLETE:{len(rows)} primary={type(primary_error).__name__}:{primary_error}"
        )
    normalized = [
        {
            "rank": int(row.get("rank") or idx),
            "title": base.clean(row.get("title"), 500),
            **(
                {"source_url": base.clean(row.get("source_url"), 1200)}
                if row.get("source_url") else {}
            ),
        }
        for idx, row in enumerate(rows, start=1)
    ]
    evidence = {
        "collectorVersion": "dramabox-render-singapore-pilot-v1",
        "fallbackService": "jsm-dramabox-pilot-probe",
        "fallbackUrl": DRAMABOX_RENDER_FALLBACK,
        "sourceUrl": top.get("source_url"),
        "sourceEvidence": top.get("evidence") or {},
        "primaryCollectorError": f"{type(primary_error).__name__}: {primary_error}",
        "productionWrite": False,
    }
    return normalized, evidence


DIRECT_PRIMARY_PLATFORMS = {"FlexTV", "MoboReels", "NetShort", "ReelShort"}


def _verified_parser(cfg, collection_date):
    platform = cfg.get("platform")
    if platform == "DramaBox":
        try:
            return exact.run_verified_parser(cfg, collection_date)
        except Exception as exc:
            return _dramabox_render_fallback(exc)

    if platform in DIRECT_PRIMARY_PLATFORMS:
        rows, evidence = exact.run_direct_exact(cfg, collection_date)
        audit = base.audit_rows(rows)
        source_control = base.audit_source_evidence(cfg, evidence)
        if not audit.get("batchComplete") or not source_control.get("pass"):
            raise RuntimeError(
                f"DIRECT_PRIMARY_INVALID:{platform}:"
                f"rows={len(rows)}:"
                f"source_errors={','.join(source_control.get('errors') or [])}"
            )
        return rows, evidence

    if platform == "ShortMax":
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

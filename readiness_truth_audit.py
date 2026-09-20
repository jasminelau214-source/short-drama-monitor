from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

from production_readiness import (
    empty_gate_state,
    evaluate_platform_readiness,
    evaluate_system_readiness,
    qualify_truth_evidence,
)

ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "pilot_data"
OUT_ROOT = ROOT / "readiness_audit"
TZ = ZoneInfo("Asia/Shanghai")
TOP_N = 10

TARGETS = {
    "DramaBox": {
        "url": "https://www.dramaboxdb.com/channel/trending",
        "host": "dramaboxdb.com",
        "semantic": "trending",
    },
    "FlexTV": {
        "url": "https://www.flextv.cc/drama/Top-in-FlexTV",
        "host": "flextv.cc",
        "semantic": "top in flextv",
    },
    "GoodShort": {
        "url": "https://www.goodshort.com/channel/Top-in-GoodShort",
        "host": "goodshort.com",
        "semantic": "top in goodshort",
    },
    "MoboReels": {
        "url": "https://www.moboreels.com/",
        "host": "moboreels.com",
        "semantic": "popular series",
    },
    "NetShort": {
        "url": "https://netshort.com/",
        "host": "netshort.com",
        "semantic": "trending now",
    },
    "ReelShort": {
        "url": "https://www.reelshort.com/shelf/top-short-movies-dramas-51001122",
        "host": "reelshort.com",
        "semantic": "top",
    },
    "ShortMax": {
        "url": "https://www.shorttv.live/",
        "host": "shorttv.live",
        "semantic": "most popular",
    },
}

ORACLE_JS = r"""
({platform}) => {
  const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
  const norm = s => clean(s).toLowerCase().replace(/[^a-z0-9]+/g, '');

  const unique = rows => {
    const seen = new Set();
    const out = [];
    for (const row of rows || []) {
      const title = clean(row && row.title);
      const key = norm(title);
      if (!title || !key || seen.has(key)) continue;
      seen.add(key);
      out.push({title, href: clean(row && row.href)});
      if (out.length >= 30) break;
    }
    return out;
  };

  const titleFromLink = a => {
    if (!a) return '';
    const itemprop = a.querySelector('meta[itemprop="name"][content]');
    const itempropValue = clean(itemprop && itemprop.getAttribute('content'));
    if (itempropValue && itempropValue.length <= 220) return itempropValue;

    const preferred = a.querySelector(
      'h1,h2,h3,h4,[class*="title"],[class*="name"]'
    );
    if (preferred) {
      const value = clean(preferred.textContent);
      if (value && value.length <= 220) return value;
    }

    const attr = clean(a.getAttribute('title') || a.getAttribute('aria-label'));
    if (attr && attr.length <= 220) {
      return attr
        .replace(/\s+Watch Short Drama Online$/i, '')
        .replace(/\s+Short Drama Cover$/i, '');
    }

    const image = a.querySelector('img[alt]');
    const alt = clean(image && image.getAttribute('alt'))
      .replace(/\s+Short Drama Cover$/i, '');
    if (alt && alt.length <= 220) return alt;

    const lines = String(a.innerText || '').split(/\n+/).map(clean).filter(Boolean);
    return (lines[0] || '').slice(0, 220);
  };

  const links = (root, selector) => unique(
    Array.from((root || document).querySelectorAll(selector)).map(a => ({
      title: titleFromLink(a),
      href: String(a.href || '')
    }))
  );

  const heading = wanted => {
    const headings = Array.from(
      document.querySelectorAll('h1,h2,h3,h4,h5,[role="heading"]')
    );
    return headings.find(
      h => wanted.some(x => clean(h.textContent).toLowerCase().includes(x))
    ) || null;
  };

  const scopedLinks = (h, selector) => {
    if (!h) return [];
    let node = h.closest('section') || h.parentElement;
    let best = [];
    for (let depth = 0; node && depth < 6; depth++, node = node.parentElement) {
      const rows = links(node, selector);
      if (rows.length >= 10 && (best.length === 0 || rows.length < best.length)) {
        best = rows;
      }
      if (rows.length >= 10 && rows.length <= 30) break;
    }
    return best;
  };

  let rows = [];
  let anchor = '';
  let method = '';

  if (platform === 'GoodShort') {
    rows = links(document, 'div.book a.book-name');
    const h = heading(['top in goodshort']);
    anchor = clean(h && h.textContent) || document.title;
    method = 'independent-live-dom:div.book>a.book-name';
  } else if (platform === 'MoboReels') {
    const h = Array.from(document.querySelectorAll('h2.home-list-title'))
      .find(x => clean(x.textContent).toLowerCase() === 'popular series');
    anchor = clean(h && h.textContent);
    const root = h && (h.closest('.home-list') || h.parentElement);
    if (root) {
      rows = unique(Array.from(root.querySelectorAll('h3.home-list-item-title')).map(t => {
        const a = t.closest('a') || (t.parentElement && t.parentElement.closest('a'));
        return {title: clean(t.textContent), href: a ? String(a.href || '') : ''};
      }));
    }
    method = 'independent-live-dom:Popular-Series-h3';
  } else if (platform === 'ShortMax') {
    const h = heading(['most popular']);
    anchor = clean(h && h.textContent);
    const root = h && (h.closest('section') || (h.parentElement && h.parentElement.parentElement));
    rows = links(root || document, 'a[href*="/drama/"]');
    method = 'independent-live-dom:Most-Popular-links';
  } else if (platform === 'NetShort') {
    const h = heading(['trending now']);
    anchor = clean(h && h.textContent);
    rows = scopedLinks(h, 'a[href*="/episode/"]');
    if (rows.length < 10) rows = links(document, 'a[href*="/episode/"]');
    method = 'independent-live-dom:Trending-Now-episode-links';
  } else if (platform === 'FlexTV') {
    const h = heading(['top in flextv']);
    anchor = clean(h && h.textContent) || document.title;
    rows = scopedLinks(h, 'a[href*="/episodes/episode-1-"]');
    if (rows.length < 10) rows = links(document, 'a[href*="/episodes/episode-1-"]');
    method = 'independent-live-dom:Top-in-FlexTV-episode-links';
  } else if (platform === 'ReelShort') {
    const h = heading(['top']);
    anchor = clean(h && h.textContent) || document.title;
    rows = scopedLinks(h, 'a[href*="/movie/"]');
    if (rows.length < 10) rows = links(document, 'a[href*="/movie/"]');
    method = 'independent-live-dom:TOP-movie-links';
  } else if (platform === 'DramaBox') {
    const h = heading(['trending']);
    anchor = clean(h && h.textContent) || document.title;
    rows = scopedLinks(h, 'a[href*="/movie/"]');
    if (rows.length < 10) rows = links(document, 'a[href*="/movie/"]');
    method = 'independent-live-dom:Trending-movie-links';
  }

  return {
    pageTitle: clean(document.title),
    pageUrl: String(location.href || ''),
    anchor,
    method,
    rows: rows.slice(0, 20)
  };
}
"""


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean(value).casefold())


def host_matches(url: str, expected: str) -> bool:
    host = (urlparse(url).hostname or "").casefold()
    expected = expected.casefold()
    return host == expected or host.endswith("." + expected)


def load_candidate(collection_date: str, platform: str) -> dict:
    path = DATA_ROOT / collection_date / f"{platform}.json"
    if not path.exists():
        return {"_error": f"CANDIDATE_NOT_FOUND:{path}"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_error": f"CANDIDATE_INVALID_JSON:{type(exc).__name__}:{exc}"}
    return value if isinstance(value, dict) else {"_error": "CANDIDATE_NOT_OBJECT"}


def differences(candidate_rows: list[dict], oracle_rows: list[dict]) -> list[dict]:
    out = []
    for idx in range(TOP_N):
        candidate = candidate_rows[idx] if idx < len(candidate_rows) else {}
        oracle = oracle_rows[idx] if idx < len(oracle_rows) else {}
        candidate_title = clean(candidate.get("title"))
        oracle_title = clean(oracle.get("title"))
        if norm(candidate_title) != norm(oracle_title):
            out.append(
                {
                    "rank": idx + 1,
                    "candidate": candidate_title,
                    "oracle": oracle_title,
                }
            )
    return out


def semantic_ok(anchor: str, page_title: str, expected: str) -> bool:
    haystack = (clean(anchor) + " " + clean(page_title)).casefold()
    return expected.casefold() in haystack


def candidate_raw_hash(candidate: dict) -> str:
    evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
    direct = str(evidence.get("rawHtmlSha256") or "").strip()
    if direct:
        return direct
    source = evidence.get("sourceEvidence") if isinstance(evidence.get("sourceEvidence"), dict) else {}
    return str(source.get("raw_html_sha256") or "").strip()


def batch_delay_seconds(observed_at: datetime) -> float | None:
    raw = os.environ.get("CANDIDATE_BATCH_COMPLETED_AT", "").strip()
    if not raw:
        return None
    try:
        batch = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return max(0.0, (observed_at.astimezone(batch.tzinfo) - batch).total_seconds())
    except Exception:
        return None


def audit_platform(browser, collection_date: str, platform: str, cfg: dict) -> dict:
    candidate = load_candidate(collection_date, platform)
    if candidate.get("_error"):
        return {
            "platform": platform,
            "status": "BLOCKED_CANDIDATE_UNAVAILABLE",
            "error": candidate["_error"],
            "productionWrite": False,
        }

    candidate_rows = candidate.get("rows") if isinstance(candidate.get("rows"), list) else []
    candidate_status = clean(candidate.get("status"))
    structure_ok = (
        len(candidate_rows) == TOP_N
        and [int(x.get("rank") or 0) for x in candidate_rows] == list(range(1, TOP_N + 1))
        and len({norm(x.get("title")) for x in candidate_rows if norm(x.get("title"))}) == TOP_N
    )

    mobile_witness = platform == "ShortMax"
    page = browser.new_page(
        viewport={"width": 390, "height": 844}
        if mobile_witness
        else {"width": 1440, "height": 1200},
        locale="en-US",
        user_agent=(
            "Mozilla/5.0 (Linux; Android 15; Pixel 9 Pro) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Mobile Safari/537.36"
            if mobile_witness
            else (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36"
            )
        ),
        is_mobile=mobile_witness,
        has_touch=mobile_witness,
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    http_status = None
    oracle = {}
    error = ""
    witness_html = ""
    witness_observed_at = datetime.now(TZ)
    try:
        response = page.goto(cfg["url"], wait_until="domcontentloaded", timeout=90000)
        http_status = int(response.status) if response is not None else None
        page.wait_for_timeout(3500)
        for _ in range(3):
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(300)
        page.evaluate("window.scrollTo(0,0)")
        page.wait_for_timeout(400)
        oracle = page.evaluate(ORACLE_JS, {"platform": platform}) or {}
        witness_html = page.content()
        witness_observed_at = datetime.now(TZ)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        page.close()

    oracle_rows = oracle.get("rows") if isinstance(oracle.get("rows"), list) else []
    oracle_top = oracle_rows[:TOP_N]
    diff = differences(candidate_rows, oracle_top)
    official_host = host_matches(str(oracle.get("pageUrl") or cfg["url"]), cfg["host"])
    semantic = semantic_ok(
        str(oracle.get("anchor") or ""),
        str(oracle.get("pageTitle") or ""),
        cfg["semantic"],
    )
    witness_complete = (
        http_status is not None
        and http_status < 400
        and len(oracle_top) == TOP_N
        and len({norm(x.get("title")) for x in oracle_top if norm(x.get("title"))}) == TOP_N
    )
    exact_order_match = witness_complete and not diff

    witness_sha256 = ""
    witness_snapshot_file = ""
    if witness_html:
        raw = witness_html.encode("utf-8", errors="replace")
        witness_sha256 = hashlib.sha256(raw).hexdigest()
        evidence_dir = OUT_ROOT / collection_date / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        snapshot = evidence_dir / f"{platform}.witness.html.gz"
        with gzip.open(snapshot, "wb") as fh:
            fh.write(raw)
        witness_snapshot_file = str(snapshot.relative_to(ROOT))

    candidate_sha256 = candidate_raw_hash(candidate)
    same_snapshot_hash = bool(
        candidate_sha256 and witness_sha256 and candidate_sha256 == witness_sha256
    )
    evidence_mode = (
        "INDEPENDENT_PARSER_FROZEN"
        if same_snapshot_hash
        else "INDEPENDENT_PARSER_LIVE"
        if witness_complete
        else "SELF_CHECK"
    )
    delay = batch_delay_seconds(witness_observed_at)
    truth_evidence = qualify_truth_evidence(
        evidence_mode,
        exact_match=exact_order_match,
        official_host=official_host,
        semantic_anchor=semantic,
        post_batch_delay_seconds=delay,
    )

    if not structure_ok:
        status = "FAIL_STRUCTURE"
    elif not official_host:
        status = "FAIL_OFFICIAL_HOST"
    elif not witness_complete:
        status = "BLOCKED_WITNESS_UNAVAILABLE"
    elif not semantic:
        status = "FAIL_SEMANTIC_ANCHOR"
    elif not exact_order_match:
        status = "FAIL_TRUTH_MISMATCH"
    elif not truth_evidence.get("sufficient"):
        status = "BLOCKED_TRUTH_EVIDENCE"
    else:
        status = "PASS_TRUTH"

    gates = empty_gate_state()
    gates["execution"] = "PASS"
    gates["structure"] = "PASS" if structure_ok else "FAIL"
    gates["truth"] = (
        "PASS"
        if status == "PASS_TRUTH"
        else "BLOCKED"
        if status.startswith("BLOCKED_")
        else "FAIL"
    )
    gates["semantic"] = "PASS" if semantic else "FAIL"
    platform_readiness = evaluate_platform_readiness(
        platform,
        gates,
        truth_evidence=truth_evidence,
        integration_ready=False,
    )

    return {
        "platform": platform,
        "status": status,
        "candidateStatus": candidate_status,
        "candidateStrategy": candidate.get("strategy"),
        "candidateEvidence": candidate.get("evidence")
        if isinstance(candidate.get("evidence"), dict)
        else {},
        "candidateRows": [
            {"rank": int(x.get("rank") or i), "title": clean(x.get("title"))}
            for i, x in enumerate(candidate_rows[:TOP_N], 1)
        ],
        "oracleMethod": oracle.get("method"),
        "oraclePageTitle": oracle.get("pageTitle"),
        "oraclePageUrl": oracle.get("pageUrl"),
        "semanticAnchor": oracle.get("anchor"),
        "httpStatus": http_status,
        "officialHost": official_host,
        "semanticAnchorOk": semantic,
        "witnessComplete": witness_complete,
        "exactOrderedTitleMatch": exact_order_match,
        "truthEvidence": truth_evidence,
        "candidateRawHtmlSha256": candidate_sha256,
        "witnessRawHtmlSha256": witness_sha256,
        "sameSnapshotHash": same_snapshot_hash,
        "witnessSnapshotFile": witness_snapshot_file,
        "witnessObservedAt": witness_observed_at.isoformat(),
        "postBatchDelaySeconds": delay,
        "readiness": platform_readiness,
        "oracleRows": [
            {"rank": i, "title": clean(x.get("title")), "href": clean(x.get("href"))}
            for i, x in enumerate(oracle_top, 1)
        ],
        "differences": diff,
        "error": error,
        "productionWrite": False,
    }


def render_markdown(payload: dict) -> str:
    lines = [
        f"# Production Readiness A — Truth Test — {payload['collectionDate']}",
        "",
        f"Overall A gate: **{payload['truthGate']}**",
        f"Promotion: **{payload['readiness']['status']}**",
        "",
        "| Platform | Truth status | Evidence | Candidate | Witness rows | Exact ordered match | Next B |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for item in payload["platforms"]:
        lines.append(
            f"| {item['platform']} | {item['status']} | "
            f"L{(item.get('truthEvidence') or {}).get('level', 0)} "
            f"{(item.get('truthEvidence') or {}).get('mode', '-')} | "
            f"{item.get('candidateStatus','-')} | "
            f"{len(item.get('oracleRows') or [])}/10 | "
            f"{'YES' if item.get('exactOrderedTitleMatch') else 'NO'} | "
            f"{'YES' if ((item.get('readiness') or {}).get('phaseEligibility') or {}).get('B_FAULT_INJECTION') else 'NO'} |"
        )
    lines += [
        "",
        "PASS_TRUTH requires an independent code path over the same official page, "
        "exact ordered Top10 title agreement, official-host validation, and a matching "
        "ranking/shelf semantic anchor.",
        "A green workflow by itself does not imply PRODUCTION_READY.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()

    out_dir = OUT_ROOT / args.date
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            rows = [
                audit_platform(browser, args.date, platform, cfg)
                for platform, cfg in TARGETS.items()
            ]
        finally:
            browser.close()

    truth_gate = (
        "PASS"
        if rows and all(item["status"] == "PASS_TRUTH" for item in rows)
        else "BLOCKED"
    )
    platform_results = {
        item["platform"]: item.get("readiness") or {}
        for item in rows
    }
    readiness = evaluate_system_readiness(
        platform_results,
        required_platforms=list(TARGETS),
        integration_ready=False,
    )

    payload = {
        "collectionDate": args.date,
        "generatedAt": datetime.now(TZ).isoformat(),
        "phase": "A_TRUTH_TEST",
        "productionWrite": False,
        "truthGate": truth_gate,
        "platformCount": len(rows),
        "passTruthCount": sum(1 for item in rows if item["status"] == "PASS_TRUTH"),
        "blockedOrFailedCount": sum(
            1 for item in rows if item["status"] != "PASS_TRUTH"
        ),
        "platforms": rows,
        "platformReadiness": platform_results,
        "readiness": readiness,
    }

    (out_dir / "truth_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "TRUTH_AUDIT.md").write_text(
        render_markdown(payload),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "phase": payload["phase"],
                "truthGate": truth_gate,
                "passTruthCount": payload["passTruthCount"],
                "blockedOrFailedCount": payload["blockedOrFailedCount"],
                "promotion": readiness["status"],
                "statuses": {
                    item["platform"]: item["status"] for item in rows
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

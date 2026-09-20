from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

TOP_N = 10
TIMEZONE = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parent
SCOPE_PATH = ROOT / "pilot_scope.json"
DATA_ROOT = ROOT / "pilot_data"
EVIDENCE_ROOT = ROOT / "pilot_evidence"

TITLE_BAD_WORDS = {
    "home", "download", "privacy", "terms", "about", "contact", "login", "sign in",
    "watch now", "more", "view more", "see all", "app store", "google play",
}


SOURCE_CONTROL_REQUIRED = {
    "FlexTV",
    "GoodShort",
    "MoboReels",
    "NetShort",
    "ReelShort",
    "ShortMax",
}


@dataclass
class ProbeResult:
    platform: str
    collection_date: str
    target_key: str
    ranking_type: str
    source_url: str
    strategy: str
    rows: list[dict[str, Any]]
    extraction_status: str
    audit: dict[str, Any]
    evidence: dict[str, Any]
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "collection_date": self.collection_date,
            "source_type": "OFFICIAL_WEB_PILOT",
            "target_key": self.target_key,
            "ranking_type": self.ranking_type,
            "source_url": self.source_url,
            "strategy": self.strategy,
            "top_n": TOP_N,
            "rows": self.rows,
            "row_count": len(self.rows),
            "status": self.extraction_status,
            "audit": self.audit,
            "evidence": self.evidence,
            "error": self.error,
            "production_write": False,
        }


def clean(value: Any, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def norm_title(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_scope() -> dict[str, Any]:
    return json.loads(SCOPE_PATH.read_text(encoding="utf-8"))


def local_today() -> str:
    return datetime.now(TIMEZONE).date().isoformat()


def _canonical_host(value: str) -> str:
    host = (urlparse(str(value or "")).hostname or "").casefold().strip(".")
    return host[4:] if host.startswith("www.") else host


def _source_evidence_layers(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    layers = []
    queue = [evidence] if isinstance(evidence, dict) else []
    seen = set()
    while queue and len(layers) < 8:
        item = queue.pop(0)
        if not isinstance(item, dict) or id(item) in seen:
            continue
        seen.add(id(item))
        layers.append(item)
        for key in ("payloadEvidence", "sourceEvidence"):
            child = item.get(key)
            if isinstance(child, dict):
                queue.append(child)
    return layers


def _source_field(evidence: dict[str, Any], *names: str) -> Any:
    for layer in _source_evidence_layers(evidence):
        for name in names:
            if name in layer and layer.get(name) not in (None, ""):
                return layer.get(name)
    return None


def audit_source_evidence(
    cfg: dict[str, Any],
    evidence: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: int = 900,
) -> dict[str, Any]:
    platform = str(cfg.get("platform") or "")
    required = platform in SOURCE_CONTROL_REQUIRED
    if not required:
        return {
            "required": False,
            "pass": True,
            "errors": [],
        }

    errors = []
    expected_host = _canonical_host(str(cfg.get("url") or ""))

    raw_status = _source_field(evidence, "httpStatus", "http_status")
    try:
        http_status = int(raw_status)
    except (TypeError, ValueError):
        http_status = 0
    if not 200 <= http_status < 400:
        errors.append("HTTP_STATUS_INVALID")

    page_url = clean(_source_field(evidence, "pageUrl", "finalUrl", "final_url"), 1200)
    actual_host = _canonical_host(page_url)
    if not page_url or not actual_host or actual_host != expected_host:
        errors.append("OFFICIAL_HOST_MISMATCH")

    semantic_verified = _source_field(evidence, "semanticVerified", "semantic_verified") is True
    if not semantic_verified:
        errors.append("TARGET_SEMANTIC_UNVERIFIED")

    fetched_raw = clean(_source_field(evidence, "fetchedAt", "fetched_at"), 100)
    age_seconds = None
    if not fetched_raw:
        errors.append("FETCH_TIME_MISSING")
    else:
        try:
            fetched = datetime.fromisoformat(fetched_raw.replace("Z", "+00:00"))
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            age_seconds = (current.astimezone(timezone.utc) - fetched.astimezone(timezone.utc)).total_seconds()
            if age_seconds < -60 or age_seconds > max_age_seconds:
                errors.append("FETCH_EVIDENCE_STALE")
        except Exception:
            errors.append("FETCH_TIME_INVALID")

    return {
        "required": True,
        "pass": not errors,
        "errors": errors,
        "httpStatus": http_status or None,
        "expectedHost": expected_host,
        "actualHost": actual_host,
        "pageUrl": page_url,
        "semanticVerified": semantic_verified,
        "fetchedAt": fetched_raw,
        "ageSeconds": age_seconds,
        "maxAgeSeconds": max_age_seconds,
    }


def _status_with_source_control(status: str, source_control: dict[str, Any]) -> str:
    if status in {"PASS_VERIFIED", "PASS_CANDIDATE"} and not source_control.get("pass", False):
        return "FAIL"
    return status


def ensure_rows(titles: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for title in titles:
        title = clean(title, 500)
        key = norm_title(title)
        if not title or not key or key in seen:
            continue
        if title.casefold() in TITLE_BAD_WORDS or len(title) < 3:
            continue
        seen.add(key)
        rows.append({"rank": len(rows) + 1, "title": title})
        if len(rows) >= TOP_N:
            break
    return rows


def audit_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ranks = [x.get("rank") for x in rows]
    titles = [clean(x.get("title"), 500) for x in rows]
    normed = [norm_title(x) for x in titles if norm_title(x)]
    missing = [x for x in range(1, TOP_N + 1) if x not in set(ranks)]
    duplicate_ranks = sorted({x for x in ranks if ranks.count(x) > 1})
    duplicate_titles = sorted({x for x in normed if normed.count(x) > 1})
    complete = (
        len(rows) == TOP_N
        and ranks == list(range(1, TOP_N + 1))
        and not missing
        and not duplicate_ranks
        and not duplicate_titles
        and all(titles)
    )
    return {
        "batchComplete": complete,
        "expectedRows": TOP_N,
        "actualRows": len(rows),
        "missingRanks": missing,
        "duplicateRanks": duplicate_ranks,
        "duplicateTitles": duplicate_titles,
        "emptyTitles": sum(1 for x in titles if not x),
    }


def status_from_audit(audit: dict[str, Any], *, verified_parser: bool, adapter_found: bool) -> str:
    if audit.get("batchComplete"):
        return "PASS_VERIFIED" if verified_parser else "PASS_CANDIDATE"
    if int(audit.get("actualRows") or 0) > 0:
        return "PARTIAL"
    return "FAIL" if adapter_found else "NEEDS_ADAPTER"


def run_verified_parser(cfg: dict[str, Any], collection_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    platform = cfg["platform"]
    if platform == "DramaBox":
        from official_web_collectors import collect_dramabox_channel
        payload = collect_dramabox_channel(channel="trending", collection_date=collection_date, top_n=TOP_N)
    elif platform == "GoodShort":
        from goodshort_collector import collect_goodshort_top
        payload = collect_goodshort_top(collection_date=collection_date, top_n=TOP_N)
    elif platform == "ShortMax":
        from official_web_collectors import collect_shortmax
        payload = collect_shortmax(collection_date=collection_date, section="Most Popular", top_n=TOP_N)
    else:
        raise RuntimeError(f"NO_VERIFIED_PARSER:{platform}")

    rows = [
        {
            "rank": int(row.get("rank") or idx),
            "title": clean(row.get("title"), 500),
            "tags": row.get("tags") if isinstance(row.get("tags"), list) else [],
            "source_url": clean(row.get("source_url") or row.get("sourceUrl"), 1200),
        }
        for idx, row in enumerate(payload.get("rows") or [], start=1)
    ]
    evidence = {
        "collectorVersion": clean(payload.get("collector_version"), 100),
        "collectedAt": clean(payload.get("collected_at"), 100),
        "payloadEvidence": payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {},
    }
    return rows, evidence


def _heading_probe_script() -> str:
    return r"""
(args) => {
  const kws = (args.keywords || []).map(x => String(x).trim().toLowerCase()).filter(Boolean);
  const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const norm = (s) => clean(s).toLowerCase();
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,[role="heading"]'));
  const hits = headings.filter(el => kws.some(k => norm(el.innerText).includes(k)));
  const results = [];

  function textForAnchor(a) {
    const direct = clean(a.innerText);
    if (direct && direct.length >= 3 && direct.length <= 160) return direct;
    const named = clean(a.getAttribute('title') || a.getAttribute('aria-label'));
    if (named && named.length >= 3 && named.length <= 160) return named;
    const img = a.querySelector('img[alt]');
    const alt = clean(img && img.getAttribute('alt'));
    if (alt && alt.length >= 3 && alt.length <= 160) return alt;
    return '';
  }

  function collect(container) {
    const out = [];
    const seen = new Set();
    for (const a of Array.from(container.querySelectorAll('a[href]'))) {
      const href = String(a.href || '');
      const text = textForAnchor(a);
      const key = text.toLowerCase().replace(/[^a-z0-9]+/g, '');
      if (!href || !text || !key || seen.has(key)) continue;
      if (['home','download','privacy','terms','login','sign in','watch now','more','view more','see all'].includes(text.toLowerCase())) continue;
      seen.add(key);
      out.push({title: text, href});
      if (out.length >= 30) break;
    }
    return out;
  }

  for (const h of hits.slice(0, 8)) {
    let node = h;
    let best = [];
    let bestDepth = 0;
    for (let depth = 0; depth < 5 && node; depth++, node = node.parentElement) {
      const candidate = collect(node);
      if (candidate.length > best.length && candidate.length <= 40) {
        best = candidate;
        bestDepth = depth;
      }
    }
    results.push({heading: clean(h.innerText), depth: bestDepth, items: best});
  }

  return {title: document.title, lang: document.documentElement.lang || '', url: location.href, headings: results};
}
"""


def choose_browser_titles(probe: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    candidates = []
    for hit in probe.get("headings") or []:
        items = hit.get("items") or []
        if not isinstance(items, list):
            continue
        titles = [clean(x.get("title"), 500) for x in items if isinstance(x, dict)]
        rows = ensure_rows(titles)
        candidates.append({"heading": clean(hit.get("heading"), 200), "depth": hit.get("depth"), "rows": rows})
    candidates.sort(key=lambda x: (-len(x["rows"]), int(x.get("depth") or 99)))
    best = candidates[0] if candidates else {"heading": "", "depth": None, "rows": []}
    return [x["title"] for x in best["rows"]], {
        "matchedHeading": best.get("heading") or "",
        "candidateCount": len(best.get("rows") or []),
        "candidateSections": [
            {"heading": x["heading"], "rowCount": len(x["rows"]), "depth": x.get("depth")}
            for x in candidates[:5]
        ],
    }


def browser_probe(browser, cfg: dict[str, Any], evidence_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    page = browser.new_page(viewport={"width": 1440, "height": 1200}, locale="en-US")
    try:
        page.goto(cfg["url"], wait_until="domcontentloaded", timeout=70000)
        page.wait_for_timeout(5000)
        html = page.content().encode("utf-8", errors="replace")
        html_path = evidence_dir / f'{cfg["platform"]}.html.gz'
        with gzip.open(html_path, "wb", compresslevel=6) as fh:
            fh.write(html)
        screenshot_path = evidence_dir / f'{cfg["platform"]}.png'
        page.screenshot(path=str(screenshot_path), full_page=True)
        probe = page.evaluate(_heading_probe_script(), {"keywords": [str(x).casefold() for x in (cfg.get("heading_keywords") or [])]})
        titles, selection = choose_browser_titles(probe)
        rows = ensure_rows(titles)
        evidence = {
            "pageTitle": clean(probe.get("title"), 300),
            "pageUrl": clean(probe.get("url"), 1200),
            "documentLang": clean(probe.get("lang"), 40),
            "matchedHeading": selection["matchedHeading"],
            "candidateSections": selection["candidateSections"],
            "rawHtmlSha256": sha256_bytes(html),
            "rawHtmlFile": str(html_path.relative_to(ROOT)),
            "screenshotFile": str(screenshot_path.relative_to(ROOT)),
            "screenshotSha256": sha256_bytes(screenshot_path.read_bytes()),
        }
        return rows, evidence, bool(selection["matchedHeading"])
    finally:
        page.close()


def collect_one(browser, cfg: dict[str, Any], collection_date: str, evidence_dir: Path) -> ProbeResult:
    platform = cfg["platform"]
    verified = cfg.get("strategy") == "verified_parser"
    parser_error = ""
    parser_evidence: dict[str, Any] = {}

    if verified:
        try:
            rows, parser_evidence = run_verified_parser(cfg, collection_date)
            audit = audit_rows(rows)
            raw_status = status_from_audit(
                audit,
                verified_parser=True,
                adapter_found=True,
            )
            source_control = audit_source_evidence(cfg, parser_evidence)
            parser_evidence = {
                **parser_evidence,
                "sourceControl": source_control,
            }
            return ProbeResult(
                platform=platform,
                collection_date=collection_date,
                target_key=cfg["target_key"],
                ranking_type=cfg["ranking_type"],
                source_url=cfg["url"],
                strategy="verified_parser",
                rows=rows,
                extraction_status=_status_with_source_control(raw_status, source_control),
                audit=audit,
                evidence=parser_evidence,
            )
        except Exception as exc:
            parser_error = f"{type(exc).__name__}: {exc}"

    try:
        rows, browser_evidence, adapter_found = browser_probe(browser, cfg, evidence_dir)
        audit = audit_rows(rows)
        evidence = {"parserFallbackError": parser_error, **parser_evidence, **browser_evidence}
        raw_status = status_from_audit(
            audit,
            verified_parser=False,
            adapter_found=adapter_found,
        )
        source_control = audit_source_evidence(cfg, evidence)
        evidence["sourceControl"] = source_control
        return ProbeResult(
            platform=platform,
            collection_date=collection_date,
            target_key=cfg["target_key"],
            ranking_type=cfg["ranking_type"],
            source_url=cfg["url"],
            strategy="browser_probe" if not verified else "verified_parser+browser_fallback",
            rows=rows,
            extraction_status=_status_with_source_control(raw_status, source_control),
            audit=audit,
            evidence=evidence,
            error=parser_error,
        )
    except Exception as exc:
        audit = audit_rows([])
        err = f"{type(exc).__name__}: {exc}"
        if parser_error:
            err = parser_error + " | browser: " + err
        return ProbeResult(
            platform=platform,
            collection_date=collection_date,
            target_key=cfg["target_key"],
            ranking_type=cfg["ranking_type"],
            source_url=cfg["url"],
            strategy=str(cfg.get("strategy") or "browser_probe"),
            rows=[],
            extraction_status="FAIL",
            audit=audit,
            evidence={"parserFallbackError": parser_error},
            error=err,
        )


def prior_summaries(exclude_date: str) -> list[dict[str, Any]]:
    out = []
    if not DATA_ROOT.exists():
        return out
    for path in sorted(DATA_ROOT.glob("*/summary.json")):
        if path.parent.name == exclude_date:
            continue
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def stability_for(platform: str, current_status: str, historical: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = []
    for summary in historical:
        for item in summary.get("platforms") or []:
            if item.get("platform") == platform:
                statuses.append(str(item.get("status") or ""))
    statuses.append(current_status)
    good = {"PASS_VERIFIED", "PASS_CANDIDATE"}
    consecutive = 0
    for status in reversed(statuses):
        if status in good:
            consecutive += 1
        else:
            break
    return {"lastThree": statuses[-3:], "consecutiveValidRuns": consecutive, "pathStable": consecutive >= 3}


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# Web Top10 Pilot — {summary['collection_date']}",
        "",
        f"Promotion state: **{summary['promotion_state']}**",
        "",
        "| Platform | Status | Rows | Stable runs | Path stable |",
        "|---|---:|---:|---:|---:|",
    ]
    for item in summary["platforms"]:
        stable = item["stability"]
        lines.append(
            f"| {item['platform']} | {item['status']} | {item['row_count']} | "
            f"{stable['consecutiveValidRuns']} | {'YES' if stable['pathStable'] else 'NO'} |"
        )
    lines.extend(["", "No pilot result is written to production tables. Core promotion requires explicit user confirmation."])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=local_today())
    args = parser.parse_args(argv)
    collection_date = args.date

    scope = load_scope()
    if scope.get("production_write") is not False:
        raise RuntimeError("PILOT_SAFETY_GUARD: production_write must remain false")

    out_dir = DATA_ROOT / collection_date
    evidence_dir = EVIDENCE_ROOT / collection_date
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for cloud Web probes") from exc

    results: list[ProbeResult] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for cfg in scope.get("platforms") or []:
                result = collect_one(browser, cfg, collection_date, evidence_dir)
                results.append(result)
                (out_dir / f'{cfg["platform"]}.json').write_text(
                    json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"{result.extraction_status:14} {result.platform:12} rows={len(result.rows)}")
        finally:
            browser.close()

    historical = prior_summaries(collection_date)
    platform_items = []
    for result in results:
        stable = stability_for(result.platform, result.extraction_status, historical)
        platform_items.append({
            "platform": result.platform,
            "status": result.extraction_status,
            "row_count": len(result.rows),
            "audit": result.audit,
            "stability": stable,
            "error": result.error,
        })

    all_stable = bool(platform_items) and all(x["stability"]["pathStable"] for x in platform_items)
    summary = {
        "pilot": scope.get("pilot"),
        "collection_date": collection_date,
        "generated_at": datetime.now(TIMEZONE).isoformat(),
        "top_n": TOP_N,
        "scope_count": len(platform_items),
        "production_write": False,
        "promotion_requires_user_confirmation": True,
        "promotion_state": "AWAITING_USER_CONFIRMATION" if all_stable else "PILOT_RUNNING",
        "platforms": platform_items,
    }

    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "REPORT.md").write_text(render_markdown(summary), encoding="utf-8")
    (DATA_ROOT / "latest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "date": collection_date,
        "promotion_state": summary["promotion_state"],
        "statuses": {x["platform"]: x["status"] for x in platform_items},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

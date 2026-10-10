"""Fail-closed six-platform Web continuity evidence acceptance (not production promotion)."""
from __future__ import annotations
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SCOPE = json.loads((ROOT / "pilot_scope.json").read_text(encoding="utf-8"))
DATE = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
ALLOW = {"DramaBox", "FlexTV", "GoodShort", "MoboReels", "NetShort", "ReelShort"}

def title_key(title):
    return re.sub(r"[^a-z0-9]+", "", str(title or "").casefold())

def main() -> int:
    if SCOPE.get("production_write") is not False or SCOPE.get("research_auto_trigger") is not False or SCOPE.get("research_queue_write") is not False:
        raise SystemExit("SAFETY_ABORT: production/research writes must remain disabled")
    registered = {p["platform"]: p for p in SCOPE["platforms"]}
    if set(registered) != ALLOW or SCOPE.get("locale") != "en-US" or SCOPE.get("region") != "US":
        raise SystemExit("SAFETY_ABORT: source/locale/region mismatch")
    day = ROOT / "pilot_data" / DATE
    accepted, rejected = [], []
    for name in sorted(ALLOW):
        path = day / (name + ".json")
        if not path.exists():
            rejected.append({"platform": name, "reason": "MISSING", "sourceDate": DATE})
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        ranks = [row.get("rank") for row in doc.get("rows", [])]
        titles = [title_key(row.get("title")) for row in doc.get("rows", [])]
        audit = doc.get("audit") or {}
        reasons = []
        if doc.get("platform") != name or doc.get("source_type") != "OFFICIAL_WEB":
            reasons.append("SOURCE_SEMANTICS")
        if doc.get("collection_date") != DATE or doc.get("target_key") != registered[name]["target_key"]:
            reasons.append("SCOPE_OR_DATE")
        if doc.get("ranking_type") != registered[name]["ranking_type"] or doc.get("source_url") != registered[name]["url"]:
            reasons.append("RANKING_PROVENANCE")
        if doc.get("production_write") is not False:
            reasons.append("PRODUCTION_WRITE")
        if len(ranks) != 10 or ranks != list(range(1, 11)) or any(not t for t in titles) or len(set(titles)) != 10:
            reasons.append("BATCH_VALIDITY")
        if audit.get("batchComplete") is not True or doc.get("status") not in ("PASS_VERIFIED", "PASS_CANDIDATE"):
            reasons.append("COLLECTOR_INCOMPLETE")
        if not isinstance(doc.get("evidence"), dict) or not doc["evidence"]:
            reasons.append("EVIDENCE_MISSING")
        entry = {"platform": name, "sourceDate": DATE, "rows": len(ranks), "collectorStatus": doc.get("status"),
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        if reasons:
            rejected.append({**entry, "reason": ",".join(reasons)})
        else:
            # A pilot pass is an observed Web snapshot, not a passed Truth/Fault/Integration Gate.
            accepted.append({**entry, "state": "ISOLATED_OBSERVATION_ONLY"})
    report = {"collectionDate": DATE, "sourceType": "OFFICIAL_WEB", "locale": "en-US", "region": "US",
              "expected": 6, "accepted": len(accepted), "coverage": f"{len(accepted)}/6",
              "acceptedPlatforms": accepted, "missingOrRejected": rejected,
              "productionWrite": False, "researchTriggered": False, "promotionState": "BLOCK_PROMOTION",
              "provenance": "legacy verified collector paths reused minimally from pilot; not current truth/fault certified"}
    day.mkdir(parents=True, exist_ok=True)
    (day / "CONTINUITY_GATE.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if not rejected else 1

if __name__ == "__main__":
    sys.exit(main())

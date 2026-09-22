from __future__ import annotations

import json
from pathlib import Path

from drama_identity import normalize_title

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = json.loads((ROOT / "staging_data" / "web_overlay_2026-09-18_21.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((ROOT / "staging_data" / "shadow_research_manifest_2026-09-18_21.json").read_text(encoding="utf-8"))
RESULTS = json.loads((ROOT / "staging_data" / "shadow_research_results_wave1_2026-09-22.json").read_text(encoding="utf-8"))
APP = (ROOT / "app.py").read_text(encoding="utf-8")
HTML = (ROOT / "index.html").read_text(encoding="utf-8")

assert OVERLAY["purpose"] == "UI_STAGING_ONLY"
assert OVERLAY["productionWrite"] is False
assert OVERLAY["formalResearchQueueWrite"] is False
assert OVERLAY["sourceSemantics"] == "OFFICIAL_WEB_OBSERVATION"
assert OVERLAY["dates"] == ["2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21"]
assert OVERLAY["acceptedBatchCount"] == 27
assert OVERLAY["acceptedRowCount"] == 270
assert OVERLAY["rejectedBatchCount"] == 5

rows = [
    (batch["collectionDate"], batch["platform"], row)
    for batch in OVERLAY["acceptedBatches"]
    for row in batch["rows"]
]
assert len(rows) == 270
assert all(normalize_title(row["title"]) for _, _, row in rows)

rejected = {(r["collectionDate"], r["platform"]) for r in OVERLAY["rejectedBatches"]}
assert rejected == {
    ("2026-09-18", "DramaWave"),
    ("2026-09-19", "DramaWave"),
    ("2026-09-20", "DramaWave"),
    ("2026-09-21", "DramaWave"),
    ("2026-09-21", "ShortMax"),
}

assert MANIFEST["purpose"] == "STAGING_SHADOW_RESEARCH_ONLY"
assert MANIFEST["productionWrite"] is False
assert MANIFEST["formalResearchQueueWrite"] is False
assert MANIFEST["totalUniquePlatformTitles"] == 92
assert MANIFEST["existingCompleteCount"] == 8
assert MANIFEST["candidateCount"] == 84

assert RESULTS["purpose"] == "STAGING_SHADOW_RESEARCH_ONLY"
assert RESULTS["productionWrite"] is False
assert RESULTS["formalResearchQueueWrite"] is False
assert RESULTS["completedCount"] == len(RESULTS["results"])
assert RESULTS["completedCount"] >= 13
core_fields = [
    "synopsis","genre","lane","audience","storyCore","storySkin","conflict","payoff",
    "localizationLevel","localizationJudgment","mismatch",
]
for result in RESULTS["results"]:
    assert result["status"] == "SHADOW_COMPLETE"
    assert result["confidence"] in {"medium", "high"}
    assert result["sources"] and all(source.get("url") for source in result["sources"])
    research = result["research"]
    assert all(str(research.get(field) or "").strip() for field in core_fields)

for token in [
    "def _merge_staging_web_overlay",
    "def _apply_staging_shadow_research",
    "formalResearchQueueWrite",
    "researchEligibility': 'SHADOW_ONLY'",
]:
    assert token in APP

for token in [
    "function sourceTypeOf(item)",
    "function shouldShowSource(date = activeDate)",
    "2026-10-01",
    "App 与网页有效数据共同进入市场结构",
    "待深研",
]:
    assert token in HTML

# Cross-source movement must remain impossible even when app/target match.
assert "sourceTypeOf(item) + '::' + rankingTargetKey(item)" in HTML
assert "eventSource === sourceType" in HTML

print("Staging Web overlay + Shadow Research contract: PASS")

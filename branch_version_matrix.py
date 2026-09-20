from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "readiness_audit"
MANIFEST = ROOT / "INTEGRATION_MANIFEST.json"


def git(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.returncode, proc.stdout.strip()


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    current_branch = os.environ.get("GITHUB_REF_NAME", "")
    if not current_branch:
        code, value = git("branch", "--show-current")
        current_branch = value if code == 0 else ""

    code, current_head = git("rev-parse", "HEAD")
    if code != 0:
        current_head = ""

    sources = []
    all_minimums_present = True
    for item in manifest.get("sourceRequirements") or []:
        branch = str(item.get("branch") or "")
        minimum = str(item.get("minimumCommit") or "")
        _, remote_head = git("rev-parse", f"origin/{branch}")
        contains_code, _ = git("merge-base", "--is-ancestor", minimum, "HEAD")
        contains = contains_code == 0
        all_minimums_present = all_minimums_present and contains
        sources.append(
            {
                **item,
                "remoteHead": remote_head,
                "currentBranchContainsMinimum": contains,
            }
        )

    prefix = str(manifest.get("integrationBranchPrefix") or "integration/")
    is_integration_branch = current_branch.startswith(prefix)
    integration_ready = bool(is_integration_branch and all_minimums_present)

    payload = {
        "currentBranch": current_branch,
        "currentHead": current_head,
        "integrationBranchPrefix": prefix,
        "isIntegrationBranch": is_integration_branch,
        "allMinimumCommitsPresent": all_minimums_present,
        "integrationReady": integration_ready,
        "sources": sources,
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "branch_version_matrix.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# Branch / Version Matrix",
        "",
        f"- Current branch: **{current_branch or 'unknown'}**",
        f"- Current head: `{current_head or 'unknown'}`",
        f"- Integration ready for D: **{'YES' if integration_ready else 'NO'}**",
        "",
        "| Role | Source branch | Minimum commit present in current branch | Remote head |",
        "|---|---|---:|---|",
    ]
    for item in sources:
        lines.append(
            f"| {item['role']} | {item['branch']} | "
            f"{'YES' if item['currentBranchContainsMinimum'] else 'NO'} | "
            f"`{item.get('remoteHead') or 'unavailable'}` |"
        )
    lines += [
        "",
        "D/E2E promotion must remain blocked until this matrix reports Integration ready = YES.",
        "",
    ]
    (OUT / "BRANCH_VERSION_MATRIX.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

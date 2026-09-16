from __future__ import annotations

from pathlib import Path

NEW_NAME = "JSM-海外AI短剧动态情报及内容分析系统"
FILES = [
    Path("index.html"),
    Path("review.html"),
    Path("collect.html"),
    Path("短剧热播榜研究工具_离线版.html"),
]

REPLACEMENTS = {
    "短剧热播榜研究工具 V1": NEW_NAME,
    "短剧热播榜研究工具": NEW_NAME,
    "短剧市场监测": NEW_NAME,
}


def main() -> None:
    changed = []
    for path in FILES:
        if not path.exists():
            raise SystemExit(f"missing brand surface: {path}")
        data = path.read_bytes()
        updated = data
        for old, new in REPLACEMENTS.items():
            updated = updated.replace(old.encode("utf-8"), new.encode("utf-8"))
        if updated != data:
            path.write_bytes(updated)
            changed.append(str(path))
        if NEW_NAME.encode("utf-8") not in updated:
            raise SystemExit(f"new system name not found after patch: {path}")
    print("brand rename complete; changed:", ", ".join(changed) if changed else "none")


if __name__ == "__main__":
    main()

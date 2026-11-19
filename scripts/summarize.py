#!/usr/bin/env python3
"""Summarise committed cycle manifests into ``manifests/summary.json``.

Pure standard library so the GitHub Actions ``publish`` job can run it without
installing project dependencies. Reads ``manifests/*/*.json`` and never touches
raw response data.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Dict, Set

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST_DIR = ROOT / "manifests"


def build_summary(manifest_dir: pathlib.Path) -> Dict[str, object]:
    """Aggregate days, records, calls and providers from cycle manifests.

    Args:
        manifest_dir: Root directory containing ``<provider>/<day>.json`` files.

    Returns:
        Summary dictionary ready to serialise as ``summary.json``.
    """
    days: Set[str] = set()
    providers: Set[str] = set()
    records = 0
    calls = 0

    for path in sorted(manifest_dir.glob("*/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        run_id = data.get("run_id")
        if run_id:
            days.add(str(run_id))
        providers.add(path.parent.name)
        records += int(data.get("records_today") or 0)
        calls += int(data.get("calls") or 0)

    return {
        "days": len(days),
        "records": records,
        "calls": calls,
        "last_day": max(days) if days else None,
        "providers": sorted(providers),
    }


def main() -> int:
    """Write ``summary.json`` inside the manifest directory."""
    manifest_dir = pathlib.Path(
        os.environ.get("PROBE_MANIFEST_DIR") or DEFAULT_MANIFEST_DIR
    )
    manifest_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary(manifest_dir)
    out = manifest_dir / "summary.json"
    out.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"summary -> {out} ({summary})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

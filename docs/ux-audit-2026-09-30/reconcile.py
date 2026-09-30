"""Rebuild the audit register using explicit implementation ownership.

Run from any directory. New slices must be added deliberately: alphabetic file
ordering can silently replace a newer focused implementation with its older
category-level worklist. This script never infers completion from status rank.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LAYERS = (
    "memory", "settings", "mobile", "identity", "connections",
    "conversation", "reporting", "support", "native-integrations",
    "desktop-maintenance", "knowledge", "personal-memory", "budget",
    "today", "task-diagnostics", "source-checkpoint-lookup", "activation",
    "plugin-adapters", "project-workspaces", "personal-notifications",
)


def main() -> None:
    source = json.loads((ROOT / "source-backlog.json").read_text(encoding="utf-8"))
    assert len(source) == 195 and all(len(row) == 24 for row in source)
    source_ids = {row["ID"] for row in source}
    assert len(source_ids) == 195
    tracker = ROOT / "implementation-tracker.json"
    previous = {row["id"]: row for row in json.loads(tracker.read_text(encoding="utf-8"))}
    assert set(previous) == source_ids
    paths = {path.stem: path for path in (ROOT / "slices").glob("*.json")}
    unknown = set(paths) - set(LAYERS)
    if unknown:
        raise ValueError(f"Assign explicit precedence to new slices: {sorted(unknown)}")
    for name in LAYERS:
        if name not in paths:
            continue
        rows = json.loads(paths[name].read_text(encoding="utf-8"))
        assert len({row["id"] for row in rows}) == len(rows), name
        for row in rows:
            assert row["id"] in source_ids, row["id"]
            value = {**previous[row["id"]], **row, "evidence_slice": f"slices/{name}.json"}
            evidence = value.get("implementation_evidence", [])
            value["implementation_evidence"] = evidence if isinstance(evidence, list) else [evidence]
            previous[row["id"]] = value
    result = [previous[row["ID"]] for row in source]
    tracker.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(dict(collections.Counter(row["status"] for row in result)), sort_keys=True))
    print("Open:", ", ".join(row["id"] for row in result if row["status"] not in {
        "implemented-test-needed", "already-fixed-test-needed", "implemented", "test-needed"}))


if __name__ == "__main__":
    main()

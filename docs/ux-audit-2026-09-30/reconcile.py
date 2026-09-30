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
    "memory", "settings", "mobile", "connections", "conversation",
    "identity", "reporting", "support", "native-integrations",
    "desktop-maintenance", "knowledge", "personal-memory", "budget",
    "today", "task-diagnostics", "source-checkpoint-lookup", "activation",
    "plugin-adapters", "project-workspaces", "personal-notifications",
    "preference-scopes", "model-tests", "web-localization", "conversation-inbox", "browser",
    "identity-final-qualification", "memory-knowledge-final-qualification", "connections-final", "settings-personal-data",
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
    counts = collections.Counter(row["status"] for row in result)
    lines = ["# Audit implementation checklist", "",
             "Generated from the complete 195-entry register. Built means code is integrated;",
             "it does not mean all acceptance checks or release qualification have passed.", "",
             "[Full evidence and remaining criteria](implementation-tracker.json) · "
             "[Original 24-field backlog](source-backlog.json) · "
             "[Local qualification](local-qualification.md)", "",
             "Current dispositions: " + ", ".join(f"{count} {status}" for status, count in sorted(counts.items())) + ".", "",
             "| ID | Disposition | Audit item |", "| --- | --- | --- |"]
    labels = {"implemented-test-needed": "Built; acceptance pending",
              "already-fixed-test-needed": "Existing fix; acceptance pending",
              "planned": "Active implementation", "test-needed": "Qualification required",
              "implemented": "Implemented", "deferred-decision": "Decision required",
              "blocked-source": "Source blocked"}
    for row in result:
        title = row["title"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {row['id']} | {labels.get(row['status'], row['status'])} | {title} |")
    (ROOT / "implementation-checklist.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(dict(counts), sort_keys=True))
    print("Open:", ", ".join(row["id"] for row in result if row["status"] not in {
        "implemented-test-needed", "already-fixed-test-needed", "implemented", "test-needed"}))


if __name__ == "__main__":
    main()

"""Recipient-specific in-product alert policy and encrypted delivery state.

The caller supplies a fresh authorized projection. No task text is persisted,
and missing/revoked source references are removed before anything is returned.
External organization webhook destinations are deliberately not reused as a
person's destination.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .crypto_at_rest import seal, unseal
from .file_lock import atomic_write_bytes, cross_process_lock, ensure_private_directory

EVENTS = frozenset({"needs-decision", "blocked", "failed", "completed", "changed-result", "deadline"})
DEFAULTS = {"mode": "immediate", "timezone": "UTC", "quiet_enabled": False,
            "quiet_start": "22:00", "quiet_end": "08:00", "digest_time": "09:00",
            "critical_exceptions": False, "events": ["needs-decision", "blocked", "failed", "completed"],
            "muted_tasks": {}, "watch_all_projects": True, "projects": [], "external_links": []}
_CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")


def validate_preferences(value: object) -> dict:
    if not isinstance(value, dict) or set(value) - DEFAULTS.keys():
        raise ValueError("Choose the supported notification preferences.")
    result = {**DEFAULTS, **value}
    if not isinstance(result["mode"], str) or result["mode"] not in {"immediate", "digest", "off"}:
        raise ValueError("Choose immediate alerts, a daily digest, or off.")
    for key in ("quiet_enabled", "critical_exceptions", "watch_all_projects"):
        if not isinstance(result[key], bool):
            raise ValueError("Choose whether to enable this notification option.")
    if not isinstance(result["timezone"], str) or len(result["timezone"]) > 100:
        raise ValueError("Choose an IANA time zone.")
    try:
        ZoneInfo(result["timezone"])
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("That time zone is unavailable. Choose a supported IANA time zone.") from exc
    for key in ("quiet_start", "quiet_end", "digest_time"):
        if not isinstance(result[key], str) or not _CLOCK.fullmatch(result[key]):
            raise ValueError("Choose a time of day.")
    if result["quiet_enabled"] and result["quiet_start"] == result["quiet_end"]:
        raise ValueError("Quiet hours need different start and end times; choose Off to stop personal alerts.")
    events = result["events"]
    if not isinstance(events, list) or len(events) > len(EVENTS) or any(not isinstance(e, str) or e not in EVENTS for e in events):
        raise ValueError("Choose supported event subscriptions.")
    result["events"] = sorted(set(events))
    projects = result["projects"]
    if not isinstance(projects, list) or len(projects) > 50 or any(type(p) is not int or p < 1 for p in projects):
        raise ValueError("Choose up to 50 available projects.")
    result["projects"] = sorted(set(projects))
    links = result["external_links"]
    if (not isinstance(links, list) or len(links) > 20
            or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value) for value in links)):
        raise ValueError("Choose up to 20 verified personal channel links.")
    result["external_links"] = sorted(set(links))
    mutes = result["muted_tasks"]
    if not isinstance(mutes, dict) or len(mutes) > 50:
        raise ValueError("Mute up to 50 tasks.")
    for goal, until in mutes.items():
        if (not isinstance(goal, str) or len(goal) > 16 or not goal.isascii() or not goal.isdigit() or not 0 < int(goal) < 2**53
                or isinstance(until, bool) or not isinstance(until, (int, float))
                or not math.isfinite(until) or until < 0 or until > time.time() + 7 * 86400):
            raise ValueError("Choose a task mute ending within seven days.")
    result["muted_tasks"] = {key: until for key, until in mutes.items() if until > time.time()}
    return result


def delivery_window(preferences: dict, *, now: float, last_digest_day: str = "",
                    critical: bool = False, allow_critical: bool = False) -> tuple[bool, str]:
    """Wall-clock comparison handles DST gaps/folds without duplicate digests."""
    if preferences["mode"] == "off":
        return False, "Personal alerts are off. Organization operational alerts are unchanged."
    if critical and allow_critical and preferences["critical_exceptions"]:
        return True, "An approval or deadline alert bypassed quiet hours under your explicit exception policy."
    local = datetime.fromtimestamp(now, timezone.utc).astimezone(ZoneInfo(preferences["timezone"]))
    clock = local.strftime("%H:%M")
    start, end = preferences["quiet_start"], preferences["quiet_end"]
    if preferences["quiet_enabled"] and ((start <= clock < end) if start < end else (clock >= start or clock < end)):
        return False, "Personal alerts are held until quiet hours end in " + preferences["timezone"] + "."
    if preferences["mode"] == "digest":
        if clock < preferences["digest_time"] or local.date().isoformat() == last_digest_day:
            return False, "Personal alerts are held for your next daily digest."
    return True, "Personal alerts are available in this inbox."


class PersonalInbox:
    def __init__(self, principal: str, path: Path | None = None):
        if not principal.startswith("user:") or not principal[5:]:
            raise ValueError("A named account is required for personal alerts.")
        self.principal = principal
        if path is None:
            from .paths import data_dir
            path = data_dir("personal-notifications", hashlib.sha256(principal.encode()).hexdigest() + ".enc")
        self.path = path

    def _read(self) -> dict:
        try:
            value = json.loads(unseal(self.path.read_bytes()).decode())
        except FileNotFoundError:
            return {"known": {}, "items": [], "digest_day": "", "initialized": False}
        if (not isinstance(value, dict) or not isinstance(value.get("known"), dict)
                or not isinstance(value.get("items"), list)):
            raise ValueError("Personal notification history could not be read.")
        return value

    def refresh(self, sources: list[dict], preferences: dict, *, now: float | None = None,
                allow_critical: bool = False, seen_id: str | None = None) -> dict:
        now = time.time() if now is None else now
        preferences = validate_preferences(preferences)
        # Source IDs and versions describe semantic changes, never streaming
        # timestamps. Authorized current sources also hydrate every display row.
        current = {source["ref"]: source for source in sources}
        ensure_private_directory(self.path.parent)
        with cross_process_lock(self.path):
            stored = self._read()
            def subscribed(source):
                return (source["category"] in preferences["events"] and preferences["mode"] != "off"
                        and (preferences["watch_all_projects"] or not source.get("goal_id")
                             or source.get("project_id") in preferences["projects"]))
            items = [item for item in stored["items"] if item["created_at"] > now - 7 * 86400
                     and item["ref"] in current and current[item["ref"]]["version"] == item["version"]
                     and subscribed(current[item["ref"]])]
            known = stored["known"]
            known_digests = stored.get("semantic_digests", {})
            for ref, source in current.items():
                if known.get(ref) == source["version"]:
                    continue
                if not subscribed(source):
                    continue
                if (source.get("semantic_digest") and
                        known_digests.get(ref) == source["semantic_digest"]):
                    continue
                if source["category"] == "changed-result" and ref not in known and not source.get("changed_after_completion"):
                    continue
                # Opening an inbox for the first time does not replay every old
                # completion. Current decisions/problems are useful immediately.
                if not stored["initialized"] and source["category"] in {"completed", "changed-result"}:
                    continue
                ident = hashlib.sha256(json.dumps([self.principal, ref, source["version"]]).encode()).hexdigest()
                items.append({"id": ident, "ref": ref, "version": source["version"], "created_at": now,
                              "delivered_at": None, "seen_at": None})
            eligible, explanation = delivery_window(preferences, now=now, last_digest_day=stored["digest_day"])
            digest_delivered = False
            visible = []
            for item in items:
                source = current[item["ref"]]
                muted = preferences["muted_tasks"].get(str(source.get("goal_id")), 0) > now
                critical, _ = delivery_window(preferences, now=now, last_digest_day=stored["digest_day"],
                                               critical=source["category"] in {"needs-decision", "deadline"},
                                               allow_critical=allow_critical)
                if not item["delivered_at"] and (eligible or critical) and not muted:
                    item["delivered_at"] = now
                    digest_delivered = digest_delivered or eligible
                if item["delivered_at"] and not muted:
                    if item["id"] == seen_id:
                        item["seen_at"] = now
                    visible.append({**source, **item, "state": "seen" if item["seen_at"] else "available"})
            digest_day = stored["digest_day"]
            if preferences["mode"] == "digest" and digest_delivered:
                digest_day = datetime.fromtimestamp(now, timezone.utc).astimezone(ZoneInfo(preferences["timezone"])).date().isoformat()
            next_state = {"known": {ref: source["version"] for ref, source in current.items()},
                          "semantic_digests": {ref: source["semantic_digest"] for ref, source in current.items()
                                               if source.get("semantic_digest")},
                          "items": items, "digest_day": digest_day, "initialized": True}
            atomic_write_bytes(self.path, seal(json.dumps(next_state, separators=(",", ":")).encode()), mode=0o600)
        return {"items": sorted(visible, key=lambda row: row["created_at"], reverse=True),
                "delivery_note": explanation, "checked_at": now,
                "channel": "In-product inbox", "external_delivery": False}

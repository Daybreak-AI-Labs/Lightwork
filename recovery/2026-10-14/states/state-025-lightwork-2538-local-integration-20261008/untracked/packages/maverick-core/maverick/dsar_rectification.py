"""Subject-scoped correction of Lightwork-held messages and personal facts.

This is deliberately narrower than the legacy free-text rectification helper:
only messages in conversations whose channel and user id exactly match the
requester, and facts in that requester's explicit ``user:<channel>:<user>:``
namespace, can be changed. Shared goals, audit records and other subjects are
never searched or written here.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from .dsar import _fact_subject_token
from .world_model import _dec_field, _enc_field

_CELL_SCHEMA = "lightwork.dsar-rectification-cell.v1"
_MAX_QUERY = 200
_MAX_REPLACEMENT = 2_000
_MAX_CELL = 1_000_000


class DsarRectificationError(ValueError):
    """A requested correction is invalid, stale or outside subject scope."""


@dataclass(frozen=True)
class Cell:
    reference: str
    kind: str
    field: str
    current: str
    key: str | int


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _reference(user_id: str, channel: str, kind: str, key: str | int, field: str) -> str:
    binding = "\0".join((_CELL_SCHEMA, user_id, channel, kind, str(key), field))
    return hashlib.sha256(binding.encode("utf-8")).hexdigest()


def _subject_cells(world: Any, user_id: str, channel: str) -> list[Cell]:
    user = (user_id or "").strip()
    source = (channel or "").strip()
    if not user or not source:
        raise DsarRectificationError("Choose the requester's exact account and channel.")
    if not hasattr(world, "_read_all") or not hasattr(world, "_writing"):
        raise DsarRectificationError("Lightwork's personal records are unavailable for correction.")

    cells: list[Cell] = []
    try:
        conversations = [
            row for row in world.list_conversations(source)
            if row.channel == source and row.user_id == user
        ]
    except Exception as exc:
        raise DsarRectificationError(
            "This person's saved records could not be read. No correction was made; ask an administrator to check the data."
        ) from exc
    for conversation in conversations:
        try:
            rows = world._read_all(
                "SELECT id, conversation_id, content FROM turns "
                "WHERE conversation_id = ? ORDER BY id",
                (conversation.id,),
            )
        except Exception as exc:
            raise DsarRectificationError(
                "This person's saved records could not be read. No correction was made; ask an administrator to check the data."
            ) from exc
        for row in rows:
            try:
                current = _dec_field(row["content"])
            except Exception as exc:
                raise DsarRectificationError(
                    "This person's saved records could not be read. No correction was made; ask an administrator to check the data."
                ) from exc
            if not isinstance(current, str) or not current:
                continue
            key = int(row["id"])
            cells.append(Cell(
                _reference(user, source, "message", key, "content"),
                "message", "message", current, key,
            ))

    # Use the same explicit personal-fact namespace as access and erasure.
    try:
        personal_facts = world.facts_matching(_fact_subject_token(source, user))
    except Exception as exc:
        raise DsarRectificationError(
            "This person's saved records could not be read. No correction was made; ask an administrator to check the data."
        ) from exc
    for key, current in personal_facts.items():
        if not isinstance(current, str) or not current:
            continue
        cells.append(Cell(
            _reference(user, source, "fact", key, "value"),
            "personal fact", "saved fact", current, key,
        ))
    return cells


def subject_channels(world: Any, user_id: str, *, limit: int = 100) -> dict[str, Any]:
    """List only the channels in which this exact requester has conversations."""
    user = (user_id or "").strip()
    if not user:
        raise DsarRectificationError("This request does not identify whose records can be corrected.")
    if not hasattr(world, "list_conversations"):
        raise DsarRectificationError("Lightwork's personal records are unavailable for correction.")
    try:
        channels = sorted({
            row.channel for row in world.list_conversations()
            if row.user_id == user and isinstance(row.channel, str) and row.channel
        })
    except Exception as exc:
        raise DsarRectificationError(
            "This person's saved records could not be read. No correction was made; ask an administrator to check the data."
        ) from exc
    cap = max(1, min(int(limit), 100))
    return {"channels": channels[:cap], "more_available": len(channels) > cap}


def _snippet(value: str, needle: str, radius: int = 48) -> str:
    match = re.search(re.escape(needle), value, re.IGNORECASE)
    if match is None:
        return ""
    start = max(0, match.start() - radius)
    end = min(len(value), match.end() + radius)
    return value[start:end]


def search_subject_records(
    world: Any,
    user_id: str,
    channel: str,
    incorrect_text: str,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Find exact cells containing the supplied phrase in this subject's data."""
    needle = (incorrect_text or "").strip()
    if not needle or len(needle) > _MAX_QUERY:
        raise DsarRectificationError("Enter the exact text the requester says is incorrect (up to 200 characters).")
    cap = max(1, min(int(limit), 100))
    matches = []
    for cell in _subject_cells(world, user_id, channel):
        excerpt = _snippet(cell.current, needle)
        if not excerpt:
            continue
        matches.append({
            "reference": cell.reference,
            "kind": cell.kind,
            "label": "Saved message" if cell.kind == "message" else "Saved personal detail",
            "field": cell.field,
            "snippet": excerpt,
            "current_sha256": _digest(cell.current),
        })
        if len(matches) > cap:
            return {"matches": matches[:cap], "more_available": True}
    return {"matches": matches, "more_available": False}


def preview_subject_correction(
    world: Any,
    user_id: str,
    channel: str,
    *,
    reference: str,
    incorrect_text: str,
    replacement_text: str,
    expected_sha256: str,
) -> dict[str, Any]:
    """Return the full before/after of one selected subject-owned cell."""
    old = (incorrect_text or "").strip()
    new = replacement_text or ""
    if not old or len(old) > _MAX_QUERY:
        raise DsarRectificationError("Enter the exact text to replace, up to 200 characters.")
    if not new.strip() or len(new) > _MAX_REPLACEMENT:
        raise DsarRectificationError("Enter the corrected text, up to 2,000 characters.")
    cell = next((
        item for item in _subject_cells(world, user_id, channel)
        if item.reference == reference
    ), None)
    if cell is None:
        raise DsarRectificationError("That record is no longer available in this requester's data.")
    current_hash = _digest(cell.current)
    if current_hash != expected_sha256:
        raise DsarRectificationError("This record changed. Search again before reviewing a correction.")
    if len(cell.current) > _MAX_CELL:
        raise DsarRectificationError("This record is too large to correct in this screen.")
    pattern = re.compile(re.escape(old), re.IGNORECASE)
    after, count = pattern.subn(lambda _match: new, cell.current)
    if not count:
        raise DsarRectificationError("The text is no longer present. Search again before reviewing a correction.")
    if after == cell.current:
        raise DsarRectificationError("The corrected text would not change this record.")
    return {
        "reference": cell.reference,
        "kind": cell.kind,
        "label": "Saved message" if cell.kind == "message" else "Saved personal detail",
        "field": cell.field,
        "before": cell.current,
        "after": after,
        "before_sha256": current_hash,
        "after_sha256": _digest(after),
        "matches_changed": count,
    }


def apply_subject_correction(
    world: Any,
    user_id: str,
    channel: str,
    *,
    reference: str,
    incorrect_text: str,
    replacement_text: str,
    expected_sha256: str,
) -> dict[str, Any]:
    """Apply one reviewed replacement after rechecking identity and content.

    Message ownership and the expected content digest are rechecked inside the
    writer lock. Personal facts use the native fact revision check, preserving
    their source, sensitivity and temporal-history behavior.
    """
    preview = preview_subject_correction(
        world, user_id, channel, reference=reference,
        incorrect_text=incorrect_text, replacement_text=replacement_text,
        expected_sha256=expected_sha256,
    )
    cell = next((
        item for item in _subject_cells(world, user_id, channel)
        if item.reference == reference
    ), None)
    if cell is None:
        raise DsarRectificationError("That record is no longer available in this requester's data.")

    if cell.kind == "message":
        with world._writing() as connection:
            row = connection.execute(
                "SELECT c.channel, c.user_id, t.content FROM turns t "
                "JOIN conversations c ON c.id=t.conversation_id "
                "WHERE t.id=?",
                (cell.key,),
            ).fetchone()
            if row is None or row["channel"] != channel or row["user_id"] != user_id:
                raise DsarRectificationError("This message is no longer in the requester's account.")
            current = _dec_field(row["content"])
            if not isinstance(current, str) or _digest(current) != expected_sha256:
                raise DsarRectificationError("This message changed. Search again before applying the correction.")
            pattern = re.compile(re.escape(incorrect_text.strip()), re.IGNORECASE)
            after, count = pattern.subn(lambda _match: replacement_text, current)
            if not count or after != preview["after"]:
                raise DsarRectificationError("This message changed. Search again before applying the correction.")
            connection.execute(
                "UPDATE turns SET content=? WHERE id=?",
                (_enc_field(after), int(cell.key)),
            )
    else:
        key = str(cell.key)
        expected_prefix = f"user:{_fact_subject_token(channel, user_id)}:"
        if not key.startswith(expected_prefix):
            raise DsarRectificationError("That saved fact is outside the requester's personal records.")
        fact = world.fact_record(key)
        if not fact or _digest(str(fact.get("value") or "")) != expected_sha256:
            raise DsarRectificationError("This saved fact changed. Search again before applying the correction.")
        if not callable(getattr(world, "upsert_fact", None)):
            raise DsarRectificationError("This installation's saved facts cannot be safely changed here.")
        world.upsert_fact(
            key,
            preview["after"],
            episode_id=fact.get("source_episode_id"),
            source=str(fact.get("source") or ""),
            trust_tier=int(fact.get("trust_tier", 3)),
            sensitivity=str(fact.get("sensitivity") or "internal"),
            expected_revision=int(fact["revision"]),
            tags=list(fact.get("tags") or []),
        )

    return {
        "reference": cell.reference,
        "kind": cell.kind,
        "field": cell.field,
        "before_sha256": preview["before_sha256"],
        "after_sha256": preview["after_sha256"],
        "matches_changed": preview["matches_changed"],
    }


def current_subject_cell_sha256(
    world: Any, user_id: str, channel: str, reference: str,
) -> str | None:
    """Read one cell's current digest for exact recovery; never changes data."""
    cell = next((
        item for item in _subject_cells(world, user_id, channel)
        if item.reference == reference
    ), None)
    return _digest(cell.current) if cell is not None else None


__all__ = [
    "DsarRectificationError",
    "apply_subject_correction",
    "current_subject_cell_sha256",
    "preview_subject_correction",
    "search_subject_records",
    "subject_channels",
]

"""Federated audit-log verification.

Each node/tenant keeps its own signed, hash-chained audit log
(``audit/signing.py``: per-file ``verify_chain`` + cross-file
``verify_anchors``). In a federation — multi-tenant hosting, a fleet of
workers, A2A-delegated peers — those logs *reference each other*: node A
records "delegated task T to node B", node B records "accepted task T from
node A". Single-node verification can't catch a node that **drops its half**
of a cross-node event to hide an action.

This adds the federation layer:

* :func:`collect_node` — verify ONE node's chain (reusing ``verify_chain`` /
  ``verify_anchors``) and extract its **cross-references** (events naming a
  peer node + a correlation id).
* :func:`cross_verify` — over a set of verified nodes, confirm every
  cross-reference is **reciprocated**: if A claims a link to B for correlation
  C, B's log must carry the matching counterpart. An unreciprocated reference
  (a dropped/forged half) is reported with which node is missing it.
* :func:`verify_federation` — the operator entry point: per-node integrity +
  cross-node reciprocity into one report.

Pure over already-extracted event rows (no signing key needed beyond what
``verify_chain`` already does); deterministic and offline. A node whose own
chain is broken is reported and excluded from reciprocity (you can't trust its
references).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Audit event fields that mark a cross-node reference. An event is a
# cross-reference when it names a peer node and a correlation id; ``direction``
# (sent/received) lets reciprocity pair the two halves.
_PEER_FIELDS = ("peer_node", "peer", "to_node", "from_node")
_CORR_FIELDS = ("correlation_id", "corr_id", "task_id", "delegation_id")


@dataclass
class CrossRef:
    node: str               # the node whose log this row came from
    peer: str               # the node it references
    correlation: str        # the shared id linking the two halves
    direction: str          # "sent" | "received" | "unknown"
    line_no: int = 0
    accepted: bool | None = None
    remote_goal_id: int | None = None
    outcome_state: str = ""
    outcome_issue: str = ""


@dataclass
class NodeReport:
    node: str
    intact: bool
    breaks: list = field(default_factory=list)     # list[ChainBreak]
    crossrefs: list[CrossRef] = field(default_factory=list)
    verification_scope: str = "caller_supplied"
    anchors_checked: bool = False
    anchors_bound_to_rows: bool = False
    checkpoints_verified: bool = False


@dataclass
class FederationReport:
    nodes: dict[str, NodeReport] = field(default_factory=dict)
    # (node, peer, correlation, direction) references whose counterpart is
    # absent from the peer's log.
    unreciprocated: list[CrossRef] = field(default_factory=list)
    # references into a node whose own chain is broken (can't be trusted).
    untrusted_peer: list[CrossRef] = field(default_factory=list)
    outcome_conflicts: list[CrossRef] = field(default_factory=list)
    unresolved_outcomes: list[CrossRef] = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        return (all(n.intact for n in self.nodes.values())
                and not self.unreciprocated and not self.untrusted_peer
                and not self.outcome_conflicts and not self.unresolved_outcomes)


def _first(d: dict, fields) -> str | None:
    for f in fields:
        v = d.get(f)
        if v not in (None, ""):
            return str(v)
    return None


def _opposite(direction: str) -> str:
    return {"sent": "received", "received": "sent"}.get(direction, direction)


def _outcome_fields(row: dict, direction: str) -> tuple[bool | None, int | None, str, str]:
    state = str(row.get("outcome_state") or "")
    accepted = row.get("accepted")
    goal_id = row.get("remote_goal_id" if direction == "sent" else "goal_id")
    if type(goal_id) is not int or goal_id <= 0:
        goal_id = None
    issue = state if state in {"pending", "unknown"} else ""
    if row.get("kind") == "federation_delegate":
        if direction not in {"sent", "received"}:
            issue = "invalid_direction"
        elif "outcome_state" in row and not isinstance(row["outcome_state"], str):
            issue = "invalid_outcome_state"
        elif state not in {"", "accepted", "rejected", "pending", "unknown"}:
            issue = "invalid_outcome_state"
        elif not issue and type(accepted) is not bool:
            issue = "invalid_acceptance"
        elif ((state == "accepted" and accepted is not True)
              or (state == "rejected" and accepted is not False)):
            issue = "conflicting_outcome_state"
        elif not issue and accepted is True and goal_id is None:
            issue = "invalid_goal_identity"
    if type(accepted) is not bool or issue:
        accepted = None
    return accepted, goal_id, state, issue


def extract_crossrefs(node: str, rows: list[dict]) -> list[CrossRef]:
    """Pull cross-node references out of one node's audit rows.

    A row is a reference when it names a peer node AND a correlation id. The
    ``direction`` is read from a ``direction``/``dir`` field (sent/received),
    defaulting to "unknown" — reciprocity still pairs on (peer, correlation).
    """
    out: list[CrossRef] = []
    for i, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        peer = _first(row, _PEER_FIELDS)
        corr = _first(row, _CORR_FIELDS)
        if not peer or not corr:
            continue
        direction = str(row.get("direction") or row.get("dir") or "unknown").lower()
        if direction not in ("sent", "received", "unknown"):
            direction = "unknown"
        accepted, goal_id, outcome_state, issue = _outcome_fields(row, direction)
        out.append(CrossRef(
            node=node, peer=peer, correlation=corr, direction=direction, line_no=i,
            accepted=accepted, remote_goal_id=goal_id, outcome_state=outcome_state,
            outcome_issue=issue,
        ))
    return out


def collect_node(node: str, audit_dir: Path, rows: list[dict] | None = None, *,
                 pubkey_hex: str | None = None) -> NodeReport:
    """Verify one node's chain integrity and extract its cross-references.

    References are extracted from the exact segment snapshots whose signatures
    were checked. Optional legacy ``rows`` must match those decoded events in
    order and content; they are never an independent source of trusted claims.
    Anchor checks use a separate read of current files. Neither a globally
    atomic multi-day snapshot nor independent checkpoint verification is claimed.
    """
    breaks: list = []
    captured: list[dict] = []
    anchors_checked = False
    try:
        from ..file_lock import cross_process_lock
        from .segments import ReadReport, SegmentSnapshot
        from .signing import ChainBreak, verify_anchors, verify_chain
        d = Path(audit_dir)
        if not d.is_dir():
            breaks.append(ChainBreak(0, "missing_audit_dir", str(d)))
        else:
            audit_files = [
                f for f in sorted(d.glob("*.ndjson"))
                if not f.name.startswith(".")
            ]
            if not audit_files:
                breaks.append(ChainBreak(0, "missing_audit_logs", str(d)))
            for f in audit_files:
                with cross_process_lock(f, strict=True):
                    with SegmentSnapshot(f, ReadReport()) as snapshot:
                        breaks.extend(verify_chain(
                            f, pubkey_hex, lines=snapshot.lines(), max_breaks=64,
                        ))
                        for line in snapshot.lines():
                            if not line.strip():
                                continue
                            row = json.loads(line)
                            if not isinstance(row, dict):
                                raise ValueError("non-object audit event")
                            captured.append(row)
            breaks.extend(verify_anchors(d, pubkey_hex))
            anchors_checked = True
            if audit_files and rows is not None and rows != captured:
                breaks.append(ChainBreak(
                    0, "supplied_rows_mismatch",
                    "supplied events differ from verified segment snapshots",
                ))
    # failure-policy: fail_closed
    except Exception as e:  # pragma: no cover -- verification never crashes the report
        from .signing import ChainBreak
        breaks.append(ChainBreak(0, "verify_error", str(e)))
    return NodeReport(
        node=node, intact=not breaks, breaks=breaks,
        crossrefs=extract_crossrefs(node, captured),
        verification_scope="selected_segment_snapshots",
        anchors_checked=anchors_checked,
    )


def cross_verify(nodes: dict[str, NodeReport]) -> tuple[list[CrossRef], list[CrossRef]]:
    """Confirm every cross-reference is reciprocated by the named peer.

    Returns ``(unreciprocated, untrusted_peer)``. A reference is reciprocated
    when the peer's log holds a row with the same correlation that names this
    node back (opposite direction when both declare one). A reference into a
    node whose own chain is broken is ``untrusted_peer`` (its absence proves
    nothing — the log can't be trusted).
    """
    # Index each node's refs by (correlation) -> set of peers it names.
    by_node_corr: dict[str, dict[str, list[CrossRef]]] = {}
    for name, rep in nodes.items():
        per_corr: dict[str, list[CrossRef]] = {}
        for ref in rep.crossrefs:
            per_corr.setdefault(ref.correlation, []).append(ref)
        by_node_corr[name] = per_corr

    unreciprocated: list[CrossRef] = []
    untrusted: list[CrossRef] = []
    for name, rep in nodes.items():
        if not rep.intact:
            continue
        for ref in rep.crossrefs:
            peer_rep = nodes.get(ref.peer)
            if peer_rep is None or not peer_rep.intact:
                untrusted.append(ref)
                continue
            # The peer must hold a counterpart row: same correlation, naming us.
            counterparts = [
                r for r in by_node_corr.get(ref.peer, {}).get(ref.correlation, [])
                if r.peer == name
            ]
            if ref.direction in ("sent", "received"):
                counterparts = [r for r in counterparts
                                if r.direction in (_opposite(ref.direction), "unknown")]
            if not counterparts:
                unreciprocated.append(ref)
    return unreciprocated, untrusted


def _outcome_conflicts(nodes: dict[str, NodeReport]) -> list[CrossRef]:
    """Compare affirmative outcome claims; unknown is never a refusal.

    Retries can have multiple records. Each known claim needs a compatible
    counterpart, rather than comparing unrelated attempts pairwise. This is
    still a statement about recorded acceptance, not proof of task completion.
    """
    indexed: dict[tuple[str, str, str, str], set[tuple[bool, int | None]]] = {}
    for rep in nodes.values():
        if rep.intact:
            for ref in rep.crossrefs:
                if ref.accepted is not None:
                    key = (ref.node, ref.peer, ref.correlation, ref.direction)
                    indexed.setdefault(key, set()).add((ref.accepted, ref.remote_goal_id))
    conflicts = []
    for rep in nodes.values():
        if not rep.intact:
            continue
        for ref in rep.crossrefs:
            peer = nodes.get(ref.peer)
            if ref.accepted is None or peer is None or not peer.intact:
                continue
            counterparts = indexed.get(
                (ref.peer, ref.node, ref.correlation, _opposite(ref.direction)), set(),
            )
            if counterparts and not any(
                accepted == ref.accepted
                and (not ref.accepted or ref.remote_goal_id is None
                     or goal_id is None or goal_id == ref.remote_goal_id)
                for accepted, goal_id in counterparts
            ):
                conflicts.append(ref)
    return conflicts


def verify_federation(node_inputs: dict[str, tuple[Path, list[dict] | None]], *,
                      pubkey_hex: str | None = None) -> FederationReport:
    """Operator entry point: per-node integrity + cross-node reciprocity.

    ``node_inputs`` maps node name -> ``(audit_dir, rows)``. Returns a
    :class:`FederationReport`; ``consistent`` requires intact chains, reciprocal
    references and no conflicting or unresolved recorded delegation outcomes.
    A generic reference without delegation outcome fields proves reciprocity
    only. An accepted delegation is not evidence that its work completed.
    """
    report = FederationReport()
    for name, (audit_dir, rows) in node_inputs.items():
        report.nodes[name] = collect_node(name, audit_dir, rows, pubkey_hex=pubkey_hex)
    report.unreciprocated, report.untrusted_peer = cross_verify(report.nodes)
    report.outcome_conflicts = _outcome_conflicts(report.nodes)
    for node in report.nodes.values():
        if not node.intact:
            continue
        latest = {(ref.peer, ref.correlation, ref.direction): ref for ref in node.crossrefs}
        for ref in node.crossrefs:
            if not ref.outcome_issue:
                continue
            last = latest[(ref.peer, ref.correlation, ref.direction)]
            if (ref.outcome_issue in {"pending", "unknown"}
                    and last.accepted is not None and not last.outcome_issue
                    and (ref.remote_goal_id is None or ref.remote_goal_id == last.remote_goal_id)):
                continue  # An explicit later result resolves this node's uncertainty.
            report.unresolved_outcomes.append(ref)
    return report


def render(report: FederationReport) -> str:
    lines = [f"federation: {len(report.nodes)} node(s); "
             + ("CONSISTENT" if report.consistent else "INCONSISTENT")]
    for name, rep in sorted(report.nodes.items()):
        status = "intact" if rep.intact else f"BROKEN ({len(rep.breaks)} break(s))"
        lines.append(f"  {name}: {status}, {len(rep.crossrefs)} cross-ref(s), "
                     f"scope={rep.verification_scope}, anchors_checked={rep.anchors_checked}")
    for ref in report.unreciprocated:
        lines.append(f"  UNRECIPROCATED: {ref.node} -> {ref.peer} "
                     f"(corr={ref.correlation}, {ref.direction}) has no counterpart")
    for ref in report.untrusted_peer:
        lines.append(f"  UNTRUSTED PEER: {ref.node} -> {ref.peer} "
                     f"(corr={ref.correlation}) — peer chain is broken")
    for ref in report.outcome_conflicts:
        lines.append(f"  OUTCOME CONFLICT: {ref.node} -> {ref.peer} "
                     f"(corr={ref.correlation}) — acceptance or goal identity differs")
    for ref in report.unresolved_outcomes:
        lines.append(f"  UNRESOLVED OUTCOME: {ref.node} -> {ref.peer} "
                     f"(corr={ref.correlation}, {ref.outcome_issue})")
    lines.append("  Scope: use the per-node evidence scope and integrity result above. "
                 "Anchor checks are separate from the captured rows; no atomic "
                 "whole-history snapshot or independent checkpoint proof. "
                 "Acceptance records do not prove execution completed.")
    return "\n".join(lines)


__all__ = [
    "CrossRef", "NodeReport", "FederationReport", "extract_crossrefs",
    "collect_node", "cross_verify", "verify_federation", "render",
]

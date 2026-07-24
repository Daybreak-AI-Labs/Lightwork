"""Structured consequence previews for governed system-of-record writes.

:mod:`maverick.governed_rest` can already route a live Salesforce/ServiceNow
write through simulate -> approve -> commit -> lineage, but its
``preview_write`` returns a *sentence* ("would PATCH salesforce/... with fields
['Amount']"). Earned Autonomy (:mod:`maverick.earned_autonomy`) needs more than
a sentence to do its job: :class:`~maverick.earned_autonomy.ConsequencePreview`
wants the dollars at risk, the entities touched, and -- above all -- whether
the action is *reversible*, while
:func:`~maverick.earned_autonomy.shadow_execute` wants
:class:`~maverick.earned_autonomy.SagaStep` pairs carrying a real ``undo``.

This module is the adapter between the two. :func:`plan_write` turns one
connector write into a :class:`WritePlan`: a populated ``ConsequencePreview``
plus the saga steps that can execute and compensate it.

**Reversibility is earned here, never asserted.** The single rule this module
exists to enforce: an action is reversible only when we are *holding the thing
that inverts it* -- prior field values read back from the record, or a created
record's id captured from the create response. Anything else (a full-replace
PUT whose prior contains non-writable system fields, a DELETE that would come
back with a new id and dangling references, a record we could not read) is
reported ``reversible=False`` with ``undo=None``, which makes
:func:`~maverick.earned_autonomy.run_saga` refuse *before any effect* and
routes the action to a human. A failed restore-read demotes the plan; it never
degrades into an optimistic guess.

Two contract seams this module deliberately bridges:

* ``preview_write`` keeps its no-network guarantee (a REST write cannot be
  dry-run server-side, so the simulate path must not touch the API). Capturing
  a restore point *requires* reading prior state, so that read lives here, in
  the separate :func:`plan_write` seam, is GET-only, and is gated by
  ``[governed_connectors] restore_points``. ``plan_write`` still calls
  ``preview_write`` first, so op/path validation and the human sentence stay in
  exactly one place.
* :class:`RestConnector` reports upstream failure by *returning* a string that
  starts with ``ERROR:``; ``run_saga`` only compensates when a step *raises*.
  An unwrapped step would therefore book a failed write as a success and never
  roll back. :func:`_checked` converts one into the other -- see
  :class:`PlanError`.

Per-service knowledge (how a path names a record, which fields carry money,
where a create response hides the new id) lives in a :class:`RestDialect`;
:class:`SalesforceDialect` and :class:`ServiceNowDialect` ship, and an unknown
connector falls back to a conservative generic dialect that reads no record and
earns no reversibility.

Additive per kernel rule 1: nothing calls this unless an operator has enabled
governed connectors and Earned Autonomy.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlsplit

from .earned_autonomy import ConsequencePreview, SagaStep

log = logging.getLogger(__name__)

#: How :mod:`maverick.tools._rest_connector` signals failure: a returned string.
ERROR_PREFIX = "ERROR:"

#: Verbs that address one existing record and therefore admit a restore read.
_RECORD_OPS = ("patch", "put", "delete")


class PlanError(RuntimeError):
    """A planned do/undo step could not be carried out.

    Raised rather than returned so :func:`~maverick.earned_autonomy.run_saga`
    records the step as failed and compensates. The connector layer's
    ``ERROR:``-prefixed return value would otherwise be indistinguishable from
    a successful write.
    """


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------
def _loads(payload: str) -> Any:
    """Parse a connector response, tolerating the 4000-char truncation.

    ``_rest_execute`` truncates its JSON at 4000 characters, which can cut a
    document mid-token. A record we cannot parse is simply a record we do not
    have -- callers treat ``None`` as "no restore point", which fails closed.
    """
    if not isinstance(payload, str) or not payload.strip():
        return None
    try:
        return json.loads(payload)
    except ValueError:
        log.debug("connector response was not parseable JSON (truncated?)")
        return None


def _as_money(value: Any) -> float | None:
    """Coerce a field value to a dollar figure, or ``None`` if it isn't one.

    Handles the three shapes these APIs actually return: a JSON number, a
    string (ServiceNow currency fields arrive as ``"USD;1200.00"``, and both
    services stringify decimals), and a nested ``{"value": ...}`` object.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("value", "amount", "display_value"):
            if key in value:
                return _as_money(value[key])
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if ";" in text:  # ServiceNow currency: "<CODE>;<amount>"
        text = text.rsplit(";", 1)[-1]
    text = text.replace(",", "").replace("$", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Per-service dialects
# ---------------------------------------------------------------------------
def _path_only(path: str) -> str:
    """The path component of a URL-ish string, percent-decoded.

    ``/api/now/table/incident/abc123?sysparm_fields=x`` must not yield a record
    id of ``abc123?sysparm_fields=x``, and an encoded segment must match the
    same way its decoded form would.
    """
    return unquote(urlsplit(str(path or "")).path)


class RestDialect:
    """What one system of record needs for a structured, reversible preview.

    The generic base is deliberately incapable: it matches no record path, so
    it captures no restore point and earns no reversibility for an in-place
    update. An unrecognized connector degrades to "route this to a human"
    rather than guessing at another vendor's URL grammar.
    """

    name = "generic"

    #: Lower-cased field names whose values are dollar exposure.
    money_fields: frozenset[str] = frozenset()

    #: Matches a path addressing ONE existing record; group 1 = object/table,
    #: group 2 = record id. ``None`` means "this dialect can't tell".
    record_re: re.Pattern[str] | None = None

    #: Keys a create response may carry the new record's id under.
    id_keys: tuple[str, ...] = ("id", "Id", "sys_id")

    #: Lower-cased fields that can be READ but never written back: audit stamps,
    #: auto-numbers, formula and roll-up fields. Their prior values look like a
    #: perfectly good restore point and silently fail to apply, so a write that
    #: touches one earns no inverse at all.
    unrestorable: frozenset[str] = frozenset()

    #: Field carrying the optimistic-concurrency token. Captured with the prior
    #: values and re-checked before restoring, so an undo refuses rather than
    #: overwriting somebody else's edit. Empty = this dialect has no such token,
    #: and therefore cannot promise a non-clobbering restore.
    concurrency_field: str = ""

    #: True when PUT *merges* the supplied fields (like PATCH) rather than
    #: replacing the record. A merge has a field-level inverse; a replace does
    #: not.
    put_merges: bool = False

    def target(self, path: str) -> tuple[str, str] | None:
        """``(object, record_id)`` when ``path`` addresses one record."""
        if self.record_re is None:
            return None
        match = self.record_re.search(_path_only(path))
        return (match.group(1), match.group(2)) if match else None

    def restore_query(self, fields: list[str] | None) -> dict:
        """Query params for the capture GET.

        ``fields=None`` means "the whole record" (used to price a DELETE);
        otherwise the read is narrowed to exactly the fields being changed, so
        the restore can never replay -- and clobber -- a field the agent never
        touched.
        """
        return {}

    def entity(self, path: str) -> str:
        """A stable, human-readable name for what this write touches."""
        found = self.target(path)
        return f"{found[0]}/{found[1]}" if found else (path or "").strip()

    def unwrap(self, payload: Any) -> dict | None:
        """The record dict inside a GET response, or ``None``."""
        return payload if isinstance(payload, dict) else None

    def created_id(self, payload: Any) -> str | None:
        """The id of a record a POST just created, or ``None``."""
        record = self.unwrap(payload) or {}
        if not isinstance(record, dict):
            return None
        for key in self.id_keys:
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def is_money(self, field_name: str) -> bool:
        return str(field_name).strip().lower() in self.money_fields


class SalesforceDialect(RestDialect):
    """Salesforce REST: ``/services/data/vNN.N/sobjects/<Object>/<Id>``.

    Ids are the familiar 15- or 18-character case-sensitive keys. A create
    returns ``{"id": ..., "success": true}``; an update returns 204 with an
    empty body and no before-image, which is why the restore point must be
    captured *before* the write and the undo never depends on the update
    response.

    Restoring a previously-blank field requires sending explicit JSON ``null``
    -- REST has no SOAP ``fieldsToNull`` and omitting a key is a no-op, not a
    clear. Capturing prior values verbatim (including the ``None`` that a GET
    returns for an empty field) gives exactly that behavior for free.
    """

    name = "salesforce"
    record_re = re.compile(r"/sobjects/([A-Za-z0-9_]+)/([A-Za-z0-9]{15,18})/?$")
    money_fields = frozenset({
        "amount", "expectedrevenue", "totalprice", "unitprice", "listprice",
        "annualrevenue", "grandtotal", "subtotal", "totalamount", "netamount",
    })
    # Audit stamps have no Update property; ExpectedRevenue (Amount x
    # Probability) and TotalPrice (UnitPrice x Quantity) are formulas, and
    # TotalAmount / AmountAllOpportunities are roll-ups -- all recompute
    # themselves and swallow a restore silently.
    unrestorable = frozenset({
        "id", "isdeleted", "createdbyid", "createddate", "lastmodifiedbyid",
        "lastmodifieddate", "systemmodstamp", "lastactivitydate",
        "lastvieweddate", "lastreferenceddate", "expectedrevenue", "totalprice",
        "totalamount", "amountallopportunities", "amountwonopportunities",
    })
    concurrency_field = "LastModifiedDate"

    def restore_query(self, fields: list[str] | None) -> dict:
        if not fields:
            return {}
        wanted = list(dict.fromkeys([*fields, self.concurrency_field]))
        return {"fields": ",".join(wanted)}


class ServiceNowDialect(RestDialect):
    """ServiceNow Table API: ``/api/now/table/<table>/<sys_id>``.

    Every response wraps the payload in ``{"result": ...}`` -- a dict for a
    single record, a list for a query -- so :meth:`unwrap` peels it, and
    refuses to guess when a query returned anything other than one row.

    Two ServiceNow-specific facts shape the capture. PUT and PATCH behave
    IDENTICALLY here (both merge only the supplied fields; PUT does *not* null
    omitted ones), so a full-replace PUT is not a thing on this API and a
    captured-prior PATCH restores either verb. And the capture GET must ask for
    raw values: display values are not writable back (a choice field reads
    ``"New"`` instead of ``"1"``, a reference field reads a nested object), so
    a display-value capture would produce a restore point that cannot restore.
    """

    name = "servicenow"
    record_re = re.compile(
        r"/api/now/(?:v\d+/)?table/([A-Za-z0-9_]+)/([0-9a-fA-F]{32})/?$")
    money_fields = frozenset({
        "price", "cost", "amount", "list_price", "total_cost", "sale_price",
        "estimated_cost", "unit_price", "cost_per_unit", "purchase_price",
    })
    unrestorable = frozenset({
        "sys_id", "sys_created_on", "sys_created_by", "sys_updated_on",
        "sys_updated_by", "sys_mod_count", "number",
    })
    concurrency_field = "sys_mod_count"
    put_merges = True

    def restore_query(self, fields: list[str] | None) -> dict:
        query = {"sysparm_display_value": "false",
                 "sysparm_exclude_reference_link": "true"}
        if fields:
            wanted = list(dict.fromkeys([*fields, self.concurrency_field]))
            query["sysparm_fields"] = ",".join(wanted)
        return query

    def unwrap(self, payload: Any) -> dict | None:
        if isinstance(payload, dict) and "result" in payload:
            result = payload["result"]
            if isinstance(result, list):
                # A query that matched exactly one row is unambiguous; anything
                # else is not the record we are about to change.
                if len(result) == 1 and isinstance(result[0], dict):
                    return result[0]
                return None
            return result if isinstance(result, dict) else None
        return payload if isinstance(payload, dict) else None


#: Reference dialects, keyed by the connector name used in ``governed_rest``.
DIALECTS: dict[str, RestDialect] = {
    "salesforce": SalesforceDialect(),
    "servicenow": ServiceNowDialect(),
}


def dialect_for(name: str) -> RestDialect:
    """The dialect for a connector name; a conservative generic if unknown."""
    return DIALECTS.get(str(name or "").strip().lower(), RestDialect())


# ---------------------------------------------------------------------------
# Plan shapes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RestorePoint:
    """Evidence that a write can be undone -- or the reason it cannot.

    ``kind`` is the mechanism:

    ``"fields"``
        We read the record and are holding its prior values for exactly the
        fields about to change. ``captured`` is ``True``; the undo writes
        ``prior`` back.
    ``"created"``
        Nothing existed before, so the inverse is deleting whatever the create
        returns. ``captured`` is ``False`` because the id does not exist until
        the do-step runs -- the undo reads it from the response and *raises* if
        it isn't there.
    ``"none"``
        No inverse. ``detail`` says why, and the plan is not reversible.
    """

    kind: str = "none"
    entity: str = ""
    prior: dict = field(default_factory=dict)
    captured: bool = False
    detail: str = ""
    #: Value of the dialect's optimistic-concurrency token when ``prior`` was
    #: read. The undo re-checks it and refuses if the record moved -- restoring
    #: over somebody else's edit is not an undo, it is a second incident.
    token: str = ""


@dataclass(frozen=True)
class WritePlan:
    """A governed write, ready to preview to a human and to execute as a saga.

    ``preview`` is the consequence card's content; ``steps`` feed
    :func:`~maverick.earned_autonomy.run_saga` or
    :func:`~maverick.earned_autonomy.shadow_execute`; ``warnings`` explain
    every reason confidence or reversibility was reduced, so an approver sees
    the gaps rather than a bare verdict.
    """

    preview: ConsequencePreview
    steps: tuple[SagaStep, ...] = ()
    restore: RestorePoint = field(default_factory=RestorePoint)
    warnings: tuple[str, ...] = ()

    @property
    def reversible(self) -> bool:
        return self.preview.reversible


# ---------------------------------------------------------------------------
# Step plumbing
# ---------------------------------------------------------------------------
def _checked(call: Callable[[], str], what: str) -> Callable[[], str]:
    """Wrap a connector call so an ``ERROR:``-prefixed *return* raises.

    This is the load-bearing adapter between the two failure conventions: the
    connector returns error strings, the saga compensates only on exceptions.
    Without it a rejected write is recorded as a committed one.
    """

    def _run() -> str:
        out = call()
        if isinstance(out, str) and out.strip().startswith(ERROR_PREFIX):
            raise PlanError(f"{what} failed: {out.strip()[:200]}")
        return out

    return _run


def restore_points_enabled() -> bool:
    """Whether :func:`plan_write` may read prior state to earn an undo.

    On by default *when governed connectors are in use at all*: without the
    read, every in-place update is irreversible, every action routes to a
    human, and the autonomy dial can never earn anything. An operator whose
    service account is write-only turns it off and accepts that trade.
    """
    try:
        from .config import get_governed_connectors

        return bool(get_governed_connectors().get("restore_points", True))
    except Exception:  # pragma: no cover -- config unreadable: no risky read
        log.debug("governed-connector config unreadable; restore reads off")
        return False


def _read_record(conn: Any, dialect: RestDialect, path: str,
                 query: dict | None = None) -> tuple[dict | None, str]:
    """GET the record at ``path``. Returns ``(record, warning)``.

    ``query`` is the dialect's :meth:`~RestDialect.restore_query` -- it narrows
    the read to the fields being changed and, on services that have one, forces
    the raw-value representation that can actually be written back.

    Read-only and failure-tolerant: any error yields ``(None, why)``, which
    the caller turns into "not reversible", never into an optimistic default.
    """
    try:
        raw = conn.read({"path": path, "params": dict(query or {})})
    except Exception as exc:  # noqa: BLE001 -- a failed read must not raise
        return None, f"restore read raised {type(exc).__name__}"
    if isinstance(raw, str) and raw.strip().startswith(ERROR_PREFIX):
        return None, f"restore read failed: {raw.strip()[:120]}"
    record = dialect.unwrap(_loads(raw))
    if not isinstance(record, dict) or not record:
        return None, "restore read returned no single record"
    return record, ""


def _token_of(record: dict | None, dialect: RestDialect) -> str:
    """The record's optimistic-concurrency token, or ``""`` if it has none.

    A JSON ``null`` must read as absent, not as the string ``"None"`` -- an
    absent token has to fail the emptiness check that refuses the undo, and a
    truthy stand-in would sail straight past it.
    """
    if not isinstance(record, dict) or not dialect.concurrency_field:
        return ""
    raw = record.get(dialect.concurrency_field)
    return "" if raw is None else str(raw).strip()


# ---------------------------------------------------------------------------
# Inverse construction, per verb
# ---------------------------------------------------------------------------
def _inverse_for_update(conn: Any, dialect: RestDialect, path: str, body: dict,
                        record: dict | None, query: dict | None = None,
                        after: list[dict] | None = None,
                        ) -> tuple[RestorePoint, Callable[[], str] | None, list[str]]:
    """A merging write: restore the prior value of exactly the changed fields.

    Three ways this refuses, all of them before any effect:

    * we hold no prior state at all;
    * a changed field is system-managed (:attr:`RestDialect.unrestorable`) --
      its prior value reads back perfectly and then silently fails to apply, so
      a restore built on it would report success while changing nothing;
    * the record was read without the dialect's optimistic-concurrency token,
      leaving no way to tell a genuine undo from an overwrite of somebody
      else's edit.

    ``after`` is the post-write snapshot the do-step records. The concurrency
    check compares against the token as of OUR OWN write, not the plan-time
    one: our write bumps the token itself, so comparing to the plan-time value
    would flag every undo as somebody else's edit and never restore anything.
    """
    entity = dialect.entity(path)
    if record is None:
        return (RestorePoint("none", entity,
                             detail="no prior state captured"),
                None, [])
    blocked = sorted(k for k in body if str(k).strip().lower() in dialect.unrestorable)
    if blocked:
        return (RestorePoint(
            "none", entity,
            detail=f"{blocked} cannot be written back"), None,
            [f"fields {blocked} are system-managed on {dialect.name} (audit "
             "stamps, auto-numbers, formulas or roll-ups); their prior values "
             "cannot be written back, so no inverse exists"])
    missing = sorted(k for k in body if k not in record)
    if missing:
        # A partial restore is not an inverse. Field-level read permission or a
        # typo'd field would leave those values changed forever.
        return (RestorePoint(
            "none", entity,
            detail=f"prior value unreadable for {missing}"), None,
            [f"fields {missing} absent from the record read; "
             "no complete inverse exists"])
    token = ""
    if dialect.concurrency_field:
        token = _token_of(record, dialect)
        if not token:
            return (RestorePoint(
                "none", entity,
                detail=f"no {dialect.concurrency_field} to detect a "
                       "concurrent edit"), None,
                [f"the capture read returned no {dialect.concurrency_field}; "
                 "a restore could not tell an undo from overwriting somebody "
                 "else's edit"])
    prior = {k: record[k] for k in body}

    def _undo() -> str:
        if token:
            expected = _token_of(after[-1] if after else None, dialect)
            if not expected:
                raise PlanError(
                    f"the write's own effect on {entity} was never observed; a "
                    "restore could not tell an undo from overwriting somebody "
                    "else's edit")
            current, why = _read_record(conn, dialect, path, query)
            if current is None:
                raise PlanError(f"cannot confirm {entity} is unchanged: {why}")
            now = _token_of(current, dialect)
            if now != expected:
                raise PlanError(
                    f"{entity} changed since the write "
                    f"({dialect.concurrency_field} {expected!r} -> {now!r}); "
                    "restoring would overwrite that edit")
        return _checked(
            lambda: conn.write({"op": "patch", "path": path, "body": prior}),
            f"restore {entity}")()

    return (RestorePoint("fields", entity, prior=dict(prior), captured=True,
                         token=token,
                         detail=f"prior values held for {sorted(prior)}"),
            _undo, [])


def _inverse_for_create(conn: Any, dialect: RestDialect, path: str,
                        responses: list[str],
                        ) -> tuple[RestorePoint, Callable[[], str]]:
    """POST: the inverse is deleting the record the create returns.

    The id only exists after the do-step, so the undo closes over the same
    response list the do-step appends to, and raises if the id never shows up
    -- an undo that quietly does nothing is worse than one that fails loudly.
    """

    def _undo() -> str:
        if not responses:
            raise PlanError("create never reported a response; nothing to undo")
        new_id = dialect.created_id(_loads(responses[-1]))
        if not new_id:
            raise PlanError(
                "create response carried no record id; the new record must be "
                "removed by hand")
        target = f"{path.rstrip('/')}/{new_id}"
        return _checked(
            lambda: conn.write({"op": "delete", "path": target, "body": {}}),
            f"delete created {new_id}")()

    return (RestorePoint(
        "created", dialect.entity(path),
        detail="inverse is deleting the id returned by the create"), _undo)


def _plan_inverse(conn: Any, dialect: RestDialect, op: str, path: str,
                  body: dict, record: dict | None, responses: list[str],
                  query: dict | None = None, after: list[dict] | None = None,
                  ) -> tuple[RestorePoint, Callable[[], str] | None, list[str]]:
    """Dispatch to the verb's inverse. Everything unhandled fails closed."""
    entity = dialect.entity(path)
    if op == "post":
        point, undo = _inverse_for_create(conn, dialect, path, responses)
        return point, undo, []
    if op == "patch":
        return _inverse_for_update(conn, dialect, path, body, record, query, after)
    if op == "put":
        if dialect.put_merges:
            # On this service PUT and PATCH are the same operation: only the
            # supplied fields change, omitted ones are left alone. A captured
            # per-field prior therefore inverts it exactly.
            return _inverse_for_update(conn, dialect, path, body, record, query,
                                       after)
        # Elsewhere a full replace's only inverse is replaying the whole prior
        # record, which carries system-managed fields (Id, CreatedDate) that
        # the API refuses to write. Honestly irreversible.
        return (RestorePoint(
            "none", entity,
            detail="full-replace PUT has no writable inverse"), None,
            ["PUT replaces the whole record; its prior state contains "
             "non-writable system fields, so it cannot be restored"])
    # DELETE: recreating the record mints a NEW id, orphaning every reference
    # to the old one. That is a different record, not a restoration.
    return (RestorePoint(
        "none", entity, detail="a deleted record cannot be restored in place"),
        None,
        ["DELETE is irreversible: recreating the record yields a new id and "
         "leaves existing references dangling"])


# ---------------------------------------------------------------------------
# Exposure + prediction
# ---------------------------------------------------------------------------
def _money_in(dialect: RestDialect, source: dict | None) -> dict[str, float]:
    """The money-bearing fields of a payload, coerced to floats."""
    if not isinstance(source, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in source.items():
        if not dialect.is_money(key):
            continue
        amount = _as_money(value)
        if amount is not None:
            out[str(key)] = amount
    return out


def _exposure(dialect: RestDialect, op: str, body: dict, record: dict | None,
              ) -> float | None:
    """Dollars this write puts at risk, or ``None`` when it moves no money.

    A DELETE risks everything on the record. An update risks only the *change*
    -- moving an opportunity from $100k to $120k exposes $20k, not $120k --
    which is computable precisely because a restore point was captured. Absent
    a prior read, the whole written figure is reported: overstating exposure
    keeps a human in the loop, understating it would not.
    """
    if op == "delete":
        doomed = _money_in(dialect, record)
        return round(sum(abs(v) for v in doomed.values()), 2) if doomed else None
    written = _money_in(dialect, body)
    if not written:
        return None
    total = 0.0
    for key, new_value in written.items():
        old_value = _as_money(record.get(key)) if isinstance(record, dict) else None
        total += abs(new_value - old_value) if old_value is not None else abs(new_value)
    return round(total, 2)


def _predicted(op: str, reversible: bool, warnings: tuple[str, ...]) -> float:
    """A structural prior that this write lands as described.

    Explicitly a prior, not a claim. Earned Autonomy exists to score this
    number against the real outcome and demote whatever miscalibrates, so it is
    derived from things we actually observed -- the verb, whether an inverse
    was earned, how many gaps the plan hit -- and stays auditable. Callers with
    a real model pass ``predicted=`` to :func:`plan_write` and override it.
    """
    base = {"patch": 0.90, "post": 0.85, "delete": 0.85, "put": 0.80}.get(op, 0.75)
    if not reversible:
        base -= 0.05
    base -= 0.05 * len(warnings)
    return max(0.05, min(0.99, round(base, 2)))


def params_digest(params: dict) -> str:
    """SHA-256 over the write's params.

    Cards commit to *which* parameters were previewed without storing them, so
    a card can be tied to the request that produced it without copying customer
    data into the audit trail.
    """
    try:
        canonical = json.dumps(params, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover -- exotic params
        canonical = repr(sorted(map(str, params)))
    return hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------
def _refused(action: str, effect: str, params: dict) -> WritePlan:
    """A plan for a write that never validated: no steps, nothing reversible."""
    return WritePlan(
        preview=ConsequencePreview(
            action=action, predicted_outcome=0.0, effect=effect, risk="high",
            reversible=False, params_sha256=params_digest(params)),
        restore=RestorePoint("none", detail="the write did not validate"),
        warnings=(effect,))


def plan_write(conn: Any, params: dict, *, predicted: float | None = None,
               capture: bool | None = None) -> WritePlan:
    """Plan one governed connector write as a structured, compensable action.

    Unlike ``conn.preview_write`` (which this calls first, and which still
    performs no network I/O), planning MAY issue one read-only GET to capture
    the prior state that earns an undo -- gated by
    ``[governed_connectors] restore_points`` and by ``capture=``.

    Returns a :class:`WritePlan` whose ``preview`` is ready to pin as a
    consequence card and whose ``steps`` are ready for
    :func:`~maverick.earned_autonomy.shadow_execute`. When no inverse could be
    earned the single step carries ``undo=None``, so the saga refuses before
    any effect and the action routes to a human. Never raises: a connector that
    misbehaves yields a refusing plan.
    """
    name = str(getattr(conn, "name", "connector"))
    action = f"{name}.write"
    try:
        effect = conn.preview_write(params)
    except Exception as exc:  # noqa: BLE001 -- planning must not break the loop
        return _refused(action, f"{ERROR_PREFIX} preview raised "
                                f"{type(exc).__name__}", params)
    if not isinstance(effect, str) or effect.strip().startswith(ERROR_PREFIX):
        return _refused(action, str(effect), params)

    op = str(params.get("op", "")).strip().lower()
    path = str(params.get("path", "")).strip()
    raw_body = params.get("body")
    body = raw_body if isinstance(raw_body, dict) else {}
    dialect = dialect_for(name)

    warnings: list[str] = []
    record: dict | None = None
    # A DELETE has no inverse to build, but its whole record must be read to
    # price what is about to be destroyed. Every other verb reads exactly the
    # fields it changes, so a restore can never replay a field nobody touched.
    query = dialect.restore_query(None if op == "delete" else sorted(body))
    may_capture = restore_points_enabled() if capture is None else bool(capture)
    if op in _RECORD_OPS:
        if dialect.target(path) is None:
            warnings.append(
                f"{path!r} does not address a single {dialect.name} record; "
                "prior state cannot be read")
        elif not may_capture:
            warnings.append(
                "restore reads are disabled ([governed_connectors] "
                "restore_points); no inverse can be earned")
        else:
            record, why = _read_record(conn, dialect, path, query)
            if why:
                warnings.append(why)

    responses: list[str] = []
    after: list[dict] = []

    restore, undo, more = _plan_inverse(
        conn, dialect, op, path, body, record, responses, query, after)
    warnings.extend(more)
    frozen_warnings = tuple(warnings)
    reversible = undo is not None

    # Only a token-checking undo reads ``after``; anywhere else the snapshot
    # would be a read nobody looks at.
    watch = bool(restore.kind == "fields" and restore.token)

    def _do() -> str:
        out = conn.write(params)
        if isinstance(out, str) and out.strip().startswith(ERROR_PREFIX):
            raise PlanError(f"{op.upper()} {path} failed: {out.strip()[:200]}")
        responses.append(out if isinstance(out, str) else str(out))
        if watch:
            # Prefer the write's own echo of the updated record (ServiceNow
            # returns it) and fall back to one GET where the write answers with
            # a bare 204 (Salesforce PATCH). Either way the undo ends up holding
            # the token as of OUR write, which is the only value a later read
            # can be honestly compared against.
            snap = dialect.unwrap(_loads(responses[-1]))
            if not _token_of(snap, dialect):
                snap, _why = _read_record(conn, dialect, path, query)
            after.append(snap if isinstance(snap, dict) else {})
        return responses[-1]

    entity = dialect.entity(path)
    preview = ConsequencePreview(
        action=action,
        predicted_outcome=(_predicted(op, reversible, frozen_warnings)
                           if predicted is None else max(0.0, min(1.0, float(predicted)))),
        effect=effect,
        exposure_dollars=_exposure(dialect, op, body, record),
        entities=(entity,) if entity else (),
        reversible=reversible,
        params_sha256=params_digest(params),
        risk="high",
    )
    step = SagaStep(name=f"{name}.{op}", do=_do, undo=undo)
    return WritePlan(preview=preview, steps=(step,), restore=restore,
                     warnings=frozen_warnings)


__all__ = [
    "DIALECTS",
    "ERROR_PREFIX",
    "PlanError",
    "RestDialect",
    "RestorePoint",
    "SalesforceDialect",
    "ServiceNowDialect",
    "WritePlan",
    "dialect_for",
    "params_digest",
    "plan_write",
    "restore_points_enabled",
]

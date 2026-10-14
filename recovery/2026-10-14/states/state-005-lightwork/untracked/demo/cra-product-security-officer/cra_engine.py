"""Self-contained engine for the unsigned local CRA Officer SKU.

The engine intentionally imports nothing from ``maverick``.  It calculates
deadlines and prepares local drafts, but it never makes legal decisions or
performs regulator filings.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAX_SBOM_BYTES = 512 * 1024
MAX_SBOM_DEPTH = 12
MAX_SBOM_NODES = 50_000
MAX_COMPONENTS = 2_048
MAX_DEPENDENCIES = 8_192
MAX_LOCAL_STORE_BYTES = 8 * 1024 * 1024
_CASE_TYPES = frozenset({"actively_exploited_vulnerability", "severe_incident"})
_REPORT_STAGES = frozenset({"early_warning", "full_notification", "final_report"})
_VEX_STATUSES = frozenset({"affected", "fixed", "not_affected", "under_investigation"})
_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|access[_-]?token|authorization|credential|api[_-]?key|private[_-]?key|cookie|secret)",
    re.IGNORECASE,
)

VENDORED_CLOCK_RULES = (
    {"case_type": "actively_exploited_vulnerability", "stage": "early_warning", "anchor": "awareness", "amount": 24, "unit": "hours", "article": "14(2)(a)"},
    {"case_type": "actively_exploited_vulnerability", "stage": "full_notification", "anchor": "awareness", "amount": 72, "unit": "hours", "article": "14(2)(b)"},
    {"case_type": "actively_exploited_vulnerability", "stage": "final_report", "anchor": "corrective_measure_available", "amount": 14, "unit": "calendar_days", "article": "14(2)(c)"},
    {"case_type": "severe_incident", "stage": "early_warning", "anchor": "awareness", "amount": 24, "unit": "hours", "article": "14(4)(a)"},
    {"case_type": "severe_incident", "stage": "full_notification", "anchor": "awareness", "amount": 72, "unit": "hours", "article": "14(4)(b)"},
    {"case_type": "severe_incident", "stage": "final_report", "anchor": "full_notification_submission", "amount": 1, "unit": "calendar_months", "article": "14(4)(c)"},
)


class RevisionConflict(ValueError):
    """The caller's compare-and-swap revision is stale."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _required(value: object, label: str, limit: int = 300) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    text = value.strip()
    if not text or len(text) > limit:
        raise ValueError(f"{label} must contain 1 to {limit} characters")
    return text


def _optional(value: object, label: str, limit: int = 2_000) -> str:
    if value in (None, ""):
        return ""
    return _required(value, label, limit)


def _parse_timestamp(value: object, label: str) -> datetime:
    text = _required(value, label, 80)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return _iso(datetime.now(timezone.utc))


def _calendar_month(value: datetime) -> datetime:
    month_index = value.month
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _checked_revision(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("expected_revision must be a non-negative integer")
    return value


@contextmanager
def _cross_process_lock(path: Path):
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if lock_path.is_symlink():
        raise ValueError("local store lock must not be a symbolic link")
    try:
        os.chmod(lock_path.parent, 0o700)
    except OSError:
        pass
    with lock_path.open("a+b") as handle:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        if os.name == "nt":  # pragma: no cover - Windows CI exercises this path
            import msvcrt

            while True:
                try:
                    if lock_path.stat().st_size == 0:
                        handle.seek(0)
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - POSIX CI exercises this path
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class LocalStore:
    """Atomic, private local JSON store with global revision CAS."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(
            path
            or os.environ.get(
                "CRA_PSO_STORE", ".cra-product-security-officer/records.json"
            )
        )
        self._lock = threading.RLock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {"revision": 0, "records": []}
        if self.path.is_symlink():
            raise ValueError("local store must not be a symbolic link")
        if self.path.stat().st_size > MAX_LOCAL_STORE_BYTES:
            raise ValueError("local store exceeds the supported size")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("revision"), int)
            or isinstance(value.get("revision"), bool)
            or not isinstance(value.get("records"), list)
        ):
            raise ValueError("local store is malformed")
        return value

    def _write(self, state: dict) -> None:
        try:
            payload = json.dumps(
                state,
                allow_nan=False,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("local store contains unsupported JSON values") from exc
        if len(payload) > MAX_LOCAL_STORE_BYTES:
            raise ValueError("local store write would exceed the supported 8 MiB size")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        with tempfile.NamedTemporaryFile(
            "wb", dir=self.path.parent, delete=False
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def current_revision(self) -> int:
        return self._read()["revision"]

    def list_records(self, kind: str | None = None) -> list[dict]:
        rows = self._read()["records"]
        return [dict(row) for row in rows if kind is None or row.get("type") == kind]

    def get_record(self, record_id: str) -> dict | None:
        for row in reversed(self.list_records()):
            if row.get("id") == record_id:
                return row
        return None

    def save(self, record: dict, expected_revision: int) -> dict:
        expected = _checked_revision(expected_revision)
        with self._lock, _cross_process_lock(self.path):
            state = self._read()
            if state["revision"] != expected:
                raise RevisionConflict(
                    f"revision changed: expected {expected}, found {state['revision']}"
                )
            row = dict(record)
            row.setdefault("id", uuid.uuid4().hex)
            row.setdefault("created_at", _now_iso())
            state["revision"] += 1
            row["revision"] = state["revision"]
            state["records"].append(row)
            self._write(state)
            return row

    def save_with_replacement(
        self,
        record: dict,
        replacement_id: str,
        replacement: dict,
        expected_revision: int,
    ) -> dict:
        """Atomically append one record and replace one related record."""
        expected = _checked_revision(expected_revision)
        with self._lock, _cross_process_lock(self.path):
            state = self._read()
            if state["revision"] != expected:
                raise RevisionConflict(
                    f"revision changed: expected {expected}, found {state['revision']}"
                )
            index = next(
                (
                    index
                    for index in range(len(state["records"]) - 1, -1, -1)
                    if state["records"][index].get("id") == replacement_id
                ),
                None,
            )
            if index is None:
                raise ValueError("stored replacement record not found")
            previous = state["records"][index]
            revision = state["revision"] + 1

            appended = dict(record)
            appended.setdefault("id", uuid.uuid4().hex)
            appended.setdefault("created_at", _now_iso())
            appended["revision"] = revision

            replaced = dict(replacement)
            replaced["id"] = replacement_id
            replaced.setdefault("created_at", previous.get("created_at", _now_iso()))
            replaced["revision"] = revision

            state["revision"] = revision
            state["records"][index] = replaced
            state["records"].append(appended)
            self._write(state)
            return appended

    def replace(self, record_id: str, record: dict, expected_revision: int) -> dict:
        expected = _checked_revision(expected_revision)
        with self._lock, _cross_process_lock(self.path):
            state = self._read()
            if state["revision"] != expected:
                raise RevisionConflict(
                    f"revision changed: expected {expected}, found {state['revision']}"
                )
            index = next(
                (
                    index
                    for index in range(len(state["records"]) - 1, -1, -1)
                    if state["records"][index].get("id") == record_id
                ),
                None,
            )
            if index is None:
                raise ValueError("stored record not found")
            previous = state["records"][index]
            row = dict(record)
            row["id"] = record_id
            row.setdefault("created_at", previous.get("created_at", _now_iso()))
            state["revision"] += 1
            row["revision"] = state["revision"]
            state["records"][index] = row
            self._write(state)
            return row


def _validate_tree(value: object, depth: int = 0, counter: list[int] | None = None) -> None:
    if depth > MAX_SBOM_DEPTH:
        raise ValueError(f"SBOM nesting exceeds {MAX_SBOM_DEPTH} levels")
    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > MAX_SBOM_NODES:
        raise ValueError("SBOM contains too many values")
    if isinstance(value, dict):
        if len(value) > 4_096:
            raise ValueError("SBOM object contains too many keys")
        for key, item in value.items():
            if not isinstance(key, str) or len(key.encode("utf-8")) > 512:
                raise ValueError("SBOM keys must be bounded strings")
            _validate_tree(item, depth + 1, counter)
    elif isinstance(value, list):
        if len(value) > MAX_DEPENDENCIES:
            raise ValueError("SBOM array contains too many items")
        for item in value:
            _validate_tree(item, depth + 1, counter)
    elif isinstance(value, str) and len(value.encode("utf-8")) > 64 * 1024:
        raise ValueError("SBOM strings may not exceed 64 KiB")


def _short(value: object, label: str, limit: int = 500) -> str:
    if value in (None, ""):
        return ""
    return _required(value, label, limit)


def _cdx_licenses(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 32:
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("expression"), str):
            label = _short(item["expression"], "license expression", 500)
        else:
            license_item = item.get("license")
            if not isinstance(license_item, dict):
                continue
            label = _short(
                license_item.get("id") or license_item.get("name"), "license", 300
            )
        if label and label not in result:
            result.append(label)
    return result


def _normalize_cyclonedx(document: dict) -> dict:
    version = document.get("specVersion")
    if version not in {"1.5", "1.6"}:
        raise ValueError("CycloneDX specVersion must be 1.5 or 1.6")
    components = document.get("components", [])
    if not isinstance(components, list) or len(components) > MAX_COMPONENTS:
        raise ValueError(f"components must contain at most {MAX_COMPONENTS} items")
    normalized: dict[str, dict] = {}
    for item in components:
        if not isinstance(item, dict):
            raise ValueError("each CycloneDX component must be an object")
        name = _required(item.get("name"), "component name", 500)
        version_text = _short(item.get("version"), "component version", 300)
        purl = _short(item.get("purl"), "component purl", 1_000)
        cpe = _short(item.get("cpe"), "component cpe", 1_000)
        component_type = _short(item.get("type"), "component type", 80)
        ref = _short(item.get("bom-ref"), "component bom-ref", 1_000)
        if not ref:
            ref = f"generated:{_digest([name, version_text, purl, cpe])[:24]}"
        row = {
            "ref": ref,
            "name": name,
            "version": version_text,
            "purl": purl,
            "cpe": cpe,
            "component_type": component_type,
            "licenses": _cdx_licenses(item.get("licenses", [])),
        }
        if ref in normalized and normalized[ref] != row:
            raise ValueError(f"conflicting duplicate component reference: {ref}")
        normalized[ref] = row
    dependencies = document.get("dependencies", [])
    if not isinstance(dependencies, list) or len(dependencies) > MAX_DEPENDENCIES:
        raise ValueError(f"dependencies must contain at most {MAX_DEPENDENCIES} items")
    edges: set[tuple[str, str]] = set()
    for relation in dependencies:
        if not isinstance(relation, dict):
            raise ValueError("each CycloneDX dependency must be an object")
        source = _required(relation.get("ref"), "dependency ref", 1_000)
        targets = relation.get("dependsOn", [])
        if source not in normalized:
            raise ValueError(f"dependency references unknown component: {source}")
        if not isinstance(targets, list) or len(targets) > MAX_COMPONENTS:
            raise ValueError("dependsOn must be a bounded list")
        for target in targets:
            target_ref = _required(target, "dependency target", 1_000)
            if target_ref not in normalized:
                raise ValueError(f"dependency references unknown component: {target_ref}")
            edges.add((source, target_ref))
    return {
        "format": "cyclonedx",
        "spec_version": version,
        "serial_number": _short(document.get("serialNumber"), "serial number", 500),
        "components": [normalized[key] for key in sorted(normalized)],
        "dependencies": [list(edge) for edge in sorted(edges)],
    }


def _spdx_purl_cpe(item: dict) -> tuple[str, str]:
    references = item.get("externalRefs", [])
    if not isinstance(references, list) or len(references) > 64:
        return "", ""
    purl = ""
    cpe = ""
    for reference in references:
        if not isinstance(reference, dict):
            continue
        kind = str(reference.get("referenceType", "")).casefold()
        locator = _short(reference.get("referenceLocator"), "external reference", 1_000)
        if "purl" in kind and not purl:
            purl = locator
        if "cpe" in kind and not cpe:
            cpe = locator
    return purl, cpe


def _normalize_spdx(document: dict) -> dict:
    if document.get("spdxVersion") != "SPDX-2.3":
        raise ValueError("spdxVersion must be SPDX-2.3")
    packages = document.get("packages", [])
    if not isinstance(packages, list) or len(packages) > MAX_COMPONENTS:
        raise ValueError(f"packages must contain at most {MAX_COMPONENTS} items")
    normalized: dict[str, dict] = {}
    for item in packages:
        if not isinstance(item, dict):
            raise ValueError("each SPDX package must be an object")
        ref = _required(item.get("SPDXID"), "package SPDXID", 1_000)
        purl, cpe = _spdx_purl_cpe(item)
        row = {
            "ref": ref,
            "name": _required(item.get("name"), "package name", 500),
            "version": _short(item.get("versionInfo"), "package version", 300),
            "purl": purl,
            "cpe": cpe,
            "component_type": "package",
            "licenses": [
                value
                for value in (
                    _short(item.get("licenseConcluded"), "license concluded", 500),
                    _short(item.get("licenseDeclared"), "license declared", 500),
                )
                if value and value != "NOASSERTION"
            ],
        }
        if ref in normalized and normalized[ref] != row:
            raise ValueError(f"conflicting duplicate package reference: {ref}")
        normalized[ref] = row
    relationships = document.get("relationships", [])
    if not isinstance(relationships, list) or len(relationships) > MAX_DEPENDENCIES:
        raise ValueError(f"relationships must contain at most {MAX_DEPENDENCIES} items")
    edges: set[tuple[str, str]] = set()
    for relation in relationships:
        if not isinstance(relation, dict):
            raise ValueError("each SPDX relationship must be an object")
        if relation.get("relationshipType") != "DEPENDS_ON":
            continue
        source = _required(relation.get("spdxElementId"), "relationship source", 1_000)
        target = _required(relation.get("relatedSpdxElement"), "relationship target", 1_000)
        if source not in normalized or target not in normalized:
            raise ValueError("DEPENDS_ON relationship references an unknown package")
        edges.add((source, target))
    return {
        "format": "spdx",
        "spec_version": "2.3",
        "document_namespace": _short(
            document.get("documentNamespace"), "document namespace", 1_000
        ),
        "components": [normalized[key] for key in sorted(normalized)],
        "dependencies": [list(edge) for edge in sorted(edges)],
    }


def normalize_sbom(document: object) -> dict:
    """Return a safe normalized graph; never return or persist the raw input."""
    if not isinstance(document, dict):
        raise ValueError("SBOM must be a JSON object")
    encoded = _canonical(document)
    if len(encoded) > MAX_SBOM_BYTES:
        raise ValueError(f"SBOM exceeds {MAX_SBOM_BYTES} bytes")
    _validate_tree(document)
    if document.get("bomFormat") == "CycloneDX":
        normalized = _normalize_cyclonedx(document)
    elif str(document.get("spdxVersion", "")).startswith("SPDX-"):
        normalized = _normalize_spdx(document)
    else:
        raise ValueError("SBOM must be CycloneDX JSON or SPDX 2.3 JSON")
    normalized["source_document_sha256"] = hashlib.sha256(encoded).hexdigest()
    normalized["normalized_sha256"] = _digest(normalized)
    normalized["component_count"] = len(normalized["components"])
    normalized["dependency_count"] = len(normalized["dependencies"])
    normalized["raw_document_persisted"] = False
    normalized["resolution_claim"] = "declared_components_only_not_resolved_transitive"
    return normalized


def create_product(name: object, manufacturer: object, support_end_date: object) -> dict:
    support_end = _required(support_end_date, "support_end_date", 10)
    try:
        datetime.strptime(support_end, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("support_end_date must use YYYY-MM-DD") from exc
    return {
        "type": "product",
        "name": _required(name, "name", 300),
        "manufacturer": _required(manufacturer, "manufacturer", 300),
        "support_end_date": support_end,
        "cra_applicability": {"status": "pending_human_decision"},
        "authority": "unsigned_local",
    }


def decide_product_applicability(
    product: dict, decision: object, reviewer: object, rationale: object
) -> dict:
    if product.get("type") != "product":
        raise ValueError("record is not a product")
    if product.get("cra_applicability", {}).get("status") != "pending_human_decision":
        raise ValueError("product applicability has already been decided")
    if decision not in {"applicable", "not_applicable"}:
        raise ValueError("decision must be applicable or not_applicable")
    row = dict(product)
    row["cra_applicability"] = {
        "status": decision,
        "reviewer": _required(reviewer, "reviewer", 160),
        "rationale": _required(rationale, "rationale", 2_000),
        "decided_at": _now_iso(),
        "human_decision": True,
    }
    return row


def create_release(
    product: dict,
    version: object,
    released_at: object,
    sbom_document: object,
) -> dict:
    if product.get("type") != "product" or not product.get("id"):
        raise ValueError("a stored product is required")
    release_time = _iso(_parse_timestamp(released_at, "released_at"))
    sbom = normalize_sbom(sbom_document)
    binding = {
        "product_id": product["id"],
        "version": _required(version, "version", 160),
        "released_at": release_time,
        "sbom_sha256": sbom["normalized_sha256"],
    }
    return {
        "type": "release",
        **binding,
        "release_binding_sha256": _digest(binding),
        "sbom": sbom,
        "authority": "unsigned_local",
    }


def _validated_full_receipt_submission(case: dict, receipt: dict) -> datetime:
    """Return a severe-case anchor only for a chronology-bound local receipt."""
    packet_digest = receipt.get("packet_sha256") if isinstance(receipt, dict) else None
    approved_digest = (
        receipt.get("approved_packet_sha256")
        if isinstance(receipt, dict)
        else None
    )
    if (
        not isinstance(receipt, dict)
        or receipt.get("type") != "submission_receipt"
        or receipt.get("stage") != "full_notification"
        or not isinstance(receipt.get("report_id"), str)
        or not receipt["report_id"].strip()
        or not isinstance(case.get("id"), str)
        or not case["id"].strip()
        or receipt.get("case_id") != case.get("id")
        or not isinstance(packet_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", packet_digest)
        or packet_digest != approved_digest
    ):
        raise ValueError("full-notification receipt anchor is invalid")
    submitted = _parse_timestamp(receipt.get("submitted_at"), "receipt submitted_at")
    approved_at = _parse_timestamp(
        receipt.get("approval_decided_at"),
        "receipt approval_decided_at",
    )
    if submitted < approved_at:
        raise ValueError("receipt submitted_at cannot precede exact-packet approval")
    if submitted < _parse_timestamp(case.get("awareness_at"), "awareness_at"):
        raise ValueError("receipt submitted_at cannot precede case awareness")
    return submitted


def project_article14_clocks(case: dict, rules: tuple[dict, ...] = VENDORED_CLOCK_RULES) -> dict:
    if case.get("reportability", {}).get("status") != "reportable":
        raise ValueError(
            "a human must mark the case reportable before projecting CRA clocks"
        )
    case_type = case.get("case_type")
    if case_type not in _CASE_TYPES:
        raise ValueError("unsupported CRA case type")
    awareness = _parse_timestamp(case.get("awareness_at"), "awareness_at")
    full_receipt = case.get("full_notification_submission_receipt")
    full_submission = None
    if full_receipt is not None:
        full_submission = _validated_full_receipt_submission(case, full_receipt)
    anchors = {
        "awareness": awareness,
        "corrective_measure_available": (
            _parse_timestamp(case["corrective_measure_available_at"], "corrective_measure_available_at")
            if case.get("corrective_measure_available_at")
            else None
        ),
        "full_notification_submission": full_submission,
    }
    projected: dict[str, dict] = {}
    for rule in rules:
        if rule.get("case_type") != case_type:
            continue
        stage = rule.get("stage")
        anchor_name = rule.get("anchor")
        if stage not in _REPORT_STAGES or anchor_name not in anchors:
            raise ValueError("clock rule is invalid")
        anchor = anchors[anchor_name]
        due = None
        if anchor is not None:
            if rule.get("unit") == "hours":
                due = anchor + timedelta(hours=int(rule.get("amount", 0)))
            elif rule.get("unit") == "calendar_days":
                due = anchor + timedelta(days=int(rule.get("amount", 0)))
            elif rule.get("unit") == "calendar_months" and rule.get("amount") == 1:
                due = _calendar_month(anchor)
            else:
                raise ValueError("clock rule uses an unsupported duration")
        projected[stage] = {
            "stage": stage,
            "article": _required(rule.get("article"), "clock article", 40),
            "anchor": anchor_name,
            "anchor_at": _iso(anchor) if anchor else None,
            "due_at": _iso(due) if due else None,
            "state": "projected" if due else "awaiting_anchor",
        }
    if set(projected) != _REPORT_STAGES:
        raise ValueError("clock rules are incomplete")
    return {
        "early_warning": projected["early_warning"],
        "full_notification": projected["full_notification"],
        "final_report": projected["final_report"],
        "calculation_only": True,
        "not_a_legal_decision": True,
    }


def open_case(
    product: dict,
    release: dict,
    case_type: object,
    awareness_at: object,
    title: object,
    rules: tuple[dict, ...] = VENDORED_CLOCK_RULES,
) -> dict:
    if product.get("type") != "product" or not product.get("id"):
        raise ValueError("a stored product is required")
    if release.get("type") != "release" or not release.get("id"):
        raise ValueError("a stored release is required")
    if release.get("product_id") != product["id"]:
        raise ValueError("release does not belong to product")
    if product.get("cra_applicability", {}).get("status") != "applicable":
        raise ValueError("a human must mark the product applicable before opening a CRA case")
    if case_type not in _CASE_TYPES:
        raise ValueError("unsupported CRA case type")
    row = {
        "type": "cra_case",
        "product_id": product["id"],
        "release_id": release["id"],
        "case_type": case_type,
        "title": _required(title, "title", 500),
        "awareness_at": _iso(_parse_timestamp(awareness_at, "awareness_at")),
        "reportability": {"status": "pending_human_decision"},
        "clock_state": "pending_human_decision",
        "clocks": {},
        "authority": "unsigned_local",
    }
    return row


def decide_case_reportability(
    case: dict,
    decision: object,
    reviewer: object,
    rationale: object,
    rules: tuple[dict, ...] = VENDORED_CLOCK_RULES,
) -> dict:
    if case.get("type") != "cra_case":
        raise ValueError("record is not a CRA case")
    if case.get("reportability", {}).get("status") != "pending_human_decision":
        raise ValueError("case reportability has already been decided")
    if decision not in {"reportable", "not_reportable"}:
        raise ValueError("decision must be reportable or not_reportable")
    row = dict(case)
    row["reportability"] = {
        "status": decision,
        "reviewer": _required(reviewer, "reviewer", 160),
        "rationale": _required(rationale, "rationale", 2_000),
        "decided_at": _now_iso(),
        "human_decision": True,
    }
    row["clock_state"] = "active" if decision == "reportable" else "not_active"
    row["clocks"] = (
        project_article14_clocks(row, rules=rules)
        if decision == "reportable"
        else {}
    )
    return row


def record_case_anchor(
    case: dict,
    anchor: object,
    occurred_at: object,
    actor: object,
    rules: tuple[dict, ...] = VENDORED_CLOCK_RULES,
) -> dict:
    if case.get("type") != "cra_case":
        raise ValueError("record is not a CRA case")
    if case.get("reportability", {}).get("status") != "reportable":
        raise ValueError("a human must mark the case reportable before recording an anchor")
    if case.get("case_type") == "severe_incident":
        raise ValueError(
            "a severe-incident final clock can be anchored only by an approved "
            "full-notification submission receipt"
        )
    permitted = "corrective_measure_available_at"
    if anchor != permitted:
        raise ValueError(f"anchor must be {permitted} for this case type")
    if case.get(permitted):
        raise ValueError("case anchor has already been recorded")
    row = dict(case)
    occurred = _parse_timestamp(occurred_at, "occurred_at")
    if occurred < _parse_timestamp(case.get("awareness_at"), "awareness_at"):
        raise ValueError("case anchor cannot precede awareness")
    row[permitted] = _iso(occurred)
    row["anchor_recorded_by"] = _required(actor, "actor", 160)
    row["clocks"] = project_article14_clocks(row, rules)
    return row


def _safe_metadata(value: object, depth: int = 0) -> object:
    if depth > 5:
        raise ValueError("report facts are nested too deeply")
    if isinstance(value, dict):
        if len(value) > 64:
            raise ValueError("report facts contain too many fields")
        result = {}
        for key, item in value.items():
            name = _required(key, "fact key", 160)
            if _SENSITIVE_KEY.search(name):
                raise ValueError(f"sensitive field is not accepted: {name}")
            result[name] = _safe_metadata(item, depth + 1)
        return result
    if isinstance(value, list):
        if len(value) > 128:
            raise ValueError("report fact list contains too many items")
        return [_safe_metadata(item, depth + 1) for item in value]
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("report facts must contain finite numbers")
        return value
    if isinstance(value, str) and len(value) <= 4_000:
        return value
    raise ValueError("report facts must contain bounded JSON values")


def create_vex_draft(
    product: dict,
    release: dict,
    vulnerability_id: object,
    status: object,
    justification: object,
    author: object,
) -> dict:
    if product.get("type") != "product" or release.get("type") != "release":
        raise ValueError("stored product and release are required")
    if release.get("product_id") != product.get("id"):
        raise ValueError("release does not belong to product")
    if status not in _VEX_STATUSES:
        raise ValueError("unsupported VEX status")
    return {
        "type": "vex_draft",
        "product_id": product["id"],
        "release_id": release["id"],
        "vulnerability_id": _required(vulnerability_id, "vulnerability_id", 160),
        "status": status,
        "justification": _required(justification, "justification", 4_000),
        "author": _required(author, "author", 160),
        "workflow_status": "draft",
        "authority": "unsigned_local",
    }


def create_report_draft(
    case: dict,
    stage: object,
    summary: object,
    facts: object,
    author: object,
) -> dict:
    if case.get("type") != "cra_case":
        raise ValueError("stored CRA case is required")
    if case.get("reportability", {}).get("status") != "reportable":
        raise ValueError("a human must mark the case reportable before drafting")
    if stage not in _REPORT_STAGES:
        raise ValueError("unsupported report stage")
    safe_facts = _safe_metadata(facts)
    if not isinstance(safe_facts, dict):
        raise ValueError("facts must be an object")
    packet = {
        "schema_version": 1,
        "case_id": case["id"],
        "case_type": case["case_type"],
        "product_id": case["product_id"],
        "release_id": case["release_id"],
        "stage": stage,
        "awareness_at": case["awareness_at"],
        "clocks": case["clocks"],
        "summary": _required(summary, "summary", 8_000),
        "facts": safe_facts,
        "author": _required(author, "author", 160),
    }
    return {
        "type": "report_draft",
        "case_id": case["id"],
        "stage": stage,
        "packet": packet,
        "packet_sha256": _digest(packet),
        "status": "pending_human_approval",
        "authority": "unsigned_local",
        "network_filing_performed": False,
    }


def decide_report(
    report: dict,
    decision: object,
    reviewer: object,
    rationale: object,
    packet_sha256: object,
) -> dict:
    if report.get("type") != "report_draft":
        raise ValueError("record is not a report draft")
    if report.get("status") != "pending_human_approval":
        raise ValueError("report has already received a decision")
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be approve or reject")
    current_digest = _digest(report.get("packet"))
    provided = _required(packet_sha256, "packet_sha256", 64).lower()
    if current_digest != report.get("packet_sha256") or provided != current_digest:
        raise ValueError("packet digest does not match the exact current draft")
    row = dict(report)
    row["status"] = "locally_approved" if decision == "approve" else "locally_rejected"
    row["approval"] = {
        "decision": decision,
        "reviewer": _required(reviewer, "reviewer", 160),
        "rationale": _required(rationale, "rationale", 2_000),
        "packet_sha256": current_digest,
        "decided_at": _now_iso(),
        "authority": "unsigned_local_human",
    }
    return row


def _validated_report_approval(report: dict) -> tuple[str, datetime]:
    if report.get("type") != "report_draft" or report.get("status") != "locally_approved":
        raise ValueError("a locally approved report is required")
    current = _digest(report.get("packet"))
    persisted = report.get("packet_sha256")
    approval = report.get("approval")
    if (
        not isinstance(approval, dict)
        or approval.get("decision") != "approve"
        or persisted != current
        or approval.get("packet_sha256") != current
        or not str(approval.get("reviewer") or "").strip()
        or not str(approval.get("rationale") or "").strip()
    ):
        raise ValueError("report approval does not bind the exact current packet")
    approved_at = _parse_timestamp(approval.get("decided_at"), "approval decided_at")
    return current, approved_at


def validate_submission_receipt(report: dict, receipt: dict) -> datetime:
    """Revalidate a stored local attestation before it is exported or anchored."""
    digest, approved_at = _validated_report_approval(report)
    if (
        not isinstance(receipt, dict)
        or receipt.get("type") != "submission_receipt"
        or receipt.get("report_id") != report.get("id")
        or receipt.get("case_id") != report.get("case_id")
        or receipt.get("stage") != report.get("stage")
        or receipt.get("packet_sha256") != digest
        or receipt.get("approved_packet_sha256") != digest
        or receipt.get("approval_decided_at") != _iso(approved_at)
    ):
        raise ValueError("submission receipt does not bind the exact approved report")
    submitted = _parse_timestamp(receipt.get("submitted_at"), "submitted_at")
    awareness = _parse_timestamp(
        report.get("packet", {}).get("awareness_at"),
        "awareness_at",
    )
    if submitted < awareness:
        raise ValueError("receipt submitted_at cannot precede case awareness")
    if submitted < approved_at:
        raise ValueError("receipt submitted_at cannot precede exact-packet approval")
    return submitted


def record_submission_receipt(
    report: dict,
    packet_sha256: object,
    receipt_reference: object,
    submitted_at: object,
    recorded_by: object,
) -> dict:
    current, approved_at = _validated_report_approval(report)
    digest = _required(packet_sha256, "packet_sha256", 64).lower()
    if digest != current:
        raise ValueError("receipt digest does not match the exact approved packet")
    submitted = _parse_timestamp(submitted_at, "submitted_at")
    awareness = _parse_timestamp(report.get("packet", {}).get("awareness_at"), "awareness_at")
    if submitted < awareness:
        raise ValueError("receipt submitted_at cannot precede case awareness")
    if submitted < approved_at:
        raise ValueError("receipt submitted_at cannot precede exact-packet approval")
    receipt = {
        "type": "submission_receipt",
        "report_id": report["id"],
        "case_id": report["case_id"],
        "stage": report["stage"],
        "packet_sha256": digest,
        "approved_packet_sha256": current,
        "approval_decided_at": _iso(approved_at),
        "receipt_reference": _required(receipt_reference, "receipt_reference", 500),
        "submitted_at": _iso(submitted),
        "recorded_by": _required(recorded_by, "recorded_by", 160),
        "recorded_at": _now_iso(),
        "network_call_made": False,
        "filing_performed_by_lightwork": False,
        "authority": "unsigned_local_user_attestation",
    }
    validate_submission_receipt(report, receipt)
    return receipt


def anchor_case_from_full_notification_receipt(
    case: dict,
    receipt: dict,
    rules: tuple[dict, ...] = VENDORED_CLOCK_RULES,
) -> dict:
    """Derive a severe-incident final clock from its approved receipt only."""
    if case.get("type") != "cra_case" or case.get("case_type") != "severe_incident":
        raise ValueError("a stored severe-incident case is required")
    if case.get("reportability", {}).get("status") != "reportable":
        raise ValueError("the severe-incident case must be reportable")
    if case.get("full_notification_submission_receipt"):
        raise ValueError("the full-notification receipt anchor is already recorded")
    try:
        _validated_full_receipt_submission(case, receipt)
    except ValueError as exc:
        raise ValueError("an approved full-notification receipt is required") from exc
    row = dict(case)
    row["full_notification_submission_receipt"] = {
        key: receipt[key]
        for key in (
            "type",
            "report_id",
            "case_id",
            "stage",
            "packet_sha256",
            "approved_packet_sha256",
            "approval_decided_at",
            "submitted_at",
        )
    }
    row["anchor_recorded_by"] = receipt["recorded_by"]
    row["clocks"] = project_article14_clocks(row, rules)
    return row


__all__ = [
    "LocalStore",
    "MAX_LOCAL_STORE_BYTES",
    "MAX_SBOM_BYTES",
    "RevisionConflict",
    "VENDORED_CLOCK_RULES",
    "create_product",
    "create_release",
    "create_report_draft",
    "create_vex_draft",
    "anchor_case_from_full_notification_receipt",
    "decide_case_reportability",
    "decide_product_applicability",
    "decide_report",
    "normalize_sbom",
    "open_case",
    "project_article14_clocks",
    "record_case_anchor",
    "record_submission_receipt",
    "validate_submission_receipt",
]

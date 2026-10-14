"""Bounded, deterministic CycloneDX and SPDX software-bill ingestion.

Only the fields needed for product-security decisions are retained.  The raw
document is content-bound by SHA-256 but is not copied into the governed record
store.  Dependency relationships remain labelled as a *declared graph* unless
the producer supplied Lightwork's explicit resolution assertion; a manifest or
lock-file claim is never silently presented as verified transitive resolution.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

_DEFAULT_MAX_BYTES = 8 * 1024 * 1024
_DEFAULT_MAX_COMPONENTS = 20_000
_DEFAULT_MAX_NODES = 200_000
_MAX_DEPTH = 64
_CYCLONEDX_VERSIONS = frozenset({"1.4", "1.5", "1.6"})
_SPDX_VERSIONS = frozenset({"SPDX-2.2", "SPDX-2.3"})
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


class _DuplicateKeyError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError("SBOM contains a duplicate JSON object key")
        value[key] = item
    return value


def _bounded_list(value: object, label: str, limit: int) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    if len(value) > limit:
        raise ValueError(f"{label} exceeds the {limit}-item limit")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _text(
    value: object,
    label: str,
    limit: int,
    *,
    required: bool = False,
) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise ValueError(f"{label} must be a string")
    if required and not text:
        raise ValueError(f"{label} is required")
    if len(text) > limit:
        raise ValueError(f"{label} exceeds the {limit}-character limit")
    return text


def _decode(document: bytes | str | dict[str, Any], max_bytes: int) -> tuple[dict, bytes]:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    if isinstance(document, bytes):
        raw = document
        if len(raw) > max_bytes:
            raise ValueError("SBOM exceeds the configured size limit")
        try:
            parsed = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON value {value!r}")
                ),
            )
        except _DuplicateKeyError as exc:
            raise ValueError(str(exc)) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("SBOM must be valid UTF-8 JSON") from exc
    elif isinstance(document, str):
        raw = document.encode("utf-8")
        if len(raw) > max_bytes:
            raise ValueError("SBOM exceeds the configured size limit")
        try:
            parsed = json.loads(
                document,
                object_pairs_hook=_unique_object,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON value {value!r}")
                ),
            )
        except _DuplicateKeyError as exc:
            raise ValueError(str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise ValueError("SBOM must be valid JSON") from exc
    elif isinstance(document, dict):
        parsed = document
        try:
            raw = _canonical(parsed)
        except (TypeError, ValueError) as exc:
            raise ValueError("SBOM must contain JSON-compatible finite values") from exc
        if len(raw) > max_bytes:
            raise ValueError("SBOM exceeds the configured size limit")
    else:
        raise ValueError("SBOM must be bytes, JSON text, or an object")
    if not isinstance(parsed, dict):
        raise ValueError("SBOM root must be an object")
    return parsed, raw


def _enforce_structure(value: object, *, max_nodes: int) -> None:
    if (
        not isinstance(max_nodes, int)
        or isinstance(max_nodes, bool)
        or max_nodes <= 0
    ):
        raise ValueError("max_nodes must be a positive integer")
    stack: list[tuple[object, int]] = [(value, 1)]
    seen = 0
    while stack:
        current, depth = stack.pop()
        seen += 1
        if seen > max_nodes:
            raise ValueError("SBOM exceeds the configured structure limit")
        if depth > _MAX_DEPTH:
            raise ValueError("SBOM exceeds the nesting limit")
        if isinstance(current, dict):
            stack.extend((key, depth + 1) for key in current)
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
        elif isinstance(current, float) and not math.isfinite(current):
            raise ValueError("SBOM contains a non-finite number")
        elif current is not None and not isinstance(
            current, (str, int, float, bool)
        ):
            raise ValueError("SBOM contains a non-JSON value")


def _component_hashes(values: object) -> list[dict[str, str]]:
    if values is None:
        return []
    bounded = _bounded_list(values, "component hashes", 64)
    out: dict[tuple[str, str], dict[str, str]] = {}
    for raw in bounded:
        if not isinstance(raw, dict):
            raise ValueError("component hash must be an object")
        algorithm = _text(
            raw.get("alg") or raw.get("algorithm"),
            "hash algorithm",
            40,
            required=True,
        ).lower()
        content = _text(
            raw.get("content") or raw.get("checksumValue"),
            "hash content",
            256,
            required=True,
        ).lower()
        if algorithm in {"sha-256", "sha256"} and not _SHA256.fullmatch(content):
            raise ValueError("SHA-256 component hash must be 64 hexadecimal characters")
        out[(algorithm, content)] = {"algorithm": algorithm, "content": content}
    return [out[key] for key in sorted(out)]


def _cyclonedx_licenses(values: object) -> list[str]:
    if values is None:
        return []
    bounded = _bounded_list(values, "CycloneDX licenses", 64)
    out: set[str] = set()
    for raw in bounded:
        if not isinstance(raw, dict):
            raise ValueError("CycloneDX license must be an object")
        license_value = raw.get("license")
        if isinstance(license_value, dict):
            value = license_value.get("id") or license_value.get("name")
        else:
            value = raw.get("expression")
        label = _text(value, "license", 500)
        if label:
            out.add(label)
    return sorted(out)


def _cyclonedx_identity(raw: dict[str, Any]) -> str:
    ref = _text(raw.get("bom-ref"), "bom-ref", 1000)
    if ref:
        return ref
    purl = _text(raw.get("purl"), "purl", 1000)
    if purl:
        return purl
    kind = _text(raw.get("type"), "component type", 80, required=True)
    name = _text(raw.get("name"), "component name", 500, required=True)
    version = _text(raw.get("version"), "component version", 300)
    return f"cyclonedx:{kind}:{name}@{version or '<unknown>'}"


def _cyclonedx_component(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("CycloneDX component must be an object")
    name = _text(raw.get("name"), "component name", 500, required=True)
    component = {
        "id": _cyclonedx_identity(raw),
        "type": _text(raw.get("type"), "component type", 80, required=True),
        "name": name,
        "version": _text(raw.get("version"), "component version", 300),
        "purl": _text(raw.get("purl"), "purl", 1000),
        "cpe": _text(raw.get("cpe"), "cpe", 1000),
        "hashes": _component_hashes(raw.get("hashes")),
        "licenses": _cyclonedx_licenses(raw.get("licenses")),
    }
    return component


def _flatten_cyclonedx_components(
    values: object,
    *,
    max_components: int,
) -> list[dict[str, Any]]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("CycloneDX components must be a list")
    out: list[dict[str, Any]] = []
    stack = list(reversed(values))
    while stack:
        raw = stack.pop()
        component = _cyclonedx_component(raw)
        out.append(component)
        if len(out) > max_components:
            raise ValueError("SBOM exceeds the component limit")
        children = raw.get("components")
        if children is not None:
            if not isinstance(children, list):
                raise ValueError("nested CycloneDX components must be a list")
            stack.extend(reversed(children))
    return out


def _unique_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for component in components:
        identity = component["id"]
        if identity in by_id:
            raise ValueError(f"duplicate component identity {identity!r}")
        by_id[identity] = component
    return [by_id[key] for key in sorted(by_id)]


def _cyclonedx_dependencies(
    values: object,
    known: set[str],
) -> list[dict[str, str]]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("CycloneDX dependencies must be a list")
    out: dict[tuple[str, str], dict[str, str]] = {}
    for raw in values:
        if not isinstance(raw, dict):
            raise ValueError("CycloneDX dependency must be an object")
        source = _text(raw.get("ref"), "dependency ref", 1000, required=True)
        targets = raw.get("dependsOn", [])
        if not isinstance(targets, list):
            raise ValueError("CycloneDX dependsOn must be a list")
        if source not in known:
            raise ValueError(f"dependency references unknown component {source!r}")
        for target_value in targets:
            target = _text(
                target_value,
                "dependency target",
                1000,
                required=True,
            )
            if target not in known:
                raise ValueError(
                    f"dependency references unknown component {target!r}"
                )
            if target == source:
                raise ValueError("component cannot depend on itself")
            out[(source, target)] = {
                "from": source,
                "to": target,
                "relationship": "depends_on",
            }
    return [out[key] for key in sorted(out)]


def _cyclonedx_resolution(metadata: dict[str, Any]) -> tuple[str, str]:
    properties = metadata.get("properties", [])
    if properties is None:
        properties = []
    if not isinstance(properties, list):
        raise ValueError("CycloneDX metadata properties must be a list")
    for raw in properties:
        if not isinstance(raw, dict):
            raise ValueError("CycloneDX metadata property must be an object")
        name = _text(raw.get("name"), "metadata property name", 300)
        value = _text(raw.get("value"), "metadata property value", 1000)
        if name == "lightwork:dependency-resolution" and value == "resolved":
            return (
                "producer_asserted_resolved",
                "CycloneDX metadata property lightwork:dependency-resolution=resolved",
            )
    return "declared_graph", ""


def _parse_cyclonedx(document: dict[str, Any], max_components: int) -> dict[str, Any]:
    version = _text(
        document.get("specVersion"),
        "CycloneDX specVersion",
        20,
        required=True,
    )
    if version not in _CYCLONEDX_VERSIONS:
        raise ValueError(f"unsupported CycloneDX specVersion {version!r}")
    metadata = document.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("CycloneDX metadata must be an object")
    components: list[dict[str, Any]] = []
    root = metadata.get("component")
    if root is not None:
        components.append(_cyclonedx_component(root))
    components.extend(
        _flatten_cyclonedx_components(
            document.get("components"),
            max_components=max_components,
        )
    )
    if len(components) > max_components:
        raise ValueError("SBOM exceeds the component limit")
    components = _unique_components(components)
    dependencies = _cyclonedx_dependencies(
        document.get("dependencies"),
        {component["id"] for component in components},
    )
    resolution, evidence = _cyclonedx_resolution(metadata)
    return {
        "format": "cyclonedx",
        "spec_version": version,
        "document_id": _text(document.get("serialNumber"), "serialNumber", 1000),
        "components": components,
        "dependencies": dependencies,
        "dependency_resolution": resolution,
        "resolution_evidence": evidence,
    }


def _spdx_purl(raw: dict[str, Any]) -> str:
    refs = raw.get("externalRefs", [])
    if refs is None:
        refs = []
    bounded = _bounded_list(refs, "SPDX externalRefs", 128)
    found: set[str] = set()
    for ref in bounded:
        if not isinstance(ref, dict):
            raise ValueError("SPDX externalRef must be an object")
        kind = _text(ref.get("referenceType"), "referenceType", 200).lower()
        if kind == "purl" or kind.endswith("package-manager-purl"):
            found.add(
                _text(
                    ref.get("referenceLocator"),
                    "purl referenceLocator",
                    1000,
                    required=True,
                )
            )
    if len(found) > 1:
        raise ValueError("SPDX package contains conflicting purl references")
    return next(iter(found), "")


def _spdx_licenses(raw: dict[str, Any]) -> list[str]:
    values = {
        _text(raw.get("licenseConcluded"), "licenseConcluded", 500),
        _text(raw.get("licenseDeclared"), "licenseDeclared", 500),
    }
    return sorted(value for value in values if value and value != "NOASSERTION")


def _spdx_components(values: object, max_components: int) -> list[dict[str, Any]]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("SPDX packages must be a list")
    if len(values) > max_components:
        raise ValueError("SBOM exceeds the component limit")
    out = []
    for raw in values:
        if not isinstance(raw, dict):
            raise ValueError("SPDX package must be an object")
        out.append({
            "id": _text(raw.get("SPDXID"), "package SPDXID", 1000, required=True),
            "type": "package",
            "name": _text(raw.get("name"), "package name", 500, required=True),
            "version": _text(raw.get("versionInfo"), "package version", 300),
            "purl": _spdx_purl(raw),
            "cpe": "",
            "hashes": _component_hashes(raw.get("checksums")),
            "licenses": _spdx_licenses(raw),
        })
    return _unique_components(out)


def _spdx_dependencies(values: object, known: set[str]) -> list[dict[str, str]]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("SPDX relationships must be a list")
    out: dict[tuple[str, str], dict[str, str]] = {}
    for raw in values:
        if not isinstance(raw, dict):
            raise ValueError("SPDX relationship must be an object")
        relation = _text(
            raw.get("relationshipType"),
            "relationshipType",
            100,
            required=True,
        ).upper()
        if relation not in {"DEPENDS_ON", "DEPENDENCY_OF"}:
            continue
        left = _text(
            raw.get("spdxElementId"),
            "spdxElementId",
            1000,
            required=True,
        )
        right = _text(
            raw.get("relatedSpdxElement"),
            "relatedSpdxElement",
            1000,
            required=True,
        )
        source, target = (left, right) if relation == "DEPENDS_ON" else (right, left)
        if source not in known or target not in known:
            missing = source if source not in known else target
            raise ValueError(f"dependency references unknown component {missing!r}")
        if source == target:
            raise ValueError("component cannot depend on itself")
        out[(source, target)] = {
            "from": source,
            "to": target,
            "relationship": "depends_on",
        }
    return [out[key] for key in sorted(out)]


def _parse_spdx(document: dict[str, Any], max_components: int) -> dict[str, Any]:
    version = _text(
        document.get("spdxVersion"),
        "spdxVersion",
        20,
        required=True,
    )
    if version not in _SPDX_VERSIONS:
        raise ValueError(f"unsupported SPDX version {version!r}")
    components = _spdx_components(document.get("packages"), max_components)
    dependencies = _spdx_dependencies(
        document.get("relationships"),
        {component["id"] for component in components},
    )
    return {
        "format": "spdx",
        "spec_version": version.removeprefix("SPDX-"),
        "document_id": _text(
            document.get("documentNamespace") or document.get("SPDXID"),
            "SPDX document identity",
            1000,
            required=True,
        ),
        "components": components,
        "dependencies": dependencies,
        "dependency_resolution": "declared_graph",
        "resolution_evidence": "",
    }


def parse_sbom(
    document: bytes | str | dict[str, Any],
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    max_components: int = _DEFAULT_MAX_COMPONENTS,
    max_nodes: int = _DEFAULT_MAX_NODES,
) -> dict[str, Any]:
    """Validate and normalise one CycloneDX or SPDX JSON document.

    The returned structure is deterministic and intentionally excludes freeform
    properties, credentials, download URLs, and raw document content.
    """
    if (
        not isinstance(max_components, int)
        or isinstance(max_components, bool)
        or max_components <= 0
    ):
        raise ValueError("max_components must be a positive integer")
    parsed, raw = _decode(document, max_bytes)
    _enforce_structure(parsed, max_nodes=max_nodes)
    if parsed.get("bomFormat") == "CycloneDX":
        result = _parse_cyclonedx(parsed, max_components)
    elif str(parsed.get("spdxVersion") or "").startswith("SPDX-"):
        result = _parse_spdx(parsed, max_components)
    else:
        raise ValueError("unsupported SBOM format; expected CycloneDX or SPDX JSON")
    result["document_sha256"] = hashlib.sha256(raw).hexdigest()
    result["component_count"] = len(result["components"])
    result["dependency_count"] = len(result["dependencies"])
    result["normalised_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result


__all__ = ["parse_sbom"]

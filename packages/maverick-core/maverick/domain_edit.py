"""Per-client pack customization -- the data layer behind the dashboard editor.

A built-in pack ships read-only inside the package; a client tailors it by
writing a *tenant override* (a partial TOML pack in the workspace domains dir).
:func:`maverick.domain.available_domains` overlays that override onto the
built-in base field-by-field, so the client inherits everything it didn't
change. This module is the editing surface over that mechanism:

  * :func:`resolved_view` -- the merged pack plus *provenance* (which fields are
    overridden vs inherited) and lint findings, for the editor UI.
  * :func:`write_override` -- validate then persist an override patch.
  * :func:`remove_override` -- drop the override, reverting to the built-in.
  * :func:`list_agents` -- the roster, flagged by override/workflow status.

Overrides serialize with the same ``json.dumps`` idiom the intake factory uses
(``maverick.intake._to_toml``): ``json.dumps`` emits valid TOML string/array
literals, so an override round-trips back through the loader.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path

from .domain import (
    _OVERLAYABLE,
    DomainProfile,
    _coerce,
    _envelope_errors,
    _load_raw_domains,
    available_domains,
    builtin_dir,
    declared_prompt_gate,
    enforced_gate,
    lint_profile,
    load_domains,
    overlay_profile,
    overridden_fields,
    suite_for,
    user_dir,
)
from .file_lock import (
    atomic_read_bytes,
    atomic_write_text,
    cross_process_lock,
    ensure_private_directory,
)

# Scalars vs arrays serialize the same way (json.dumps), but we emit them in a
# stable, readable order; ``models`` and ``workflow`` become TOML tables.
_SCALAR_KEYS = ("name", "extends", "compartment", "description", "persona",
                "max_risk", "effort", "authoring")
_ARRAY_KEYS = ("allow_tools", "deny_tools", "allow_paths", "allow_hosts",
               "mcp_servers", "knowledge_sources", "refuse")

# Override names become filenames. Keep this deliberately narrower than a
# platform path component so the same identifier is safe on POSIX and Windows
# (where a backslash is a separator) and cannot address a sibling file.
_OVERRIDE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_MAX_OVERRIDE_BYTES = 256 * 1024


def _validated_override_name(name: str) -> str:
    name = str(name or "")
    if not _OVERRIDE_NAME_RE.fullmatch(name):
        raise ValueError(
            "agent name must be 1-128 letters, numbers, underscores, or hyphens"
        )
    return name


def overlay_toml(patch: dict) -> str:
    """Serialize an override patch dict to TOML that round-trips through the
    loader. Only the keys present in ``patch`` are written (it stays a thin
    overlay); empty scalars are skipped."""
    lines: list[str] = []
    for k in _SCALAR_KEYS:
        if k in patch and patch[k] not in (None, ""):
            lines.append(f"{k} = {json.dumps(patch[k])}")
    for k in _ARRAY_KEYS:
        if k in patch:
            lines.append(f"{k} = {json.dumps(list(patch[k]))}")
    if patch.get("models"):
        lines.append("")
        lines.append("[models]")
        for role, model in dict(patch["models"]).items():
            lines.append(f"{role} = {json.dumps(model)}")
    out = patch.get("output")
    if out:
        lines.append("")
        lines.append("[output]")
        for key in ("shape", "deliverable", "cadence", "gate"):
            if out.get(key):
                lines.append(f"{key} = {json.dumps(out[key])}")
        if out.get("consumers"):
            lines.append(f"consumers = {json.dumps(list(out['consumers']))}")
    for step in patch.get("workflow") or []:
        if not str(step.get("name") or "").strip():
            continue
        lines.append("")
        lines.append("[[workflow]]")
        lines.append(f"name = {json.dumps(step['name'])}")
        if step.get("instruction"):
            lines.append(f"instruction = {json.dumps(step['instruction'])}")
        if step.get("tools"):
            lines.append(f"tools = {json.dumps(list(step['tools']))}")
        if step.get("gate"):
            lines.append(f"gate = {json.dumps(step['gate'])}")
    return "\n".join(lines) + "\n"


def _resolve(name: str, patch: dict) -> DomainProfile:
    """The profile a ``name``/``patch`` override resolves to (base overlaid, or
    a standalone pack when there is no base to inherit)."""
    base_name = str(patch.get("extends") or name)
    base = available_domains().get(base_name) if base_name != name else None
    if base is None:
        base = load_domains(builtin_dir()).get(base_name)
    return overlay_profile(base, patch) if base else _coerce(name, patch)


def _base_profile(name: str, patch: dict) -> DomainProfile | None:
    """Active profile an override inherits from, if one exists."""
    base_name = str(patch.get("extends") or name)
    if base_name != name:
        base = available_domains().get(base_name)
        if base is not None:
            return base
    return load_domains(builtin_dir()).get(base_name)


def validate_override(name: str, patch: dict) -> tuple[list[str], list[str]]:
    """Lint the *merged* result of applying ``patch`` to its base. Returns
    ``(errors, warnings)`` -- the editor blocks a save on any error."""
    try:
        name = _validated_override_name(name)
    except ValueError as exc:
        return [str(exc)], []
    patch = dict(patch)
    merged = _resolve(name, patch)
    errors, warnings = lint_profile(merged)
    if not errors:
        errors.extend(_envelope_errors(_base_profile(name, patch), merged))
    return errors, warnings


class OverrideConflict(ValueError):
    """The agent changed after the editor loaded it."""


def _override_generation(name: str, path: Path) -> str:
    content = atomic_read_bytes(path) if path.exists() else b""
    return hashlib.sha256(name.encode() + b"\0" + content).hexdigest()


def write_override(name: str, patch: dict, directory: str | Path | None = None, *,
                   expected_generation: str | None = None, return_view: bool = False) -> str | dict:
    """Validate then persist a tenant override for ``name``. Raises
    ``ValueError`` (with the lint errors) rather than writing an override that
    would weaken the safety envelope. Returns the path written."""
    name = _validated_override_name(name)
    patch = {k: v for k, v in dict(patch).items() if k in _OVERLAYABLE or k in ("name", "extends")}
    # The route/argument identifier is authoritative; a caller-supplied payload
    # name must not create an identity mismatch inside a differently named file.
    patch["name"] = name
    content = overlay_toml(patch)
    if len(content.encode("utf-8")) > _MAX_OVERRIDE_BYTES:
        raise ValueError("agent override is too large")
    errors, _ = validate_override(name, patch)
    if errors:
        raise ValueError("; ".join(errors))
    d = ensure_private_directory(Path(directory) if directory else user_dir())
    path = d / f"{name}.toml"
    # Lock a stable sidecar, then publish a complete private file. Atomic
    # replacement also replaces a planted final-component symlink rather than
    # following it to an attacker-selected target.
    with cross_process_lock(path, strict=True):
        if expected_generation == "new":
            if path.exists() or name in available_domains():
                raise OverrideConflict("An agent with this name already exists. Open its latest version or choose a new name.")
        elif expected_generation is not None and not hmac.compare_digest(expected_generation, _override_generation(name, path)):
            raise OverrideConflict("This agent changed after you opened it. Reload the latest version before applying your edits.")
        atomic_write_text(path, content, mode=0o600)
        view = _resolved_view(name, directory) if return_view else None
    # The pack's capability surface may have changed. If it already has a
    # governance assessment, re-draft it so drift flips an accepted review back
    # to needs-review (best-effort; only touches the default user dir).
    if directory is None:
        try:
            from . import assessments
            assessments.refresh_on_change("agent", name)
        except Exception:  # pragma: no cover -- never let assessment break a save
            pass
    return view if return_view else str(path)


def read_override(name: str, directory: str | Path | None = None) -> dict:
    """The raw override patch for ``name`` (``{}`` if the client hasn't
    customized this pack)."""
    name = _validated_override_name(name)
    d = Path(directory) if directory else user_dir()
    return _load_raw_domains(d).get(name, {})


def remove_override(name: str, directory: str | Path | None = None, *, expected_generation: str | None = None) -> bool:
    """Delete a tenant override, reverting to the built-in pack. Returns whether
    a file was removed."""
    name = _validated_override_name(name)
    d = Path(directory) if directory else user_dir()
    path = d / f"{name}.toml"
    with cross_process_lock(path, strict=True):
        if expected_generation is not None and not hmac.compare_digest(expected_generation, _override_generation(name, path)):
            raise OverrideConflict("This agent changed after you opened it. Reload its latest version before resetting it.")
        if path.is_file():
            path.unlink()
            return True
    return False


def _step_dict(step) -> dict:
    return {"name": step.name, "instruction": step.instruction,
            "tools": list(step.tools), "gate": step.gate}


def _output_dict(out) -> dict:
    """A pack's output contract as a plain dict for the editor/API payload."""
    return {"shape": out.shape, "deliverable": out.deliverable,
            "consumers": list(out.consumers), "cadence": out.cadence,
            "gate": out.gate}


def resolved_view(name: str, directory: str | Path | None = None) -> dict | None:
    try:
        name = _validated_override_name(name)
    except ValueError:
        return None
    d = ensure_private_directory(Path(directory) if directory else user_dir())
    with cross_process_lock(d / f"{name}.toml", strict=True):
        return _resolved_view(name, directory)


def _resolved_view(name: str, directory: str | Path | None = None) -> dict | None:
    """The merged pack the agent actually runs, plus provenance and lint -- the
    payload the editor renders. ``None`` if no such pack exists.

    ``overridden`` lists the fields the client has customized; everything else
    is inherited from the built-in base."""
    prof = available_domains().get(name)
    if prof is None:
        return None
    raw = read_override(name, directory)
    errors, warnings = lint_profile(prof)
    return {
        "name": prof.name,
        "generation": _override_generation(name, (Path(directory) if directory else user_dir()) / f"{name}.toml"),
        "suite": suite_for(prof.name) or suite_for(name),
        "description": prof.description,
        "persona": prof.persona,
        "allow_tools": list(prof.allow_tools),
        "deny_tools": list(prof.deny_tools),
        "max_risk": prof.max_risk,
        "knowledge_sources": list(prof.knowledge_sources),
        "models": dict(prof.models),
        "workflow": [_step_dict(s) for s in prof.workflow],
        "output": _output_dict(prof.output),
        # Keep prompt declarations separate from controls the release layer
        # actually enforces.  Consumers must never infer enforcement merely
        # because a gate-shaped string appeared in the LLM's instructions.
        "declared_prompt_gate": declared_prompt_gate(prof),
        "enforced_gate": enforced_gate(prof),
        "is_override": bool(raw),
        "overridden": sorted(overridden_fields(raw)),
        "errors": errors,
        "warnings": warnings,
    }


def list_agents(directory: str | Path | None = None) -> list[dict]:
    """Roster for the editor: every available pack, flagged by whether the
    client has overridden it and whether it carries a workflow."""
    overrides = _load_raw_domains(Path(directory) if directory else user_dir())
    out: list[dict] = []
    for name, prof in sorted(available_domains().items()):
        out.append({
            "name": name,
            "suite": suite_for(prof.name) or suite_for(name),
            "description": prof.description,
            "is_override": name in overrides,
            "has_workflow": bool(prof.workflow),
        })
    return out


__all__ = [
    "overlay_toml", "validate_override", "write_override", "read_override",
    "remove_override", "resolved_view", "list_agents",
]

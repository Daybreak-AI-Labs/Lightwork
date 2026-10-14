"""Tenant-bound computer-use runtime state and multimodal tool results.

This module is intentionally independent from the dashboard and the input
backend.  It is the stable control-plane seam used by both:

* computer-use actions record sessions, observations and typed outcomes here;
* operator surfaces can list sessions, stream revisioned events, inspect
  integrity-checked frames, and pause/take over/stop a session without reaching
  into ``pyautogui`` or a provider SDK.

Runtime state is stored in a private directory for one explicit tenant.  Raw
frames are separate private files and are never embedded in the JSON state.
Every read validates tenant/principal ownership; ``principal=None`` is the
deliberate tenant-admin view, not a cross-tenant wildcard.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import platform
import re
import secrets
import shutil
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .file_lock import (
    atomic_read_bytes,
    atomic_read_text,
    atomic_write_bytes,
    atomic_write_text,
    cross_process_lock,
    ensure_private_directory,
    ensure_private_file,
)
from .paths import data_dir

LOCAL_TENANT_ID = "__local__"
LOCAL_PRINCIPAL = "local"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{12,128}$")
_MAX_EVENTS = 2_000
_TRUTHY = frozenset({"1", "true", "yes", "on"})
log = logging.getLogger(__name__)


# This callback contains no approval authority.  It is only a scoped observer
# which lets the computer/browser runtime mirror the authoritative consent
# queue into a session timeline. ContextVar propagation is preserved when the
# agent moves synchronous tools into ``asyncio.to_thread``.
ApprovalEventObserver = Callable[[str, Mapping[str, Any]], None]
_APPROVAL_EVENT_OBSERVER: ContextVar[ApprovalEventObserver | None] = ContextVar(
    "maverick_computer_use_approval_event_observer",
    default=None,
)

_COMPUTER_PROVIDER_CAPABILITIES: dict[str, dict[str, Any]] = {
    "anthropic": {
        "supported": True,
        "tool_schema": "json_tool",
        "image_result": "anthropic_image_content_block",
        "coordinate_space": "observation_pixels",
    },
    "openai": {
        "supported": False,
        "reason": "current Chat Completions adapter flattens image tool results",
    },
    "gemini": {
        "supported": False,
        "reason": "current OpenAI-compatible adapter has no computer action bridge",
    },
}


def computer_provider_capability(
    provider: str,
    model: str | None = None,
) -> dict[str, Any]:
    """Return the closed capability decision for a selected provider route."""
    from .providers import _canonical

    canonical = _canonical(str(provider or "").strip().lower())
    capability = dict(
        _COMPUTER_PROVIDER_CAPABILITIES.get(
            canonical,
            {
                "supported": False,
                "reason": "provider transport has no verified multimodal tool-result bridge",
            },
        )
    )
    capability.update({"provider": canonical, "model": str(model or "")})
    if canonical == "anthropic":
        from .tools import _supports_anthropic_computer_toolset

        native = _supports_anthropic_computer_toolset(model)
        capability["tool_schema"] = (
            "computer_toolset_20260801" if native else "governed_json_tool"
        )
        capability["native_toolset"] = native
    return capability


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


def _clean_identity(value: str, field_name: str) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 256 or "\x00" in value:
        raise ValueError(f"{field_name} must be a non-empty identity")
    return value


def _clean_id(value: str, field_name: str) -> str:
    value = str(value or "").strip()
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {field_name}")
    return value


def _safe_origin(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(str(value))
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{port}"
    except (TypeError, ValueError):
        return None


def runtime_identity() -> tuple[str, str]:
    """Return the current tenant/principal with isolated local-mode fallbacks."""
    from .connections import current_principal
    from .paths import current_tenant_id_strict

    # Security-sensitive runtime state must not turn an invalid/corrupt tenant
    # authority into local mode.  ``None`` is still the explicit legacy-local
    # case; policy errors propagate.
    tenant_id = current_tenant_id_strict() or LOCAL_TENANT_ID
    principal = current_principal() or LOCAL_PRINCIPAL
    return (str(tenant_id), str(principal))


@contextmanager
def approval_event_scope(
    observer: ApprovalEventObserver | None,
) -> Iterator[None]:
    """Install a non-authoritative consent lifecycle observer for one call."""
    token = _APPROVAL_EVENT_OBSERVER.set(observer)
    try:
        yield
    finally:
        _APPROVAL_EVENT_OBSERVER.reset(token)


def emit_approval_event(kind: str, **payload: Any) -> None:
    """Notify the scoped runtime mirror without affecting consent outcome."""
    observer = _APPROVAL_EVENT_OBSERVER.get()
    if observer is None:
        return
    try:
        observer(str(kind), dict(payload))
    except Exception:
        # The world-model queue and its signed decision remain the authority.
        # A replay/dashboard projection outage cannot approve, deny, or change
        # the action being gated.
        log.warning("computer-use approval mirror failed", exc_info=True)


class SessionState(str, Enum):
    STARTING = "starting"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    TAKEOVER = "takeover"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    RECOVERING = "recovering"
    ORPHANED = "orphaned"


class OutcomeState(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED_PRE_EFFECT = "failed_pre_effect"
    PARTIAL = "partial"
    INDETERMINATE = "indeterminate"
    DENIED = "denied"
    CANCELLED = "cancelled"


class SessionConflict(RuntimeError):
    """The caller acted on a stale revision or invalid state transition."""


class SessionAccessDenied(PermissionError):
    """The requested session does not belong to the supplied identity."""


class SessionStopped(RuntimeError):
    """An action cannot run while the operator owns or stopped the session."""


class RuntimeStateCorrupt(RuntimeError):
    """A runtime record failed integrity/parsing checks and cannot be hidden."""


class EvidenceCustodyError(RuntimeError):
    """Required visual evidence could not be sealed into custody."""


@dataclass(frozen=True)
class ReadinessBlocker:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ComputerReadiness:
    ready: bool
    checked_at: str
    platform: str
    display: str | None
    sandbox_required: bool
    host_allowed: bool
    backend: str
    backend_ready: bool
    execution_cell_enrolled: bool
    approval_ready: bool
    stop_path_ready: bool
    provider: str
    model: str
    image_tool_supported: bool
    visual_evidence_required: bool
    screenshot_custody_ready: bool
    observation_protection: str
    image_content_scanned: bool
    dependencies: dict[str, bool]
    monitors: tuple[dict[str, int], ...] = ()
    blockers: tuple[ReadinessBlocker, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["monitors"] = list(self.monitors)
        result["blockers"] = [item.to_dict() for item in self.blockers]
        result["warnings"] = list(self.warnings)
        return result


@dataclass(frozen=True)
class ComputerSession:
    session_id: str
    tenant_id: str
    principal: str
    surface: str
    goal_id: str | None
    state: SessionState
    revision: int
    created_at: str
    updated_at: str
    heartbeat_at: str
    monitor_id: int | None = None
    active_window: str | None = None
    last_observation_id: str | None = None
    last_outcome_id: str | None = None
    takeover_by: str | None = None
    stop_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["state"] = self.state.value
        return result


@dataclass(frozen=True)
class ComputerObservation:
    observation_id: str
    session_id: str
    captured_at: str
    media_type: str
    sha256: str
    byte_length: int
    width: int
    height: int
    scale_factor: float
    monitor_id: int
    left: int
    top: int
    active_window: str | None
    app: str | None
    origin: str | None
    media_ref: str
    seal_status: str
    seal_reference: str | None
    transmitted_sha256: str | None = None
    transmitted_width: int | None = None
    transmitted_height: int | None = None
    transmission_scale_x: float = 1.0
    transmission_scale_y: float = 1.0
    kind: str = "full"
    coordinate_frame_observation_id: str | None = None
    provider: str | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComputerAction:
    action_id: str
    session_id: str
    action: str
    requested_at: str
    observation_id: str | None
    observation_sha256: str | None
    session_revision: int
    expected_observation_sha256: str | None
    effect_commitment: str
    effect_commitment_salt: str | None
    effect_commitment_version: int
    coordinate: tuple[int, int] | None = None
    target: str | None = None
    text_length: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.coordinate is not None:
            result["coordinate"] = list(self.coordinate)
        return result


@dataclass(frozen=True)
class ComputerActionOutcome:
    outcome_id: str
    session_id: str
    action_id: str
    state: OutcomeState
    completed_at: str
    effect_applied: bool | None
    retry_safe: bool
    message: str
    observation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["state"] = self.state.value
        return result


@dataclass(frozen=True)
class ComputerApproval:
    approval_id: str
    session_id: str
    status: str
    risk: str
    requester: str
    requested_at: str
    expires_at: str
    effect_commitment: str
    effect_commitment_salt: str
    effect_commitment_version: int
    observation_id: str | None
    target_summary: str | None
    authority: str = "world_model"
    authority_approval_id: str | None = None
    claimed_by: str | None = None
    decided_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolResultContent(str):
    """String-compatible result carrying native provider content blocks.

    Existing tools, shields, hooks and audit code continue to see the concise
    textual value.  The agent protocol can additionally deliver ``blocks``
    (notably image blocks) without encoding them into text or applying the text
    truncation limit to their base64 bytes.
    """

    __slots__ = ("blocks",)

    def __new__(
        cls, text: str, *, blocks: tuple[dict[str, Any], ...] | list[dict[str, Any]] = ()
    ) -> "ToolResultContent":
        obj = str.__new__(cls, text)
        obj.blocks = tuple(dict(block) for block in blocks)
        return obj

    def with_text(self, text: str) -> "ToolResultContent":
        return ToolResultContent(text, blocks=self.blocks)

    def to_anthropic_content(self) -> list[dict[str, Any]]:
        return [{"type": "text", "text": str(self)}, *[dict(b) for b in self.blocks]]


def image_block(data: bytes, media_type: str = "image/png") -> dict[str, Any]:
    """Build an Anthropic-native base64 image content block."""
    import base64

    if media_type not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
        raise ValueError(f"unsupported image media type {media_type!r}")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(data).decode("ascii"),
        },
    }


def computer_readiness(
    *, provider: str | None = None, model: str | None = None,
) -> ComputerReadiness:
    """Probe local dependencies and isolation without touching input devices."""
    from .computer_use_settings import load_computer_use_settings

    settings_error: Exception | None = None
    try:
        settings = load_computer_use_settings()
    except Exception as exc:
        settings_error = exc
        settings = None
    dependencies = {
        name: importlib.util.find_spec(name) is not None
        for name in ("pyautogui", "mss", "PIL")
    }
    display = (os.environ.get("DISPLAY") or "").strip() or None
    if not model:
        try:
            from .config import get_role_model
            from .llm import DEFAULT_MODEL

            model = get_role_model("orchestrator") or DEFAULT_MODEL
        except Exception:
            model = ""
    model = str(model or "").strip()
    if provider:
        provider = str(provider).strip().lower()
    else:
        provider = model.split(":", 1)[0].lower() if ":" in model else "anthropic"
    capability = computer_provider_capability(provider, model)
    provider = str(capability["provider"])
    image_tool_supported = bool(capability["supported"])
    visual_evidence_required = bool(settings and settings.require_evidence)
    try:
        from .screenshot_seal import _key as _screenshot_key

        _screenshot_key(None)
        screenshot_custody_ready = True
    except Exception:
        screenshot_custody_ready = False
    ocr_requested = (
        os.environ.get("MAVERICK_COMPUTER_OCR", "").strip().lower() in _TRUTHY
    )
    ocr_ready = bool(ocr_requested and shutil.which("tesseract"))
    observation_protection = "ocr_text_and_metadata" if ocr_ready else "metadata_only"
    image_content_scanned = False
    sandbox_required = settings.require_sandbox if settings else True
    allow_host = settings.allow_host if settings else False
    configured = (os.environ.get("MAVERICK_COMPUTER_DISPLAY") or "").strip()
    system = platform.system().lower()
    # DISPLAY is meaningful evidence only on the X11/Linux route. Windows and
    # macOS host control needs an explicit host override. A remote execution
    # cell proves isolation over its authenticated health route and never uses
    # this local host-drive decision.
    isolated_display = bool(
        system == "linux"
        and display and configured and display == configured
        and display not in {":0", ":0.0"}
    )
    host_allowed = allow_host or isolated_display
    blockers: list[ReadinessBlocker] = []
    warnings: list[str] = []
    backend = settings.mode if settings else "invalid"
    execution_cell_enrolled = bool(settings and settings.execution_cell_enrolled)
    backend_ready = False
    stop_path_ready = False
    remote_status: dict[str, Any] = {}
    if settings_error is not None:
        blockers.append(ReadinessBlocker(
            "configuration_untrusted",
            f"Computer-use configuration could not be authenticated: {type(settings_error).__name__}",
        ))
    if backend == "host":
        for dep, present in dependencies.items():
            if not present:
                blockers.append(ReadinessBlocker(
                    "dependency_missing", f"{dep} is not installed"
                ))
        backend_ready = all(dependencies.values()) and host_allowed
        if sandbox_required and not host_allowed:
            blockers.append(ReadinessBlocker(
                "execution_cell_unverified",
                "No isolated display is verified; host control requires an explicit operator override",
            ))
    elif backend == "remote":
        try:
            from .computer_use_backend import RemoteComputerBackend

            if settings is None:
                raise RuntimeError("settings unavailable")
            remote = RemoteComputerBackend(
                settings.execution_cell_url,
                settings.execution_cell_token,
                settings.execution_cell_id,
            )
            remote_status = remote.readiness()
            backend_ready = True
            stop_path_ready = remote_status.get("kill_switch_ready") is True
        except Exception as exc:
            blockers.append(ReadinessBlocker(
                "remote_backend_unavailable",
                f"Remote execution cell is not ready: {type(exc).__name__}",
            ))
    else:
        blockers.append(ReadinessBlocker(
            "backend_invalid", "Computer backend selection is invalid"
        ))
    if backend == "host":
        stop_path_ready = bool(dependencies.get("pyautogui") and host_allowed)
    if not stop_path_ready:
        blockers.append(ReadinessBlocker(
            "stop_path_unverified",
            "The selected backend did not prove an interruptible stop path",
        ))
    if not image_tool_supported:
        blockers.append(ReadinessBlocker(
            "provider_image_tool_unsupported",
            f"Provider {provider!r} does not support Lightwork computer-use image results",
        ))
    if visual_evidence_required and not screenshot_custody_ready:
        blockers.append(ReadinessBlocker(
            "screenshot_custody_key_missing",
            "Visual evidence is required but no screenshot sealing key is configured",
        ))
    if not ocr_ready:
        warnings.append(
            "Screenshot pixels are not scanned for prompt injection; deterministic consent remains required"
        )
    if allow_host:
        warnings.append("Computer use is explicitly allowed to control the operator host")

    approval_ready = False
    consent_mode = settings.consent_mode if settings is not None else "invalid"
    if consent_mode == "dashboard":
        try:
            from .world_model import close_world_if_owned, open_world

            world = open_world()
            try:
                world.pending_approvals()
            finally:
                close_world_if_owned(world)
            approval_ready = True
        except Exception as exc:
            blockers.append(ReadinessBlocker(
                "approval_queue_unavailable",
                f"Dashboard approval queue is unavailable: {type(exc).__name__}",
            ))
    elif consent_mode == "ask":
        approval_ready = bool(sys.stdin.isatty())
        if not approval_ready:
            blockers.append(ReadinessBlocker(
                "approval_prompt_unavailable",
                "Consent mode 'ask' needs an interactive terminal",
            ))
    else:
        blockers.append(ReadinessBlocker(
            "approval_mode_invalid",
            "Computer use requires consent_mode 'dashboard' or 'ask'",
        ))

    monitors: list[dict[str, int]] = []
    if backend == "host" and dependencies["mss"]:
        try:
            from .multi_monitor import list_monitors

            monitors = [asdict(m) for m in list_monitors()]
            if not monitors:
                blockers.append(ReadinessBlocker("display_missing", "No monitor is available"))
        except Exception as exc:
            blockers.append(ReadinessBlocker(
                "display_unavailable", f"Monitor enumeration failed: {type(exc).__name__}"
            ))
    return ComputerReadiness(
        ready=not blockers,
        checked_at=_now(),
        platform=system,
        display=display,
        sandbox_required=sandbox_required,
        host_allowed=host_allowed,
        backend=backend,
        backend_ready=backend_ready,
        execution_cell_enrolled=execution_cell_enrolled,
        approval_ready=approval_ready,
        stop_path_ready=stop_path_ready,
        provider=provider,
        model=model,
        image_tool_supported=image_tool_supported,
        visual_evidence_required=visual_evidence_required,
        screenshot_custody_ready=screenshot_custody_ready,
        observation_protection=observation_protection,
        image_content_scanned=image_content_scanned,
        dependencies=dependencies,
        monitors=tuple(monitors),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


class ComputerRuntimeStore:
    """Private, revisioned runtime store for one or more tenant sessions."""

    def __init__(self, root: str | Path | None = None):
        self._root_override = Path(root) if root is not None else None

    def readiness(
        self, *, provider: str | None = None, model: str | None = None,
    ) -> ComputerReadiness:
        return computer_readiness(provider=provider, model=model)

    def _tenant_root(self, tenant_id: str) -> Path:
        tenant_id = _clean_identity(tenant_id, "tenant_id")
        if self._root_override is None:
            root = data_dir("computer-use-runtime", tenant=tenant_id)
        else:
            # A digest keeps caller-controlled tenant strings out of path names
            # while retaining strict separation in injected/test stores.
            segment = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
            root = self._root_override / "tenants" / segment
        return ensure_private_directory(root)

    def _state_path(self, tenant_id: str, session_id: str) -> Path:
        return self._tenant_root(tenant_id) / "sessions" / f"{_clean_id(session_id, 'session_id')}.json"

    @staticmethod
    def _session_from(raw: dict[str, Any]) -> ComputerSession:
        return ComputerSession(
            **{**raw, "state": SessionState(raw["state"])}
        )

    def _load(self, tenant_id: str, session_id: str) -> dict[str, Any]:
        path = self._state_path(tenant_id, session_id)
        try:
            ensure_private_file(path)
            state = json.loads(atomic_read_text(path))
        except FileNotFoundError as exc:
            raise KeyError(f"unknown computer-use session {session_id!r}") from exc
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValueError("computer-use session state is invalid")
        return state

    @staticmethod
    def _authorize(
        state: dict[str, Any], tenant_id: str, principal: str | None
    ) -> None:
        session = state.get("session") or {}
        if session.get("tenant_id") != tenant_id:
            raise SessionAccessDenied("computer-use session tenant mismatch")
        if principal is not None and session.get("principal") != principal:
            raise SessionAccessDenied("computer-use session principal mismatch")

    @staticmethod
    def _append_event(
        state: dict[str, Any], kind: str, payload: dict[str, Any]
    ) -> int:
        session = state["session"]
        revision = int(session.get("revision", 0)) + 1
        now = _now()
        session["revision"] = revision
        session["updated_at"] = now
        event = {"revision": revision, "at": now, "kind": kind, "payload": payload}
        events = state.setdefault("events", [])
        events.append(event)
        if len(events) > _MAX_EVENTS:
            del events[:-_MAX_EVENTS]
        return revision

    @staticmethod
    def _write(path: Path, state: dict[str, Any]) -> None:
        atomic_write_text(path, json.dumps(state, sort_keys=True, separators=(",", ":")))

    def create_session(
        self,
        tenant_id: str,
        principal: str,
        *,
        goal_id: str | None = None,
        surface: str = "desktop",
        monitor_id: int | None = None,
        session_id: str | None = None,
    ) -> ComputerSession:
        from .computer_use_settings import load_computer_use_settings

        tenant_id = _clean_identity(tenant_id, "tenant_id")
        principal = _clean_identity(principal, "principal")
        surface = str(surface).strip().lower()
        if surface not in {"desktop", "browser"}:
            raise ValueError("surface must be 'desktop' or 'browser'")
        session_id = _clean_id(session_id, "session_id") if session_id else _new_id("cus")
        if monitor_id is not None and (isinstance(monitor_id, bool) or int(monitor_id) < 1):
            raise ValueError("monitor_id must be a positive integer")
        path = self._state_path(tenant_id, session_id)
        ensure_private_directory(path.parent)
        settings = load_computer_use_settings()
        admission = path.parent / ".admission"
        # Tenant-wide lock makes max_sessions a real admission control rather
        # than a racy UI hint when several workers start concurrently.
        with cross_process_lock(admission, strict=True):
            active = [
                item
                for item in self.list_sessions(tenant_id)
                if item.state
                not in {SessionState.STOPPED, SessionState.ERROR, SessionState.ORPHANED}
            ]
            if len(active) >= settings.max_sessions:
                raise SessionConflict(
                    f"computer-use max_sessions limit ({settings.max_sessions}) reached"
                )
            with cross_process_lock(path, strict=True):
                if path.exists():
                    raise SessionConflict(
                        f"computer-use session {session_id!r} already exists"
                    )
                now = _now()
                session = ComputerSession(
                    session_id=session_id,
                    tenant_id=tenant_id,
                    principal=principal,
                    surface=surface,
                    goal_id=str(goal_id) if goal_id is not None else None,
                    state=SessionState.READY,
                    revision=1,
                    created_at=now,
                    updated_at=now,
                    heartbeat_at=now,
                    monitor_id=int(monitor_id) if monitor_id is not None else None,
                )
                state = {
                    "version": 1,
                    "session": session.to_dict(),
                    "observations": {},
                    "actions": {},
                    "outcomes": {},
                    "approvals": {},
                    "events": [{
                        "revision": 1,
                        "at": now,
                        "kind": "session.created",
                        "payload": {"state": SessionState.READY.value},
                    }],
                }
                self._write(path, state)
        return session

    def get_session(
        self, session_id: str, tenant_id: str, principal: str | None = None
    ) -> ComputerSession:
        tenant_id = _clean_identity(tenant_id, "tenant_id")
        state = self._load(tenant_id, session_id)
        self._authorize(state, tenant_id, principal)
        return self._session_from(state["session"])

    def list_sessions(
        self, tenant_id: str, principal: str | None = None
    ) -> list[ComputerSession]:
        tenant_id = _clean_identity(tenant_id, "tenant_id")
        sessions_dir = self._tenant_root(tenant_id) / "sessions"
        if not sessions_dir.exists():
            return []
        result: list[ComputerSession] = []
        for path in sessions_dir.glob("*.json"):
            try:
                state = self._load(tenant_id, path.stem)
                self._authorize(state, tenant_id, principal)
                result.append(self._session_from(state["session"]))
            except (KeyError, FileNotFoundError):
                # A concurrent retention purge may remove an entry after glob.
                continue
            except SessionAccessDenied:
                continue
            except Exception as exc:
                raise RuntimeStateCorrupt(
                    f"computer-use session record {path.name!r} is unreadable"
                ) from exc
        return sorted(result, key=lambda item: item.updated_at, reverse=True)

    def list_events(
        self,
        session_id: str,
        tenant_id: str,
        principal: str | None = None,
        *,
        after_revision: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        if after_revision < 0:
            raise ValueError("after_revision must be non-negative")
        limit = max(1, min(int(limit), 1_000))
        state = self._load(tenant_id, session_id)
        self._authorize(state, tenant_id, principal)
        events = [
            item for item in state.get("events", [])
            if int(item.get("revision", 0)) > after_revision
        ][:limit]
        revision = int(state["session"]["revision"])
        next_cursor = int(events[-1]["revision"]) if events else after_revision
        return {
            "session_id": session_id,
            "revision": revision,
            "next_cursor": next_cursor,
            "events": events,
        }

    def _mutate(
        self,
        session_id: str,
        tenant_id: str,
        principal: str | None,
        mutation,
        *,
        expected_revision: int | None = None,
    ) -> tuple[dict[str, Any], Any]:
        path = self._state_path(tenant_id, session_id)
        with cross_process_lock(path, strict=True):
            state = self._load(tenant_id, session_id)
            self._authorize(state, tenant_id, principal)
            actual = int(state["session"]["revision"])
            if expected_revision is not None and actual != int(expected_revision):
                raise SessionConflict(
                    f"stale computer-use session revision {expected_revision}; current is {actual}"
                )
            value = mutation(state)
            self._write(path, state)
            return state, value

    def _transition(
        self,
        session_id: str,
        tenant_id: str,
        principal: str | None,
        *,
        allowed: set[SessionState],
        target: SessionState,
        kind: str,
        payload: dict[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> ComputerSession:
        def mutation(state: dict[str, Any]) -> None:
            current = SessionState(state["session"]["state"])
            if current not in allowed:
                raise SessionConflict(f"cannot {kind} a session in state {current.value}")
            state["session"]["state"] = target.value
            _ = self._append_event(state, kind, payload or {"state": target.value})

        state, _ = self._mutate(
            session_id, tenant_id, principal, mutation,
            expected_revision=expected_revision,
        )
        return self._session_from(state["session"])

    def heartbeat(
        self, session_id: str, tenant_id: str, principal: str | None = None
    ) -> ComputerSession:
        def mutation(state: dict[str, Any]) -> None:
            now = _now()
            state["session"]["heartbeat_at"] = now
            self._append_event(state, "session.heartbeat", {})

        state, _ = self._mutate(session_id, tenant_id, principal, mutation)
        return self._session_from(state["session"])

    def pause(self, session_id: str, tenant_id: str, principal: str | None = None, *, expected_revision: int | None = None) -> ComputerSession:
        return self._transition(session_id, tenant_id, principal, allowed={SessionState.READY, SessionState.RUNNING}, target=SessionState.PAUSED, kind="session.paused", expected_revision=expected_revision)

    def resume(self, session_id: str, tenant_id: str, principal: str | None = None, *, expected_revision: int | None = None) -> ComputerSession:
        return self._transition(session_id, tenant_id, principal, allowed={SessionState.PAUSED}, target=SessionState.RUNNING, kind="session.resumed", expected_revision=expected_revision)

    def takeover(self, session_id: str, tenant_id: str, principal: str | None = None, *, operator: str, expected_revision: int | None = None) -> ComputerSession:
        operator = _clean_identity(operator, "operator")

        def mutation(state: dict[str, Any]) -> None:
            current = SessionState(state["session"]["state"])
            if current not in {SessionState.READY, SessionState.RUNNING, SessionState.PAUSED}:
                raise SessionConflict(f"cannot take over a session in state {current.value}")
            state["session"]["state"] = SessionState.TAKEOVER.value
            state["session"]["takeover_by"] = operator
            self._append_event(state, "session.takeover", {"operator": operator})

        state, _ = self._mutate(session_id, tenant_id, principal, mutation, expected_revision=expected_revision)
        return self._session_from(state["session"])

    def release_takeover(self, session_id: str, tenant_id: str, principal: str | None = None, *, operator: str, expected_revision: int | None = None) -> ComputerSession:
        operator = _clean_identity(operator, "operator")

        def mutation(state: dict[str, Any]) -> None:
            session = state["session"]
            if SessionState(session["state"]) is not SessionState.TAKEOVER:
                raise SessionConflict("session is not in takeover")
            if session.get("takeover_by") != operator:
                raise SessionAccessDenied("only the takeover owner may release it")
            session["state"] = SessionState.PAUSED.value
            session["takeover_by"] = None
            self._append_event(state, "session.takeover_released", {"operator": operator})

        state, _ = self._mutate(session_id, tenant_id, principal, mutation, expected_revision=expected_revision)
        return self._session_from(state["session"])

    def stop(self, session_id: str, tenant_id: str, principal: str | None = None, *, reason: str = "operator requested", expected_revision: int | None = None) -> ComputerSession:
        reason = " ".join(str(reason).split())[:500] or "operator requested"

        def mutation(state: dict[str, Any]) -> None:
            session = state["session"]
            if SessionState(session["state"]) is SessionState.STOPPED:
                return
            session["state"] = SessionState.STOPPED.value
            session["stop_reason"] = reason
            self._append_event(state, "session.stopped", {"reason": reason})

        state, _ = self._mutate(session_id, tenant_id, principal, mutation, expected_revision=expected_revision)
        return self._session_from(state["session"])

    def mark_orphaned(
        self,
        session_id: str,
        tenant_id: str,
        principal: str | None = None,
        *,
        reason: str = "runtime heartbeat expired",
        expected_revision: int | None = None,
    ) -> ComputerSession:
        reason = " ".join(str(reason).split())[:500]
        return self._transition(
            session_id, tenant_id, principal,
            allowed={
                SessionState.STARTING, SessionState.READY,
                SessionState.RUNNING, SessionState.PAUSED,
            },
            target=SessionState.ORPHANED,
            kind="session.orphaned",
            payload={"reason": reason},
            expected_revision=expected_revision,
        )

    def recover(
        self,
        session_id: str,
        tenant_id: str,
        principal: str | None = None,
        *,
        expected_revision: int | None = None,
    ) -> ComputerSession:
        return self._transition(
            session_id, tenant_id, principal,
            allowed={SessionState.ORPHANED, SessionState.ERROR},
            target=SessionState.RECOVERING,
            kind="session.recovering",
            expected_revision=expected_revision,
        )

    def complete_recovery(
        self,
        session_id: str,
        tenant_id: str,
        principal: str | None = None,
        *,
        expected_revision: int | None = None,
    ) -> ComputerSession:
        # Recovery never resumes autonomy implicitly; the operator gets a
        # paused, inspectable session and chooses when to resume.
        return self._transition(
            session_id, tenant_id, principal,
            allowed={SessionState.RECOVERING},
            target=SessionState.PAUSED,
            kind="session.recovered",
            expected_revision=expected_revision,
        )

    def sweep_orphaned(
        self,
        tenant_id: str,
        principal: str | None = None,
        *,
        stale_after_seconds: float | None = None,
    ) -> list[ComputerSession]:
        if stale_after_seconds is None:
            from .computer_use_settings import load_computer_use_settings

            stale_after_seconds = load_computer_use_settings().heartbeat_timeout
        threshold = max(1.0, float(stale_after_seconds))
        now = datetime.now(timezone.utc)
        changed: list[ComputerSession] = []
        for session in self.list_sessions(tenant_id, principal):
            if session.state not in {
                SessionState.STARTING, SessionState.READY,
                SessionState.RUNNING, SessionState.PAUSED,
            }:
                continue
            heartbeat = datetime.fromisoformat(
                session.heartbeat_at.replace("Z", "+00:00")
            )
            if (now - heartbeat).total_seconds() <= threshold:
                continue
            try:
                changed.append(self.mark_orphaned(
                    session.session_id, tenant_id, principal,
                    expected_revision=session.revision,
                ))
            except SessionConflict:
                # A concurrent heartbeat won; the session remains live.
                continue
        return changed

    def cleanup_orphaned(
        self,
        tenant_id: str,
        principal: str | None = None,
        *,
        older_than_seconds: float | None = None,
    ) -> list[ComputerSession]:
        """Conservatively stop old orphans; retain their replay/evidence."""
        if older_than_seconds is None:
            from .computer_use_settings import load_computer_use_settings

            older_than_seconds = load_computer_use_settings().session_timeout
        threshold = max(1.0, float(older_than_seconds))
        now = datetime.now(timezone.utc)
        changed: list[ComputerSession] = []
        for session in self.list_sessions(tenant_id, principal):
            if session.state is not SessionState.ORPHANED:
                continue
            updated = datetime.fromisoformat(
                session.updated_at.replace("Z", "+00:00")
            )
            if (now - updated).total_seconds() <= threshold:
                continue
            try:
                changed.append(self.stop(
                    session.session_id, tenant_id, principal,
                    reason="orphan cleanup; replay retained",
                    expected_revision=session.revision,
                ))
            except SessionConflict:
                continue
        return changed

    def expire_sessions(
        self,
        tenant_id: str,
        principal: str | None = None,
        *,
        lifetime_seconds: float | None = None,
    ) -> list[ComputerSession]:
        """Stop sessions which exceed the configured hard runtime lifetime."""
        if lifetime_seconds is None:
            from .computer_use_settings import load_computer_use_settings

            lifetime_seconds = load_computer_use_settings().session_timeout
        threshold = max(1.0, float(lifetime_seconds))
        now = datetime.now(timezone.utc)
        changed: list[ComputerSession] = []
        for session in self.list_sessions(tenant_id, principal):
            if session.state in {SessionState.STOPPED, SessionState.ERROR}:
                continue
            created = datetime.fromisoformat(
                session.created_at.replace("Z", "+00:00")
            )
            if (now - created).total_seconds() <= threshold:
                continue
            try:
                changed.append(self.stop(
                    session.session_id,
                    tenant_id,
                    principal,
                    reason="configured session lifetime expired",
                    expected_revision=session.revision,
                ))
            except SessionConflict:
                continue
        return changed

    def purge_expired_evidence(
        self,
        tenant_id: str,
        principal: str | None = None,
        *,
        retention_days: int | None = None,
    ) -> list[str]:
        """Purge terminal session records only after the custody retention window."""
        if retention_days is None:
            from .computer_use_settings import load_computer_use_settings

            retention_days = load_computer_use_settings().evidence_retention_days
        threshold = max(1, int(retention_days)) * 86_400
        now = datetime.now(timezone.utc)
        removed: list[str] = []
        for session in self.list_sessions(tenant_id, principal):
            if session.state not in {SessionState.STOPPED, SessionState.ERROR}:
                continue
            updated = datetime.fromisoformat(
                session.updated_at.replace("Z", "+00:00")
            )
            if (now - updated).total_seconds() <= threshold:
                continue
            path = self._state_path(tenant_id, session.session_id)
            with cross_process_lock(path, strict=True):
                state = self._load(tenant_id, session.session_id)
                self._authorize(state, tenant_id, principal)
                current = self._session_from(state["session"])
                if (
                    current.revision != session.revision
                    or current.state not in {SessionState.STOPPED, SessionState.ERROR}
                ):
                    continue
                root = self._tenant_root(tenant_id).resolve()
                frames = (
                    root / "session_frames" / session.session_id
                ).resolve()
                expected_parent = (root / "session_frames").resolve()
                if frames.parent != expected_parent:
                    raise PermissionError("session frame directory escaped tenant store")
                refs = {
                    str(raw.get("media_ref") or "")
                    for raw in state.get("observations", {}).values()
                }
                if any(
                    not ref.startswith(f"session_frames/{session.session_id}/")
                    for ref in refs
                ):
                    # Legacy shared-ledger evidence cannot be independently
                    # purged without invalidating other sessions. Retain it.
                    continue
                observation_names = {
                    f"{_clean_id(str(raw.get('observation_id') or ''), 'observation_id')}.png"
                    for raw in state.get("observations", {}).values()
                }
                allowed_names = observation_names | {
                    ".seals.jsonl", ".seals.jsonl.lock",
                }
                if frames.exists() and any(
                    item.name not in allowed_names or not item.is_file()
                    for item in frames.iterdir()
                ):
                    # Unknown retained content must remain discoverable; do not
                    # partially purge the evidence unit.
                    continue
                for raw in state.get("observations", {}).values():
                    observation_id = _clean_id(
                        str(raw.get("observation_id") or ""), "observation_id"
                    )
                    frame = (frames / f"{observation_id}.png").resolve()
                    if frame.parent != frames:
                        raise PermissionError("retained frame escaped tenant store")
                    try:
                        frame.unlink()
                    except FileNotFoundError:
                        pass
                # This ledger belongs only to the terminal session; removing
                # it cannot alter verification for another session's frames.
                for name in (".seals.jsonl", ".seals.jsonl.lock"):
                    ledger_file = (frames / name).resolve()
                    if ledger_file.parent != frames:
                        raise PermissionError("seal ledger escaped session store")
                    try:
                        ledger_file.unlink()
                    except FileNotFoundError:
                        pass
                try:
                    frames.rmdir()
                except FileNotFoundError:
                    pass
                except OSError:
                    # Unknown retained content fails closed: do not delete the
                    # session index which makes it discoverable for recovery.
                    continue
                path.unlink()
                removed.append(session.session_id)
        return removed

    def maintain(
        self,
        tenant_id: str,
        principal: str | None = None,
    ) -> dict[str, list[Any]]:
        """Run the bounded lifecycle/retention maintenance hooks once."""
        orphaned = self.sweep_orphaned(tenant_id, principal)
        expired = self.expire_sessions(tenant_id, principal)
        stopped = self.cleanup_orphaned(tenant_id, principal)
        purged = self.purge_expired_evidence(tenant_id, principal)
        return {
            "orphaned": orphaned,
            "expired": expired,
            "stopped": stopped,
            "purged": purged,
        }

    def assert_action_allowed(self, session_id: str, tenant_id: str, principal: str) -> ComputerSession:
        session = self.get_session(session_id, tenant_id, principal)
        from .computer_use_settings import load_computer_use_settings

        created = datetime.fromisoformat(session.created_at.replace("Z", "+00:00"))
        if (
            datetime.now(timezone.utc) - created
        ).total_seconds() > load_computer_use_settings().session_timeout:
            self.stop(
                session_id,
                tenant_id,
                principal,
                reason="configured session lifetime expired",
                expected_revision=session.revision,
            )
            raise SessionStopped("computer-use session lifetime expired")
        if session.state in {
            SessionState.PAUSED, SessionState.TAKEOVER,
            SessionState.STOPPING, SessionState.STOPPED,
            SessionState.ERROR, SessionState.RECOVERING,
            SessionState.ORPHANED,
        }:
            raise SessionStopped(f"computer-use session is {session.state.value}")
        return session

    def record_observation(
        self,
        session_id: str,
        tenant_id: str,
        principal: str,
        *,
        image: bytes,
        width: int,
        height: int,
        scale_factor: float,
        monitor_id: int,
        left: int,
        top: int,
        active_window: str | None,
        app: str | None = None,
        origin: str | None = None,
        media_type: str = "image/png",
        transmitted_sha256: str | None = None,
        transmitted_width: int | None = None,
        transmitted_height: int | None = None,
        transmission_scale_x: float = 1.0,
        transmission_scale_y: float = 1.0,
        kind: str = "full",
        coordinate_frame_observation_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        promote_latest: bool = True,
    ) -> ComputerObservation:
        if not image or width < 1 or height < 1 or scale_factor <= 0:
            raise ValueError("observation image and positive geometry are required")
        if media_type != "image/png":
            raise ValueError("runtime persistence currently accepts image/png only")
        kind = str(kind).strip().lower()
        if kind not in {"full", "zoom"}:
            raise ValueError("observation kind must be full or zoom")
        tx_width = int(transmitted_width or width)
        tx_height = int(transmitted_height or height)
        if (
            tx_width < 1
            or tx_height < 1
            or not 0 < float(transmission_scale_x) <= 1
            or not 0 < float(transmission_scale_y) <= 1
        ):
            raise ValueError("invalid observation transmission geometry")
        observation_id = _new_id("obs")
        digest = hashlib.sha256(image).hexdigest()
        captured_at = _now()
        # One seal ledger per session. Retention can then remove an entire
        # terminal session's evidence unit without breaking custody validation
        # for every other live/retained frame in the tenant.
        media_ref = f"session_frames/{session_id}/{observation_id}.png"
        observation: ComputerObservation | None = None

        def mutation(state: dict[str, Any]) -> None:
            nonlocal observation
            root = self._tenant_root(tenant_id)
            frames = ensure_private_directory(root / "session_frames" / session_id)
            frame_path = frames / f"{observation_id}.png"
            atomic_write_bytes(frame_path, image)
            seal_status = "unsealed"
            seal_reference = None
            try:
                from .screenshot_seal import SealKeyMissing, seal

                try:
                    entry = seal(frame_path)
                except SealKeyMissing:
                    pass
                else:
                    seal_status = "sealed"
                    seal_reference = (
                        f"session_frames/{session_id}/.seals.jsonl#{entry.file}"
                    )
            except Exception:
                # The state says exactly that custody failed; it never upgrades
                # an integrity-only frame into evidence.
                seal_status = "seal_error"
            observation = ComputerObservation(
                observation_id=observation_id,
                session_id=session_id,
                captured_at=captured_at,
                media_type=media_type,
                sha256=digest,
                byte_length=len(image),
                width=int(width),
                height=int(height),
                scale_factor=float(scale_factor),
                monitor_id=int(monitor_id),
                left=int(left),
                top=int(top),
                active_window=(str(active_window)[:500] if active_window else None),
                app=(str(app)[:200] if app else None),
                origin=_safe_origin(origin),
                media_ref=media_ref,
                seal_status=seal_status,
                seal_reference=seal_reference,
                transmitted_sha256=(
                    str(transmitted_sha256)
                    if transmitted_sha256 is not None else digest
                ),
                transmitted_width=tx_width,
                transmitted_height=tx_height,
                transmission_scale_x=float(transmission_scale_x),
                transmission_scale_y=float(transmission_scale_y),
                kind=kind,
                coordinate_frame_observation_id=coordinate_frame_observation_id,
                provider=(str(provider)[:80] if provider else None),
                model=(str(model)[:200] if model else None),
            )
            state["observations"][observation_id] = observation.to_dict()
            session = state["session"]
            if promote_latest:
                session["last_observation_id"] = observation_id
            session["active_window"] = observation.active_window
            session["monitor_id"] = observation.monitor_id
            if SessionState(session["state"]) is SessionState.READY:
                session["state"] = SessionState.RUNNING.value
            self._append_event(state, "observation.captured", observation.to_dict())

        self._mutate(session_id, tenant_id, principal, mutation)
        assert observation is not None
        from .computer_use_settings import load_computer_use_settings

        if (
            load_computer_use_settings().require_evidence
            and observation.seal_status != "sealed"
        ):
            raise EvidenceCustodyError(
                "visual evidence is required but screenshot custody failed"
            )
        return observation

    def get_observation(self, session_id: str, observation_id: str, tenant_id: str, principal: str | None = None) -> ComputerObservation:
        observation_id = _clean_id(observation_id, "observation_id")
        state = self._load(tenant_id, session_id)
        self._authorize(state, tenant_id, principal)
        try:
            raw = state["observations"][observation_id]
        except KeyError as exc:
            raise KeyError(f"unknown observation {observation_id!r}") from exc
        return ComputerObservation(**raw)

    def read_observation_media(self, session_id: str, observation_id: str, tenant_id: str, principal: str | None = None) -> tuple[bytes, str, str]:
        observation = self.get_observation(session_id, observation_id, tenant_id, principal)
        root = self._tenant_root(tenant_id).resolve()
        expected_ref = (
            f"session_frames/{session_id}/{observation.observation_id}.png"
        )
        # Read legacy pre-session-ledger frames, but new writes always use the
        # isolated layout and retention never purges the shared legacy ledger.
        legacy_ref = f"frames/{observation.observation_id}.png"
        if observation.media_ref == expected_ref:
            expected_parent = (root / "session_frames" / session_id).resolve()
        elif observation.media_ref == legacy_ref:
            expected_parent = (root / "frames").resolve()
        else:
            raise PermissionError("observation media reference escaped its session store")
        expected = (expected_parent / f"{observation.observation_id}.png").resolve()
        if expected.parent != expected_parent:
            raise PermissionError("observation media reference escaped its session store")
        ensure_private_file(expected)
        payload = atomic_read_bytes(expected)
        digest = hashlib.sha256(payload).hexdigest()
        if digest != observation.sha256 or len(payload) != observation.byte_length:
            raise ValueError("observation media failed integrity verification")
        if observation.seal_status == "sealed":
            from .screenshot_seal import verify_file

            if verify_file(expected) != "VALID":
                raise ValueError("observation media failed custody verification")
        return payload, observation.media_type, digest

    def validate_observation(self, session_id: str, observation_id: str, tenant_id: str, principal: str, *, max_age_seconds: float = 30.0) -> ComputerObservation:
        observation = self.get_observation(session_id, observation_id, tenant_id, principal)
        if self.get_session(session_id, tenant_id, principal).last_observation_id != observation_id:
            raise SessionConflict("action is not bound to the latest observation")
        captured = datetime.fromisoformat(observation.captured_at.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - captured).total_seconds()
        if age > max(0.1, float(max_age_seconds)):
            raise SessionConflict(f"observation is stale ({age:.1f}s old)")
        return observation

    def record_action(
        self,
        session_id: str,
        tenant_id: str,
        principal: str,
        *,
        action: str,
        observation_id: str | None = None,
        expected_observation_sha256: str | None = None,
        coordinate: tuple[int, int] | None = None,
        target: str | None = None,
        text_length: int | None = None,
        effect_commitment: str | None = None,
        effect_commitment_salt: str | None = None,
        effect_commitment_version: int = 1,
    ) -> ComputerAction:
        record: ComputerAction | None = None

        def mutation(state: dict[str, Any]) -> None:
            nonlocal record
            observation_sha256 = None
            if observation_id:
                try:
                    observation_sha256 = state["observations"][observation_id]["sha256"]
                except KeyError as exc:
                    raise KeyError(f"unknown observation {observation_id!r}") from exc
            if (
                expected_observation_sha256 is not None
                and observation_sha256 != expected_observation_sha256
            ):
                raise SessionConflict("expected observation digest does not match")
            session_revision = int(state["session"]["revision"])
            commitment_payload = {
                "action": str(action),
                "coordinate": list(coordinate) if coordinate is not None else None,
                "observation_id": observation_id,
                "observation_sha256": observation_sha256,
                "session_id": session_id,
                "session_revision": session_revision,
                "target": str(target)[:500] if target else None,
                "text_length": text_length,
            }
            exact_commitment = str(effect_commitment or "").strip().lower()
            if exact_commitment and not re.fullmatch(r"[0-9a-f]{64}", exact_commitment):
                raise ValueError("effect_commitment must be a SHA-256 digest")
            salt = str(effect_commitment_salt or "").strip().lower() or None
            if salt is not None and not re.fullmatch(r"[0-9a-f]{32,128}", salt):
                raise ValueError("effect_commitment_salt must be hexadecimal")
            if not exact_commitment:
                # Compatibility fallback binds metadata but cannot distinguish
                # same-length secret text. Live computer/browser adapters must
                # pass the exact salted approval commitment.
                exact_commitment = hashlib.sha256(
                    json.dumps(
                        commitment_payload, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")
                ).hexdigest()
            record = ComputerAction(
                action_id=_new_id("act"), session_id=session_id,
                action=str(action), requested_at=_now(),
                observation_id=observation_id,
                observation_sha256=observation_sha256,
                session_revision=session_revision,
                expected_observation_sha256=expected_observation_sha256,
                effect_commitment=exact_commitment,
                effect_commitment_salt=salt,
                effect_commitment_version=int(effect_commitment_version),
                coordinate=coordinate,
                target=(str(target)[:500] if target else None),
                text_length=text_length,
            )
            state["actions"][record.action_id] = record.to_dict()
            if SessionState(state["session"]["state"]) is SessionState.READY:
                state["session"]["state"] = SessionState.RUNNING.value
            self._append_event(state, "action.requested", record.to_dict())

        self._mutate(session_id, tenant_id, principal, mutation)
        assert record is not None
        return record

    def record_outcome(self, session_id: str, tenant_id: str, principal: str, *, action_id: str, state: OutcomeState, effect_applied: bool | None, retry_safe: bool, message: str, observation_id: str | None = None) -> ComputerActionOutcome:
        outcome = ComputerActionOutcome(
            outcome_id=_new_id("out"), session_id=session_id,
            action_id=_clean_id(action_id, "action_id"), state=OutcomeState(state),
            completed_at=_now(), effect_applied=effect_applied,
            retry_safe=bool(retry_safe), message=" ".join(str(message).split())[:1_000],
            observation_id=observation_id,
        )

        def mutation(raw: dict[str, Any]) -> None:
            if outcome.action_id not in raw["actions"]:
                raise KeyError(f"unknown action {outcome.action_id!r}")
            raw["outcomes"][outcome.outcome_id] = outcome.to_dict()
            raw["session"]["last_outcome_id"] = outcome.outcome_id
            self._append_event(raw, "action.outcome", outcome.to_dict())

        self._mutate(session_id, tenant_id, principal, mutation)
        return outcome

    def record_approval_requested(
        self,
        session_id: str,
        tenant_id: str,
        principal: str,
        *,
        risk: str,
        requester: str,
        expires_at: str,
        effect_commitment: str,
        effect_commitment_salt: str,
        effect_commitment_version: int = 1,
        observation_id: str | None = None,
        target_summary: str | None = None,
        approval_id: str | None = None,
        authority: str = "world_model",
        authority_approval_id: str | None = None,
    ) -> ComputerApproval:
        risk = str(risk).strip().lower()
        if risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("invalid approval risk")
        commitment = str(effect_commitment).strip().lower()
        salt = str(effect_commitment_salt).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", commitment):
            raise ValueError("invalid approval effect commitment")
        if not re.fullmatch(r"[0-9a-f]{32,128}", salt):
            raise ValueError("invalid approval effect salt")
        # Parse now so malformed/non-timezone expiry never enters the queue.
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise ValueError("approval expiry must be a future timezone-aware time")
        approval = ComputerApproval(
            approval_id=(
                _clean_id(approval_id, "approval_id")
                if approval_id else _new_id("apr")
            ),
            session_id=session_id,
            status="pending",
            risk=risk,
            requester=_clean_identity(requester, "requester"),
            requested_at=_now(),
            expires_at=str(expires_at),
            effect_commitment=commitment,
            effect_commitment_salt=salt,
            effect_commitment_version=int(effect_commitment_version),
            observation_id=(
                _clean_id(observation_id, "observation_id")
                if observation_id else None
            ),
            target_summary=(
                " ".join(str(target_summary).split())[:300]
                if target_summary else None
            ),
            authority=_clean_identity(authority, "approval authority"),
            authority_approval_id=(
                _clean_identity(
                    authority_approval_id,
                    "authority approval id",
                )
                if authority_approval_id else None
            ),
        )

        def mutation(state: dict[str, Any]) -> None:
            approvals = state.setdefault("approvals", {})
            if approval.approval_id in approvals:
                raise SessionConflict("approval id already exists")
            if (
                approval.observation_id
                and approval.observation_id not in state["observations"]
            ):
                raise KeyError("approval observation does not exist")
            approvals[approval.approval_id] = approval.to_dict()
            self._append_event(
                state, "approval.requested", approval.to_dict(),
            )

        self._mutate(session_id, tenant_id, principal, mutation)
        return approval

    def record_approval_decided(
        self,
        session_id: str,
        approval_id: str,
        tenant_id: str,
        principal: str,
        *,
        status: str,
        claimed_by: str,
        effect_commitment: str,
    ) -> ComputerApproval:
        approval_id = _clean_id(approval_id, "approval_id")
        status = str(status).strip().lower()
        if status not in {"approved", "denied", "expired", "cancelled"}:
            raise ValueError("invalid approval decision")
        claimed_by = _clean_identity(claimed_by, "claimed_by")
        decided: ComputerApproval | None = None

        def mutation(state: dict[str, Any]) -> None:
            nonlocal decided
            try:
                raw = dict(state.setdefault("approvals", {})[approval_id])
            except KeyError as exc:
                raise KeyError(f"unknown approval {approval_id!r}") from exc
            if raw.get("status") != "pending":
                raise SessionConflict("approval is no longer pending")
            if raw.get("effect_commitment") != effect_commitment:
                raise SessionConflict("approval effect commitment changed")
            expiry = datetime.fromisoformat(
                str(raw["expires_at"]).replace("Z", "+00:00")
            )
            final_status = (
                "expired" if expiry <= datetime.now(timezone.utc) else status
            )
            raw.update({
                "status": final_status,
                "claimed_by": claimed_by,
                "decided_at": _now(),
            })
            decided = ComputerApproval(**raw)
            state["approvals"][approval_id] = decided.to_dict()
            self._append_event(state, "approval.decided", decided.to_dict())

        self._mutate(session_id, tenant_id, principal, mutation)
        assert decided is not None
        return decided

    def record_approval_wait_ended(
        self,
        session_id: str,
        approval_id: str,
        tenant_id: str,
        principal: str,
        *,
        status: str,
        reason: str,
    ) -> ComputerApproval:
        """End only the local wait; never decide the authority's queue row."""
        approval_id = _clean_id(approval_id, "approval_id")
        status = str(status).strip().lower()
        if status not in {"timed_out", "authority_unavailable"}:
            raise ValueError("invalid approval wait status")
        reason = " ".join(str(reason).split())[:300]
        ended: ComputerApproval | None = None

        def mutation(state: dict[str, Any]) -> None:
            nonlocal ended
            try:
                raw = dict(state.setdefault("approvals", {})[approval_id])
            except KeyError as exc:
                raise KeyError(f"unknown approval {approval_id!r}") from exc
            if raw.get("status") != "pending":
                return
            raw.update({"status": status, "decided_at": _now()})
            ended = ComputerApproval(**raw)
            state["approvals"][approval_id] = ended.to_dict()
            self._append_event(
                state,
                "approval.wait_ended",
                {
                    "approval_id": approval_id,
                    "status": status,
                    "reason": reason,
                    "authority": ended.authority,
                    "authority_approval_id": ended.authority_approval_id,
                },
            )

        self._mutate(session_id, tenant_id, principal, mutation)
        if ended is None:
            # A concurrent authoritative decision won; report current state.
            return next(
                item
                for item in self.list_approvals(
                    session_id,
                    tenant_id,
                    principal,
                )
                if item.approval_id == approval_id
            )
        return ended

    def list_approvals(
        self,
        session_id: str,
        tenant_id: str,
        principal: str | None = None,
        *,
        status: str | None = None,
    ) -> list[ComputerApproval]:
        state = self._load(tenant_id, session_id)
        self._authorize(state, tenant_id, principal)
        result = [
            ComputerApproval(**raw)
            for raw in state.get("approvals", {}).values()
            if status is None or raw.get("status") == status
        ]
        return sorted(result, key=lambda item: item.requested_at, reverse=True)


def runtime_approval_observer(
    store: ComputerRuntimeStore,
    *,
    session_id: str,
    tenant_id: str,
    principal: str,
    effect_args: Mapping[str, Any],
    observation_id: str | None = None,
    target_summary: str | None = None,
) -> ApprovalEventObserver:
    """Create a safe projection of the one authoritative consent lifecycle.

    ``effect_args`` is retained by reference so the observer sees the exact
    commitment which :mod:`maverick.safety.action_gate` writes immediately
    before calling consent. Raw arguments, typed values, URLs, selectors, and
    form values are never copied into runtime state.
    """
    mirror_id: str | None = None
    authority_id: str | None = None

    def observe(kind: str, payload: Mapping[str, Any]) -> None:
        nonlocal authority_id, mirror_id
        event_authority = str(payload.get("authority") or "world_model")
        event_authority_id = str(
            payload.get("authority_approval_id") or ""
        ).strip() or None
        if kind == "requested":
            commitment = str(
                effect_args.get("_approval_effect_commitment") or ""
            )
            salt = str(effect_args.get("_approval_effect_salt") or "")
            version = int(effect_args.get("_approval_effect_version") or 1)
            approval = store.record_approval_requested(
                session_id,
                tenant_id,
                principal,
                risk=str(payload.get("risk") or "medium"),
                requester=str(payload.get("requester") or principal),
                expires_at=str(payload.get("expires_at") or ""),
                effect_commitment=commitment,
                effect_commitment_salt=salt,
                effect_commitment_version=version,
                observation_id=observation_id,
                target_summary=target_summary,
                authority=event_authority,
                authority_approval_id=event_authority_id,
            )
            mirror_id = approval.approval_id
            authority_id = event_authority_id
            return
        if mirror_id is None:
            return
        if event_authority_id != authority_id:
            raise SessionConflict("approval authority correlation changed")
        if kind == "decided":
            store.record_approval_decided(
                session_id,
                mirror_id,
                tenant_id,
                principal,
                status=str(payload.get("status") or "denied"),
                claimed_by=str(payload.get("claimed_by") or "operator"),
                effect_commitment=str(
                    effect_args.get("_approval_effect_commitment") or ""
                ),
            )
        elif kind in {"wait_timeout", "unavailable"}:
            store.record_approval_wait_ended(
                session_id,
                mirror_id,
                tenant_id,
                principal,
                status=(
                    "timed_out"
                    if kind == "wait_timeout"
                    else "authority_unavailable"
                ),
                reason=str(payload.get("reason") or kind),
            )

    return observe


__all__ = [
    "LOCAL_PRINCIPAL", "LOCAL_TENANT_ID", "ComputerAction", "ComputerApproval",
    "ComputerActionOutcome", "ComputerObservation", "ComputerReadiness",
    "ComputerRuntimeStore", "ComputerSession", "OutcomeState",
    "EvidenceCustodyError", "ReadinessBlocker", "RuntimeStateCorrupt",
    "SessionAccessDenied", "SessionConflict",
    "SessionState", "SessionStopped", "ToolResultContent",
    "approval_event_scope", "computer_readiness", "emit_approval_event",
    "image_block", "runtime_approval_observer", "runtime_identity",
]

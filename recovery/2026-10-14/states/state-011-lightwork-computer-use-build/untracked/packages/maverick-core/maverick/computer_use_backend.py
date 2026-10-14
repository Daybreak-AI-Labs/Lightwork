"""Execution backends for governed computer use.

The production route is an authenticated remote execution cell.  Host-bound
``pyautogui`` remains available only when the operator explicitly enables it;
backend selection never falls back from a failed remote cell to the host.

Remote protocol (``v1``): requests and responses are JSON over HTTPS.  Each
request carries a timestamp, nonce, cell id and HMAC-SHA256 signature.  The
response must echo timestamp/nonce and sign its status/body digest.  This is a
The matching deployable cell is :mod:`maverick.computer_use_cell`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


class BackendUnavailable(RuntimeError):
    """The selected backend could not prove it is ready and isolated."""


class BackendIndeterminate(BackendUnavailable):
    """An effectful request may have reached the execution cell."""


class BackendRejected(BackendUnavailable):
    """The authenticated cell refused a request before applying an effect."""


@dataclass(frozen=True)
class BackendCapture:
    png: bytes
    width: int
    height: int
    scale_factor: float
    monitor_id: int
    left: int
    top: int
    active_window: str | None = None
    app: str | None = None


@dataclass(frozen=True)
class BackendActionResult:
    message: str
    effect_applied: bool | None
    capture: BackendCapture | None = None
    state: str = "succeeded"
    duplicate: bool = False


class ComputerBackend(Protocol):
    name: str

    def readiness(self) -> dict[str, Any]: ...

    def capture(self, *, monitor_id: int | None = None) -> BackendCapture: ...

    def execute(
        self,
        action: str,
        args: dict[str, Any],
        *,
        action_id: str,
        effect_commitment: str,
    ) -> BackendActionResult: ...

    def reconcile(
        self,
        *,
        action_id: str,
        effect_commitment: str,
    ) -> BackendActionResult: ...


def _canonical_request(
    timestamp: str, nonce: str, method: str, path: str, body: bytes,
) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return f"{timestamp}\n{nonce}\n{method.upper()}\n{path}\n{digest}".encode()


def _canonical_response(
    timestamp: str, nonce: str, status: int, body: bytes,
) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return f"{timestamp}\n{nonce}\n{status}\n{digest}".encode()


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward cell credentials or HMAC headers across a redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RemoteComputerBackend:
    """Strict HMAC-authenticated client for an isolated execution cell."""

    name = "remote"

    def __init__(
        self,
        endpoint: str,
        shared_key: str,
        cell_id: str,
        *,
        timeout: float = 15.0,
        opener=None,
    ):
        parsed = urllib.parse.urlsplit(str(endpoint).strip())
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("computer execution-cell endpoint must be a clean HTTPS origin")
        self.endpoint = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
        )
        self.shared_key = str(shared_key).strip()
        self.cell_id = str(cell_id).strip()
        if len(self.shared_key) < 32:
            raise ValueError("execution-cell shared key must be at least 32 characters")
        if not self.cell_id or len(self.cell_id) > 200:
            raise ValueError("execution-cell id is required")
        self.timeout = max(1.0, min(float(timeout), 60.0))
        self._opener = opener or urllib.request.build_opener(
            _NoRedirectHandler(),
        ).open

    @classmethod
    def from_env(cls) -> "RemoteComputerBackend":
        from .computer_use_settings import load_computer_use_settings

        settings = load_computer_use_settings()
        endpoint = settings.execution_cell_url
        key = settings.execution_cell_token
        cell_id = settings.execution_cell_id
        if not endpoint or not key or not cell_id:
            raise BackendUnavailable(
                "remote computer backend needs MAVERICK_COMPUTER_CELL_URL, "
                "MAVERICK_COMPUTER_CELL_KEY and MAVERICK_COMPUTER_EXECUTION_CELL_ID"
            )
        return cls(endpoint, key, cell_id)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        effectful: bool = False,
    ) -> dict[str, Any]:
        body = json.dumps(
            payload or {}, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(body) > 65_536:
            raise BackendRejected("execution-cell request exceeded 64 KiB")
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(18)
        signature = hmac.new(
            self.shared_key.encode(),
            _canonical_request(timestamp, nonce, method, path, body),
            hashlib.sha256,
        ).hexdigest()
        request = urllib.request.Request(
            f"{self.endpoint}{path}",
            data=body,
            method=method.upper(),
            headers={
                "Content-Type": "application/json",
                "X-Maverick-Cell": self.cell_id,
                "X-Maverick-Timestamp": timestamp,
                "X-Maverick-Nonce": nonce,
                "X-Maverick-Signature": signature,
            },
        )
        try:
            response = self._opener(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            # A no-redirect opener reports 3xx here too. Treat it as a response
            # but require the same signed-response contract before trusting it.
            response = exc
        except (OSError, urllib.error.URLError) as exc:
            error = (
                BackendIndeterminate
                if effectful
                else BackendUnavailable
            )
            raise error(
                f"execution cell request failed: {type(exc).__name__}"
            ) from exc
        with response:
            response_body = response.read(20_000_001)
            status = int(getattr(response, "status", 200))
            headers = response.headers
            final_url = str(getattr(response, "geturl", lambda: "")() or "")
        if len(response_body) > 20_000_000:
            error = BackendIndeterminate if effectful else BackendUnavailable
            raise error("execution cell response exceeded 20 MB")
        if final_url and final_url != f"{self.endpoint}{path}":
            error = BackendIndeterminate if effectful else BackendUnavailable
            raise error("execution cell redirected unexpectedly")
        content_type = str(headers.get("Content-Type") or "").split(";", 1)[0]
        if content_type.strip().lower() != "application/json":
            error = BackendIndeterminate if effectful else BackendUnavailable
            raise error("execution cell response is not application/json")
        response_ts = str(headers.get("X-Maverick-Timestamp") or "")
        response_nonce = str(headers.get("X-Maverick-Nonce") or "")
        response_sig = str(headers.get("X-Maverick-Signature") or "")
        if response_ts != timestamp or response_nonce != nonce:
            raise BackendUnavailable("execution cell response binding mismatch")
        expected = hmac.new(
            self.shared_key.encode(),
            _canonical_response(timestamp, nonce, status, response_body),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, response_sig):
            error = BackendIndeterminate if effectful else BackendUnavailable
            raise error("execution cell response signature invalid")
        try:
            result = json.loads(response_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackendUnavailable("execution cell returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise BackendUnavailable("execution cell response must be an object")
        if result.get("cell_id") != self.cell_id:
            raise BackendUnavailable("execution cell identity mismatch")
        if status < 200 or status >= 300:
            code = str(result.get("error") or f"http_{status}")[:120]
            if 400 <= status < 500:
                raise BackendRejected(f"execution cell rejected request: {code}")
            error = BackendIndeterminate if effectful else BackendUnavailable
            raise error(f"execution cell returned HTTP {status}: {code}")
        return result

    def readiness(self) -> dict[str, Any]:
        result = self._request("POST", "/v1/computer/health", {})
        if result.get("ready") is not True or result.get("isolated") is not True:
            raise BackendUnavailable("execution cell did not attest ready + isolated")
        return result

    @staticmethod
    def _capture_from(result: dict[str, Any]) -> BackendCapture:
        raw = result.get("capture")
        if not isinstance(raw, dict):
            raise BackendUnavailable("execution cell omitted capture")
        try:
            png = base64.b64decode(str(raw["png_base64"]), validate=True)
            capture = BackendCapture(
                png=png,
                width=int(raw["width"]),
                height=int(raw["height"]),
                scale_factor=float(raw.get("scale_factor", 1.0)),
                monitor_id=int(raw.get("monitor_id", 1)),
                left=int(raw.get("left", 0)),
                top=int(raw.get("top", 0)),
                active_window=(str(raw["active_window"])[:500] if raw.get("active_window") else None),
                app=(str(raw["app"])[:200] if raw.get("app") else None),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BackendUnavailable("execution cell returned invalid capture metadata") from exc
        if not capture.png or capture.width < 1 or capture.height < 1 or capture.scale_factor <= 0:
            raise BackendUnavailable("execution cell returned invalid capture geometry")
        if (
            len(capture.png) < 24
            or capture.png[:8] != b"\x89PNG\r\n\x1a\n"
            or capture.png[12:16] != b"IHDR"
        ):
            raise BackendUnavailable("execution cell returned a non-PNG capture")
        encoded_width = int.from_bytes(capture.png[16:20], "big")
        encoded_height = int.from_bytes(capture.png[20:24], "big")
        if (
            encoded_width != capture.width
            or encoded_height != capture.height
            or capture.width > 16_384
            or capture.height > 16_384
            or capture.width * capture.height > 100_000_000
        ):
            raise BackendUnavailable("execution cell capture dimensions are untrusted")
        return capture

    def capture(self, *, monitor_id: int | None = None) -> BackendCapture:
        result = self._request(
            "POST", "/v1/computer/capture", {"monitor_id": monitor_id}
        )
        return self._capture_from(result)

    def execute(
        self,
        action: str,
        args: dict[str, Any],
        *,
        action_id: str,
        effect_commitment: str,
    ) -> BackendActionResult:
        if not re.fullmatch(r"act_[A-Za-z0-9_-]{12,124}", str(action_id)):
            raise BackendRejected("invalid remote action id")
        if not re.fullmatch(r"[0-9a-f]{64}", str(effect_commitment)):
            raise BackendRejected("invalid remote effect commitment")
        result = self._request(
            "POST",
            "/v1/computer/action",
            {
                "action": action,
                "action_id": action_id,
                "effect_commitment": effect_commitment,
                "args": args,
            },
            effectful=True,
        )
        capture = self._capture_from(result) if result.get("capture") is not None else None
        effect = result.get("effect_applied")
        if effect not in {True, False, None}:
            raise BackendUnavailable("execution cell returned invalid effect state")
        return BackendActionResult(
            message=str(result.get("message") or "action completed")[:1_000],
            effect_applied=effect,
            capture=capture,
            state=str(result.get("state") or "succeeded"),
            duplicate=result.get("duplicate") is True,
        )

    def reconcile(
        self,
        *,
        action_id: str,
        effect_commitment: str,
    ) -> BackendActionResult:
        result = self._request(
            "POST",
            "/v1/computer/outcome",
            {
                "action_id": action_id,
                "effect_commitment": effect_commitment,
            },
        )
        effect = result.get("effect_applied")
        if effect not in {True, False, None}:
            raise BackendUnavailable("execution cell returned invalid effect state")
        return BackendActionResult(
            message=str(result.get("message") or "outcome reconciled")[:1_000],
            effect_applied=effect,
            state=str(result.get("state") or "indeterminate"),
            duplicate=result.get("duplicate") is True,
        )


def selected_backend_name() -> str:
    """Return explicit backend choice; production defaults to remote."""
    from .computer_use_settings import load_computer_use_settings

    raw = load_computer_use_settings().mode
    if raw not in {"remote", "host"}:
        raise BackendUnavailable(f"unknown computer backend {raw!r}")
    return raw


__all__ = [
    "BackendActionResult", "BackendCapture", "BackendIndeterminate",
    "BackendRejected", "BackendUnavailable",
    "ComputerBackend", "RemoteComputerBackend", "selected_backend_name",
]

"""Hand a conversation to an outside contact centre once a person takes it over.

Off by default (``[contact_centre] provider = "off"``): nothing is sent and no
outbox file is written. With Zendesk or a signed webhook, each in-app human
handoff gets one outbox entry keyed by the handoff id, so a repeated request
never creates a second ticket. Delivery runs in a background thread with the
webhook retry schedule (:mod:`webhook_subscriptions`). A send that may have
reached the receiver is never repeated; it is shown as "not confirmed".

The in-app handoff never waits for this module and never changes its outcome.

Requests go through the same address guard (``webhooks._post_result``) and the
enterprise egress lock that outbound webhooks use. Webhooks are signed by
``webhooks.build_headers`` (the repository's own scheme). Free text is passed
through the proven redactor before it leaves. The Zendesk API token and the
webhook signing secret come from the environment or from the sealed settings
value, never from plain text in config.toml. The outbox is one sealed file per
workspace under the workspace's data directory.
"""
from __future__ import annotations

import base64
import contextlib
import contextvars
import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from . import config
from .paths import current_tenant_id, data_dir
from .webhook_subscriptions import MAX_ATTEMPTS, retry_delay
from .webhooks import NotSent, build_envelope, build_headers, encode_body

log = logging.getLogger(__name__)

EVENT = "contact_centre.handoff"
TAG = "lightwork"
ZENDESK_TOKEN_ENV = "MAVERICK_CONTACT_CENTRE_ZENDESK_TOKEN"  # pragma: allowlist secret
WEBHOOK_SECRET_ENV = "MAVERICK_CONTACT_CENTRE_WEBHOOK_SECRET"  # pragma: allowlist secret
MAX_ENTRIES = 2000
MAX_SUBJECT = 200
MAX_REASON = 1000
MAX_MESSAGE = 2000
MAX_REQUESTER = 120
TIMEOUT = 10.0
TERMINAL = frozenset({"sent", "failed", "unknown"})
LABELS = {"queued": "Waiting to send", "sending": "Sending", "retrying": "Retrying",
          "sent": "Sent", "failed": "Not sent", "unknown": "Not confirmed"}
_HANDOFF_ID_RE = re.compile(r"[A-Za-z0-9_-]{16,100}")
_EMAIL_RE = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}")
_TICKET_ID_RE = re.compile(r'"ticket"\s*:\s*\{[^}]*?"id"\s*:\s*(\d{1,20})')

_thread_lock = threading.RLock()
_active: set[str] = set()
_sleep = time.sleep


class OutboxUnavailable(RuntimeError):
    """The outbox could not be read or written safely. Nothing was changed."""


class OutboxFull(RuntimeError):
    """The outbox has no room for another handoff, even after pruning."""


# ----------------------------------------------------------------- settings
def conversation_link(goal_id: int) -> str:
    """Absolute link to a conversation, or ``""`` when no dashboard address is set."""
    base = config.get_contact_centre()["dashboard_url"]
    return f"{base}/conversations?goal={int(goal_id)}" if base else ""


def _secret(env_name: str, key: str) -> str:
    """The environment (or secret file) value wins; otherwise the sealed value saved on Settings."""
    from .secret_provider import get_secret
    value = (get_secret(env_name) or "").strip()
    if value:
        return value
    try:
        section = config.load_config().get("contact_centre") or {}
    except Exception:  # pragma: no cover -- config never blocks a delivery read
        return ""
    saved = section.get(key) if isinstance(section, dict) else None
    return saved.strip() if isinstance(saved, str) else ""


def _problem(policy: dict[str, Any]) -> str:
    """Why the chosen provider cannot send yet, in plain words. ``""`` when ready."""
    if policy["provider"] == "zendesk":
        if not policy["zendesk_subdomain"]:
            return "Enter the Zendesk subdomain in Settings."
        if not policy["zendesk_email"]:
            return "Enter the Zendesk agent email in Settings."
        if not _secret(ZENDESK_TOKEN_ENV, "zendesk_api_token"):
            return "Set the Zendesk API token in Settings."
    elif policy["provider"] == "webhook":
        if not policy["webhook_url"]:
            return "Enter the https address of the webhook in Settings."
        if not _secret(WEBHOOK_SECRET_ENV, "webhook_secret"):
            return "Set the webhook signing secret in Settings."
    return ""


def _destination(policy: dict[str, Any]) -> str:
    if policy["provider"] == "zendesk":
        # Endpoint form per Zendesk's API reference; see docs (not verified against a live tenant).
        return f"https://{policy['zendesk_subdomain']}.zendesk.com/api/v2/tickets.json"
    return policy["webhook_url"]


# ------------------------------------------------------------------ content
def _redact(text: str) -> str:
    from .provable_redaction import redact_proven
    proof = redact_proven(text)
    if not proof.proven:
        return "[Removed: this text could not be checked for personal data or secrets.]"
    return proof.redacted


def _clean(value: object, limit: int) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return _redact(value)[:limit]


def _content(*, title: str, reason: str, requester_name: str, requester_email: str,
             messages: list[tuple[str, str]], link: str, limit: int) -> dict[str, Any]:
    """The reviewed handoff content. Each free-text field is redacted and bounded."""
    email = requester_email.strip() if isinstance(requester_email, str) else ""
    rows = [{"from": "customer" if role == "user" else "lightwork", "text": _clean(text, MAX_MESSAGE)}
            for role, text in list(messages)[-limit:]]
    return {
        "subject": _clean(title, MAX_SUBJECT).strip() or "Lightwork conversation",
        "reason": _clean(reason, MAX_REASON).strip(),
        "requester": {"name": _clean(requester_name, MAX_REQUESTER).strip(),
                      "email": email if _EMAIL_RE.fullmatch(email) else ""},
        "messages": rows,
        "link": link if isinstance(link, str) else "",
    }


def _zendesk_body(content: dict[str, Any], key: str) -> bytes:
    requester = content["requester"]
    lines = ["Reason for the handoff:", content["reason"] or "Not given", "",
             "Customer: " + (requester["name"] or requester["email"] or "Not known")]
    if requester["name"] and requester["email"]:
        lines[-1] += f" <{requester['email']}>"
    lines += ["", "Recent messages (oldest first):"]
    lines += [f"{row['from']}: {row['text']}" for row in content["messages"]] or ["(none)"]
    if content["link"]:
        lines += ["", "Open the conversation in Lightwork: " + content["link"]]
    ticket: dict[str, Any] = {"subject": content["subject"], "comment": {"body": "\n".join(lines)},
                              "tags": [TAG], "external_id": key}
    if requester["email"]:
        ticket["requester"] = {"name": requester["name"] or requester["email"], "email": requester["email"]}
    return json.dumps({"ticket": ticket}).encode("utf-8")


def _delivery_id(key: str) -> str:
    """Stable per handoff, so a retried webhook carries the same X-Lightwork-Delivery id."""
    return "cc_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _key(handoff_id: str) -> str | None:
    if not isinstance(handoff_id, str) or not _HANDOFF_ID_RE.fullmatch(handoff_id):
        return None
    return "handoff:" + handoff_id


# ------------------------------------------------------------------ outbox
def _path() -> Path:
    return data_dir("contact_centre", "outbox.sealed")


@contextlib.contextmanager
def _locked(*, create: bool):
    from .file_lock import cross_process_lock, ensure_private_directory
    path = _path()
    with _thread_lock:
        if create:
            ensure_private_directory(path.parent)
        with cross_process_lock(path, strict=True):
            yield path


def _load(path: Path) -> dict[str, dict]:
    from .file_lock import atomic_read_bytes
    from .tenant.kms import unseal_text_for_tenant
    if not path.exists():
        return {}
    try:
        raw = unseal_text_for_tenant(current_tenant_id(), atomic_read_bytes(path))
        data = json.loads(raw)
    except Exception as exc:
        raise OutboxUnavailable("The contact centre outbox could not be read safely; nothing was changed.") from exc
    if not isinstance(data, dict) or not all(isinstance(row, dict) for row in data.values()):
        raise OutboxUnavailable("The contact centre outbox could not be read safely; nothing was changed.")
    return data


def _save(path: Path, data: dict[str, dict]) -> None:
    from .file_lock import atomic_write_bytes
    from .tenant.kms import seal_text_for_tenant
    blob = seal_text_for_tenant(current_tenant_id(), json.dumps(data, sort_keys=True))
    atomic_write_bytes(path, blob, mode=0o600)


def _prune(data: dict[str, dict]) -> None:
    """Drop the oldest finished entries so the newest handoffs keep their status."""
    finished = sorted((row for row in data.values() if row.get("state") in TERMINAL),
                      key=lambda row: float(row.get("updated_at") or 0.0))
    for row in finished:
        if len(data) < MAX_ENTRIES:
            break
        data.pop(row["key"], None)


def _public(entry: dict[str, Any]) -> dict[str, Any]:
    """What people may see: no payload, no destination address, never a credential."""
    state = str(entry.get("state") or "")
    return {"provider": str(entry.get("provider") or ""), "state": state,
            "label": LABELS.get(state, ""), "reason": str(entry.get("reason") or ""),
            "ticket_id": str(entry.get("ticket_id") or ""),
            "updated_at": float(entry.get("updated_at") or 0.0)}


def _audit(key: str, provider: str, action: str, state: str) -> None:
    try:
        from .audit import audit_event
        audit_event("contact_centre_handoff", agent="contact_centre", action=action,
                    provider=provider, state=state, handoff=key)
    except Exception:  # pragma: no cover -- audit outage never blocks the handoff
        log.debug("contact centre: audit unavailable for %s", action)


# ------------------------------------------------------------------ public API
def enqueue_handoff(*, handoff_id: str, title: str, reason: str, requester_name: str,
                    requester_email: str, messages: list[tuple[str, str]], link: str) -> dict | None:
    """Record one outbox entry for an in-app handoff and start its background delivery.

    Returns ``None`` when the provider is off (nothing is recorded or sent). An
    existing entry for the same handoff is returned unchanged, so a repeated call
    never creates a second ticket. Raises :class:`OutboxFull` or
    :class:`OutboxUnavailable`; the caller keeps the in-app handoff either way.
    """
    policy = config.get_contact_centre()
    if policy["provider"] == "off":
        return None
    key = _key(handoff_id)
    if key is None:
        raise ValueError("The handoff identity is not valid for the contact centre.")
    problem = _problem(policy)
    now = time.time()
    with _locked(create=True) as path:
        data = _load(path)
        if key in data:
            return _public(data[key])
        if len(data) >= MAX_ENTRIES:
            _prune(data)
        if len(data) >= MAX_ENTRIES:
            raise OutboxFull("The contact centre outbox is full. The handoff stays in Lightwork but was not queued.")
        content = {} if problem else _content(title=title, reason=reason, requester_name=requester_name,
                                              requester_email=requester_email, messages=messages,
                                              link=link, limit=policy["max_messages"])
        entry = {"key": key, "provider": policy["provider"],
                 "destination": "" if problem else _destination(policy),
                 "state": "failed" if problem else "queued", "attempts": 0, "next_attempt_at": now,
                 "reason": problem or "Lightwork will send it in the background.", "ticket_id": "", "payload": content,
                 "created_at": now, "updated_at": now}
        data[key] = entry
        _save(path, data)
    _audit(key, policy["provider"], "failed" if problem else "queued", entry["state"])
    if not problem:
        _start(key)
    return _public(entry)


def status_for(handoff_id: str) -> dict | None:
    """The public delivery status of one handoff, or ``None`` when it was never queued."""
    key = _key(handoff_id)
    if key is None or not _path().exists():
        return None
    with _locked(create=False) as path:
        entry = _load(path).get(key)
    return _public(entry) if entry else None


def _send(key: str, entry: dict[str, Any]) -> tuple[str, str, str]:
    """Build the signed request for one attempt and send it.

    Returns ``(outcome, reason, ticket_id)`` where outcome is ``sent``, ``retry``,
    ``unknown`` or ``failed``. Credentials are read here, never stored in the outbox.
    """
    from .enterprise import enterprise_egress_denial
    provider, destination = entry["provider"], entry["destination"]
    name = "Zendesk" if provider == "zendesk" else "the webhook"
    content = dict(entry.get("payload") or {})
    if enterprise_egress_denial(destination, tool="contact_centre") is not None:
        return "failed", "The enterprise egress lock blocks this destination. Nothing was sent.", ""
    if provider == "zendesk":
        token = _secret(ZENDESK_TOKEN_ENV, "zendesk_api_token")
        email = config.get_contact_centre()["zendesk_email"]
        if not token or not email:
            return "failed", "Set the Zendesk email and API token in Settings. Nothing was sent.", ""
        credential = base64.b64encode(f"{email}/token:{token}".encode()).decode("ascii")
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": "Lightwork-ContactCentre/1.0", "Authorization": "Basic " + credential}
        body = _zendesk_body(content, key)
    else:
        secret = _secret(WEBHOOK_SECRET_ENV, "webhook_secret")
        if not secret:
            return "failed", "Set the webhook signing secret in Settings. Nothing was sent.", ""
        delivery = _delivery_id(key)
        body = encode_body(build_envelope(EVENT, content, link=content.get("link", ""), event_id=delivery))
        headers = build_headers(body, EVENT, secret, event_id=delivery)
    try:
        status, _latency, snippet, error = _post(destination, body, headers)
    except Exception as exc:
        log.warning("contact centre: send to %s did not complete (%s)", name, type(exc).__name__)
        return ("unknown", f"{name} did not confirm this send. It was not sent again; "
                           f"check {name} before sending it by hand.", "")
    return _classify(provider, status, error, snippet)


def _post(url: str, body: bytes, headers: dict[str, str]) -> tuple[int | None, int, str, str]:
    """The real transport: the SSRF-pinned client that outbound webhooks use."""
    from .webhooks import _post_result
    return _post_result(url, body, headers, TIMEOUT)


def _classify(provider: str, status: int | None, error: object, snippet: str) -> tuple[str, str, str]:
    name = "Zendesk" if provider == "zendesk" else "the webhook"
    if status is None:
        if isinstance(error, NotSent):
            if str(error).startswith("blocked"):
                return "failed", (f"Lightwork's address check refused the {name} address. "
                                  "Check the address in Settings. Nothing was sent."), ""
            return "retry", f"Could not reach {name}. Lightwork will try again.", ""
        return "unknown", (f"{name} did not confirm this send. It was not sent again; "
                           f"check {name} before sending it by hand."), ""
    if 200 <= status < 300:
        ticket = ""
        if provider == "zendesk":
            found = _TICKET_ID_RE.search(snippet or "")
            ticket = found.group(1) if found else ""
        return "sent", ("Sent to Zendesk." if provider == "zendesk" else "Delivered to the webhook."), ticket
    if status == 429:
        return "retry", f"{name} is busy. Lightwork will try again.", ""
    if status >= 500:
        if provider == "webhook":
            return "retry", f"The webhook had a server error (HTTP {status}). Lightwork will try again.", ""
        return "unknown", (f"Zendesk had a server error (HTTP {status}). It may have created the ticket, "
                           "so it was not sent again. Check Zendesk for the tag \"lightwork\" first."), ""
    if status in (401, 403):
        if provider == "zendesk":
            return "failed", (f"Zendesk did not accept the email or API token (HTTP {status}). "
                              "Check them in Settings."), ""
        return "failed", f"The webhook refused the request (HTTP {status}). Check its address and signing secret.", ""
    if status == 404:
        if provider == "zendesk":
            return "failed", "Zendesk was not found at this subdomain (HTTP 404). Check the subdomain in Settings.", ""
        return "failed", "The webhook address was not found (HTTP 404). Check it in Settings.", ""
    return "failed", f"{name} refused the request (HTTP {status}).", ""


def _attempt(key: str, *, honour_schedule: bool) -> tuple[str, float]:
    """One send attempt. Returns ``("wait", seconds)`` when a retry is scheduled, else ``("done", 0.0)``.

    ``honour_schedule`` is False right after the caller has slept for the scheduled
    wait, so the attempt does not depend on the clock having advanced further.
    The entry is marked ``sending`` and saved before the request is made, so a
    crash during the request leaves it visibly unconfirmed instead of resending.
    """
    now = time.time()
    with _locked(create=False) as path:
        data = _load(path)
        entry = data.get(key)
        if entry is None or entry.get("state") not in {"queued", "retrying"}:
            return "done", 0.0
        due = float(entry.get("next_attempt_at") or 0.0)
        if honour_schedule and due > now:
            return "wait", due - now
        entry.update(state="sending", attempts=int(entry.get("attempts") or 0) + 1, updated_at=now)
        _save(path, data)
        attempt = entry["attempts"]
        snapshot = dict(entry)
    outcome, reason, ticket = _send(key, snapshot)
    now = time.time()
    delay = 0.0
    if outcome == "retry" and attempt >= MAX_ATTEMPTS:
        outcome = "failed"
        reason = f"{'Zendesk' if snapshot['provider'] == 'zendesk' else 'The webhook'} did not accept it after {MAX_ATTEMPTS} tries. Nothing was sent twice."
    elif outcome == "retry":
        scheduled = retry_delay(attempt)
        if scheduled is None:
            outcome, reason = "failed", f"Lightwork stopped retrying after {MAX_ATTEMPTS} tries. Nothing was sent twice."
        else:
            delay = scheduled
    with _locked(create=False) as path:
        data = _load(path)
        entry = data.get(key)
        if entry is None:
            return "done", 0.0
        entry.update(updated_at=now, reason=reason)
        if outcome == "retry":
            entry.update(state="retrying", next_attempt_at=now + delay)
        else:
            entry.update(state=outcome, payload={}, next_attempt_at=None)
            if outcome == "sent":
                entry["ticket_id"] = ticket
        _save(path, data)
    _audit(key, snapshot["provider"], outcome, entry["state"])
    return ("wait", delay) if outcome == "retry" else ("done", 0.0)


def run_delivery(key: str) -> None:
    """Make attempts for one handoff until it reaches a final state, waiting between retries."""
    action, seconds = _attempt(key, honour_schedule=True)
    while action == "wait":
        _sleep(seconds)
        action, seconds = _attempt(key, honour_schedule=False)


def _start(key: str) -> None:
    """Deliver in a daemon thread that keeps the caller's workspace context."""
    with _thread_lock:
        if key in _active:
            return
        _active.add(key)
    context = contextvars.copy_context()

    def _main() -> None:
        try:
            context.run(run_delivery, key)
        except Exception as exc:
            log.warning("contact centre: delivery stopped unexpectedly (%s)", type(exc).__name__)
        finally:
            with _thread_lock:
                _active.discard(key)

    threading.Thread(target=_main, name="mvk-contact-centre", daemon=True).start()


def resume_pending() -> int:
    """After a restart: an interrupted send becomes unconfirmed (never resent); queued and retrying handoffs continue."""
    if not _path().exists():
        return 0
    now = time.time()
    marked: list[tuple[str, str, str]] = []
    with _locked(create=False) as path:
        data = _load(path)
        for key, entry in data.items():
            if entry.get("state") == "sending":
                entry.update(state="unknown", payload={}, updated_at=now,
                             reason=("Lightwork stopped while this was being sent, so the outcome is unknown. "
                                     "It was not sent again; check the contact centre before sending it by hand."))
                marked.append((key, str(entry.get("provider") or ""), "unknown"))
        if marked:
            _save(path, data)
        pending = [key for key, entry in data.items() if entry.get("state") in {"queued", "retrying"}]
    for key, provider, state in marked:
        _audit(key, provider, "unknown", state)
    for key in pending:
        _start(key)
    return len(pending)


def resume_all_workspaces() -> int:
    """Resume pending deliveries in the shared workspace and in every active workspace.

    One failing workspace is logged by exception type and does not stop the others.
    """
    from .paths import reset_tenant, set_tenant
    from .tenant.registry import list_tenants
    total = 0
    try:
        total += resume_pending()
    except Exception as exc:
        log.warning("contact centre: shared workspace not resumed (%s)", type(exc).__name__)
    for record in list_tenants():
        if not record.active:
            continue
        token = set_tenant(record.id)
        try:
            total += resume_pending()
        except Exception as exc:
            log.warning("contact centre: a workspace was not resumed (%s)", type(exc).__name__)
        finally:
            reset_tenant(token)
    return total


__all__ = ["OutboxFull", "OutboxUnavailable", "conversation_link", "enqueue_handoff",
           "resume_all_workspaces", "resume_pending", "run_delivery", "status_for"]

"""Bounded recipient observer owned by the dashboard shutdown supervisor."""
from __future__ import annotations

import logging
import os
import threading
import time

from maverick.authority_effects import authority_effect_fence
from maverick.paths import current_tenant_id_strict, tenant_scope

log = logging.getLogger(__name__)
_scheduler_thread = None
_scheduler_stop = threading.Event()
_start_lock = threading.Lock()
_cursor = 0


def _enabled() -> bool:
    from maverick import config
    return (not config.config_source_errors(include_tenant=True)
            and (config.load_config().get("notifications") or {}).get("personal_alerts") is True
            and (config.load_config().get("notifications") or {}).get("personal_background") is True)


def _tenants() -> list[str | None]:
    from maverick.tenant.registry import list_tenants
    from maverick.paths import canonical_tenant_id
    floor = os.environ.get("MAVERICK_TENANT", "").strip()
    if floor:
        return [canonical_tenant_id(floor)]
    records = list_tenants()
    if records:
        return [record.id for record in records if record.active]
    return [current_tenant_id_strict()]


def observe(principal: str) -> bool:
    from maverick import config, experience_preferences as preferences
    from maverick.personal_identity import principal_current
    from maverick.personal_notification_recipients import observed, withdraw
    from maverick.personal_notifications import PersonalInbox, validate_preferences
    from maverick.tenant.registry import assert_tenant_active
    from maverick.world_model import open_world, close_world_if_owned
    from .personal_notifications_api import _projection
    with authority_effect_fence():
        if _scheduler_stop.is_set() or not _enabled():
            return False
        assert_tenant_active(current_tenant_id_strict())
        if not principal_current(principal):
            withdraw(principal)
            return False
        world = open_world()
        try:
            sources, _, _, _ = _projection(None, principal, world=world)
            choice = validate_preferences(preferences.state(principal)["values"].get("notification_preferences", {}))
            PersonalInbox(principal).refresh(sources, choice,
                allow_critical=(config.load_config().get("notifications") or {}).get("personal_critical_bypass") is True)
            observed(principal)
        finally:
            close_world_if_owned(world)
    return True


def cycle() -> None:
    """Fair cursor across enrolled tenants/accounts; never infer identities from files."""
    from maverick.personal_notification_recipients import principals
    from maverick.tenant.registry import assert_tenant_active
    global _cursor
    pending = []
    for tenant in _tenants():
        if _scheduler_stop.is_set():
            return
        with tenant_scope(tenant=tenant), authority_effect_fence():
            try:
                assert_tenant_active(tenant)
                if _enabled():
                    pending.extend((tenant, principal) for principal in principals())
            except Exception as exc:
                log.warning("Personal alert recipient policy unavailable (%s)", type(exc).__name__)
    if not pending:
        return
    deadline = time.monotonic() + 20
    for _ in range(min(len(pending), 100)):
        if _scheduler_stop.is_set() or time.monotonic() >= deadline:
            return
        tenant, principal = pending[_cursor % len(pending)]
        _cursor += 1
        try:
            with tenant_scope(tenant=tenant):
                observe(principal)
        except Exception as exc:
            # Do not log names, destinations, URLs, text, or credential-bearing exceptions.
            log.warning("Personal alert observation failed (%s)", type(exc).__name__)


def _loop() -> None:
    while not _scheduler_stop.is_set():
        try:
            cycle()
        except Exception as exc:
            log.warning("Personal alert scheduler unavailable (%s)", type(exc).__name__)
        _scheduler_stop.wait(30)


def start_personal_notification_scheduler() -> bool:
    global _scheduler_thread
    with _start_lock:
        if _scheduler_thread is not None and _scheduler_thread.is_alive():
            return False
        _scheduler_stop.clear()
        _scheduler_thread = threading.Thread(target=_loop, name="personal-notification-observer", daemon=True)
        _scheduler_thread.start()
        return True


def stop_personal_notification_scheduler(*, timeout=20, wait=True) -> bool:
    _scheduler_stop.set()
    if wait and _scheduler_thread is not None:
        _scheduler_thread.join(max(0, timeout))
    return _scheduler_thread is None or not _scheduler_thread.is_alive()

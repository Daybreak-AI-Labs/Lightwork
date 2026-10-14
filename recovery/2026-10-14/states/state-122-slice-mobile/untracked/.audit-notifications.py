from pathlib import Path

def edit(name, old, new):
    path=Path(name); text=path.read_text(encoding='utf-8'); assert old in text, (name,old[:80]); path.write_text(text.replace(old,new),encoding='utf-8',newline='\n')
core='packages/maverick-core/maverick/'
edit(core+'notifications.py', 'import threading\n', 'import threading\nimport uuid\nfrom contextvars import ContextVar, copy_context\n')
edit(core+'notifications.py', '_executor = None', '''_outcome = ContextVar("notification_outcome", default=None)


def _response_accepted(status: int) -> bool:
    if 200 <= status < 300:
        _outcome.set(("delivered", "Accepted by the notification service. This does not mean the person saw it.", False))
        return True
    if 400 <= status < 500 and status != 408:
        _outcome.set(("failed", "The notification service refused this request. Check the saved destination and credentials.", True))
    else:
        _outcome.set(("unknown", "The service did not confirm delivery. Check the destination before sending again.", False))
    return False


def _network_unknown() -> None:
    _outcome.set(("unknown", "Delivery could not be confirmed. Check the destination before sending again.", False))


_executor = None''')
edit(core+'notifications.py', 'return resp.status_code < 400', 'return _response_accepted(resp.status_code)')
edit(core+'notifications.py', '        log.warning("notify ntfy failed: %s", e)', '        _network_unknown()\n        log.warning("notify ntfy failed: %s", type(e).__name__)')
edit(core+'notifications.py', '        log.warning("notify pushover failed: %s", e)', '        _network_unknown()\n        log.warning("notify pushover failed: %s", type(e).__name__)')
edit(core+'notifications.py', '        log.warning("notify discord failed: %s", type(e).__name__)', '        _network_unknown()\n        log.warning("notify discord failed: %s", type(e).__name__)')
edit(core+'notifications.py', '        log.warning("notify slack failed: %s", type(e).__name__)', '        _network_unknown()\n        log.warning("notify slack failed: %s", type(e).__name__)')
edit(core+'notifications.py', '    batch: bool = True,\n) -> int:', '''    batch: bool = True,
    delivery_key: str | None = None,
    delivery_kind: str = "event",
    retry: bool = False,
    authorize=None,
) -> int:''')
edit(core+'notifications.py', '"""Send a notification. Returns the number of backends fired.', '"""Queue a notification. Async returns accepted queue count, never proof of delivery.')
edit(core+'notifications.py', '    if async_dispatch:\n        exec_ = _get_executor()', '''    from .notification_receipts import DeliveryReceipts
    receipts = DeliveryReceipts() if cfg.get("delivery_receipts", True) is not False else None
    event = delivery_key or uuid.uuid4().hex
    if len(event) > 200:
        raise ValueError("Delivery reference is too long")
    if retry and (receipts is None or delivery_kind != "test"):
        return 0
    admitted = []
    for backend in dict.fromkeys(requested):
        if receipts is None:
            admitted.append((backend, None))
        else:
            ident, acquired = receipts.claim(event, backend, kind=delivery_kind, retry=retry)
            if acquired:
                admitted.append((backend, ident))

    def tracked(backend: str, ident: str | None) -> bool:
        token = _outcome.set(("failed", "Not sent. Check the channel configuration and installed backend dependencies.", True))
        try:
            if authorize is not None:
                authorize()
            # Avoid dispatching to a destination that changed after review/queue.
            if cfg != _load_config():
                raise ValueError("Notification configuration changed")
            if receipts is not None:
                receipts.update(ident, "sending", "Contacting the notification service.")
            result = _dispatch(backend)
            outcome = _outcome.get()
            if result and outcome[0] != "delivered":
                outcome = ("delivered", "Accepted by the notification service. This does not mean the person saw it.", False)
            if receipts is not None:
                receipts.update(ident, outcome[0], outcome[1], safe_to_retry=outcome[2])
            return result
        except Exception as exc:
            # Never include exception text: it can contain secret webhook URLs.
            log.warning("notify dispatch unavailable: %s", type(exc).__name__)
            if receipts is not None:
                try:
                    receipts.update(ident, "unknown", "Delivery could not be confirmed. Refresh status and check the destination before sending again.")
                except Exception:
                    log.warning("notify receipt could not be saved")
            return False
        finally:
            _outcome.reset(token)

    if async_dispatch:
        exec_ = _get_executor()''')
edit(core+'notifications.py', '''        for backend in requested:
            exec_.submit(_dispatch, backend)
        return len(requested)''', '''        for backend, ident in admitted:
            context = copy_context()
            exec_.submit(context.run, tracked, backend, ident)
        return len(admitted)''')
edit(core+'notifications.py', '    for backend in requested:\n        if _dispatch(backend):', '    for backend, ident in admitted:\n        if tracked(backend, ident):')
edit(core+'tools/notify.py', 'return f"sent ({count}', 'return f"queued; delivery not yet confirmed ({count}')
edit(core+'push_v2.py', 'backends=[backend])', 'backends=[backend], async_dispatch=False, batch=False)')

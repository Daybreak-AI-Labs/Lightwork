from pathlib import Path
import textwrap
p=Path('packages/maverick-core/maverick/notifications.py')
s=p.read_text(encoding='utf-8')
start=s.index('    def _legacy_url(')
end=s.index('    from .notification_receipts import DeliveryReceipts',start)
dispatch=textwrap.dedent(s[start:end]).replace('def _dispatch(backend: str) -> bool:', 'def _dispatch(backend: str, *, cfg: dict, title: str, body: str, priority: str) -> bool:')
s=s[:start]+s[end:]
start=s.index('    from .notification_receipts import DeliveryReceipts',s.index('def notify('))
end=s.index('    def tracked(',start)
admission=textwrap.dedent(s[start:end])
admission=admission.replace('event = delivery_key or uuid.uuid4().hex\n','').replace('    return 0\n','    return receipts, []\n')
admission='def _admit(cfg, requested, event, delivery_kind, retry):\n'+textwrap.indent(admission,'    ')+'    return receipts, admitted\n\n\n'
s=s[:start]+'''    event = delivery_key or uuid.uuid4().hex
    receipts, admitted = _admit(cfg, requested, event, delivery_kind, retry)

'''+s[end:]
start=s.index('    def tracked(')
end=s.index('    if async_dispatch:',start)
s=s[:start]+'''    def tracked(backend, ident):
        return _tracked(backend, ident, cfg=cfg, title=title, body=body,
                        priority=priority, receipts=receipts, authorize=authorize)

'''+s[end:]
tracking='''def _tracked(backend, ident, *, cfg, title, body, priority, receipts, authorize):
    from .authority_effects import authority_effect_fence
    token = _outcome.set(("failed", "Not sent. Check the channel configuration and installed backend dependencies.", True))
    started = False
    try:
        with authority_effect_fence():
            if authorize is not None:
                authorize()
            if cfg != _load_config():
                raise ValueError("Notification configuration changed")
            if receipts is not None:
                receipts.update(ident, "sending", "Contacting the notification service.")
            started = True
            result = _dispatch(backend, cfg=cfg, title=title, body=body, priority=priority)
            outcome = _outcome.get()
            if result and outcome[0] != "delivered":
                outcome = ("delivered", "Accepted by the notification service. This does not mean the person saw it.", False)
            if receipts is not None:
                receipts.update(ident, outcome[0], outcome[1], safe_to_retry=outcome[2])
            return result
    except Exception as exc:
        log.warning("notify dispatch unavailable: %s", type(exc).__name__)
        if receipts is not None:
            try:
                message = ("Delivery could not be confirmed. Check the destination before sending again." if started
                           else "Not sent. Current access, configuration or delivery storage could not be verified.")
                receipts.update(ident, "unknown" if started else "failed", message)
            except Exception:
                log.warning("notify receipt could not be saved")
        return False
    finally:
        _outcome.reset(token)


'''
position=s.index('def notify(')
s=s[:position]+dispatch+admission+tracking+s[position:]
p.write_text(s,encoding='utf-8',newline='\n')

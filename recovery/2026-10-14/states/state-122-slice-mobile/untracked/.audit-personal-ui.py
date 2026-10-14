from pathlib import Path
def edit(name,old,new):
 p=Path(name);s=p.read_text(encoding='utf-8');assert old in s,(name,old[:80]);p.write_text(s.replace(old,new),encoding='utf-8',newline='\n')
d='packages/maverick-dashboard/maverick_dashboard/'
edit(d+'notification_health.py','router = APIRouter()','from .personal_notifications_api import router as personal_notifications_router\n\nrouter = APIRouter()\nrouter.include_router(personal_notifications_router)')
edit(d+'page_assets.py','    "lightwork-notification-health.js",','    "lightwork-notification-health.js",\n    "lightwork-personal-notifications.js",')
edit(d+'settings_catalog.py','    S("notifications", "delivery_receipts",','''    S("notifications", "personal_alerts", "Allow personal notification inboxes",
      "Let signed-in people choose subscriptions, quiet hours, daily digests and task mutes for their in-product inbox. Requires personal preferences. External personal delivery is not connected.",
      "toggle", "Integrations", False, "maverick.personal_notifications"),
    S("notifications", "personal_critical_bypass", "Allow personal approval and deadline exceptions",
      "Let people explicitly allow approval and deadline alerts to bypass their personal quiet hours and digest timing. Organization operational alerts are unchanged.",
      "toggle", "Integrations", False, "maverick.personal_notifications"),
    S("notifications", "delivery_receipts",''')
w='apps/installer-cli/maverick_installer/wizard.py'
edit(w,'    """Run-end notification webhook. Returns (config, env_vars_needed)."""\n    if not _q_confirm(','''    """Run-end notification webhook. Returns (config, env_vars_needed)."""
    personal_alerts = _q_confirm(
        "Allow signed-in people to use a personal in-product alert inbox with quiet hours and daily digests? Requires personal preferences; external personal delivery is not connected.",
        default=False,
    )
    personal_critical_bypass = personal_alerts and _q_confirm(
        "Allow people to opt approval/deadline alerts out of their quiet hours and digest timing?", default=False,
    )
    personal_config = {"personal_alerts": personal_alerts, "personal_critical_bypass": personal_critical_bypass}
    if not _q_confirm(''')
edit(w,'        "Get pinged when long runs finish? (ntfy / Pushover / Slack / Discord)",\n        default=False,\n    ):\n        return {}, []','        "Get pinged when long runs finish? (ntfy / Pushover / Slack / Discord)",\n        default=False,\n    ):\n        return personal_config, []')
edit(w,'{"backend": "ntfy", "ntfy_topic": topic, "delivery_receipts": delivery_receipts}','{**personal_config, "backend": "ntfy", "ntfy_topic": topic, "delivery_receipts": delivery_receipts}')
edit(w,'                "backend": "pushover",\n                "delivery_receipts": delivery_receipts,','                **personal_config,\n                "backend": "pushover",\n                "delivery_receipts": delivery_receipts,')
edit(w,'{"backend": "slack", "slack_webhook": "${SLACK_NOTIFY_WEBHOOK}", "delivery_receipts": delivery_receipts}','{**personal_config, "backend": "slack", "slack_webhook": "${SLACK_NOTIFY_WEBHOOK}", "delivery_receipts": delivery_receipts}')
edit(w,'{"backend": "discord", "discord_webhook": "${DISCORD_NOTIFY_WEBHOOK}", "delivery_receipts": delivery_receipts}','{**personal_config, "backend": "discord", "discord_webhook": "${DISCORD_NOTIFY_WEBHOOK}", "delivery_receipts": delivery_receipts}')
# Preserve personal preferences if a selected external channel has no topic.
edit(w,'"delivery_receipts": delivery_receipts}, []) if topic else ({}, [])','"delivery_receipts": delivery_receipts}, []) if topic else (personal_config, [])')

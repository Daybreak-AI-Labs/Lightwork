from pathlib import Path
def edit(name,old,new):
    p=Path(name);text=p.read_text(encoding='utf-8');assert old in text,(name,old[:80]);p.write_text(text.replace(old,new),encoding='utf-8',newline='\n')
d='packages/maverick-dashboard/maverick_dashboard/'
edit(d+'admin_pages.py','router = APIRouter()','from .notification_health import router as notification_health_router\n\nrouter = APIRouter()\nrouter.include_router(notification_health_router)')
edit(d+'page_assets.py','    "lightwork-model-guidance.js",','    "lightwork-model-guidance.js",\n    "lightwork-notification-health.js",')
edit(d+'settings_catalog.py','    S("notifications", "backend",','    S("notifications", "delivery_receipts", "Keep notification delivery history",\n      "Keep content-free delivery results for seven days, up to 1,000 attempts. View history and send a test on Notification delivery in Settings.",\n      "toggle", "Integrations", True, "maverick.notification_receipts"),\n    S("notifications", "backend",')
edit(d+'templates/settings.html','  <div class="section-head" data-settings-group="models"><h2>Models</h2></div>','  <div class="panel" data-settings-group="integrations"><h3>Notification delivery</h3><p>Check whether organization alerts reached the notification service.</p><a class="btn btn--ghost" href="/settings/notifications">Delivery history and test</a></div>\n\n  <div class="section-head" data-settings-group="models"><h2>Models</h2></div>')
w='apps/installer-cli/maverick_installer/wizard.py'
edit(w,'    pick = _q_select(\n        "  Backend:",','    delivery_receipts = _q_confirm(\n        "Keep content-free delivery results for seven days and enable in-product delivery tests?", default=True,\n    )\n    pick = _q_select(\n        "  Backend:",')
edit(w,'{"backend": "ntfy", "ntfy_topic": topic}','{"backend": "ntfy", "ntfy_topic": topic, "delivery_receipts": delivery_receipts}')
edit(w,'                "backend": "pushover",\n','                "backend": "pushover",\n                "delivery_receipts": delivery_receipts,\n')
edit(w,'{"backend": "slack", "slack_webhook": "${SLACK_NOTIFY_WEBHOOK}"}','{"backend": "slack", "slack_webhook": "${SLACK_NOTIFY_WEBHOOK}", "delivery_receipts": delivery_receipts}')
edit(w,'{"backend": "discord", "discord_webhook": "${DISCORD_NOTIFY_WEBHOOK}"}','{"backend": "discord", "discord_webhook": "${DISCORD_NOTIFY_WEBHOOK}", "delivery_receipts": delivery_receipts}')

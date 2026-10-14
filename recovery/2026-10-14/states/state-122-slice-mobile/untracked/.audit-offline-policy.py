from pathlib import Path
def edit(name,old,new):
 p=Path(name);s=p.read_text(encoding='utf-8');assert old in s,(name,old[:80]);p.write_text(s.replace(old,new),encoding='utf-8',newline='\n')
c='packages/maverick-core/maverick/'
d='packages/maverick-dashboard/maverick_dashboard/'
edit(c+'offline_bundle.py','    return {\n        "schema": SCHEMA,','    from .mobile_offline_policy import effective_policy\n    return {\n        "cache_policy": effective_policy(),\n        "schema": SCHEMA,')
edit(d+'api.py','    from maverick.offline_bundle import build_bundle\n\n    return await run_in_threadpool(','''    from maverick.offline_bundle import build_bundle

    if request.headers.get("X-Lightwork-Offline-Policy") != "1":
        raise HTTPException(status_code=403, detail="Update the companion to support organization offline-retention policy.")
    return await run_in_threadpool(''')
edit(d+'settings_catalog.py','    S("notifications", "personal_alerts",','''    S("mobile_offline", "enabled", "Allow offline copies on mobile devices",
      "Let current policy-aware companions keep a bounded offline task copy. People can turn saving off; workspace policy can only narrow this installation ceiling.",
      "toggle", "Data retention", True, "maverick.mobile_offline_policy"),
    S("mobile_offline", "max_age_seconds", "Keep mobile offline copies for at most",
      "A cached copy expires after this time, even while the device is offline. Shorter workspace limits take precedence.",
      "number", "Data retention", 86400, "maverick.mobile_offline_policy", minimum=1, maximum=1440,
      scale=60, integer=True, unit="minutes"),
    S("mobile_offline", "max_bytes", "Largest mobile offline copy",
      "Refuse larger offline copies instead of silently dropping parts of them.",
      "number", "Data retention", 2097152, "maverick.mobile_offline_policy", minimum=1, maximum=2048,
      scale=1024, integer=True, unit="KB"),
    S("notifications", "personal_alerts",''')
w='apps/installer-cli/maverick_installer/wizard.py'
edit(w,'    advanced["model_guidance"] = _q_confirm(','''    advanced["mobile_offline"] = {"enabled": _q_confirm(
        "Allow mobile companions to save task copies for up to 24 hours and 2 MB? Shorter limits are available in All settings.", default=True,
    ), "max_age_seconds": 86400, "max_bytes": 2097152}
    advanced["model_guidance"] = _q_confirm(''')
edit(w,'    lines += _cfg_table("notifications", notifications)','    lines += _cfg_table("notifications", notifications)\n    lines += _cfg_table("mobile_offline", (advanced or {}).get("mobile_offline"))')
mobile='apps/mobile-companion/'
edit(mobile+'src/api.ts','export type OfflineBundle = {','export type OfflinePolicy = {allowed: boolean; max_age_seconds: number; max_bytes: number; revision: string; message?: string};\n\nexport type OfflineBundle = {\n  cache_policy?: OfflinePolicy;')
edit(mobile+'src/api.ts','    const headers: Record<string, string> = { Accept: "application/json" };','    const headers: Record<string, string> = { Accept: "application/json" };') if False else None
edit(mobile+'src/api.ts','  const headers: Record<string, string> = { Accept: "application/json" };','  const headers: Record<string, string> = { Accept: "application/json" };\n  if (path === "/api/v1/offline/bundle") headers["X-Lightwork-Offline-Policy"] = "1";')
edit(mobile+'src/screens/SettingsScreen.tsx','Up to 2 MB is kept for 24 hours; turn this off and save to remove the previous cache.','Your organization can disable offline saving or set a shorter limit. Copies never exceed 2 MB or 24 hours; turn this off and save to remove the previous cache.')
edit(mobile+'App.tsx',"settings.offlineEnabled === false ? 'Offline saving is off' : cache.savedAt", "settings.offlineEnabled === false ? 'Offline saving is off' : cache.policyMessage || (cache.savedAt")
edit(mobile+'App.tsx',": 'Offline copy not ready'}", ": 'Offline copy not ready')}")

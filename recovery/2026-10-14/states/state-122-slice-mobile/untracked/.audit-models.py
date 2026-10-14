from pathlib import Path

def edit(name, old, new):
    path = Path(name)
    text = path.read_text(encoding='utf-8')
    assert old in text, (name, old[:70])
    path.write_text(text.replace(old, new), encoding='utf-8', newline='\n')

core = 'packages/maverick-core/maverick/'
dash = 'packages/maverick-dashboard/maverick_dashboard/'
edit(core+'model_discovery.py', '    free_models: list[str] | None = None,\n) -> dict[str, Any]:', '    free_models: list[str] | None = None,\n    capabilities: dict | None = None,\n) -> dict[str, Any]:')
edit(core+'model_discovery.py', '        "free_models": list(free_models or []),', '        "free_models": list(free_models or []),\n        "capabilities": capabilities or {},')
edit(core+'model_discovery.py', '    free: list[str] = []\n    seen:', '    free: list[str] = []\n    capabilities: dict = {}\n    seen:')
edit(core+'model_discovery.py', '            ids, token = parse_models(style, payload)', '            ids, token = parse_models(style, payload)\n            from .model_guidance import parse_capabilities\n            capabilities.update(parse_capabilities(style, payload))')
edit(core+'model_discovery.py', '        free_models=[m for m in _ids(free) if m in kept],', '        free_models=[m for m in _ids(free) if m in kept],\n        capabilities={m: capabilities[m] for m in kept if m in capabilities},')
edit(core+'model_discovery.py', '        out[name] = _entry(name, status, _ids(models), detail, fetched, free_models=_ids(free))', '''        from .model_guidance import clean_capabilities
        raw_caps = row.get("capabilities")
        capabilities = {m: clean_capabilities(raw_caps.get(m)) for m in _ids(models)} if isinstance(raw_caps, dict) else {}
        out[name] = _entry(name, status, _ids(models), detail, fetched,
                           free_models=_ids(free), capabilities=capabilities)''')
edit(dash+'model_pickers.py', '    known = {m["spec"] for g in groups for m in g["models"]}', '''    from maverick.model_guidance import describe, enabled
    guidance_enabled = enabled()
    metadata = {}
    if guidance_enabled:
        for group in groups:
            entry = discovered.get(group["name"]) or {}
            for model in group["models"]:
                metadata[model["spec"]] = describe(model["spec"], provider=group["name"],
                    capability=(entry.get("capabilities") or {}).get(model["id"]),
                    fetched_at=entry.get("fetched_at"), stale=bool(entry.get("stale", True)))
    known = {m["spec"] for g in groups for m in g["models"]}''')
edit(dash+'model_pickers.py', '        "model_groups": groups,', '        "model_groups": groups,\n        "model_guidance": metadata,\n        "model_guidance_enabled": guidance_enabled,')
edit(dash+'settings_catalog_runtime.py', '        S("models", "catalog",', '''        S("models", "guidance", "Show model choice guidance",
          "Show provider-reported capabilities and dated pricing evidence alongside model choices. Unknown details remain unknown.",
          "toggle", "AI models", True, "maverick.model_guidance", subgroup="Model choices"),
        S("models", "catalog",''')
wizard = 'apps/installer-cli/maverick_installer/wizard.py'
edit(wizard, 'def _cfg_role_models(role_models: dict[str, str], *, discovery: bool = True) -> list[str]:\n    if not role_models and discovery:', 'def _cfg_role_models(role_models: dict[str, str], *, discovery: bool = True, guidance: bool = True) -> list[str]:\n    if not role_models and discovery and guidance:')
edit(wizard, '    lines = ["[models]"]\n    for role, spec', '    lines = ["[models]"]\n    if not guidance:\n        lines.append("guidance = false")\n    for role, spec')
edit(wizard, '    advanced["model_discovery"] = _q_confirm(', '''    advanced["model_guidance"] = _q_confirm(
        "Show dated model pricing evidence and provider-reported capabilities in Settings?",
        default=True,
    )
    advanced["model_discovery"] = _q_confirm(''')
edit(wizard, 'role_models, discovery=advanced.get("model_discovery") is not False,', 'role_models, discovery=advanced.get("model_discovery") is not False,\n        guidance=advanced.get("model_guidance") is not False,')
edit(dash+'templates/settings.html', '  <div class="section-head" data-settings-group="models"><h2>Default model</h2></div>', '''  {% if model_guidance_enabled %}
  <div class="panel" data-settings-group="models">
    <h3>Find a suitable model</h3>
    <label>Filter choices <select class="select" id="model-capability-filter">
      <option value="all">All approved choices</option><option value="tools">Known tool support</option>
      <option value="vision">Known image support</option><option value="self_hosted">Local or self-hosted</option>
      <option value="unknown">Unknown capabilities</option>
    </select></label>
    <p class="muted">Filters use reported evidence. Missing metadata does not mean a model lacks a capability. Other model (advanced) stays available and the approved-model policy still applies.</p>
    <p class="muted">Refresh model lists above checks availability with a bounded request; it does not run a paid generation or prove tool quality. Pricing is a dated planning reference, not a final invoice.</p>
    <script type="application/json" id="model-guidance-data">{{ model_guidance|tojson }}</script>
  </div>
  {% endif %}

  <div class="section-head" data-settings-group="models"><h2>Default model</h2></div>''')
edit(dash+'templates/settings.html', '  <script src="/static/lightwork-voice-setup.js" defer></script>', '  <script src="/static/lightwork-model-guidance.js" defer></script>\n  <script src="/static/lightwork-voice-setup.js" defer></script>')

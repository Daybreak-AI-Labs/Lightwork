exec(open('.audit-core-fix.py', encoding='utf-8').read().split("core = ")[0])
core = 'packages/maverick-core/tests/'
inst = 'apps/installer-cli/tests/'
replace(inst + 'test_wizard_parity.py',
    'cfg, envs = pick_notifications()\n    assert cfg == {}',
    'cfg, envs = pick_notifications()\n    assert cfg == {"personal_alerts": False, "personal_critical_bypass": False, "personal_background": False}')
replace(inst + 'test_wizard_parity.py',
    'assert cfg == {"backend": "ntfy", "ntfy_topic": "my-alerts"}',
    'assert cfg == {"backend": "ntfy", "ntfy_topic": "my-alerts", "delivery_receipts": True,\n'
    '                   "personal_alerts": True, "personal_critical_bypass": True, "personal_background": True,\n'
    '                   "personal_external": True, "personal_dashboard_url": "my-alerts"}')
replace(inst + 'test_wizard_consumer_mode.py',
    'assert wizard.pick_budget() == {"strict_pricing": True}',
    'monkeypatch.setattr(wizard, "_q_text", lambda *_a, **kw: kw["default"])\n'
    '    assert wizard.pick_budget() == {"strict_pricing": True, "guidance": True, "warnings": True,\n'
    '                                    "warning_thresholds": [0.8, 0.95, 1.0]}')
replace(inst + 'test_wizard_pricing.py',
    '            True,\n        )\n    ]',
    '            True,\n        ),\n'
    '        ("  Show known budget headroom before work? This never changes execution limits.", True),\n'
    '        ("  Show in-product warnings as tasks approach their limits?", True),\n'
    '    ]\n    assert budget["guidance"] is True and budget["warnings"] is True\n'
    '    assert budget["warning_thresholds"] == [0.8, 0.95, 1.0]')
replace(inst + 'test_wizard_knowledge_publication.py',
    '"enable": True, "embedder": "deterministic", "store": "qdrant",',
    '"enable": True, "embedder": "deterministic", "store": "qdrant", "reindex_jobs": True,')
features = '''        "personal_preferences": True, "configuration_profiles": True,
        "employee_task_diagnostics": True, "employee_activation": True,
        "deliverable_bundles": False, "project_workspaces": False, "task_handoffs": False,
        "employee_companions": False, "employee_content_access_requests": False,
        "permission_access_requests": False, "employee_departures": False, "personal_data_controls": False,
'''
replace(core + 'test_features_wiring.py', '    assert get_features() == {\n', '    assert get_features() == {\n' + features)
replace(core + 'test_ai_evidence_local_parsers.py',
    '    link.symlink_to(real)\n',
    '    try:\n        link.symlink_to(real)\n    except OSError as exc:\n'
    '        if getattr(exc, "winerror", None) == 1314:\n'
    '            pytest.skip("Windows symlink creation privilege is unavailable")\n'
    '        raise\n')
replace(core + 'test_tenancy.py',
    'assert _memory_root() == tmp_path / ".maverick" / "memory"  # legacy default',
    'shared = _memory_root()\n    assert shared.parent == paths.data_dir("memory") / "owned"\n    assert len(shared.name) == 64')
replace(core + 'test_tenancy.py',
    'assert _memory_root() == tmp_path / ".maverick" / "tenants" / "acme" / "memory"',
    'assert _memory_root().parent == paths.data_dir("memory") / "owned"\n        assert _memory_root() != shared')
replace(core + 'test_tenancy.py',
    'assert _memory_root() == tmp_path / "mem"  # override ignores tenant',
    'assert _memory_root().parent == tmp_path / "mem" / "owned"\n        assert len(_memory_root().name) == 64')
replace(core + 'test_tenancy.py',
    '        with pytest.raises(TenantNamespaceCollision):\n            _run({"command": "view", "path": "private.md"})',
    '        result = _run({"command": "view", "path": "private.md"})\n'
    '        assert result == "ERROR: Current memory ownership could not be verified."\n'
    '        assert "secret" not in result')
replace(core + 'test_tenancy.py',
    'def test_memory_store_cannot_be_reopened_through_case_alias(monkeypatch, tmp_path):\n    from maverick.paths import TenantNamespaceCollision\n',
    'def test_memory_store_cannot_be_reopened_through_case_alias(monkeypatch, tmp_path):\n')

"""[auth.local_owner] totp: the owner's authenticator app setting reads as optional unless set otherwise."""
from __future__ import annotations

import pytest
from maverick import config


def _with_global(monkeypatch, cfg):
    monkeypatch.setattr("maverick.config.load_global_config", lambda *a, **k: cfg)


def test_the_default_is_optional(monkeypatch):
    _with_global(monkeypatch, {})
    assert config.get_auth_local_owner_totp() == "optional"


@pytest.mark.parametrize("value, expected", [
    ("required", "required"),
    ("off", "off"),
    (" OFF ", "off"),
    ("optional", "optional"),
    ("maybe", "optional"),
    ("", "optional"),
    (True, "optional"),
    (None, "optional"),
    (["required"], "optional"),
])
def test_only_the_three_modes_are_read_and_anything_else_is_optional(monkeypatch, value, expected):
    _with_global(monkeypatch, {"auth": {"local_owner": {"totp": value}}})
    assert config.get_auth_local_owner_totp() == expected


@pytest.mark.parametrize("cfg", [
    {"auth": "not a table"},
    {"auth": {"local_owner": "not a table"}},
    {"auth": {"local_owner": None}},
])
def test_a_malformed_table_reads_as_optional(monkeypatch, cfg):
    _with_global(monkeypatch, cfg)
    assert config.get_auth_local_owner_totp() == "optional"


def test_an_unreadable_config_reads_as_optional(monkeypatch):
    def broken(*a, **k):
        raise OSError("config unreadable")

    monkeypatch.setattr("maverick.config.load_global_config", broken)
    assert config.get_auth_local_owner_totp() == "optional"

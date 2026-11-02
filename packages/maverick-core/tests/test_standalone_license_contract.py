"""Standalone licenses remain bounded when signed payloads are malformed."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def signing_key(monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    monkeypatch.setenv("LIGHTWORK_LICENSE_PUBKEY", public)
    return key


def _token(key, payload):
    body = json.dumps(payload).encode()

    def encode(data):
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    return f"LW1.{encode(body)}.{encode(key.sign(body))}"


def _load(demo):
    path = ROOT / "demo" / demo / "license_kit.py"
    spec = importlib.util.spec_from_file_location("standalone_license_kit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("demo", ["pia-concierge", "dsar-concierge"])
@pytest.mark.parametrize("payload", [
    [],
    {"expires_at": "not a timestamp"},
    {"expires_at": float("nan")},
    {"expires_at": float("inf")},
    {"expires_at": 4102444800, "capabilities": 42},
    {"expires_at": 4102444800, "capabilities": ["read", None]},
])
def test_bad_signed_license_stays_in_evaluation_mode(demo, payload, signing_key, monkeypatch):
    monkeypatch.setenv("LIGHTWORK_LICENSE", _token(signing_key, payload))
    license_info = _load(demo).load_license()
    assert license_info["mode"] == "evaluation"
    assert license_info["open_case_cap"] == 5
    assert license_info["reason"]


@pytest.mark.parametrize("payload", [
    [],
    {"expires_at": "not a timestamp"},
    {"expires_at": float("nan")},
    {"expires_at": float("inf")},
    {"expires_at": 4102444800, "capabilities": 42},
    {"expires_at": 4102444800, "capabilities": ["read", None]},
])
def test_platform_verifier_rejects_bad_signed_payloads(payload, signing_key):
    from maverick.licensing import LicenseError, verify_license

    with pytest.raises(LicenseError):
        verify_license(_token(signing_key, payload), os.environ["LIGHTWORK_LICENSE_PUBKEY"])


@pytest.mark.parametrize("demo", ["pia-concierge", "dsar-concierge"])
def test_valid_and_expired_signed_licenses_keep_their_contract(demo, signing_key, monkeypatch):
    module = _load(demo)
    monkeypatch.setenv("LIGHTWORK_LICENSE", _token(signing_key, {
        "customer": "Example", "sku": "standalone", "expires_at": "4102444800",
        "capabilities": ["read"],
    }))
    result = module.load_license()
    assert result["mode"] == "licensed"
    assert result["capabilities"] == ["read"]
    assert result["open_case_cap"] is None

    monkeypatch.setenv("LIGHTWORK_LICENSE", _token(signing_key, {
        "customer": "Example", "sku": "standalone", "expires_at": 1,
    }))
    result = module.load_license()
    assert result["mode"] == "evaluation"
    assert result["reason"] == "license has expired"

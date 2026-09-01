from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from maverick.consent_native_sdk import (
    NativeApplicationAuthority,
    NativeConsentDeviceRegistry,
    NativeConsentGateway,
    NativeDeviceAuthority,
    VerifiedNativeAttestation,
    build_envelope,
)
from maverick_dashboard.consent_native_sdk_api import (
    NativeConsentSDKAPIDependencies,
    create_consent_native_sdk_router,
)


def _digest(value: bytes | str) -> str:
    raw = value if isinstance(value, bytes) else value.encode()
    return hashlib.sha256(raw).hexdigest()


def _public_hex(private: Ed25519PrivateKey) -> str:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ).hex()


class _Signer:
    key_id = "native-receipt"
    key_revision = 1

    def __init__(self, private: Ed25519PrivateKey) -> None:
        self.private = private

    def sign(self, body: bytes) -> bytes:
        return self.private.sign(body)


class _Verifier:
    def verify(self, evidence, material):
        assert _digest(material) == evidence.attestation_evidence_sha256
        return VerifiedNativeAttestation(
            authority="play-integrity",
            application_ref=evidence.application_ref,
            tenant_id=evidence.tenant_id,
            application_id=evidence.application_id,
            platform=evidence.platform,
            install_subject_sha256=evidence.install_subject_sha256,
            public_key_hex=evidence.public_key_hex,
            attestation_nonce=evidence.attestation_nonce,
            attestation_evidence_sha256=evidence.attestation_evidence_sha256,
            verified_at=1_000,
            valid_until=2_000,
            verdict="allow",
            sdk_version=evidence.sdk_version,
        )


def _authority() -> NativeApplicationAuthority:
    return NativeApplicationAuthority(
        application_ref="native-prod",
        tenant_id="tenant-one",
        property_id="property-one",
        application_id="com.example.native",
        platform="android",
        public_client_credential_sha256=_digest("client"),
        public_client_revision=1,
        public_client_record_sha256=_digest("client-record"),
        property_revision=1,
        property_sha256=_digest("property"),
        publication_sha256=_digest("publication"),
        publication_bundle_sha256=_digest("bundle"),
        configuration_sha256=_digest("configuration"),
        policy_id="policy-one",
        policy_version=1,
        policy_sha256=_digest("policy"),
        standards_contract_sha256=_digest("standards"),
        minimum_sdk_version=1,
        default_locale="en-US",
        locales=("en-US",),
    )


def test_enrollment_and_signed_dispatch_are_mounted_and_private(tmp_path) -> None:
    authority = _authority()
    registry = NativeConsentDeviceRegistry(tmp_path / "native.sqlite3", clock=lambda: 1_000)
    registry.register_application(authority, expected_revision=0)
    receipt_private = Ed25519PrivateKey.generate()
    gateway = NativeConsentGateway(
        registry,
        client_authority_resolver=lambda _value: SimpleNamespace(
            credential_sha256=authority.public_client_credential_sha256,
            tenant_id=authority.tenant_id,
            property_id=authority.property_id,
            client_kind="service",
            service_client_id=authority.application_ref,
            target_bundle_id=authority.application_id,
            target_platform=authority.platform,
            revision=authority.public_client_revision,
            record_sha256=authority.public_client_record_sha256,
            property_revision=authority.property_revision,
            property_sha256=authority.property_sha256,
            publication_sha256=authority.publication_sha256,
            bundle_sha256=authority.publication_bundle_sha256,
            policy_id=authority.policy_id,
            policy_version=authority.policy_version,
            policy_sha256=authority.policy_sha256,
        ),
        handler=lambda *_args: {"configuration_sha256": authority.configuration_sha256},
        receipt_signer=_Signer(receipt_private),
    )
    calls = []
    deps = NativeConsentSDKAPIDependencies(
        gate=lambda: True,
        registry=registry,
        attestation_verifier=_Verifier(),
        gateway=gateway,
        rate_limit=lambda subject, operation: calls.append((subject, operation)),
    )
    app = FastAPI()
    app.include_router(create_consent_native_sdk_router(deps))
    hostile_client = TestClient(app, headers={"Origin": "https://attacker.invalid"})
    material = b"play-integrity-statement"
    private = Ed25519PrivateKey.generate()
    enrollment = {
        "application_ref": authority.application_ref,
        "tenant_id": authority.tenant_id,
        "application_id": authority.application_id,
        "platform": authority.platform,
        "install_subject_sha256": _digest("installation"),
        "public_key_hex": _public_hex(private),
        "attestation_nonce": "nonce-one",
        "attestation_evidence_sha256": _digest(material),
        "attestation_material_b64url": base64.urlsafe_b64encode(material).rstrip(b"=").decode(),
        "issued_at": 999,
        "expires_at": 1_120,
        "sdk_version": 1,
    }
    rejected = hostile_client.post("/api/v1/public/consent/native/enroll", json=enrollment)
    assert rejected.status_code == 401
    # Exact-origin Tizen/webOS JSON POSTs are admitted; ambient credentials are not.
    native_client = TestClient(app, headers={"Origin": "http://testserver"})
    enrolled = native_client.post(
        "/api/v1/public/consent/native/enroll",
        json=enrollment,
    )
    assert enrolled.status_code == 201
    assert enrolled.headers["cache-control"] == "no-store, private, max-age=0"
    device = NativeDeviceAuthority(**enrolled.json()["device"])
    envelope = build_envelope(
        authority,
        device,
        request_id="request-one",
        counter=1,
        operation="configuration",
        payload={},
        signer=_Signer(private),
        issued_at=1_000,
    )
    dispatched = native_client.post(
        "/api/v1/public/consent/native/dispatch",
        json=asdict(envelope),
    )
    assert dispatched.status_code == 200
    assert dispatched.json()["request_id"] == "request-one"
    assert dispatched.json()["result"]["configuration_sha256"] == authority.configuration_sha256
    assert len(dispatched.json()["signature_hex"]) == 128
    signed_payload = dispatched.json()["signed_payload_b64url"]
    decoded = base64.urlsafe_b64decode(signed_payload + "=" * (-len(signed_payload) % 4))
    assert json.loads(decoded)["request_id"] == "request-one"
    receipt_private.public_key().verify(bytes.fromhex(dispatched.json()["signature_hex"]), decoded)

    refresh_material = b"play-integrity-refresh-statement"
    refresh_request = {
        **enrollment,
        "device_id": device.device_id,
        "expected_revision": device.revision,
        "attestation_nonce": "nonce-refresh-one",
        "attestation_evidence_sha256": _digest(refresh_material),
        "attestation_material_b64url": base64.urlsafe_b64encode(refresh_material)
        .rstrip(b"=")
        .decode(),
    }
    credentialed_client = TestClient(
        app,
        headers={"Origin": "http://testserver", "Cookie": "session=ambient"},
    )
    assert (
        credentialed_client.post(
            "/api/v1/public/consent/native/refresh",
            json=refresh_request,
        ).status_code
        == 401
    )
    refreshed_response = native_client.post(
        "/api/v1/public/consent/native/refresh",
        json=refresh_request,
    )
    assert refreshed_response.status_code == 200
    refreshed = NativeDeviceAuthority(**refreshed_response.json()["device"])
    assert refreshed.revision == device.revision + 1
    assert refreshed.key_revision == device.key_revision
    assert refreshed.public_key_hex == device.public_key_hex
    assert refreshed.application_authority_sha256 == authority.authority_sha256

    replayed = native_client.post(
        "/api/v1/public/consent/native/refresh",
        json=refresh_request,
    )
    assert replayed.status_code == 409
    rebound_material = b"play-integrity-rebound-statement"
    rebound = {
        **refresh_request,
        "expected_revision": refreshed.revision,
        "public_key_hex": _public_hex(Ed25519PrivateKey.generate()),
        "attestation_nonce": "nonce-refresh-rebound",
        "attestation_evidence_sha256": _digest(rebound_material),
        "attestation_material_b64url": base64.urlsafe_b64encode(rebound_material)
        .rstrip(b"=")
        .decode(),
    }
    assert (
        native_client.post("/api/v1/public/consent/native/refresh", json=rebound).status_code
        == 401
    )
    assert (
        native_client.post(
            "/api/v1/public/consent/native/dispatch",
            json=asdict(envelope),
        ).status_code
        == 409
    )
    next_envelope = build_envelope(
        authority,
        refreshed,
        request_id="request-two",
        counter=2,
        operation="configuration",
        payload={},
        signer=_Signer(private),
        issued_at=1_000,
    )
    assert (
        native_client.post(
            "/api/v1/public/consent/native/dispatch",
            json=asdict(next_envelope),
        ).status_code
        == 200
    )
    assert [operation for _subject, operation in calls] == [
        "enroll",
        "dispatch",
        "refresh",
        "refresh",
        "refresh",
        "dispatch",
        "dispatch",
    ]


def test_disabled_native_boundary_is_not_discoverable(tmp_path) -> None:
    authority = _authority()
    registry = NativeConsentDeviceRegistry(tmp_path / "native.sqlite3", clock=lambda: 1_000)
    registry.register_application(authority, expected_revision=0)
    gateway = NativeConsentGateway(
        registry,
        client_authority_resolver=lambda _value: None,
        handler=lambda *_args: {},
        receipt_signer=_Signer(Ed25519PrivateKey.generate()),
    )
    app = FastAPI()
    app.include_router(
        create_consent_native_sdk_router(
            NativeConsentSDKAPIDependencies(
                gate=lambda: False,
                registry=registry,
                attestation_verifier=_Verifier(),
                gateway=gateway,
                rate_limit=lambda *_args: None,
            )
        )
    )
    material = b"attestation"
    response = TestClient(app).post(
        "/api/v1/public/consent/native/enroll",
        json={
            "application_ref": authority.application_ref,
            "tenant_id": authority.tenant_id,
            "application_id": authority.application_id,
            "platform": authority.platform,
            "install_subject_sha256": _digest("installation"),
            "public_key_hex": _public_hex(Ed25519PrivateKey.generate()),
            "attestation_nonce": "nonce-one",
            "attestation_evidence_sha256": _digest(material),
            "attestation_material_b64url": base64.urlsafe_b64encode(material).rstrip(b"=").decode(),
            "issued_at": 999,
            "expires_at": 1_120,
            "sdk_version": 1,
        },
    )
    assert response.status_code == 404

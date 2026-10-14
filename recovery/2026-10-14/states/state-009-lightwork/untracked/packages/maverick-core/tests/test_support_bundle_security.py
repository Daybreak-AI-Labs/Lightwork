"""Adversarial checks for the production support-bundle boundary."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from maverick import support_bundle as support
from maverick.file_lock import (
    atomic_create_bytes,
    ensure_private_directory,
    private_path_is_restricted,
)


def _key_pair(root: Path, name: str = "recipient") -> tuple[Path, Path, str]:
    custody = root / name
    ensure_private_directory(custody)
    private = X25519PrivateKey.generate()
    private_path = custody / "private.pem"
    public_path = custody / "public.pem"
    atomic_create_bytes(
        private_path,
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    atomic_create_bytes(
        public_path,
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )
    return private_path, public_path, support.recipient_public_key_sha256(public_path)


def _private_output_dir(root: Path, name: str = "review") -> Path:
    path = root / name
    ensure_private_directory(path)
    return path


def _bundle(monkeypatch, marker: str = "customer-case-981734") -> dict:
    monkeypatch.setattr(
        "maverick.config.load_config",
        lambda: {"support_context": marker, "api_key": "sk-LEAKME"},  # pragma: allowlist secret
    )
    bundle = support.collect_production()
    assert bundle["config_redacted"]["support_context"] == marker
    assert bundle["config_redacted"]["api_key"] == "[REDACTED]"
    return bundle


def _sealed_round_trip(tmp_path: Path, monkeypatch):
    marker = "customer-case-981734"
    bundle = _bundle(monkeypatch, marker)
    review = _private_output_dir(tmp_path)
    private_path, public_path, fingerprint = _key_pair(tmp_path)
    preview = support.write_preview(review / "preview.json", bundle)
    envelope = tmp_path / "case.mvsupport"
    receipt = support.publish_encrypted_preview(
        preview.path,
        envelope,
        public_path,
        consent_sha256=preview.bundle_sha256,
        expected_recipient_key_sha256=fingerprint,
    )
    return bundle, marker, preview, receipt, private_path, public_path, fingerprint


def test_production_round_trip_publishes_ciphertext_only(tmp_path, monkeypatch):
    bundle, marker, _preview, receipt, private_path, _public, fingerprint = (
        _sealed_round_trip(tmp_path, monkeypatch)
    )

    envelope_bytes = receipt.path.read_bytes()
    assert private_path_is_restricted(_preview.path)
    assert private_path_is_restricted(receipt.path)
    assert marker.encode() not in envelope_bytes
    assert b"config_redacted" not in envelope_bytes
    assert json.loads(envelope_bytes)["algorithm"] == support.ENVELOPE_ALGORITHM
    opened = support.open_encrypted_bundle(
        receipt.path,
        private_path,
        expected_recipient_key_sha256=fingerprint,
    )
    assert opened == bundle


def test_wrong_private_key_and_tampered_ciphertext_fail_closed(tmp_path, monkeypatch):
    _bundle_value, marker, _preview, receipt, _private, _public, fingerprint = (
        _sealed_round_trip(tmp_path, monkeypatch)
    )
    wrong_private, _wrong_public, _wrong_fingerprint = _key_pair(tmp_path, "wrong")
    with pytest.raises(support.SupportBundleSecurityError):
        support.open_encrypted_bundle(receipt.path, wrong_private)

    envelope = json.loads(receipt.path.read_bytes())
    ciphertext = bytearray(base64.b64decode(envelope["ciphertext"]))
    ciphertext[-1] ^= 1
    envelope["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
    tampered = tmp_path / "tampered.mvsupport"
    atomic_create_bytes(tampered, json.dumps(envelope, sort_keys=True).encode())
    with pytest.raises(support.SupportBundleSecurityError) as exc:
        support.open_encrypted_bundle(
            tampered,
            _private,
            expected_recipient_key_sha256=fingerprint,
        )
    assert marker not in str(exc.value)


def test_consent_and_recipient_pin_are_both_required(tmp_path, monkeypatch):
    bundle = _bundle(monkeypatch)
    review = _private_output_dir(tmp_path)
    preview = support.write_preview(review / "preview.json", bundle)
    _private, public, fingerprint = _key_pair(tmp_path)

    output = tmp_path / "refused.mvsupport"
    with pytest.raises(support.SupportBundleConsentError):
        support.publish_encrypted_preview(
            preview.path,
            output,
            public,
            consent_sha256="0" * 64,
            expected_recipient_key_sha256=fingerprint,
        )
    assert not output.exists()

    with pytest.raises(support.SupportBundleSecurityError):
        support.publish_encrypted_preview(
            preview.path,
            output,
            public,
            consent_sha256=preview.bundle_sha256,
            expected_recipient_key_sha256="0" * 64,
        )
    assert not output.exists()


def test_valid_but_substituted_recipient_key_is_rejected(tmp_path, monkeypatch):
    bundle = _bundle(monkeypatch)
    review = _private_output_dir(tmp_path)
    preview = support.write_preview(review / "preview.json", bundle)
    _private, _public, pinned = _key_pair(tmp_path, "pinned")
    _attacker_private, attacker_public, _attacker_pin = _key_pair(
        tmp_path, "attacker"
    )
    output = tmp_path / "substituted.mvsupport"

    with pytest.raises(support.SupportBundleSecurityError):
        support.publish_encrypted_preview(
            preview.path,
            output,
            attacker_public,
            consent_sha256=preview.bundle_sha256,
            expected_recipient_key_sha256=pinned,
        )
    assert not output.exists()

    appended = tmp_path / "appended-public.pem"
    atomic_create_bytes(appended, _public.read_bytes() + b"ignored-trailing-data")
    with pytest.raises(support.SupportBundleSecurityError):
        support.publish_encrypted_preview(
            preview.path,
            output,
            appended,
            consent_sha256=preview.bundle_sha256,
            expected_recipient_key_sha256=pinned,
        )
    assert not output.exists()


def test_existing_and_symlink_destinations_are_never_overwritten(tmp_path, monkeypatch):
    bundle = _bundle(monkeypatch)
    review = _private_output_dir(tmp_path)
    existing_preview = review / "existing-preview.json"
    atomic_create_bytes(existing_preview, b"keep-preview")
    with pytest.raises(support.SupportBundleSecurityError):
        support.write_preview(existing_preview, bundle)
    assert existing_preview.read_bytes() == b"keep-preview"

    preview = support.write_preview(review / "preview.json", bundle)
    _private, public, fingerprint = _key_pair(tmp_path)

    existing = tmp_path / "existing.mvsupport"
    existing.write_bytes(b"keep-me")
    with pytest.raises(support.SupportBundleSecurityError):
        support.publish_encrypted_preview(
            preview.path,
            existing,
            public,
            consent_sha256=preview.bundle_sha256,
            expected_recipient_key_sha256=fingerprint,
        )
    assert existing.read_bytes() == b"keep-me"

    outside = tmp_path / "outside"
    outside.write_bytes(b"outside-kept")
    linked = tmp_path / "linked.mvsupport"
    try:
        linked.symlink_to(outside)
    except OSError:
        # The reparse simulation below exercises the Windows-equivalent branch
        # on hosts where unprivileged symlink creation is unavailable.
        return
    with pytest.raises(support.SupportBundleSecurityError):
        support.publish_encrypted_preview(
            preview.path,
            linked,
            public,
            consent_sha256=preview.bundle_sha256,
            expected_recipient_key_sha256=fingerprint,
        )
    assert outside.read_bytes() == b"outside-kept"


def test_reparse_parent_is_refused_even_when_destination_is_absent(
    tmp_path, monkeypatch,
):
    bundle = _bundle(monkeypatch)
    review = _private_output_dir(tmp_path)
    preview = support.write_preview(review / "preview.json", bundle)
    _private, public, fingerprint = _key_pair(tmp_path)
    redirected_parent = tmp_path / "redirected"
    redirected_parent.mkdir()
    original = getattr(Path, "is_junction", None)

    def simulated_junction(path: Path) -> bool:
        if path == redirected_parent:
            return True
        return bool(callable(original) and original(path))

    monkeypatch.setattr(Path, "is_junction", simulated_junction, raising=False)
    with pytest.raises(support.SupportBundleSecurityError):
        support.publish_encrypted_preview(
            preview.path,
            redirected_parent / "case.mvsupport",
            public,
            consent_sha256=preview.bundle_sha256,
            expected_recipient_key_sha256=fingerprint,
        )
    assert not (redirected_parent / "case.mvsupport").exists()


def test_hard_linked_key_and_preview_are_refused(tmp_path, monkeypatch):
    bundle = _bundle(monkeypatch)
    review = _private_output_dir(tmp_path)
    preview = support.write_preview(review / "preview.json", bundle)
    _private, public, fingerprint = _key_pair(tmp_path)

    public_link = tmp_path / "public-hardlink.pem"
    public_link.hardlink_to(public)
    with pytest.raises(support.SupportBundleSecurityError):
        support.publish_encrypted_preview(
            preview.path,
            tmp_path / "hardlinked-key.mvsupport",
            public_link,
            consent_sha256=preview.bundle_sha256,
            expected_recipient_key_sha256=fingerprint,
        )
    public_link.unlink()

    preview_link = tmp_path / "preview-hardlink.json"
    preview_link.hardlink_to(preview.path)
    with pytest.raises(support.SupportBundleSecurityError):
        support.publish_encrypted_preview(
            preview_link,
            tmp_path / "hardlinked-preview.mvsupport",
            public,
            consent_sha256=preview.bundle_sha256,
            expected_recipient_key_sha256=fingerprint,
        )


def test_key_and_preview_aliases_are_refused(tmp_path, monkeypatch):
    bundle = _bundle(monkeypatch)
    review = _private_output_dir(tmp_path)
    preview = support.write_preview(review / "preview.json", bundle)
    _private, public, fingerprint = _key_pair(tmp_path)
    public_alias = tmp_path / "public-alias.pem"
    preview_alias = tmp_path / "preview-alias.json"
    try:
        public_alias.symlink_to(public)
        preview_alias.symlink_to(preview.path)
    except OSError:
        # Hard-link and reparse-point cases cover the same no-alias reader
        # boundary on Windows hosts without symlink privilege.
        return

    with pytest.raises(support.SupportBundleSecurityError):
        support.publish_encrypted_preview(
            preview.path,
            tmp_path / "bad-key.mvsupport",
            public_alias,
            consent_sha256=preview.bundle_sha256,
            expected_recipient_key_sha256=fingerprint,
        )
    with pytest.raises(support.SupportBundleSecurityError):
        support.publish_encrypted_preview(
            preview_alias,
            tmp_path / "bad-preview.mvsupport",
            public,
            consent_sha256=preview.bundle_sha256,
            expected_recipient_key_sha256=fingerprint,
        )


def test_recursive_and_oversized_collection_inputs_fail_closed(monkeypatch):
    recursive: dict = {}
    recursive["child"] = recursive
    monkeypatch.setattr("maverick.config.load_config", lambda: recursive)
    with pytest.raises(support.SupportBundleValidationError, match="recursive"):
        support.collect_production()

    secret_recursive: dict = {}
    secret_recursive["api_key"] = secret_recursive
    monkeypatch.setattr("maverick.config.load_config", lambda: secret_recursive)
    with pytest.raises(support.SupportBundleValidationError, match="recursive"):
        support.collect_production()

    monkeypatch.setattr(
        "maverick.config.load_config",
        lambda: {"diagnostic": "x" * (support.MAX_STRING_BYTES + 1)},
    )
    with pytest.raises(support.SupportBundleValidationError, match="oversized"):
        support.collect_production()


def test_redactor_and_verifier_failure_never_create_preview(tmp_path, monkeypatch):
    from maverick import secrets

    review = _private_output_dir(tmp_path)
    destination = review / "blocked.json"
    monkeypatch.setattr(secrets, "scrub", lambda _value: (_ for _ in ()).throw(
        RuntimeError("redactor offline")
    ))

    with pytest.raises(support.SupportBundleValidationError):
        support.write_preview(destination)
    assert not destination.exists()


def test_classifier_and_post_redaction_verifier_fail_before_preview(
    tmp_path, monkeypatch,
):
    review = _private_output_dir(tmp_path)
    classifier_destination = review / "classifier-blocked.json"
    original_classifier = support._classification
    monkeypatch.setattr(
        support,
        "_classification",
        lambda: (_ for _ in ()).throw(RuntimeError("classifier offline")),
    )
    with pytest.raises(support.SupportBundleValidationError, match="classification"):
        support.write_preview(classifier_destination)
    assert not classifier_destination.exists()

    monkeypatch.setattr(support, "_classification", original_classifier)
    bundle = _bundle(monkeypatch)
    verifier_destination = review / "verifier-blocked.json"
    monkeypatch.setattr(
        support,
        "_verify_no_recognized_secrets",
        lambda _raw: (_ for _ in ()).throw(
            support.SupportBundleValidationError("verifier offline")
        ),
    )
    with pytest.raises(support.SupportBundleValidationError, match="verifier"):
        support.write_preview(verifier_destination, bundle)
    assert not verifier_destination.exists()


def test_encryption_failure_occurs_before_atomic_publication(tmp_path, monkeypatch):
    bundle = _bundle(monkeypatch)
    review = _private_output_dir(tmp_path)
    preview = support.write_preview(review / "preview.json", bundle)
    _private, public, fingerprint = _key_pair(tmp_path)
    destination = review / "encryption-blocked.mvsupport"
    monkeypatch.setattr(
        support.os,
        "urandom",
        lambda _size: (_ for _ in ()).throw(RuntimeError("entropy offline")),
    )
    with pytest.raises(support.SupportBundleSecurityError, match="encryption"):
        support.publish_encrypted_preview(
            preview.path,
            destination,
            public,
            consent_sha256=preview.bundle_sha256,
            expected_recipient_key_sha256=fingerprint,
        )
    assert not destination.exists()


def test_required_audit_failure_prevents_preview_and_envelope_publication(
    tmp_path, monkeypatch,
):
    from maverick import audit, production_scope

    marker = "audit-must-not-see-this-case-body"
    bundle = _bundle(monkeypatch, marker)
    review = _private_output_dir(tmp_path)
    existing_preview = support.write_preview(review / "existing.json", bundle)
    _private, public, fingerprint = _key_pair(tmp_path)
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)

    def refuse(kind: str, **fields):
        calls.append((kind, fields))
        raise audit.AuditRequiredWriteFailed("audit unavailable")

    monkeypatch.setattr(audit, "required_audit_event", refuse)
    blocked_preview = review / "blocked-preview.json"
    with pytest.raises(audit.AuditRequiredWriteFailed):
        support.write_preview(blocked_preview, bundle)
    assert not blocked_preview.exists()

    blocked_envelope = review / "blocked-envelope.mvsupport"
    with pytest.raises(audit.AuditRequiredWriteFailed):
        support.publish_encrypted_preview(
            existing_preview.path,
            blocked_envelope,
            public,
            consent_sha256=existing_preview.bundle_sha256,
            expected_recipient_key_sha256=fingerprint,
        )
    assert not blocked_envelope.exists()
    assert marker not in json.dumps(calls)


def test_preview_parser_rejects_duplicate_keys_and_extra_schema(tmp_path, monkeypatch):
    bundle = _bundle(monkeypatch)
    review = _private_output_dir(tmp_path)

    duplicate = review / "duplicate.json"
    raw = json.dumps(bundle, sort_keys=True)
    atomic_create_bytes(
        duplicate,
        (f'{{"schema":"{support.BUNDLE_SCHEMA}",' + raw[1:]).encode(),
    )
    with pytest.raises(support.SupportBundleValidationError, match="duplicate"):
        support.load_preview(duplicate)

    extra = dict(bundle)
    extra["unreviewed"] = True
    extra_path = review / "extra.json"
    atomic_create_bytes(extra_path, json.dumps(extra, sort_keys=True).encode())
    with pytest.raises(support.SupportBundleValidationError, match="not exact"):
        support.load_preview(extra_path)

    too_deep = review / "deep.json"
    atomic_create_bytes(too_deep, (b"[" * 1000) + b"0" + (b"]" * 1000))
    with pytest.raises(support.SupportBundleValidationError):
        support.load_preview(too_deep)


def test_production_cli_is_two_step_and_never_prints_plaintext(
    tmp_path, monkeypatch,
):
    from maverick import audit, production_scope
    from maverick.cli import main

    marker = "customer-case-never-on-stdout-24680"
    bundle = _bundle(monkeypatch, marker)
    review = _private_output_dir(tmp_path)
    private, public, fingerprint = _key_pair(tmp_path)
    audit_events: list[tuple[str, dict]] = []
    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)
    monkeypatch.setattr(support, "collect_production", lambda: bundle)
    monkeypatch.setattr(
        audit,
        "required_audit_event",
        lambda kind, **fields: audit_events.append((kind, fields)) or True,
    )
    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)

    preview_path = review / f"{marker}.preview.json"
    first = CliRunner().invoke(
        main, ["support", "--preview-output", str(preview_path)]
    )
    assert first.exit_code == 0, first.output
    assert marker not in first.output
    digest = support.bundle_sha256(support.load_preview(preview_path))
    assert digest in first.output

    output = tmp_path / f"{marker}.mvsupport"
    second = CliRunner().invoke(
        main,
        [
            "support",
            "--from-preview",
            str(preview_path),
            "--output",
            str(output),
            "--recipient-public-key",
            str(public),
            "--recipient-key-sha256",
            fingerprint,
            "--consent-sha256",
            digest,
        ],
    )
    assert second.exit_code == 0, second.output
    assert marker not in second.output
    assert marker.encode() not in output.read_bytes()
    assert support.open_encrypted_bundle(output, private) == bundle
    assert marker not in json.dumps(audit_events)


def test_production_cli_refuses_legacy_stdout_before_collection(monkeypatch):
    from maverick import production_scope
    from maverick.cli import main

    marker = "must-never-be-printed"
    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)
    monkeypatch.setattr(
        support,
        "collect",
        lambda: (_ for _ in ()).throw(AssertionError(marker)),
    )
    result = CliRunner().invoke(main, ["support"])
    assert result.exit_code != 0
    assert marker not in result.output
    assert "--from-preview" in result.output


def test_production_ticket_intake_refuses_unverified_legacy_payload(monkeypatch):
    from maverick import production_scope

    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)
    with pytest.raises(support.SupportBundleValidationError):
        support.ticket_summary(
            {
                "correlation_id": "sup_legacy",
                "recent_failures": {"failure_modes": "unverified-secret"},
            }
        )

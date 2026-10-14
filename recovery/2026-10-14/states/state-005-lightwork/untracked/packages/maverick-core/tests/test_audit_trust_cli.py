from __future__ import annotations

import maverick.audit.postgres as pg_audit
from click.testing import CliRunner
from maverick.cli import main


def test_trust_root_digest_cli_is_canonical():
    public = "11" * 32
    result = CliRunner().invoke(
        main, ["audit", "trust-root-digest", "--public-key", public]
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip() == pg_audit.canonical_audit_trust_root_digest(public)


def test_trust_root_digest_cli_rejects_malformed_key():
    result = CliRunner().invoke(
        main, ["audit", "trust-root-digest", "--public-key", "not-a-key"]
    )
    assert result.exit_code == 1
    assert "public key is invalid" in result.output


def test_trust_bootstrap_and_rotation_cli_use_explicit_ceremony(monkeypatch):
    calls: list[tuple[str, object]] = []

    class Trust:
        def bootstrap(self):
            calls.append(("bootstrap", None))
            return "1" * 16

        def rotate(
            self,
            public_key,
            *,
            revoke_previous=False,
            replace_pending_key_id=None,
        ):
            calls.append(
                (
                    "rotate",
                    (public_key, revoke_previous, replace_pending_key_id),
                )
            )
            return "2" * 16

    monkeypatch.setattr(pg_audit, "PostgresAuditTrust", Trust)
    runner = CliRunner()
    boot = runner.invoke(main, ["audit", "trust-bootstrap"])
    assert boot.exit_code == 0, boot.output
    assert "active key_id = " + "1" * 16 in boot.output

    rotated = runner.invoke(
        main,
        [
            "audit", "trust-rotate", "--next-public-key", "22" * 32,
            "--revoke-previous",
            "--replace-pending-key-id", "3" * 16,
        ],
    )
    assert rotated.exit_code == 0, rotated.output
    assert "current signer remains active" in rotated.output
    assert calls == [
        ("bootstrap", None),
        ("rotate", ("22" * 32, True, "3" * 16)),
    ]


def test_local_rotate_key_refuses_shared_postgres_authority(monkeypatch):
    monkeypatch.setattr(pg_audit, "shared_audit_selected", lambda: True)
    result = CliRunner().invoke(main, ["audit", "rotate-key"])
    assert result.exit_code == 1
    assert "shared signer-trust ceremony" in result.output

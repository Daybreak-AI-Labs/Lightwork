from __future__ import annotations

import base64
from dataclasses import asdict

import pytest
from maverick.trust_center_branding import (
    BrandingConflict,
    BrandingValidationError,
    BrandPublicationAuthority,
    DomainDeploymentBinding,
    DomainVerificationEvidence,
    TrustCenterBrandingConfig,
    TrustCenterBrandingStore,
    _sha256,
    validate_raster,
)

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class Clock:
    def __init__(self, value: float = 1_800_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class DeploymentAuthority:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.revoked = []

    def verify(self, request):
        body = {
            "tenant_sha256": request.tenant_sha256,
            "claim_id": request.claim_id,
            "claim_version": request.claim_version,
            "host": request.host,
            "challenge_sha256": request.challenge_sha256,
            "dns_evidence_sha256": "4" * 64,
            "tls_certificate_sha256": "5" * 64,
            "observed_at": self.clock(),
            "valid_until": self.clock() + 600,
        }
        return DomainVerificationEvidence(**body, evidence_sha256=_sha256(body))

    def activate(self, request, evidence):
        return DomainDeploymentBinding(
            tenant_sha256=request.tenant_sha256,
            claim_id=request.claim_id,
            claim_version=request.claim_version,
            host=request.host,
            challenge_sha256=request.challenge_sha256,
            dns_evidence_sha256=evidence.dns_evidence_sha256,
            tls_certificate_sha256=evidence.tls_certificate_sha256,
            deployment_sha256="6" * 64,
            activated_at=self.clock(),
            valid_until=self.clock() + 600,
        )

    def reverify(self, binding):
        return DomainDeploymentBinding(
            **{
                **asdict(binding),
                "deployment_sha256": "7" * 64,
                "activated_at": self.clock(),
                "valid_until": self.clock() + 700,
            }
        )

    def revoke(self, binding):
        self.revoked.append(binding.deployment_sha256)
        return True


def publication(origin: str = "https://brand.example.com") -> BrandPublicationAuthority:
    return BrandPublicationAuthority(
        tenant_id="tenant-a",
        property_id="property-a",
        property_revision=3,
        publication_id="publication-a",
        publication_revision=5,
        publication_sha256="1" * 64,
        bundle_sha256="2" * 64,
        client_credential_id="client-a",
        client_revision=7,
        client_record_sha256="3" * 64,
        public_origin=origin,
    )


def make_store(tmp_path, *, clock=None, deployment=None, current=None, key_revision=1):
    clock = clock or Clock()
    current = current or [publication()]
    keys = {1: b"a" * 32, 2: b"b" * 32}
    generated: list[str] = []

    def next_id(prefix: str) -> str:
        generated.append(prefix)
        return f"{prefix}-{len(generated)}"

    return TrustCenterBrandingStore(
        tmp_path / "brand.sqlite3",
        tenant_id="tenant-a",
        config=TrustCenterBrandingConfig(
            enable=True,
            key_revision=key_revision,
            retention_seconds=60,
            challenge_ttl_seconds=300,
            verification_ttl_seconds=900,
        ),
        key_resolver=lambda _tenant, _ref, revision: keys[revision],
        publication_resolver=lambda _property, _client: current[0],
        domain_authority=deployment,
        clock=clock,
        token_factory=lambda: "challenge-token-with-at-least-32-bytes",
        id_factory=next_id,
    )


def activate_logo(store):
    asset = store.propose_asset(
        "primary-logo",
        expected_version=0,
        asset_kind="logo",
        media_type="image/png",
        content=PNG_1X1,
        actor_ref="uploader",
    )
    with pytest.raises(BrandingConflict, match="branding_independent_review_required"):
        store.review_asset(
            "primary-logo",
            1,
            expected_authority_revision=asset.authority_revision,
            actor_ref="uploader",
            approve=True,
        )
    asset = store.review_asset(
        "primary-logo",
        1,
        expected_authority_revision=asset.authority_revision,
        actor_ref="reviewer",
        approve=True,
    )
    return store.activate_asset(
        "primary-logo",
        1,
        expected_authority_revision=asset.authority_revision,
        actor_ref="reviewer",
    )


def test_raster_parser_rejects_polyglot_metadata_and_mismatch():
    assert validate_raster(
        PNG_1X1,
        "image/png",
        max_bytes=1000,
        max_width=10,
        max_height=10,
        max_pixels=100,
    )[2:4] == (1, 1)
    with pytest.raises(BrandingValidationError):
        validate_raster(
            b"<svg><script>alert(1)</script></svg>",
            "image/png",
            max_bytes=1000,
            max_width=10,
            max_height=10,
            max_pixels=100,
        )
    with pytest.raises(BrandingValidationError):
        validate_raster(
            PNG_1X1 + b"trailing-html",
            "image/png",
            max_bytes=1000,
            max_width=10,
            max_height=10,
            max_pixels=100,
        )
    with pytest.raises(BrandingValidationError):
        validate_raster(
            PNG_1X1,
            "image/svg+xml",
            max_bytes=1000,
            max_width=10,
            max_height=10,
            max_pixels=100,
        )


def test_asset_projection_rotation_revocation_and_retention(tmp_path):
    current = [publication()]
    store = make_store(tmp_path, current=current)
    active = activate_logo(store)
    projection = store.bind_projection(
        "property-a",
        "client-a",
        asset_id="primary-logo",
        asset_version=1,
        domain_claim_id=None,
        domain_claim_version=None,
        expected_projection_revision=0,
        actor_ref="publisher",
    )
    delivered = store.read_public_asset(
        publication(), projection.projection_id, projection.asset_content_sha256
    )
    assert delivered.content == PNG_1X1
    assert store.status()["svg_html_css_url_uploads_accepted"] is False

    current[0] = publication("https://other.example.com")
    with pytest.raises(BrandingConflict, match="branding_projection_stale"):
        store.resolve_projection(current[0])
    current[0] = publication()

    rotated_store = make_store(tmp_path, current=current, key_revision=2)
    rotated = rotated_store.rotate_asset_custody(
        "primary-logo",
        1,
        expected_custody_revision=active.custody_revision,
        actor_ref="custodian",
    )
    assert (rotated.custody_revision, rotated.key_revision) == (2, 2)
    revoked = rotated_store.revoke_asset(
        "primary-logo",
        1,
        expected_authority_revision=rotated.authority_revision,
        actor_ref="revoker",
    )
    with pytest.raises(BrandingConflict, match="branding_projection_asset_changed"):
        rotated_store.resolve_projection(publication())
    assert rotated_store.purge_assets(before=revoked.retained_until, limit=10) == {
        "purged": 1
    }
    assert rotated_store.status()["live_asset_versions"] == 0


def test_exact_domain_lifecycle_reverify_rebind_revoke_and_recovery(tmp_path):
    clock = Clock()
    deployment = DeploymentAuthority(clock)
    store = make_store(tmp_path, clock=clock, deployment=deployment)
    activate_logo(store)
    with pytest.raises(BrandingValidationError):
        store.challenge_domain(
            "brand-domain",
            "*.example.com",
            expected_claim_version=0,
            actor_ref="domain-admin",
        )
    claim, token = store.challenge_domain(
        "brand-domain",
        "brand.example.com",
        expected_claim_version=0,
        actor_ref="domain-admin",
    )
    assert token == "challenge-token-with-at-least-32-bytes"
    claim = store.verify_domain(
        claim.claim_id,
        claim.claim_version,
        expected_authority_revision=claim.authority_revision,
        actor_ref="domain-admin",
    )
    claim = store.activate_domain(
        claim.claim_id,
        claim.claim_version,
        expected_authority_revision=claim.authority_revision,
        actor_ref="domain-admin",
    )
    projection = store.bind_projection(
        "property-a",
        "client-a",
        asset_id="primary-logo",
        asset_version=1,
        domain_claim_id=claim.claim_id,
        domain_claim_version=claim.claim_version,
        expected_projection_revision=0,
        actor_ref="publisher",
    )
    assert projection.domain_deployment_sha256 == "6" * 64

    claim = store.reverify_domain(
        claim.claim_id,
        claim.claim_version,
        expected_authority_revision=claim.authority_revision,
        actor_ref="domain-admin",
    )
    with pytest.raises(BrandingConflict, match="branding_projection_domain_changed"):
        store.resolve_projection(publication())
    projection = store.bind_projection(
        "property-a",
        "client-a",
        asset_id="primary-logo",
        asset_version=1,
        domain_claim_id=claim.claim_id,
        domain_claim_version=claim.claim_version,
        expected_projection_revision=projection.projection_revision,
        actor_ref="publisher",
    )
    assert store.resolve_projection(publication()).projection_id == projection.projection_id
    claim, confirmed = store.revoke_domain(
        claim.claim_id,
        claim.claim_version,
        expected_authority_revision=claim.authority_revision,
        actor_ref="domain-admin",
    )
    assert confirmed is True
    assert deployment.revoked == ["7" * 64]
    recovered, _ = store.challenge_domain(
        "brand-domain",
        "brand.example.com",
        expected_claim_version=claim.claim_version,
        actor_ref="domain-admin",
    )
    assert recovered.claim_version == 2


def test_config_is_nested_strict_and_bounded():
    config = TrustCenterBrandingConfig.from_mapping(
        {
            "consent_preferences": {
                "trust_center": {
                    "branding": {
                        "enable": True,
                        "max_asset_bytes": 4096,
                    }
                }
            }
        }
    )
    assert config.enable is True
    with pytest.raises(Exception, match="branding_configuration_unknown_key"):
        TrustCenterBrandingConfig.from_mapping(
            {"consent_preferences": {"trust_center": {"branding": {"url": "x"}}}}
        )

"""Bounded CycloneDX/SPDX ingestion for the CRA product-security suite."""

from __future__ import annotations

import json

import pytest
from maverick import sbom_ingest


def test_cyclonedx_normalises_components_dependencies_and_digest():
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": "urn:uuid:6f7dc97c-2161-4f33-8739-4747367caec9",
        "metadata": {
            "component": {
                "bom-ref": "pkg:application/lightwork@1.2.3",
                "type": "application",
                "name": "Lightwork",
                "version": "1.2.3",
            }
        },
        "components": [
            {
                "bom-ref": "pkg:pypi/httpx@0.27.0",
                "type": "library",
                "name": "httpx",
                "version": "0.27.0",
                "purl": "pkg:pypi/httpx@0.27.0",
                "hashes": [{"alg": "SHA-256", "content": "a" * 64}],
            }
        ],
        "dependencies": [
            {
                "ref": "pkg:application/lightwork@1.2.3",
                "dependsOn": ["pkg:pypi/httpx@0.27.0"],
            }
        ],
    }

    normalised = sbom_ingest.parse_sbom(json.dumps(document))

    assert normalised["format"] == "cyclonedx"
    assert normalised["spec_version"] == "1.6"
    assert normalised["document_sha256"]
    assert normalised["component_count"] == 2
    assert normalised["dependency_count"] == 1
    assert normalised["dependency_resolution"] == "declared_graph"
    assert normalised["components"][1]["purl"] == "pkg:pypi/httpx@0.27.0"
    assert normalised["dependencies"] == [
        {
            "from": "pkg:application/lightwork@1.2.3",
            "to": "pkg:pypi/httpx@0.27.0",
            "relationship": "depends_on",
        }
    ]


def test_spdx_relationships_are_declared_not_misrepresented_as_resolved():
    document = {
        "spdxVersion": "SPDX-2.3",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "Lightwork release",
        "documentNamespace": "https://example.invalid/sbom/lightwork-1.2.3",
        "packages": [
            {
                "SPDXID": "SPDXRef-Lightwork",
                "name": "Lightwork",
                "versionInfo": "1.2.3",
            },
            {
                "SPDXID": "SPDXRef-httpx",
                "name": "httpx",
                "versionInfo": "0.27.0",
                "externalRefs": [
                    {
                        "referenceType": "purl",
                        "referenceLocator": "pkg:pypi/httpx@0.27.0",
                    }
                ],
            },
        ],
        "relationships": [
            {
                "spdxElementId": "SPDXRef-Lightwork",
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": "SPDXRef-httpx",
            }
        ],
    }

    normalised = sbom_ingest.parse_sbom(document)

    assert normalised["format"] == "spdx"
    assert normalised["dependency_resolution"] == "declared_graph"
    assert normalised["components"][1]["purl"] == "pkg:pypi/httpx@0.27.0"


def test_cyclonedx_requires_explicit_producer_assertion_for_resolved_graph():
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {
            "properties": [
                {
                    "name": "lightwork:dependency-resolution",
                    "value": "resolved",
                }
            ]
        },
        "components": [
            {
                "bom-ref": "pkg:pypi/httpx@0.27.0",
                "type": "library",
                "name": "httpx",
                "version": "0.27.0",
            }
        ],
        "dependencies": [],
    }

    normalised = sbom_ingest.parse_sbom(document)

    assert normalised["dependency_resolution"] == "producer_asserted_resolved"
    assert normalised["resolution_evidence"] == (
        "CycloneDX metadata property lightwork:dependency-resolution=resolved"
    )


@pytest.mark.parametrize(
    "document, message",
    [
        (
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "components": [
                    {"bom-ref": "dup", "type": "library", "name": "one"},
                    {"bom-ref": "dup", "type": "library", "name": "two"},
                ],
            },
            "duplicate component identity",
        ),
        (
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "components": [
                    {"bom-ref": "known", "type": "library", "name": "one"}
                ],
                "dependencies": [{"ref": "known", "dependsOn": ["missing"]}],
            },
            "unknown component",
        ),
    ],
)
def test_malformed_graphs_fail_closed(document, message):
    with pytest.raises(ValueError, match=message):
        sbom_ingest.parse_sbom(document)


def test_size_and_nesting_limits_are_enforced_before_use():
    with pytest.raises(ValueError, match="size limit"):
        sbom_ingest.parse_sbom(b"{" + (b" " * 1024) + b"}", max_bytes=128)

    nested: object = "leaf"
    for _ in range(70):
        nested = [nested]
    with pytest.raises(ValueError, match="nesting limit"):
        sbom_ingest.parse_sbom({"bomFormat": "CycloneDX", "nested": nested})


def test_duplicate_json_keys_are_rejected_before_format_selection():
    document = (
        '{"bomFormat":"CycloneDX","bomFormat":"SPDX",'
        '"specVersion":"1.6","components":[]}'
    )

    with pytest.raises(ValueError, match="duplicate JSON object key"):
        sbom_ingest.parse_sbom(document)


@pytest.mark.parametrize(
    "component, message",
    [
        (
            {
                "hashes": [
                    {"alg": "SHA-1", "content": f"hash-{index}"}
                    for index in range(65)
                ]
            },
            "component hashes exceeds the 64-item limit",
        ),
        (
            {
                "licenses": [
                    {"license": {"id": f"LicenseRef-{index}"}}
                    for index in range(65)
                ]
            },
            "CycloneDX licenses exceeds the 64-item limit",
        ),
    ],
)
def test_cyclonedx_rejects_excess_security_metadata_instead_of_truncating(
    component, message,
):
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "components": [{
            "bom-ref": "component",
            "type": "library",
            "name": "component",
            **component,
        }],
    }

    with pytest.raises(ValueError, match=message):
        sbom_ingest.parse_sbom(document)


def test_spdx_rejects_excess_external_refs_instead_of_truncating():
    document = {
        "spdxVersion": "SPDX-2.3",
        "SPDXID": "SPDXRef-DOCUMENT",
        "documentNamespace": "https://example.invalid/sbom",
        "packages": [{
            "SPDXID": "SPDXRef-package",
            "name": "package",
            "externalRefs": [
                {
                    "referenceType": "security-advisory",
                    "referenceLocator": f"https://example.invalid/{index}",
                }
                for index in range(129)
            ],
        }],
    }

    with pytest.raises(ValueError, match="SPDX externalRefs exceeds the 128-item limit"):
        sbom_ingest.parse_sbom(document)

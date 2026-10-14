from __future__ import annotations

import copy

import pytest

from scripts import build_standalone_skus as distribution


def test_standalone_archives_only_share_approved_repository_root_files():
    for sku_relative in distribution.SKU_DIRS:
        manifest = distribution._read_manifest(distribution.REPO_ROOT / sku_relative)

        assert set(manifest["shared_files"]) == distribution.SHARED_FILE_ALLOWLIST
        distribution._archive_payloads(distribution.REPO_ROOT / sku_relative, manifest)


@pytest.mark.parametrize(
    "internal_path",
    [
        "PLATFORM_IMPROVEMENT_AUDIT.md",
        "docs/patents/01-invention-disclosure.md",
        "docs/research/lightwork-invention-register.md",
        r"docs\patents\01-invention-disclosure.md",
    ],
)
def test_standalone_archive_refuses_internal_repository_documents(internal_path: str):
    sku_relative = distribution.SKU_DIRS[0]
    sku_dir = distribution.REPO_ROOT / sku_relative
    manifest = copy.deepcopy(distribution._read_manifest(sku_dir))
    manifest["shared_files"].append(internal_path)

    with pytest.raises(ValueError, match="not approved for customer archives"):
        distribution._archive_payloads(sku_dir, manifest)

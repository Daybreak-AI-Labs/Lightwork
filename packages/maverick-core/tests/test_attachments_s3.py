"""Tests for the opt-in S3 attachment mirror. boto3 is faked; the local store
remains the source of truth and the mirror is fail-open."""
from __future__ import annotations

import hashlib
import sys
from unittest.mock import MagicMock

from maverick import attachments as att
from maverick import file_lock


def _content_name(data: bytes, filename: str = "zz.txt") -> str:
    return f"{hashlib.sha256(data).hexdigest()[:16]}-{filename}"


def _fake_boto3(monkeypatch):
    client = MagicMock(name="s3 client")
    boto3 = MagicMock(name="boto3")
    boto3.client.return_value = client
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    return client


def test_mirror_off_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("MAVERICK_ATTACH_S3_BUCKET", raising=False)
    import maverick.config as config_mod
    monkeypatch.setattr(config_mod, "load_config", dict)
    client = _fake_boto3(monkeypatch)
    st = att.store(1, "a.txt", "text/plain", b"hello", root=tmp_path)
    assert st.path.exists()
    assert not client.put_object.called


def test_mirror_uploads_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_ATTACH_S3_BUCKET", "my-bucket")
    monkeypatch.setenv("MAVERICK_ATTACH_S3_PREFIX", "attach")
    client = _fake_boto3(monkeypatch)
    st = att.store(7, "doc.txt", "text/plain", b"world", root=tmp_path)
    assert st.path.exists()  # local copy is still written
    client.put_object.assert_called_once()
    kw = client.put_object.call_args.kwargs
    assert kw["Bucket"] == "my-bucket"
    assert kw["Key"] == f"attach/__default__/7/{st.path.name}"  # tenant-scoped
    assert kw["Body"] == b"world"
    assert kw["ContentType"] == "text/plain"


def test_mirror_failure_is_fail_open(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_ATTACH_S3_BUCKET", "b")
    client = _fake_boto3(monkeypatch)
    client.put_object.side_effect = RuntimeError("s3 down")
    st = att.store(2, "a.txt", "text/plain", b"x", root=tmp_path)  # must not raise
    assert st.path.exists()


def test_s3_fetch_pulls_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_ATTACH_S3_BUCKET", "b")
    monkeypatch.delenv("MAVERICK_ATTACH_S3_PREFIX", raising=False)
    client = _fake_boto3(monkeypatch)
    body = MagicMock()
    data = b"bytes-from-s3"
    name = _content_name(data)
    body.read.return_value = data
    client.get_object.return_value = {"Body": body}
    p = att.s3_fetch(9, name, root=tmp_path)
    assert p is not None and p.read_bytes() == data
    assert file_lock.private_path_is_restricted(p.parent, 0o700)
    assert file_lock.private_path_is_restricted(p)
    client.get_object.assert_called_once_with(Bucket="b", Key=f"__default__/9/{name}")
    # Second fetch short-circuits on the local file.
    client.get_object.reset_mock()
    assert att.s3_fetch(9, name, root=tmp_path) == p
    assert not client.get_object.called


def test_s3_fetch_when_off_or_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("MAVERICK_ATTACH_S3_BUCKET", raising=False)
    import maverick.config as config_mod
    monkeypatch.setattr(config_mod, "load_config", dict)
    assert att.s3_fetch(1, "x.txt", root=tmp_path) is None
    monkeypatch.setenv("MAVERICK_ATTACH_S3_BUCKET", "b")
    client = _fake_boto3(monkeypatch)
    client.get_object.side_effect = RuntimeError("NoSuchKey")
    assert att.s3_fetch(1, "0" * 16 + "-x.txt", root=tmp_path) is None


def test_s3_fetch_rejects_unsafe_names_before_s3(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_ATTACH_S3_BUCKET", "b")
    client = _fake_boto3(monkeypatch)

    for name in ("../config.toml", "/tmp/config.toml", "subdir/file.txt", "bad\x00.txt"):
        try:
            att.s3_fetch(9, name, root=tmp_path)
        except att.AttachmentRejected:
            pass
        else:  # pragma: no cover - keeps the assertion message useful
            raise AssertionError(f"accepted unsafe S3 attachment name {name!r}")

    assert not client.get_object.called
    assert not (tmp_path / "config.toml").exists()


def test_s3_fetch_enforces_file_size_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_ATTACH_S3_BUCKET", "b")
    monkeypatch.setattr(att, "MAX_FILE_BYTES", 4)
    client = _fake_boto3(monkeypatch)
    body = MagicMock()
    body.read.return_value = b"abcde"
    client.get_object.return_value = {"Body": body}

    try:
        att.s3_fetch(9, "0" * 16 + "-zz.txt", root=tmp_path)
    except att.AttachmentRejected:
        pass
    else:  # pragma: no cover - keeps the assertion message useful
        raise AssertionError("accepted oversized S3 attachment")

    body.read.assert_called_once_with(4 + att._SEAL_OVERHEAD + 1)
    assert not (tmp_path / "9" / "abcd1234-zz.txt").exists()


def test_s3_fetch_denies_executable_bytes(monkeypatch, tmp_path):
    """A shared/poisoned bucket object with executable/archive bytes must be
    rejected on fetch (same magic-byte deny store() enforces on upload), not
    silently written to the local store."""
    monkeypatch.setenv("MAVERICK_ATTACH_S3_BUCKET", "b")
    client = _fake_boto3(monkeypatch)
    body = MagicMock()
    body.read.return_value = b"PK\x03\x04rest-of-a-zip"  # ZIP magic
    client.get_object.return_value = {"Body": body}

    try:
        att.s3_fetch(9, "0" * 16 + "-evil.txt", root=tmp_path)
    except att.AttachmentRejected:
        pass
    else:  # pragma: no cover
        raise AssertionError("accepted archive bytes from S3")
    assert not (tmp_path / "9" / ("0" * 16 + "-evil.txt")).exists()


def test_s3_fetch_denies_disallowed_content_type(monkeypatch, tmp_path):
    """When the object carries a ContentType, the mime allowlist applies too."""
    monkeypatch.setenv("MAVERICK_ATTACH_S3_BUCKET", "b")
    client = _fake_boto3(monkeypatch)
    body = MagicMock()
    body.read.return_value = b"harmless-looking bytes"
    client.get_object.return_value = {
        "Body": body, "ContentType": "application/octet-stream",
    }

    try:
        att.s3_fetch(9, "0" * 16 + "-blob.txt", root=tmp_path)
    except att.AttachmentRejected:
        pass
    else:  # pragma: no cover
        raise AssertionError("accepted disallowed ContentType from S3")
    assert not (tmp_path / "9" / ("0" * 16 + "-blob.txt")).exists()


def test_s3_fetch_rejects_content_address_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_ATTACH_S3_BUCKET", "b")
    client = _fake_boto3(monkeypatch)
    body = MagicMock()
    body.read.return_value = b"bucket object was replaced"
    client.get_object.return_value = {"Body": body, "ContentType": "text/plain"}
    name = "0" * 16 + "-report.txt"

    try:
        att.s3_fetch(9, name, root=tmp_path)
    except att.AttachmentRejected as exc:
        assert "content-address mismatch" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("accepted an S3 object under the wrong content address")

    assert not (tmp_path / "9" / name).exists()


def test_s3_keys_are_tenant_scoped_and_legacy_objects_still_fetch(monkeypatch, tmp_path):
    """Goal ids are per-tenant counters: two tenants must not share an object
    key. Objects mirrored before scoping are still found under the old key."""
    from maverick.paths import tenant_scope
    monkeypatch.setenv("MAVERICK_ATTACH_S3_BUCKET", "b")
    client = _fake_boto3(monkeypatch)
    with tenant_scope(tenant="acme"):
        assert att._s3_key(3, "n.txt") == "acme/3/n.txt"
    with tenant_scope(tenant="beta"):
        assert att._s3_key(3, "n.txt") == "beta/3/n.txt"
    data = b"legacy-object"
    name = _content_name(data)
    body = MagicMock()
    body.read.return_value = data

    def get_object(Bucket, Key):
        if Key != f"9/{name}":
            raise RuntimeError("NoSuchKey")
        return {"Body": body}

    client.get_object.side_effect = get_object
    p = att.s3_fetch(9, name, root=tmp_path)
    assert p is not None and att.read_bytes(p) == data


def test_sealed_attachments_bind_goal_name_and_tenant(monkeypatch, tmp_path):
    """With at-rest encryption on, bytes on disk and in the mirror are sealed
    and only open for the same tenant, goal and stored name."""
    import pytest
    from maverick import crypto_at_rest
    from maverick.paths import tenant_scope
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    monkeypatch.setenv("MAVERICK_ATTACH_S3_BUCKET", "b")
    client = _fake_boto3(monkeypatch)
    secret = b"quarterly numbers: 4.2M"
    with tenant_scope(tenant="acme"):
        st = att.store(5, "q3.txt", "text/plain", secret, root=tmp_path)
        raw = st.path.read_bytes()
        assert crypto_at_rest.is_bound(raw) and secret not in raw
        assert client.put_object.call_args.kwargs["Body"] == raw  # mirror sealed too
        assert att.read_bytes(st.path) == secret
        with att.plaintext_path(st.path) as plain:
            assert plain.read_bytes() == secret and plain != st.path
        assert not plain.exists()  # temporary plaintext removed
        moved = tmp_path / "6" / st.path.name
        moved.parent.mkdir()
        moved.write_bytes(raw)
        with pytest.raises(crypto_at_rest.EncryptionUnavailable):
            att.read_bytes(moved)  # another goal
    with tenant_scope(tenant="beta"), pytest.raises(crypto_at_rest.EncryptionUnavailable):
        att.read_bytes(st.path)  # another tenant

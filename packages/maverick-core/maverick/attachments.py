"""Attachment storage for goal inputs (files of all kinds).

Stores bytes under ``~/.maverick/attachments/<goal_id>/<sha256>`` and
records the metadata in the world model. Enforces:
  - max per-file size (default 25 MiB; raise via
    MAVERICK_ATTACH_MAX_FILE_BYTES for large audio/video)
  - max total per-goal size (default 100 MiB, env-configurable)
  - mime-type allowlist: text, image, audio, and video families plus the
    common document formats (PDF, Office/OOXML, OpenDocument, epub, RTF,
    CSV/JSON/XML/YAML). Deployments extend it with ``[attachments]
    extra_mime_prefixes`` / ``MAVERICK_ATTACH_EXTRA_MIME``, or accept any
    declared mime with ``allow_any_mime`` / ``MAVERICK_ATTACH_ALLOW_ANY``.
  - an active magic-byte deny for executables and generic archives that
    no mime setting bypasses (document ZIP containers like docx/odt/epub
    are structurally sniffed and exempted; a plain .zip is not).

The agent has a ``list_attachments`` tool that returns the on-disk paths
so the existing ``read_file`` / ``transcribe_audio`` / OCR tools can pick
them up. Images are also delivered to the orchestrator as Anthropic
vision content blocks and PDFs as native document blocks (see
``content_blocks_for_goal``) so the agent can SEE them, not just read
their bytes.

**S3-backed attachments** (opt-in): with ``[attachments] s3_bucket`` (or
``MAVERICK_ATTACH_S3_BUCKET``) set, every stored attachment is also mirrored
to ``s3://<bucket>/<prefix><goal_id>/<name>`` — the durable/shared copy for
multi-host deployments — and :func:`s3_fetch` pulls a missing attachment back
down on a worker that doesn't have the local file. Local disk remains the
source the tools read; the mirror is best-effort fail-open (an S3 outage
must never reject an upload). Uses boto3 (the ``[s3]`` extra), imported
lazily; works against any S3-compatible endpoint via ``AWS_ENDPOINT_URL``.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .paths import data_dir

log = logging.getLogger(__name__)

DEFAULT_ROOT = data_dir("attachments")

# 25 MiB per file, 100 MiB per goal. Tunable via env so a VPS deployment
# can raise the cap without a code change.
def _env_int(name: str, default: int) -> int:
    # A non-numeric value used to raise ValueError at import time, killing the
    # attachment path with an opaque traceback instead of using the default.
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


MAX_FILE_BYTES = _env_int("MAVERICK_ATTACH_MAX_FILE_BYTES", 25 * 1024 * 1024)
MAX_GOAL_BYTES = _env_int("MAVERICK_ATTACH_MAX_GOAL_BYTES", 100 * 1024 * 1024)

# Office / OpenDocument packages are ZIP containers, so they need BOTH a
# mime allowlist entry AND a structural exemption from the archive deny
# (see _is_document_package). epub is the same shape.
DOCUMENT_ZIP_MIME_PREFIXES = (
    "application/vnd.openxmlformats-officedocument.",  # docx/xlsx/pptx
    "application/vnd.oasis.opendocument.",             # odt/ods/odp
    "application/epub+zip",
)

# Mime allowlist. Text, image, audio, and video families plus the common
# document formats (PDF natively; Office/ODF as sniffed ZIP packages).
# Active deny for executables and generic archives regardless of the
# claimed mime. Extend per-deployment with [attachments]
# extra_mime_prefixes / MAVERICK_ATTACH_EXTRA_MIME (comma-separated), or
# accept everything (magic-byte deny still applies) with [attachments]
# allow_any_mime / MAVERICK_ATTACH_ALLOW_ANY=1.
ALLOWED_MIME_PREFIXES = (
    "text/",
    "image/",
    "audio/",
    "video/",
    "application/pdf",
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/csv",
    "application/rtf",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
) + DOCUMENT_ZIP_MIME_PREFIXES
ALLOWED_IMAGE_MIMES = frozenset({
    "image/jpeg", "image/png", "image/gif", "image/webp",
})


def _extra_mime_prefixes() -> tuple[str, ...]:
    """Deployment-added mime prefixes (env wins, then config). Fail-soft."""
    raw = os.environ.get("MAVERICK_ATTACH_EXTRA_MIME", "").strip()
    if not raw:
        try:
            from .config import load_config
            cfg = (load_config() or {}).get("attachments") or {}
            val = cfg.get("extra_mime_prefixes") or []
            if isinstance(val, str):
                raw = val
            else:
                return tuple(str(p).strip() for p in val if str(p).strip())
        except Exception:  # pragma: no cover -- config never blocks an upload
            return ()
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def _allow_any_mime() -> bool:
    """Accept any declared mime (magic-byte deny still enforced)."""
    raw = (os.environ.get("MAVERICK_ATTACH_ALLOW_ANY") or "").strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    try:
        from .config import load_config
        cfg = (load_config() or {}).get("attachments") or {}
        return bool(cfg.get("allow_any_mime", False))
    except Exception:  # pragma: no cover
        return False


def mime_allowed(mime: str) -> bool:
    """Whether ``mime`` passes the (built-in + deployment-extended) allowlist."""
    if _allow_any_mime():
        return True
    prefixes = ALLOWED_MIME_PREFIXES + _extra_mime_prefixes()
    return any(mime.startswith(p) for p in prefixes)

# Magic-byte deny for executables + archives. The mime allowlist above is
# advisory only -- the caller-supplied Content-Type is client-controlled, so
# the allowlist alone lets an ELF/ZIP through under a benign type. This
# enforces the docstring's "active deny" by sniffing the actual leading bytes,
# independent of the claimed mime. (PDF, the structured-text families, images
# and plain text never start with these signatures.)
_DENY_MAGIC: tuple[bytes, ...] = (
    b"\x7fELF",            # ELF executable / shared object (Linux)
    b"MZ",                 # DOS/PE executable (Windows .exe/.dll)
    b"\xca\xfe\xba\xbe",   # Mach-O fat binary / Java class
    b"\xfe\xed\xfa\xce",   # Mach-O 32-bit
    b"\xfe\xed\xfa\xcf",   # Mach-O 64-bit
    b"\xcf\xfa\xed\xfe",   # Mach-O 64-bit (reverse byte order)
    b"\xce\xfa\xed\xfe",   # Mach-O 32-bit (reverse byte order)
    b"PK\x03\x04",         # ZIP (also jar/docx/xlsx/apk)
    b"PK\x05\x06",         # empty ZIP
    b"PK\x07\x08",         # spanned ZIP
    b"Rar!\x1a\x07",       # RAR
    b"\x1f\x8b",           # gzip
    b"BZh",                # bzip2
    b"\xfd7zXZ\x00",       # xz
    b"7z\xbc\xaf\x27\x1c",  # 7-Zip
    b"ustar",              # tar (signature at offset 257, handled below)
)


def _looks_executable_or_archive(data: bytes) -> bool:
    head = data[:8]
    for sig in _DENY_MAGIC:
        if sig == b"ustar":
            continue
        if head.startswith(sig):
            return True
    # tar stores its "ustar" magic at byte offset 257.
    if len(data) >= 262 and data[257:262] == b"ustar":
        return True
    return False


def _is_document_package(data: bytes) -> bool:
    """True when ZIP bytes are a real document container, not a raw archive.

    Office (docx/xlsx/pptx) and OpenDocument/epub files ARE ZIPs, so the
    blanket PK-magic deny would reject every one of them. Sniff the actual
    ZIP structure (never the client-claimed mime): OOXML always carries
    ``[Content_Types].xml``; ODF/epub always carry a ``mimetype`` entry
    declaring an opendocument/epub type. Anything else — including invalid
    ZIP bytes and generic .zip archives — stays denied. Reads the central
    directory only; nothing is extracted.
    """
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist()[:256])
            if "[Content_Types].xml" in names:
                return True
            if "mimetype" in names:
                declared = zf.read("mimetype")[:100].decode("ascii", "replace")
                return declared.startswith(
                    ("application/vnd.oasis.opendocument", "application/epub+zip")
                )
    except Exception:  # noqa: BLE001 -- malformed zip = not a document
        return False
    return False


def _content_denied(data: bytes, mime: str) -> bool:
    """The magic-byte deny, with the document-package carve-out.

    ZIP bytes pass ONLY when the claimed mime is a document-container type
    AND the bytes structurally are one; every other executable/archive
    signature is denied regardless of mime.
    """
    if not _looks_executable_or_archive(data):
        return False
    if (
        data[:2] == b"PK"
        and mime.startswith(DOCUMENT_ZIP_MIME_PREFIXES)
        and _is_document_package(data)
    ):
        return False
    return True


class AttachmentRejected(ValueError):
    """Raised when an attachment violates a size / type / quota rule."""


@dataclass(frozen=True)
class Stored:
    filename: str
    mime: str
    size_bytes: int
    sha256: str
    path: Path


def _root_for_goal(goal_id: int, root: Path | None = None) -> Path:
    base = root or DEFAULT_ROOT
    return base / str(goal_id)


# ---- S3 mirror (opt-in) -----------------------------------------------------

def _s3_settings() -> tuple[str, str]:
    """(bucket, key_prefix); bucket == "" when the mirror is off."""
    bucket = os.environ.get("MAVERICK_ATTACH_S3_BUCKET", "").strip()
    prefix = os.environ.get("MAVERICK_ATTACH_S3_PREFIX", "").strip()
    if not bucket:
        try:
            from .config import load_config
            cfg = (load_config() or {}).get("attachments") or {}
            bucket = str(cfg.get("s3_bucket") or "").strip()
            prefix = prefix or str(cfg.get("s3_prefix") or "").strip()
        except Exception:  # pragma: no cover -- config never blocks an upload
            pass
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix


def s3_mirror_enabled() -> bool:
    return bool(_s3_settings()[0])


def _s3_client():
    import boto3  # the [s3] extra; lazy so the default path never imports it
    kwargs = {}
    endpoint = os.environ.get("AWS_ENDPOINT_URL", "").strip()
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    region = os.environ.get("AWS_REGION", "").strip()
    if region:
        kwargs["region_name"] = region
    return boto3.client("s3", **kwargs)


def _s3_key(goal_id: int, name: str) -> str:
    bucket, prefix = _s3_settings()
    assert bucket
    return f"{prefix}{goal_id}/{name}"


def _s3_mirror(goal_id: int, dest: Path, mime: str) -> None:
    """Best-effort upload of a stored attachment. Never raises."""
    try:
        client = _s3_client()
        bucket, _ = _s3_settings()
        client.put_object(
            Bucket=bucket,
            Key=_s3_key(goal_id, dest.name),
            Body=dest.read_bytes(),
            ContentType=mime or "application/octet-stream",
        )
    except Exception as e:  # noqa: BLE001 -- mirror is fail-open by design
        log.warning("attachment S3 mirror failed (local copy kept): %s", e)


def _validate_attachment_name(name: str) -> None:
    """Reject names that cannot be stored under one goal attachment dir."""
    windows_name = PureWindowsPath(name)
    if (
        not name
        or name.startswith(".")
        or "/" in name
        or "\\" in name
        or Path(name).is_absolute()
        or windows_name.is_absolute()
        or windows_name.drive
    ):
        raise AttachmentRejected(f"invalid attachment name: {name!r}")
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in name):
        raise AttachmentRejected(f"control character in attachment name: {name!r}")


def s3_fetch(goal_id: int, name: str, *, root: Path | None = None) -> Path | None:
    """Pull one mirrored attachment down to the local store.

    For a worker host that doesn't have the local file (the uploader ran
    elsewhere). ``name`` is the on-disk name (``<sha16>-<filename>``). Returns
    the local path, or None when the mirror is off / the object is missing.
    """
    if not s3_mirror_enabled():
        return None
    _validate_attachment_name(name)
    dest_dir = _root_for_goal(goal_id, root)
    dest = dest_dir / name
    if dest.exists():
        return dest
    try:
        client = _s3_client()
        bucket, _ = _s3_settings()
        obj = client.get_object(Bucket=bucket, Key=_s3_key(goal_id, name))
        content_length = obj.get("ContentLength")
        if content_length is not None and int(content_length) > MAX_FILE_BYTES:
            raise AttachmentRejected(
                f"file too large: {content_length} bytes (limit {MAX_FILE_BYTES})"
            )
        data = obj["Body"].read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise AttachmentRejected(
                f"file too large: {len(data)} bytes (limit {MAX_FILE_BYTES})"
            )
        # S3-sourced bytes get the same magic-byte deny store() enforces on the
        # upload path -- a shared/poisoned bucket must not be able to land an
        # ELF/ZIP archive on local disk that store() would have rejected. If the
        # object carries a ContentType, honour the mime allowlist too.
        content_type = str(obj.get("ContentType") or "").split(";", 1)[0].strip()
        if _content_denied(data, content_type):
            raise AttachmentRejected(
                "executable or archive content is not allowed"
            )
        if content_type and not mime_allowed(content_type):
            raise AttachmentRejected(f"mime type not allowed: {content_type}")
    except AttachmentRejected:
        raise
    except Exception as e:  # noqa: BLE001 -- absent object / S3 down -> None
        log.warning("attachment S3 fetch failed: %s", e)
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(dest_dir, 0o700)
    except OSError:
        pass
    dest.write_bytes(data)
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return dest


def store(
    goal_id: int,
    filename: str,
    mime: str,
    data: bytes,
    *,
    existing_total: int = 0,
    root: Path | None = None,
) -> Stored:
    """Validate + persist a single attachment. Returns the on-disk record.

    ``existing_total`` is the sum of ``size_bytes`` for prior attachments
    on this goal; the caller passes it in so the per-goal cap is enforced
    even when uploads arrive across requests.
    """
    if not filename:
        raise AttachmentRejected("filename is required")
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise AttachmentRejected(f"invalid filename: {filename!r}")
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in filename):
        # A null byte truncates the path at the C layer in downstream
        # consumers; other control chars enable log/terminal injection.
        # Real filenames never contain them.
        raise AttachmentRejected(f"control character in filename: {filename!r}")
    if not mime:
        raise AttachmentRejected("mime type is required")
    if not mime_allowed(mime):
        raise AttachmentRejected(f"mime type not allowed: {mime}")
    # The claimed mime is client-controlled; sniff the real bytes so an
    # executable/archive can't be planted under a benign Content-Type
    # (enforces the "active deny" promised in the module docstring).
    # Document containers (docx/odt/epub — structurally ZIPs) are the one
    # sniffed carve-out; see _content_denied.
    if _content_denied(data, mime):
        raise AttachmentRejected(
            "executable or archive content is not allowed"
        )

    size = len(data)
    if size == 0:
        raise AttachmentRejected("empty file")
    if size > MAX_FILE_BYTES:
        raise AttachmentRejected(
            f"file too large: {size} bytes (limit {MAX_FILE_BYTES})"
        )
    if existing_total + size > MAX_GOAL_BYTES:
        raise AttachmentRejected(
            f"per-goal attachment quota exceeded: "
            f"{existing_total + size} > {MAX_GOAL_BYTES}"
        )

    sha256 = hashlib.sha256(data).hexdigest()
    dest_dir = _root_for_goal(goal_id, root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Attachment bytes can be private (uploaded docs, screenshots). The
    # default umask often leaves them world-readable on a shared host, so
    # lock the goal dir + file to the owner.
    try:
        os.chmod(dest_dir, 0o700)
    except OSError:
        pass
    # SHA-prefix the on-disk name so two attachments with the same
    # filename don't collide and so a re-upload of the same bytes is a
    # no-op (idempotent).
    safe_name = filename.replace("/", "_").replace("\\", "_")
    dest = dest_dir / f"{sha256[:16]}-{safe_name}"
    if not dest.exists():
        dest.write_bytes(data)
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass
        if s3_mirror_enabled():
            _s3_mirror(goal_id, dest, mime)

    return Stored(
        filename=filename,
        mime=mime,
        size_bytes=size,
        sha256=sha256,
        path=dest,
    )


def _embed_documents_enabled() -> bool:
    """Whether PDF attachments auto-embed as document blocks. ON by default;
    off via MAVERICK_ATTACH_EMBED_DOCS=0 / [attachments] embed_documents."""
    raw = (os.environ.get("MAVERICK_ATTACH_EMBED_DOCS") or "").strip().lower()
    if raw:
        return raw not in {"0", "false", "no", "off"}
    try:
        from .config import load_config
        cfg = (load_config() or {}).get("attachments") or {}
        return bool(cfg.get("embed_documents", True))
    except Exception:  # pragma: no cover
        return True


def _document_byte_budget(model: str | None) -> int:
    """Cumulative byte budget for auto-embedded PDFs, sized to the driving
    model's context window (~3 bytes/token heuristic), hard-capped under the
    provider request limit."""
    try:
        from .context_scaling import model_window
        return min(model_window(model) * 3, 25 * 1024 * 1024)
    except Exception:  # pragma: no cover -- sizing never blocks embedding
        return 512_000


def content_blocks_for_goal(world, goal_id: int, model: str | None = None) -> list[dict]:
    """Build Anthropic content blocks for image + PDF attachments on a goal.

    Images are embedded as vision blocks (the agent needs to SEE them).
    PDFs are embedded as native ``document`` blocks — bounded by the driving
    model's context window (bigger window, more document capacity) and only
    for Anthropic models, the one provider whose message format takes them;
    on other providers (and for every other attachment kind: audio, video,
    Office docs, plain text) the agent reaches the bytes via its
    `list_attachments` + `read_file` / `transcribe_audio` tools instead.
    """
    blocks: list[dict] = []
    doc_blocks: list[dict] = []
    embed_pdfs = _embed_documents_enabled() and bool(model)
    if embed_pdfs:
        try:
            from .llm import _parse_spec
            embed_pdfs = _parse_spec(model)[0] == "anthropic"
        except Exception:  # pragma: no cover -- unknown spec: skip documents
            embed_pdfs = False
    doc_budget = _document_byte_budget(model) if embed_pdfs else 0
    for a in world.list_attachments(goal_id):
        if a.mime in ALLOWED_IMAGE_MIMES:
            try:
                b = Path(a.path).read_bytes()
            except OSError:
                continue
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": a.mime,
                    "data": base64.b64encode(b).decode("ascii"),
                },
            })
        elif a.mime == "application/pdf" and embed_pdfs:
            if a.size_bytes > doc_budget:
                continue  # over budget: stays reachable via the tools
            try:
                b = Path(a.path).read_bytes()
            except OSError:
                continue
            doc_budget -= len(b)
            doc_blocks.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(b).decode("ascii"),
                },
            })
    return blocks + doc_blocks

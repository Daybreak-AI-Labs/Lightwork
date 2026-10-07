"""Admission check for mail that can start work in Lightwork.

IMAP supplies message headers, not an authenticated sender identity. Only a
receiving server that removes untrusted Authentication-Results headers and
prepends its own result can provide the trust boundary used here. A matching
DMARC result from that server is required before the From address may be used
for an allowlist or an automation event.
"""
from __future__ import annotations

import re
from email.utils import getaddresses

_SERVER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,251}[A-Za-z0-9]")
_AUTH_SERVER = re.compile(r"\s*([^\s;]+)")
_DMARC_PASS = re.compile(r"^\s*dmarc\s*=\s*pass(?:\s|$)", re.IGNORECASE)
_HEADER_FROM = re.compile(r"(?:^|\s)header\.from\s*=\s*([A-Za-z0-9.-]+)", re.IGNORECASE)


def valid_trusted_mail_server(value: object) -> bool:
    """The administrator must identify the server that stamps mail results."""
    text = str(value or "").strip()
    return bool(_SERVER_ID.fullmatch(text) and ".." not in text)


def authenticated_from(message, trusted_server: str) -> str:
    """Return the single authenticated From address, or an empty string.

    Trust only the topmost result and only when its exact server identity was
    configured by an administrator. SPF/DKIM on their own can pass for an
    unrelated domain; the receiving server's DMARC result binds them to From.
    """
    if not valid_trusted_mail_server(trusted_server):
        return ""
    from_headers = message.get_all("From") or []
    if len(from_headers) != 1:
        return ""
    addresses = getaddresses(from_headers)
    if len(addresses) != 1:
        return ""
    address = addresses[0][1].strip()
    if address.count("@") != 1:
        return ""
    domain = address.rsplit("@", 1)[1].lower().rstrip(".")
    if not domain:
        return ""
    results = message.get_all("Authentication-Results") or []
    if not results:
        return ""
    clauses = str(results[0]).split(";")
    server = _AUTH_SERVER.match(clauses[0])
    if server is None or server.group(1).lower() != trusted_server.strip().lower():
        return ""
    for clause in clauses[1:]:
        if not _DMARC_PASS.search(clause):
            continue
        reported_from = _HEADER_FROM.search(clause)
        if reported_from and reported_from.group(1).lower().rstrip(".") == domain:
            return address
    return ""

"""Human-friendly rendering of identities and roles for the dashboard UI.

Enterprise operators should never have to read a raw OIDC subject like
``user:auth0|6a9ad1a287e571a6c9d4d080`` or a lowercase role slug like
``operator`` in the interface. The identity provider only hands us an opaque
``sub`` for federated accounts (there is no email for a break-glass admin
pinned by ``sub``), so we cannot always show a real name -- but we can always
show *what kind of account it is* ("Google account", "Email & password
account") instead of the machine string, keep a short disambiguating tail, and
tuck the exact principal into a tooltip for the operator who still needs it
(matching the audit log, configuring the admin list).

Pure string formatting, no I/O. Exposed to templates as Jinja filters
(``identity``, ``role_label``) registered in :mod:`maverick_dashboard.app`.
"""
from __future__ import annotations

from dataclasses import dataclass

# Auth0 / OIDC connection prefixes -> the provider a person recognizes. The
# ``sub`` is ``<connection>|<id>``; the connection names are Auth0's strategy
# identifiers plus a few common OIDC ``iss``-derived ones.
_PROVIDERS = {
    "auth0": "Email & password",
    "email": "Email & password",
    "google-oauth2": "Google",
    "google-apps": "Google Workspace",
    "windowslive": "Microsoft",
    "microsoft": "Microsoft",
    "waad": "Microsoft Entra ID",
    "adfs": "Microsoft Entra ID",
    "samlp": "SAML single sign-on",
    "oidc": "Single sign-on",
    "github": "GitHub",
    "gitlab": "GitLab",
    "linkedin": "LinkedIn",
    "facebook": "Facebook",
    "apple": "Apple",
    "okta": "Okta",
    "sms": "Phone number",
}

# Principal kind prefixes (the part before the first colon).
_KINDS = {
    "user": "Person",
    "agent": "AI agent",
    "service": "Service account",
    "svc": "Service account",
}

# Built-in role slugs -> the label an operator reads. Unknown (custom) roles
# fall through to a generic title-casing so a "privacy-officer" role still reads
# as "Privacy officer".
_ROLE_LABELS = {
    "admin": "Administrator",
    "operator": "Operator",
    "auditor": "Auditor",
    "viewer": "Viewer",
}

# Permission atoms -> plain verbs, for the custom-role builder.
_PERMISSION_LABELS = {
    "admin": "Administer",
    "audit": "Review audit trail",
    "operate": "Run work",
    "view": "View",
}


@dataclass(frozen=True)
class Identity:
    """A principal rendered for people. ``name`` is the human label, ``detail``
    a short disambiguating tail (or empty), ``raw`` the exact principal string
    for a tooltip / copy, ``kind`` the account-kind label."""

    name: str
    detail: str
    raw: str
    kind: str

    def __str__(self) -> str:  # so ``{{ p | identity }}`` alone is still safe
        return self.name


def _short(identifier: str) -> str:
    """A short, stable tail of an opaque id for disambiguation (never the whole
    thing -- that is the machine string we are hiding)."""
    identifier = identifier.strip()
    if len(identifier) <= 10:
        return identifier
    return "…" + identifier[-6:]


def humanize_principal(principal: object) -> Identity:
    """Turn a principal string into an :class:`Identity` for display.

    Accepts the ``kind:subject`` form the platform uses (``user:...``,
    ``agent:...``) as well as a bare subject. Never raises: anything
    unparseable renders as itself.
    """
    raw = "" if principal is None else str(principal).strip()
    if not raw:
        return Identity(name="Unknown", detail="", raw="", kind="")

    kind_prefix, sep, subject = raw.partition(":")
    if sep:
        kind = _KINDS.get(kind_prefix.lower(), kind_prefix.capitalize())
    else:
        kind, subject = "", raw

    subject = subject.strip()

    # An agent/service principal: the subject is already a chosen name.
    if kind_prefix.lower() in ("agent", "service", "svc"):
        return Identity(name=subject or kind, detail=kind, raw=raw, kind=kind)

    # Email-based subject (non-SSO invites store ``user:<email>``): show it.
    if "@" in subject and "|" not in subject:
        return Identity(name=subject, detail="Email", raw=raw, kind=kind or "Person")

    # Federated ``<connection>|<id>`` subject: show the provider + a short tail.
    if "|" in subject:
        connection, _, ident = subject.partition("|")
        provider = _PROVIDERS.get(connection.lower())
        if provider:
            return Identity(
                name=f"{provider} account", detail=_short(ident), raw=raw,
                kind=kind or "Person",
            )
        # Unknown connection: still avoid dumping the whole sub.
        pretty = connection.replace("-", " ").replace("_", " ").strip().capitalize()
        return Identity(
            name=f"{pretty or 'Federated'} account", detail=_short(ident), raw=raw,
            kind=kind or "Person",
        )

    # Loopback / local operator.
    if subject.lower() in ("local", "loopback", "localhost"):
        return Identity(name="Local operator", detail="", raw=raw, kind=kind or "Person")

    # A short, already-readable subject (or anything else): show it as-is.
    return Identity(name=subject or raw, detail="", raw=raw, kind=kind or "Person")


def role_label(role: object) -> str:
    """A built-in or custom role slug rendered as a readable label."""
    slug = "" if role is None else str(role).strip()
    if not slug:
        return ""
    if slug in _ROLE_LABELS:
        return _ROLE_LABELS[slug]
    return slug.replace("-", " ").replace("_", " ").strip().capitalize()


def permission_label(permission: object) -> str:
    """A permission atom (``operate``/``audit``/``admin``/``view``) as a verb."""
    slug = "" if permission is None else str(permission).strip()
    return _PERMISSION_LABELS.get(slug, slug.replace("-", " ").replace("_", " ").capitalize())


def humanize(value: object) -> str:
    """A generic enum/slug (status, risk level, action, kind) rendered as a
    readable label: underscores/hyphens become spaces and the first letter is
    capitalized (``in_progress`` -> ``In progress``, ``send_email`` -> ``Send
    email``). Empty in, empty out."""
    slug = "" if value is None else str(value).strip()
    if not slug:
        return ""
    return slug.replace("-", " ").replace("_", " ").strip().capitalize()


__all__ = [
    "Identity",
    "humanize_principal",
    "role_label",
    "permission_label",
    "humanize",
]

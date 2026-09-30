"""Plain-language labels for the values the console stores in code form.

Templates use these as Jinja filters so staff read "Early access" or "Grace
period", never ``edge`` or ``grace``. Stored values and form values are
unchanged; only what people read is translated.
"""
from __future__ import annotations

_CUSTOMER_STATUS = {
    "trial": "Trial", "active": "Active customer", "suspended": "Suspended",
    "churned": "Former customer",
}
_LICENSE_STATUS = {
    "active": "Active", "perpetual": "Active, never expires", "grace": "Grace period",
    "expired": "Expired", "revoked": "Revoked",
}
_CHANNEL = {"stable": "Stable", "edge": "Early access"}
_CHANNEL_HELP = {
    "stable": "Stable: tested updates for most customers",
    "edge": "Early access: newest updates, before they reach Stable",
}
_ROLE = {"owner": "Owner", "admin": "Administrator", "support": "Support", "viewer": "View only"}
_ROLE_HELP = {
    "owner": "Owner: everything, including managing the team",
    "admin": "Administrator: customers, licenses and releases",
    "support": "Support: the support desk, view everything else",
    "viewer": "View only: can look but not change anything",
}
_TICKET_STATUS = {
    "open": "Open", "in_progress": "In progress", "waiting": "Waiting on customer",
    "resolved": "Resolved", "closed": "Closed",
}
_PRIORITY = {"low": "Low", "normal": "Normal", "high": "High", "urgent": "Urgent"}
_TIER = {"basic": "Basic", "gold": "Gold", "platinum": "Platinum"}

_ACTION = {
    "customer.create": "Added a customer",
    "customer.update": "Updated customer details",
    "customer.details": "Updated customer details",
    "customer.status": "Changed customer status",
    "customer.channel": "Changed update channel",
    "customer.token.rotate": "Replaced connection token",
    "license.issue": "Issued a license",
    "license.revoke": "Revoked a license",
    "release.publish": "Published a release",
    "release.yank": "Withdrew a release",
    "staff.create": "Added a team member",
    "staff.invite": "Created a set-up link",
    "staff.invite.accept": "Used a set-up link and chose a password",
    "staff.password.change": "Changed their password",  # pragma: allowlist secret
    "staff.role": "Changed a team member's role",
    "staff.disable": "Turned off a team member's access",
    "staff.enable": "Turned on a team member's access",
    "staff.login.password": "Signed in (password step)",  # pragma: allowlist secret
    "staff.login.totp": "Signed in (code step)",
    "staff.totp.enroll": "Set up two-step sign-in",
    "ticket.create": "Opened a support ticket from a customer report",
    "ticket.bundle_appended": "Added a repeat customer report to a ticket",
    "ticket.status": "Changed ticket status",
    "ticket.assign": "Changed ticket owner or priority",
}


def _words(value) -> str:
    text = str(value or "").replace(".", " ").replace("_", " ").replace(":", " ").strip()
    return text[:1].upper() + text[1:] if text else ""


def _lookup(table: dict, value, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    return table.get(str(value), _words(value))


def customer_status(value) -> str:
    return _lookup(_CUSTOMER_STATUS, value)


def license_status(value) -> str:
    return _lookup(_LICENSE_STATUS, value)


def channel(value) -> str:
    return _lookup(_CHANNEL, value)


def channel_help(value) -> str:
    return _lookup(_CHANNEL_HELP, value)


def role(value) -> str:
    return _lookup(_ROLE, value)


def role_help(value) -> str:
    return _lookup(_ROLE_HELP, value)


def ticket_status(value) -> str:
    return _lookup(_TICKET_STATUS, value)


def priority(value) -> str:
    return _lookup(_PRIORITY, value)


def tier(value) -> str:
    return _lookup(_TIER, value)


def action(value) -> str:
    return _lookup(_ACTION, value)


def checkin(value) -> str:
    """What a deployment asked for when it last checked in."""
    text = str(value or "")
    if text == "served":
        return "Received its license"
    if text == "none":
        return "Asked for a license, none active"
    if text.startswith("release:"):
        return f"Checked for {channel(text.split(':', 1)[1])} updates"
    return _words(text) or "—"


def words(value) -> str:
    """Generic fallback: ``readiness_failing`` -> ``Readiness failing``."""
    return _words(value)


FILTERS = {
    "customer_status": customer_status, "license_status": license_status,
    "channel": channel, "channel_help": channel_help, "role": role,
    "role_help": role_help, "ticket_status": ticket_status, "priority": priority,
    "tier": tier, "action": action, "checkin": checkin, "words": words,
}


# ---- plain error pages -------------------------------------------------------

_ERROR_TITLES = {
    400: "That didn't work",
    403: "You don't have permission for this",
    404: "We couldn't find that",
    409: "Someone else changed this first",
    413: "That is too large",
}
_KNOWN_DETAILS = {
    "requires owner or admin role": "Only an owner or administrator can do this. Ask one of them for help.",
    "requires a support role": "Only support staff, administrators and owners can change tickets.",
    "requires the owner role": "Only an owner can manage the team.",
    "no such customer": "This customer doesn't exist. It may have been removed, or the link is wrong.",
    "no such license": "This license doesn't exist. The link may be wrong.",
    "no such release": "This release doesn't exist. The link may be wrong.",
    "no such ticket": "This ticket doesn't exist. The link may be wrong.",
    "no such team member": "This team member doesn't exist. The link may be wrong.",
    "invalid status": "Choose a status from the list.",
    "invalid environment": "Choose the customer's environment from the list.",
    "invalid priority": "Choose a priority from the list.",
    "seats must be a whole number of 1 or more":
        "Enter the number of seats as a whole number, or leave it empty for unlimited seats.",
    "another active ticket owns this intake identity":
        "Another open ticket already covers this customer's report. Close or merge that ticket first.",
}


def error_page(code: int, detail) -> tuple[str, str]:
    """(title, sentence) for an error page. Unknown details get a generic
    sentence; the raw text is shown only inside a collapsed details element."""
    title = _ERROR_TITLES.get(code, "Something went wrong")
    text = _KNOWN_DETAILS.get(str(detail or ""))
    if text is None:
        text = ("Check what you entered and try again." if code == 400
                else "Try again. If it keeps happening, ask an owner to check the console.")
    return title, text

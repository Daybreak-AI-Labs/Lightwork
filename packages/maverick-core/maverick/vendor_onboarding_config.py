"""Closed configuration for the bounded vendor onboarding application."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VendorOnboardingSettings:
    enable: bool = False
    resource_owner: str = ""
    document_connection: str = "gdrive"
    ticket_connection: str = "jira"
    reviewed_uploads: bool = True

    @classmethod
    def from_config(cls, config: dict) -> VendorOnboardingSettings:
        raw = config.get("vendor_onboarding", {})
        if not isinstance(raw, dict) or set(raw) - set(cls.__dataclass_fields__):
            raise ValueError("vendor_onboarding must be a closed configuration table")
        settings = cls(**raw)
        if type(settings.enable) is not bool or type(settings.reviewed_uploads) is not bool:
            raise ValueError("vendor_onboarding.enable and reviewed_uploads must be booleans")
        for key in ("resource_owner", "document_connection", "ticket_connection"):
            value = getattr(settings, key)
            if not isinstance(value, str) or len(value) > 256 or value != value.strip():
                raise ValueError(f"vendor_onboarding.{key} must be a bounded string")
            if settings.enable and not value:
                raise ValueError(f"vendor_onboarding.{key} is required when enabled")
        if settings.document_connection == settings.ticket_connection:
            raise ValueError("document and ticket credentials must be separately enrolled")
        return settings

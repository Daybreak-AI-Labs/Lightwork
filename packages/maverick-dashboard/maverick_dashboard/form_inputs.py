"""Validate public-form answers against the exact saved automation target."""
from __future__ import annotations

import math
import re
from datetime import date


class FormInputError(ValueError):
    """A received answer cannot safely start the bound automation."""


def validate_template_form(template, defaults: dict, fields: dict) -> None:
    """Refuse a missing required immutable-template parameter before append."""
    values = {**(defaults or {}), **fields}
    missing = [key for key in template.params if values.get(key) in (None, "")]
    if missing:
        raise FormInputError("Complete the required form fields: " + ", ".join(missing[:5]))


def coerce_flow_form(flow, data: dict) -> dict:
    """Use the published flow's declarations; reject ambiguous scalar values."""
    from maverick.flow.ir import coerce_inputs

    coerced, errors = coerce_inputs(flow, data)
    for spec in flow.inputs or []:
        key = str(spec.get("key") or "").strip()
        kind = str(spec.get("type") or "text")
        if not key or len(key) > 200 or kind not in {"text", "number", "bool", "date"}:
            raise FormInputError("This form's saved fields need repair before submission.")
        value = data.get(key)
        if value in (None, "") and "default" in spec:
            value = spec["default"]
        if value in (None, ""):
            continue
        if kind == "number":
            try:
                parsed = float(value) if not isinstance(value, bool) else float("nan")
            except (TypeError, ValueError):
                parsed = float("nan")
            if not math.isfinite(parsed):
                errors.append(f"Enter a valid number for {key}.")
            else:
                coerced[key] = parsed
        elif kind == "bool":
            answer = str(value).strip().lower()
            if not isinstance(value, bool) and answer not in {
                "true", "false", "1", "0", "yes", "no", "on", "off"
            }:
                errors.append(f"Choose Yes or No for {key}.")
            else:
                coerced[key] = value if isinstance(value, bool) else answer in {"true", "1", "yes", "on"}
        elif kind == "date":
            raw = str(value)
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                    raise ValueError("date format")
                date.fromisoformat(raw)
            except ValueError:
                errors.append(f"Enter a valid date for {key}.")
    if errors:
        raise FormInputError(errors[0])
    return coerced

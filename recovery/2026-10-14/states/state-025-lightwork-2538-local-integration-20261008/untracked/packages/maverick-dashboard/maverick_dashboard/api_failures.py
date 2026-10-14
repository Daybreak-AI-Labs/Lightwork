"""Safe customer-facing references for unexpected API composition failures."""

from __future__ import annotations

import logging
import secrets
import traceback

from fastapi import HTTPException


def unavailable(
    logger: logging.Logger,
    *,
    component: str,
    message: str,
    exception: Exception | None = None,
    recovery_hint: str = "Before repeating the action, check the records to see whether anything changed.",
) -> HTTPException:
    """Log a diagnosable failure and return a bounded, customer-safe response."""

    reference = "lw-failure-" + secrets.token_hex(16)
    if exception is None:
        logger.error(
            "%s is unavailable (reference %s)",
            component,
            reference,
            extra={"failure_reference": reference, "component": component},
        )
    else:
        frames = traceback.extract_tb(exception.__traceback__)
        location = " > ".join(
            f"{frame.name}:{frame.lineno}" for frame in frames[-5:]
        ) or "unknown location"
        logger.error(
            "%s is unavailable (reference %s; failure type %s; at %s)",
            component,
            reference,
            type(exception).__name__,
            location,
            extra={"failure_reference": reference, "component": component},
        )
    return HTTPException(
        status_code=503,
        detail=(
            f"{message} {recovery_hint} Open Help and include error reference {reference}."
        ),
        headers={
            "Cache-Control": "no-store, private, max-age=0",
            "X-Lightwork-Failure-Reference": reference,
        },
    )

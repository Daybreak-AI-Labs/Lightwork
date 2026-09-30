"""Trusted bootstrap for a restricted, credential-free plugin sample container.

This module is supplied as immutable bytes to ResearchContainerRunner. It is not
executed on the host. Plugin result content is reduced to a digest before return.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
from pathlib import Path


def main():
    payload = json.loads(Path("/candidate/sample.json").read_text(encoding="utf-8"))
    from importlib.metadata import version

    from maverick.plugin_lock import _dist_content_hash
    if version(payload["distribution"]) != payload["version"] or _dist_content_hash(payload["distribution"]) != payload["content_sha256"]:
        raise ValueError("image does not contain the exact reviewed plugin bytes")
    # Optional validator belongs in the reviewed image. Absence fails closed.
    from jsonschema import Draft202012Validator, FormatChecker

    module, _, attribute = payload["entry"].partition(":")
    target = importlib.import_module(module)
    for part in attribute.split("."):
        target = getattr(target, part)
    tool = target()
    schema = tool.input_schema
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("tool input schema must declare an object")
    Draft202012Validator.check_schema(schema)
    # Reject remote references before constructing the validator. The container
    # also has no network, but a missing schema must not silently be accepted.
    def refs(value):
        if isinstance(value, dict):
            if "$ref" in value and not str(value["$ref"]).startswith("#"):
                raise ValueError("external schema references are unsupported")
            for child in value.values():
                refs(child)
        elif isinstance(value, list):
            for child in value:
                refs(child)
    refs(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload["sample"])
    print(json.dumps({"operation": payload["operation"], "phase": "dispatch"}), flush=True)
    result = tool.fn(payload["sample"])
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    encoded = json.dumps(result, sort_keys=True, allow_nan=False).encode()
    print(json.dumps({"operation": payload["operation"], "phase": "result", "schema_valid": True, "sample_valid": True, "completed": True,
        "content_sha256": payload["content_sha256"], "result_sha256": hashlib.sha256(encoded).hexdigest()}))


if __name__ == "__main__":
    main()

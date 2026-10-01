"""Trusted bootstrap supplied as immutable bytes to the restricted container."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path


def main():
    payload = json.loads(Path("/candidate/sample.json").read_text(encoding="utf-8"))
    from plugin_contracts import execute, execute_grpc, execute_node

    def dispatch():
        print(json.dumps({"operation": payload["operation"], "phase": "dispatch"}), flush=True)
    if payload["group"] in {"maverick.ts", "maverick.grpc"}:
        source = Path("/candidate/plugin" + payload["source_suffix"]).read_bytes()
        if hashlib.sha256(source).hexdigest() != payload["content_sha256"]:
            raise ValueError("Node source differs from the reviewed version")
        result = execute_node(payload, dispatch) if payload["group"] == "maverick.ts" else execute_grpc(payload, dispatch)
    else:
        from importlib.metadata import version

        from maverick.plugin_lock import _dist_content_hash
        if version(payload["distribution"]) != payload["version"] or _dist_content_hash(payload["distribution"], portable_paths=True) != payload["content_sha256"]:
            raise ValueError("image does not contain the exact reviewed plugin bytes")
        module, _, attribute = payload["entry"].partition(":")
        target = importlib.import_module(module)
        for part in attribute.split("."):
            target = getattr(target, part)
        result = execute(payload["group"], target, payload["sample"], dispatch)
    encoded = json.dumps(result, sort_keys=True, allow_nan=False).encode()
    print(json.dumps({"operation": payload["operation"], "phase": "result", "schema_valid": True,
        "sample_valid": True, "completed": True, "content_sha256": payload["content_sha256"],
        "result_sha256": hashlib.sha256(encoded).hexdigest()}))


if __name__ == "__main__":
    main()

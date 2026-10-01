"""Actual plugin-kind adapters, executed only inside the reviewed container.

No host discovery imports this module. Channel checks invoke the real lifecycle
and send contract; the immutable image must supply synthetic local configuration.
Network denial is enforced by the container, never by a monkeypatched client.
"""
from __future__ import annotations

import asyncio
import inspect


def validator(schema):
    from jsonschema import Draft202012Validator, FormatChecker

    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("input schema must declare an object")
    Draft202012Validator.check_schema(schema)
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
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate(schema, sample):
    validator(schema).validate(sample)


def await_result(value):
    return asyncio.run(value) if inspect.isawaitable(value) else value


def execute(group, target, sample, dispatch):
    if group == "maverick.tools":
        tool = target()
        validate(tool.input_schema, sample)
        if not callable(tool.fn):
            raise ValueError("tool has no callable handler")
        dispatch()
        return await_result(tool.fn(sample))
    if group == "maverick.personas":
        validate({"type": "object", "additionalProperties": False}, sample)
        if not callable(target):
            raise ValueError("persona must be a callable renderer")
        dispatch()
        value = target()
        if not isinstance(value, str) or not value.strip():
            raise ValueError("persona must return nonempty prompt text")
        return value
    if group == "maverick.skills":
        from maverick.skills import Skill, _relevant_skills_lexical, render_for_prompt
        validate({"type": "object", "properties": {"goal": {"type": "string", "minLength": 1}},
                  "required": ["goal"], "additionalProperties": False}, sample)
        if (not isinstance(target, Skill) or not isinstance(target.name, str) or not target.name
                or not isinstance(target.body, str) or not target.body.strip()
                or not isinstance(target.triggers, list) or not target.triggers
                or any(not isinstance(item, str) or not item for item in target.triggers)
                or not isinstance(target.tools_needed, list)
                or any(not isinstance(item, str) or not item for item in target.tools_needed)):
            raise ValueError("skill has a malformed declared contract")
        dispatch()
        selected = _relevant_skills_lexical(sample["goal"], [target], max_n=1)
        return {"selected": [skill.name for skill in selected], "prompt": render_for_prompt(selected)}
    if group == "maverick.channels":
        from dataclasses import asdict

        from maverick_channels.base import Channel, IncomingMessage, Reply
        schema = {"type": "object", "properties": {
            "user_id": {"type": "string", "minLength": 1}, "text": {"type": "string"},
            "reply": {"type": "string"}, "startup_seconds": {"type": "number", "minimum": 0, "maximum": 5}},
            "required": ["user_id", "text", "reply"], "additionalProperties": False}
        validate(schema, sample)
        if not isinstance(target, type) or not issubclass(target, Channel):
            raise ValueError("channel must be a concrete Channel subclass")
        async def exercise():
            received = []
            async def handler(message):
                if not isinstance(message, IncomingMessage):
                    raise ValueError("channel dispatched malformed incoming message")
                received.append({"user_id": message.user_id, "text": message.text, "channel": message.channel})
                return Reply(sample["reply"])
            channel = target(handler)
            dispatch()
            started = asyncio.create_task(channel.start())
            try:
                await asyncio.sleep(sample.get("startup_seconds", 0))
                if started.done():
                    started.result()
                reply = await channel.dispatch(IncomingMessage(sample["user_id"], sample["text"], channel=channel.name))
                if not isinstance(reply, Reply):
                    raise ValueError("channel returned an invalid reply")
                await channel.send(sample["user_id"], sample["reply"])
                return {"reply": asdict(reply), "received": received, "send_completed": True}
            finally:
                await channel.stop()
                if not started.done():
                    started.cancel()
                await asyncio.gather(started, return_exceptions=True)
        return asyncio.run(exercise())
    raise ValueError("unsupported plugin contract")


def execute_node(payload, dispatch):
    from maverick.ts_plugin_host import (
        _close_all_children,
        _fetch_manifest,
        _PinnedCommand,
        load_ts_plugin,
    )
    validate({"type": "object", "properties": {"tool": {"type": "string", "minLength": 1},
               "arguments": {"type": "object"}}, "required": ["tool", "arguments"],
               "additionalProperties": False}, payload["sample"])
    command = ["node", "/candidate/plugin" + payload["source_suffix"]]
    pinned = _PinnedCommand.from_command(command)
    protocol, manifest = _fetch_manifest(pinned)
    names = set()
    for entry in manifest:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or entry["name"] in names:
            raise ValueError("malformed or duplicate NDJSON tool entry")
        names.add(entry["name"])
        # Validate every declared schema, even when the sample selects another tool.
        schema = entry.get("inputSchema")
        validator(schema)
    chosen = next((item for item in manifest if item["name"] == payload["sample"]["tool"]), None)
    if chosen is None:
        raise ValueError("sample tool is absent from this exact plugin manifest")
    validate(chosen["inputSchema"], payload["sample"]["arguments"])
    try:
        tools = load_ts_plugin(command, call_timeout=20)
        tool = next(item for item in tools if item.name == chosen["name"])
        dispatch()
        value = tool.fn(payload["sample"]["arguments"])
        # The actual host preserves transport failure/unknown outcome as ERROR.
        # Matching an expected error string must never qualify a failed call.
        if isinstance(value, str) and value.startswith("ERROR:"):
            raise ValueError("NDJSON invocation did not complete with a confirmed result")
        return value
    finally:
        _close_all_children()


def execute_grpc(payload, dispatch):
    from maverick.grpc_plugin_host import _close_all_channels, load_grpc_plugin
    validate({"type": "object", "properties": {"tool": {"type": "string", "minLength": 1},
               "arguments": {"type": "object"}}, "required": ["tool", "arguments"],
               "additionalProperties": False}, payload["sample"])
    try:
        tools = load_grpc_plugin(payload["target"],
            command=[payload["runtime"], "/candidate/plugin" + payload["source_suffix"]],
            call_timeout=20, strict_schema=True, expected_source_sha256=payload["content_sha256"])
        tool = next((item for item in tools if item.name == payload["sample"]["tool"]), None)
        if tool is None:
            raise ValueError("sample tool is absent from the actual gRPC manifest")
        validate(tool.input_schema, payload["sample"]["arguments"])
        dispatch()
        result = tool.fn(payload["sample"]["arguments"])
        if getattr(result, "attempt", None) is None or result.attempt.outcome_unknown or result.startswith("ERROR:"):
            raise ValueError("gRPC invocation did not confirm completion")
        return str(result)
    finally:
        _close_all_channels()

"""Pre-declared learning envelopes (EU AI Act Art. 43(4)).

Changes to a continuously learning high-risk system that were pre-determined at
the initial conformity assessment, and documented, are not a "substantial
modification". Declare the envelope up front -- each learnable parameter with a
numeric range or an allowed set -- and check every learning update against it:
anything outside (or any undeclared parameter) needs re-assessment.
"""
from __future__ import annotations

Envelope = dict[str, dict]  # param -> {"min": x, "max": y} | {"in": [values]}


def violations(envelope: Envelope, change: dict) -> list[str]:
    out = []
    for param, value in sorted(change.items()):
        spec = envelope.get(param)
        if spec is None:
            out.append(f"{param}: not in the declared envelope")
        elif "in" in spec and value not in spec["in"]:
            out.append(f"{param}: {value!r} not among {spec['in']!r}")
        elif "in" not in spec and not (spec.get("min", value) <= value <= spec.get("max", value)):
            out.append(f"{param}: {value!r} outside [{spec.get('min')}, {spec.get('max')}]")
    return out


def substantial(envelope: Envelope, change: dict) -> bool:
    return bool(violations(envelope, change))

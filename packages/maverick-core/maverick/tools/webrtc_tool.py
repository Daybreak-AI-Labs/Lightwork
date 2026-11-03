"""WebRTC data-channel tool.

The data-channel interface is retained for callers, but offer, answer and send
are disabled until ICE egress is policy-controlled:

  - ``offer``  — create an RTCPeerConnection + data channel, return the local
                 SDP offer (hand it to the remote peer out-of-band);
  - ``answer`` — apply the remote peer's answer SDP;
  - ``send``   — ship a text message over the open data channel;
  - ``close``  — tear the connection down.

Honest scope: DATA CHANNELS ONLY. Media tracks (audio/video capture,
playback, transcoding) are out of scope for this tool, and signalling is the
caller's job — copy the SDP blobs over any channel you already have (the
dashboard, a webhook, a file). One connection at a time, held module-level,
mirroring the browser tool's single persistent session.

Factory exported, NOT registered in the default tool set. Network operations
are unavailable until ICE, STUN and TURN can enforce Lightwork's egress policy.
"""
from __future__ import annotations

from typing import Any

from . import Tool

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "op": {
            "type": "string",
            "enum": ["offer", "answer", "send", "close"],
            "description": "offer (create + return SDP), answer (apply remote SDP), send, close.",
        },
        "remote_sdp": {"type": "string", "description": "Remote answer SDP (answer op)."},
        "message": {"type": "string", "description": "Text to send over the data channel (send op)."},
        "label": {"type": "string", "description": "Data-channel label for offer (default 'maverick')."},
    },
    "required": ["op"],
}

# One connection at a time (module-level, like the browser session).
_state: dict[str, Any] = {"pc": None, "channel": None}


def _reset_state() -> None:
    _state["pc"] = None
    _state["channel"] = None


async def _run(args: dict[str, Any]) -> str:
    op = args.get("op")

    if op == "close":  # works without aiortc installed (nothing to close)
        pc = _state["pc"]
        _reset_state()
        if pc is None:
            return "no open connection"
        await pc.close()
        return "connection closed"

    # ICE may send UDP to peer-provided or default STUN/TURN endpoints before
    # application-level URL checks can run. Fail closed until the transport
    # enforces the same network policy as the rest of Lightwork.
    return "ERROR: WebRTC network operations are unavailable because ICE egress is not policy-controlled."


def webrtc_tool() -> Tool:
    """Factory: WebRTC data-channel messaging (offer/answer/send/close)."""
    return Tool(
        name="webrtc",
        description=(
            "WebRTC network operations are unavailable until ICE egress is "
            "policy-controlled. Close can release an existing connection."
        ),
        input_schema=_SCHEMA,
        fn=_run,
    )


__all__ = ["webrtc_tool"]

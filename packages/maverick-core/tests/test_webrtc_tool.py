"""WebRTC cannot open connections until ICE egress is policy-controlled."""
import asyncio
import sys
import types

import pytest
from maverick.tools import webrtc_tool as mod
from maverick.tools.webrtc_tool import webrtc_tool


@pytest.mark.parametrize("op", ["offer", "answer", "send"])
def test_network_operations_never_create_a_peer(monkeypatch, op):
    def refuse_peer():
        pytest.fail("a blocked WebRTC operation created a peer connection")

    monkeypatch.setitem(sys.modules, "aiortc", types.SimpleNamespace(RTCPeerConnection=refuse_peer))
    result = asyncio.run(webrtc_tool().fn({"op": op, "remote_sdp": "v=0", "message": "x"}))
    assert "egress is not policy-controlled" in result


def test_close_releases_an_existing_peer():
    closed = []

    class Peer:
        async def close(self):
            closed.append(True)

    mod._state.update(pc=Peer(), channel=object())
    assert asyncio.run(webrtc_tool().fn({"op": "close"})) == "connection closed"
    assert closed and mod._state == {"pc": None, "channel": None}

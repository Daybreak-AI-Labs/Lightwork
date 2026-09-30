"""Bounded pilot transport negotiates exactly the encoding it admits."""
import httpx
import pytest
from maverick.external_gateway_runtime_pilot import (
    PilotProtocolError,
    PinnedGatewayHTTPTransport,
)


@pytest.mark.parametrize("encoded", [False, True])
def test_pilot_requests_identity_and_rejects_encoded_bytes_before_read(monkeypatch, encoded):
    requests = []

    class UnreadableEncodedStream(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("encoded response must be rejected before body consumption")
            yield b"unreachable"

    def serve(request):
        requests.append(request)
        if encoded:
            return httpx.Response(200, headers={"Content-Encoding": "gzip"},
                                  stream=UnreadableEncodedStream())
        return httpx.Response(200, json={"decision": "allow"})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(serve),
    ))

    def request():
        return PinnedGatewayHTTPTransport().request(
            "POST", "https://synthetic.example.test/api/v1/external/runtime/evaluate",
            headers={"accept-encoding": "gzip"}, body=b"{}", timeout=1,
            max_response_bytes=1024,
        )

    if encoded:
        with pytest.raises(PilotProtocolError, match="encoding is unsupported"):
            request()
    else:
        assert request().body == b'{"decision":"allow"}'
    assert len(requests) == 1
    assert requests[0].headers.get_list("Accept-Encoding") == ["identity"]

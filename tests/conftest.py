import asyncio
import pytest
import msgspec
from khorsyio.core.http import Request, Response, Router, CorsConfig, HttpApp

@pytest.fixture
def scope():
    return {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [
            (b"host", b"localhost"),
            (b"cookie", b"session=abc; user=123"),
            (b"content-type", b"application/json"),
            (b"origin", b"http://localhost:3000")
        ],
        "query_string": b"a=1&b=2",
    }

@pytest.fixture
def receive_json():
    async def _receive():
        return {"type": "http.request", "body": b'{"key": "value"}', "more_body": False}
    return _receive

class MockSend:
    def __init__(self):
        self.messages = []
    async def __call__(self, message):
        self.messages.append(message)

@pytest.fixture
def send():
    return MockSend()

import asyncio
import pytest
import msgspec
from khorsyio.core.bus import Bus
from khorsyio.core.handler import Handler
from khorsyio.core.structs import Envelope

class Data(msgspec.Struct):
    val: str

class MockHandler(Handler):
    subscribes_to = "test.event"
    publishes = "test.result"
    input_type = Data
    
    async def process(self, data: Data, ctx) -> Data:
        return Data(val=f"echo:{data.val}")

@pytest.mark.asyncio
async def test_bus_publish_subscribe():
    bus = Bus()
    handler = MockHandler()
    bus.register(handler)
    
    task = asyncio.create_task(bus.start())
    await asyncio.sleep(0.1)
    
    await bus.publish("test.event", Data(val="hello"))
    await asyncio.sleep(0.1)
    
    assert bus.metrics.processed["MockHandler"] == 1
    
    await bus.stop()
    await task

@pytest.mark.asyncio
async def test_bus_request_response():
    bus = Bus()
    bus.register(MockHandler())
    
    task = asyncio.create_task(bus.start())
    await asyncio.sleep(0.1)
    
    res = await bus.request("test.event", Data(val="req"), response_type="test.result", timeout=1.0)
    
    assert isinstance(res, Envelope)
    # Corrected attribute access: res.decode(Data) instead of res.data
    decoded = res.decode(Data)
    assert decoded.val == "echo:req"
    
    await bus.stop()
    await task

@pytest.mark.asyncio
async def test_bus_metrics_and_log():
    bus = Bus()
    bus.register(MockHandler())
    
    task = asyncio.create_task(bus.start())
    await asyncio.sleep(0.1)
    
    await bus.publish("test.event", Data(val="m"))
    await asyncio.sleep(0.1)
    
    stats = bus.metrics.snapshot()
    assert "MockHandler" in stats
    assert stats["MockHandler"]["processed"] == 1
    
    logs = bus.event_log.recent(10)
    assert len(logs) >= 2 # publish + result
    assert any(l["event_type"] == "test.event" for l in logs)
    
    await bus.stop()
    await task

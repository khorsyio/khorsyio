import asyncio
import pytest
import msgspec
from khorsyio.core.bus import Bus
from khorsyio.core.handler import Handler
from khorsyio.core.structs import Context, Envelope

class CalcData(msgspec.Struct):
    n: int

class CalcResult(msgspec.Struct):
    result: int

class HeavyHandler(Handler):
    subscribes_to = "calc.heavy"
    publishes = "calc.result"
    input_type = CalcData
    output_type = CalcResult
    execution_mode = "process"

    async def process(self, data: CalcData, ctx: Context):
        # Имитация тяжелых вычислений
        res = sum(i * i for i in range(data.n))
        return CalcResult(result=res)

@pytest.mark.asyncio
async def test_process_execution():
    bus = Bus()
    handler = HeavyHandler()
    bus.register(handler)
    
    # Запускаем шину в фоне
    bus_task = asyncio.create_task(bus.start())
    
    try:
        # Делаем запрос
        response = await bus.request(
            "calc.heavy", 
            CalcData(n=1000), 
            response_type="calc.result",
            timeout=5.0
        )
        
        assert response.event_type == "calc.result"
        decoded = response.decode(CalcResult)
        assert decoded.result == sum(i * i for i in range(1000))
        
    finally:
        await bus.stop()
        bus_task.cancel()
        try:
            await bus_task
        except asyncio.CancelledError:
            pass

if __name__ == "__main__":
    asyncio.run(test_process_execution())

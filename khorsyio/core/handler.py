from abc import ABC, abstractmethod
from khorsyio.core.structs import Envelope, Context
import msgspec


class Handler(ABC):
    subscribes_to: str = ""
    publishes: str = ""
    input_type: type = None
    output_type: type = None
    timeout: float = 30.0
    execution_mode: str = "main"

    async def handle(self, envelope: Envelope) -> Envelope | None:
        if self.input_type:
            data = envelope.decode(self.input_type)
        else:
            data = envelope
        result = await self.process(data, envelope.ctx)
        if result is None:
            return None
        if isinstance(result, Envelope):
            return result
        if self.publishes and isinstance(result, msgspec.Struct):
            return envelope.forward(self.publishes, result, source=self.__class__.__name__)
        return None

    @abstractmethod
    async def process(self, data, ctx: Context) -> msgspec.Struct | Envelope | None:
        ...

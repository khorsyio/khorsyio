import msgspec
import time
import uuid


class Context(msgspec.Struct):
    trace_id: str = msgspec.field(default_factory=lambda: uuid.uuid4().hex[:16])
    timestamp: float = msgspec.field(default_factory=time.time)
    source: str = ""
    user_id: str = ""
    extra: dict = msgspec.field(default_factory=dict)


class Error(msgspec.Struct):
    code: str = "error"
    message: str = ""
    source: str = ""
    trace_id: str = ""
    details: dict = msgspec.field(default_factory=dict)


class Envelope(msgspec.Struct):
    ctx: Context
    event_type: str
    payload: bytes
    error: Error | None = None

    @classmethod
    def create(cls, event_type: str, data: msgspec.Struct, source: str = "",
               trace_id: str | None = None, user_id: str = "", extra: dict | None = None):
        ctx = Context(source=source, trace_id=trace_id or uuid.uuid4().hex[:16],
                      user_id=user_id, extra=extra or {})
        return cls(ctx=ctx, event_type=event_type, payload=msgspec.json.encode(data))

    @classmethod
    def error_from(cls, original: "Envelope", message: str, code: str = "error",
                   source: str = "", details: dict | None = None):
        err = Error(code=code, message=message, source=source,
                    trace_id=original.ctx.trace_id, details=details or {})
        return cls(
            ctx=Context(trace_id=original.ctx.trace_id, source=source, user_id=original.ctx.user_id),
            event_type=f"{original.event_type}.error",
            payload=original.payload,
            error=err,
        )

    def decode(self, typ: type):
        return msgspec.json.decode(self.payload, type=typ)

    def forward(self, event_type: str, data: msgspec.Struct, source: str = ""):
        return Envelope.create(event_type, data, source=source,
                               trace_id=self.ctx.trace_id, user_id=self.ctx.user_id,
                               extra=self.ctx.extra)

    @property
    def trace_id(self) -> str:
        return self.ctx.trace_id

    @property
    def is_error(self) -> bool:
        return self.error is not None

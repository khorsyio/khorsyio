import inspect
from khorsyio.core.http import Route


class Inject:
    db = "db"
    client = "client"
    bus = "bus"
    transport = "transport"


class Domain:
    handlers: list = []
    routes: list[Route] = []
    namespace: str = ""

    def setup(self, app):
        for h in self.handlers:
            if isinstance(h, type):
                handler = _create_handler(h, app)
            else:
                handler = h
            if self.namespace:
                if handler.subscribes_to and not handler.subscribes_to.startswith(self.namespace + "."):
                    handler.subscribes_to = f"{self.namespace}.{handler.subscribes_to}"
                if handler.publishes and not handler.publishes.startswith(self.namespace + "."):
                    handler.publishes = f"{self.namespace}.{handler.publishes}"
            if hasattr(app.bus, "app") and app.bus.app is None:
                app.bus.app = app
            app.register(handler)
        app.router.mount(self.routes)


def _create_handler(cls, app):
    init = cls.__init__
    if init is object.__init__:
        return cls()
    sig = inspect.signature(init)
    params = {k: v for k, v in sig.parameters.items() if k != "self"}
    if not params:
        return cls()
    _map = {"db": app.db, "client": app.client, "bus": app.bus, "transport": app.transport, "app": app}
    kwargs = {}
    for name in params:
        if name in _map:
            kwargs[name] = _map[name]
        else:
            kwargs[name] = app
    return cls(**kwargs)

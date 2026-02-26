Test coverage plan for khorsyio

Scope and priorities
- Focus on pure logic and interfaces that do not require external services (DB servers, HTTP servers). Where I/O is required by API surface, use in-memory structures or stubs/mocks.
- Keep tests deterministic and fast; avoid sleeps except where mandatory to exercise timeouts with minimal durations.

Covered modules and functionality
1. khorsyio.core.http
   - Request helpers
     - headers lookup: header(), cookies parsing, query param access via param(), body() aggregation and json() decoding.
   - Response helpers
     - json(), text(), ok(), error() output structure and headers; cookie serialization with attributes.
   - Router
     - add()/get()/post() registration, mount(); exact match; parameterized path matching and resolve(); middleware short-circuit and after-hooks execution order.
   - CorsConfig and HttpApp
     - allowed_origin() wildcard and exact; preflight OPTIONS 204 with headers; simple request includes CORS headers only when Origin allowed; credentials and max-age respected.

2. khorsyio.core.bus
   - Registration and graph validation: register() adjusts handler timeout; validate_graph() warnings for published without subscribers.
   - Publish/subscribe dispatch: event delivered to all subscribers; handler result re-enqueued; Envelope propagation.
   - Request/response flow: request() waiter keyed by response_type; successful response; timeout returns .error envelope with code "timeout".
   - Metrics and EventLog: processed counters, avg_ms computation, last_error stored; event log records entries and filtering by event_type/trace_id.
   - Scheduler: basic tick publishes events at interval (single tick observed) and cancellation on stop().

3. khorsyio.db.query
   - apply_filters(): eq/ne/lt/lte/gt/gte/in/contains/icontains/startswith/istartswith/endswith/iendswith/isnull/between; produces correct SQL WHERE fragments.
   - apply_order(): single and multiple fields; +/- prefix; maintains order_by clauses.
   - apply_cursor(): asc/desc comparisons on a field.
   - paginate(): computes total, slices page/size, returns items and meta; cover both scalars() path (single selected entity) and mappings() path (multiple columns). Uses a stub AsyncSession and stub Result.

Notes
- Async tests are run via a small helper that executes coroutines with asyncio.run to avoid extra plugins.
- SQL assertions compare compiled SQL fragments (dialect=default) rather than exact strings where appropriate.

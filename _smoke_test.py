import sys, uuid, traceback

out = open("_smoke_out.txt", "w", encoding="utf-8")
def log(*a): print(*a, file=out); print(*a)

try:
    import agent_trace, trace_schema, otel_exporter
    log("imports OK")

    from trace_schema import (
        TraceEvent, SCHEMA_VERSION, SchemaValidationError,
        make_operational_body, make_cognitive_body, make_contextual_body,
        TraceSurface, TraceLevel, SpanKind,
    )
    log("SCHEMA_VERSION =", SCHEMA_VERSION)

    # Empty event should fail validation
    e = TraceEvent()
    try:
        e.validate()
        log("FAIL: empty event accepted")
    except SchemaValidationError as err:
        log("OK invalid root event rejected:", str(err)[:80])

    # Valid operational event
    tid = uuid.uuid4().hex
    sid = uuid.uuid4().hex[:16]
    ev = TraceEvent(
        surface=TraceSurface.OPERATIONAL,
        trace_id=tid, span_id=sid, agent_name="X",
        body=make_operational_body("hello", "start", args_count=2, args_summary="x,y"),
    )
    ev.validate()
    log("valid op event OK; attrs sample:", dict(list(ev.to_otel_attributes().items())[:6]))

    # Contextual HTTP
    body = make_contextual_body(
        "http_get", "requests",
        url="https://x.test/y", http_method="GET",
        status_code=200, latency_ms=42.0, headers_summary="Auth=***",
    )
    ev2 = TraceEvent(
        surface=TraceSurface.CONTEXTUAL, trace_id=tid, span_id=uuid.uuid4().hex[:16],
        agent_name="X", body=body,
    )
    ev2.validate()
    a = ev2.to_otel_attributes()
    log("http.url=", a.get("http.url"))
    log("http.status_code=", a.get("http.status_code"))
    log("io.operation=", a.get("io.operation"))

    # Cognitive
    cbody = make_cognitive_body(thought="thinking out loud", model="gpt-x", confidence=0.9)
    ev3 = TraceEvent(
        surface=TraceSurface.COGNITIVE, trace_id=tid, span_id=uuid.uuid4().hex[:16],
        agent_name="X", body=cbody,
    )
    ev3.validate()
    log("cognitive attrs:", {k: v for k, v in ev3.to_otel_attributes().items() if k.startswith("llm.")})

    # End-to-end wrapper test
    from agent_trace import init, shutdown, instrument_agent, get_logger
    init(agent_name="Smoke", enable_otel=False)

    class Agent:
        def hello(self, x):
            return "<thinking>secret reason</thinking>final=" + str(x)

    a = Agent()
    instrument_agent(a, name="SmokeAgent")
    rv = a.hello(7)
    log("returned:", repr(rv))
    log("events_written:", get_logger().events_written)
    shutdown()
    log("PASS")
except Exception:
    log("EXCEPTION:")
    log(traceback.format_exc())
    sys.exit(1)
finally:
    out.close()

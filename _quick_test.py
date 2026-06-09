"""Quick AgentTrace integration test."""
import sys
import os
from pathlib import Path
import json

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

print("=" * 60)
print("AgentTrace Quick Test")
print("=" * 60)

# 1. Test imports
print("\n[1] Importing AgentTrace modules...")
try:
    from trace_schema import TraceSurface, TraceEvent, TraceLevel, SpanKind
    from trace_schema import make_operational_body, make_cognitive_body, make_contextual_body
    from otel_exporter import OTelExporter
    import agent_trace
    print("   ✓ All modules imported successfully")
except Exception as e:
    print(f"   ✗ Import error: {e}")
    sys.exit(1)

# 2. Initialize AgentTrace
print("\n[2] Initializing AgentTrace...")
agent_trace.init(
    agent_name="QuickTestAgent",
    log_dir=Path("trace_logs"),
    enable_otel=False,
    verbose=True,
)
alogger = agent_trace.get_logger()
print(f"   ✓ ALogger ready: {alogger is not None}")
print(f"   Agent name: {alogger.agent_name}")
print(f"   Log dir: {alogger.log_dir}")

# 3. Record events across all three surfaces
print("\n[3] Recording trace events across all surfaces...")

# Operational surface
span1 = alogger.record_operational_start(method="data_ingestion", args_summary="100 alerts")
alogger.record_operational_complete(method="data_ingestion", span_id=span1, duration_ms=150.0)
print("   ✓ Operational: start + complete")

# Cognitive surface
alogger.record_cognitive(
    thought="Analyzing correlation patterns in security alerts",
    plan="Step 1: Group by IP, Step 2: Check timestamps",
    model="gemini-2.0-flash",
    prompt_tokens=500,
    completion_tokens=150,
    temperature=0.7,
)
print("   ✓ Cognitive: thought, plan, model, tokens")

# Contextual surface
alogger.record_contextual(
    operation="http_request",
    source="https://api.security.microsoft.com/alerts",
    query_summary="GET /alerts?severity=high",
    response_summary="200 OK, 47 alerts",
    byte_count=24500,
    status_code=200,
)
print("   ✓ Contextual: HTTP request I/O")

# Another operational with error
try:
    alogger.record_operational_error(
        method="failed_operation", span_id="err_span",
        duration_ms=5000.0, error="TimeoutError('API timed out')"
    )
    print("   ✓ Operational: error event")
except Exception as e:
    print(f"   ✗ Error recording error event: {e}")

print(f"\n   Total trace events written: {alogger.events_written}")

# 4. Verify trace log file
print("\n[4] Verifying trace log file...")
trace_path = Path("trace_logs/traces.jsonl")
if trace_path.exists():
    content = trace_path.read_text(encoding="utf-8")
    lines = [l for l in content.strip().splitlines() if l.strip()]
    print(f"   ✓ Trace log found: {len(lines)} events written")
    
    # Show surface distribution
    surfaces = {}
    for line in lines:
        evt = json.loads(line)
        s = evt.get("surface", "?")
        surfaces[s] = surfaces.get(s, 0) + 1
    
    for surface, count in surfaces.items():
        print(f"     - {surface}: {count}")
else:
    print(f"   ✗ Trace log NOT found at {trace_path}")

# 5. Shutdown
print("\n[5] Shutting down AgentTrace...")
agent_trace.shutdown()
print("   ✓ Shutdown complete")

print("\n" + "=" * 60)
print("AGENTTRACE QUICK TEST PASSED")
print("=" * 60)
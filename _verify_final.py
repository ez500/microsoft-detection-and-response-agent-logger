"""Final verification - saves results to _verify_final_results.txt"""
import sys
import os
from pathlib import Path

os.chdir(Path(__file__).parent)
sys.path.insert(0, '.')

lines = []

def log(msg):
    lines.append(str(msg))

log("=" * 60)
log("FINAL VERIFICATION - AgentTrace Integration")
log("=" * 60)

# 1. Check all core files exist
log("\n[1] Core file verification:")
core_files = [
    "trace_schema.py", "otel_exporter.py", "agent_trace.py",
    "config.py", "events.py", "scanner.py", "narrator.py", "writer.py", "main.py"
]
for f in core_files:
    exists = Path(f).exists()
    size = Path(f).stat().st_size if exists else 0
    log(f"  {'✓' if exists and size > 0 else '✗'} {f}: {size} bytes")

# 2. Import verification
log("\n[2] Import verification:")
try:
    from trace_schema import TraceSurface, TraceEvent, TraceLevel, SpanKind
    from trace_schema import make_operational_body, make_cognitive_body, make_contextual_body
    log("  ✓ trace_schema: OK")
except Exception as e:
    log(f"  ✗ trace_schema: {e}")

try:
    from otel_exporter import OTelExporter
    log("  ✓ otel_exporter: OK")
except Exception as e:
    log(f"  ✗ otel_exporter: {e}")

try:
    import agent_trace
    from agent_trace import ALogger, TraceContext, init, get_logger, shutdown, instrument
    log("  ✓ agent_trace: OK")
except Exception as e:
    log(f"  ✗ agent_trace: {e}")

try:
    from config import Config
    log("  ✓ config: OK")
except Exception as e:
    log(f"  ✗ config: {e}")

try:
    from events import EventType, AuditEvent
    trace_types = [e.value for e in EventType if e.value.startswith("TRACE_")]
    log(f"  ✓ events: OK (trace event types: {trace_types})")
except Exception as e:
    log(f"  ✗ events: {e}")

try:
    from scanner import ArtifactScanner
    log("  ✓ scanner: OK")
except Exception as e:
    log(f"  ✗ scanner: {e}")

try:
    from narrator import LLMNarrator
    log("  ✓ narrator: OK")
except Exception as e:
    log(f"  ✗ narrator: {e}")

try:
    from writer import AuditLogWriter
    log("  ✓ writer: OK")
except Exception as e:
    log(f"  ✗ writer: {e}")

# 3. AgentTrace initialization test
log("\n[3] AgentTrace init test:")
try:
    agent_trace.init(
        agent_name="VerifyAgent",
        log_dir=Path("trace_logs"),
        enable_otel=False,
    )
    al = get_logger()
    log(f"  ✓ ALogger created: agent={al.agent_name}")
    
    # Test all 3 surfaces
    span = al.record_operational_start(method="verify_scan", args_summary="test args")
    log(f"  ✓ Operational start: span={span[:8]}")
    
    al.record_cognitive(thought="test reasoning", model="gemini-2.0-flash", prompt_tokens=100, completion_tokens=50)
    log("  ✓ Cognitive recorded")
    
    al.record_contextual(operation="file_read", source="test.json", byte_count=1024)
    log("  ✓ Contextual recorded")
    
    al.record_operational_complete(method="verify_scan", span_id=span, duration_ms=42.5)
    log("  ✓ Operational complete")
    
    log(f"  Total events: {al.events_written}")
    
    shutdown()
    log("  ✓ Shutdown OK")
except Exception as e:
    log(f"  ✗ Error: {e}")
    import traceback
    log(f"  {traceback.format_exc()}")

# 4. Check trace log file
log("\n[4] Trace log verification:")
trace_path = Path("trace_logs/traces.jsonl")
if trace_path.exists():
    import json
    content = trace_path.read_text()
    events_list = [json.loads(l) for l in content.strip().splitlines() if l.strip()]
    surfaces = {}
    for e in events_list:
        s = e.get("surface", "?")
        surfaces[s] = surfaces.get(s, 0) + 1
    log(f"  ✓ Trace log: {len(events_list)} events")
    for s, c in surfaces.items():
        log(f"    - {s}: {c}")
else:
    log(f"  ✗ Trace log NOT found at {trace_path}")

# 5. Check audit output
log("\n[5] Audit output verification:")
jsonl_path = Path("audit_logs/2026-03-25.jsonl")
if jsonl_path.exists():
    import json
    lines_text = jsonl_path.read_text().splitlines()
    log(f"  ✓ Audit JSONL: {len(lines_text)} lines")
else:
    log(f"  ⚠ Audit JSONL: not found (expected for first run)")

report_path = Path("audit_reports/2026-03-25.md")
if report_path.exists():
    log(f"  ✓ Audit Report: {report_path.stat().st_size} bytes")
else:
    log(f"  ⚠ Audit Report: not found")

log("\n" + "=" * 60)
log("VERIFICATION COMPLETE")
log("=" * 60)

Path("_verify_final_results.txt").write_text("\n".join(lines))
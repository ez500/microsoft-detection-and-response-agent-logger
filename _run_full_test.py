"""Full integration test - writes results to _test_results.txt"""
import sys
import os
from pathlib import Path

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

results = []
def log(msg):
    results.append(str(msg))

# 1. Verify imports (including AgentTrace modules)
try:
    from config import Config
    from events import (AuditEvent, EventType, Severity, make_pipeline_start_event,
                        make_alert_ingestion_event, make_rule_triggered_event,
                        make_narrative_report_event, make_volume_spike_event)
    from scanner import ArtifactScanner
    from narrator import LLMNarrator
    from writer import AuditLogWriter
    from trace_schema import TraceSurface, TraceEvent, TraceLevel, SpanKind
    from trace_schema import make_operational_body, make_cognitive_body, make_contextual_body
    from agent_trace import init as agent_trace_init
    from agent_trace import get_logger, shutdown as agent_trace_shutdown
    log("✓ All imports (incl. AgentTrace) successful")
except Exception as e:
    log(f"✗ Import error: {e}")

# 2. AgentTrace initialization
try:
    agent_trace_init(
        agent_name="TestAgent",
        log_dir=Path("trace_logs"),
        enable_otel=False,
        verbose=True,
    )
    alogger = get_logger()
    log(f"✓ AgentTrace initialized. Logger: {alogger is not None}")
    if alogger:
        log(f"  Agent name: {alogger.agent_name}")

    # Test all three surfaces
    alogger.record_operational_start(method="integration_test", args_summary="full system test")
    alogger.record_cognitive(thought="testing cognitive surface", model="test-model")
    alogger.record_contextual(operation="file_test", source="test.txt", byte_count=100)
    log(f"  All 3 surfaces tested. Events written: {alogger.events_written}")
except Exception as e:
    log(f"✗ AgentTrace init error: {e}")

# 3. Config
config = Config.from_env()
config.agent_root = Path("C:/Users/eugen/Projects/MXDR_analysis_agent")
log(f"✓ Config loaded. Agent root: {config.agent_root}")
log(f"  API key present: {bool(config.gemini_api_key)}")
log(f"  Trace log dir: {config.trace_log_dir}")

# 4. Create directories
os.makedirs('audit_logs', exist_ok=True)
os.makedirs('audit_reports', exist_ok=True)
os.makedirs('snapshots', exist_ok=True)
os.makedirs('trace_logs', exist_ok=True)
log("✓ Directories created")

# 5. Scanner with real artifacts
scanner = ArtifactScanner(config, "2026-03-25")
events = scanner.scan_all()
log(f"✓ Scanner found {len(events)} events")
for e in events:
    log(f"  [{e.severity.value}] {e.event_type.value}: {e.raw_snippet[:80]}")

# 6. Narrator
narrator = LLMNarrator(config)
log(f"✓ Narrator created. LLM available: {narrator.available}")

narrated = narrator.narrate_events(events)
for e in narrated:
    n = e.llm_narrative[:80] if e.llm_narrative else "(empty)"
    log(f"  [{e.event_type.value}] {n}...")

# 7. Report
report = narrator.generate_report(narrated, "2026-03-25")
log(f"✓ Report generated: {len(report)} chars")
log(f"  First 200 chars:\n{report[:200]}")

# 8. Write output
writer = AuditLogWriter(config)
result = writer.write_all(narrated, report, "2026-03-25")
log(f"✓ Output written")
log(f"  JSONL: {result['jsonl']}")
log(f"  Report: {result['report']}")
log(f"  Events: {result['event_count']}")

# 9. Verify files exist
for key in ['jsonl', 'report']:
    p = result[key]
    exists = os.path.isfile(p)
    size = os.path.getsize(p) if exists else 0
    log(f"  {key}: exists={exists}, size={size} bytes")
    if exists:
        with open(p) as fh:
            if key == 'jsonl':
                first = fh.readline().strip()
                log(f"    First line (first 100): {first[:100]}")
            else:
                content = fh.read(200)
                log(f"    Content: {content[:150]}...")

# 10. Verify AgentTrace trace log
trace_log_path = Path("trace_logs/traces.jsonl")
if trace_log_path.exists():
    import json as _json
    trace_events = []
    with open(trace_log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                trace_events.append(_json.loads(line))
    surfaces = {}
    for te in trace_events:
        s = te.get("surface", "?")
        surfaces[s] = surfaces.get(s, 0) + 1
    log(f"✓ Trace log found: {len(trace_events)} events")
    for surface, count in surfaces.items():
        log(f"  - {surface}: {count}")

# 11. List all output files
for d in ['audit_logs', 'audit_reports', 'snapshots', 'trace_logs']:
    files = os.listdir(d)
    log(f"{d}: {len(files)} file(s)")
    for f in sorted(files):
        fp = os.path.join(d, f)
        log(f"  {f} ({os.path.getsize(fp)} bytes)")

# 12. Shutdown AgentTrace
agent_trace_shutdown()
log("✓ AgentTrace shutdown complete")

# Write results
with open('_test_results.txt', 'w') as f:
    f.write('\n'.join(results))
    f.write('\n\nALL TESTS COMPLETE\n')
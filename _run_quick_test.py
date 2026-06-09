"""Run quick test and write results to _quick_test_results.txt"""
import sys
import os
from pathlib import Path

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

lines = []
def log(msg):
    lines.append(str(msg))
    print(msg)

try:
    # Test imports
    from trace_schema import TraceSurface, TraceEvent, TraceLevel, SpanKind
    from trace_schema import make_operational_body, make_cognitive_body, make_contextual_body
    from otel_exporter import OTelExporter
    import agent_trace
    log("✓ All AgentTrace modules imported")
    
    # Initialize
    agent_trace.init(agent_name="TestAgent", log_dir=Path("trace_logs"), enable_otel=False)
    alogger = agent_trace.get_logger()
    log(f"✓ AgentTrace initialized. Logger: {alogger is not None}")
    
    if alogger:
        # Test operational
        span = alogger.record_operational_start(method="scan", args_summary="test")
        log(f"  Operational start: span={span[:8]}...")
        alogger.record_operational_complete(method="scan", span_id=span, duration_ms=42.0)
        log("  Operational complete OK")
        
        # Test cognitive
        alogger.record_cognitive(thought="test reasoning", model="gemini-2.0-flash",
                                  prompt_tokens=100, completion_tokens=50)
        log("  Cognitive OK")
        
        # Test contextual  
        alogger.record_contextual(operation="file_read", source="test.txt", byte_count=1024)
        log("  Contextual OK")
        
        log(f"✓ Total trace events written: {alogger.events_written}")
    
    # Verify trace file
    trace_path = Path("trace_logs/traces.jsonl")
    if trace_path.exists():
        import json
        data = trace_path.read_text().strip()
        event_count = len([l for l in data.splitlines() if l.strip()])
        log(f"✓ Trace log file: {trace_path} ({event_count} events)")
    else:
        log(f"✗ Trace log file NOT FOUND at {trace_path}")
    
    agent_trace.shutdown()
    log("✓ AgentTrace shutdown OK")
    
except Exception as e:
    log(f"✗ ERROR: {e}")
    import traceback
    log(traceback.format_exc())

# Write results
with open("_quick_test_results.txt", "w") as f:
    f.write("\n".join(lines))
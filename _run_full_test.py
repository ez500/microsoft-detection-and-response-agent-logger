"""Full integration test - writes results to _test_results.txt"""
import sys
import os
from pathlib import Path

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

results = []
def log(msg):
    results.append(str(msg))

# 1. Verify imports
try:
    from config import Config
    from events import (AuditEvent, EventType, Severity, make_pipeline_start_event,
                        make_alert_ingestion_event, make_rule_triggered_event,
                        make_narrative_report_event, make_volume_spike_event)
    from scanner import ArtifactScanner
    from narrator import LLMNarrator
    from writer import AuditLogWriter
    log("✓ All imports successful")
except Exception as e:
    log(f"✗ Import error: {e}")

# 2. Config
config = Config.from_env()
config.agent_root = Path("C:/Users/eugen/Projects/MXDR_analysis_agent")
log(f"✓ Config loaded. Agent root: {config.agent_root}")
log(f"  API key present: {bool(config.gemini_api_key)}")

# 3. Create directories
os.makedirs('audit_logs', exist_ok=True)
os.makedirs('audit_reports', exist_ok=True)
os.makedirs('snapshots', exist_ok=True)
log("✓ Directories created")

# 4. Scanner with real artifacts
scanner = ArtifactScanner(config, "2026-03-25")
events = scanner.scan_all()
log(f"✓ Scanner found {len(events)} events")
for e in events:
    log(f"  [{e.severity.value}] {e.event_type.value}: {e.raw_snippet[:80]}")

# 5. Narrator
narrator = LLMNarrator(config)
log(f"✓ Narrator created. LLM available: {narrator.available}")

narrated = narrator.narrate_events(events)
for e in narrated:
    n = e.llm_narrative[:80] if e.llm_narrative else "(empty)"
    log(f"  [{e.event_type.value}] {n}...")

# 6. Report
report = narrator.generate_report(narrated, "2026-03-25")
log(f"✓ Report generated: {len(report)} chars")
log(f"  First 200 chars:\n{report[:200]}")

# 7. Write output
writer = AuditLogWriter(config)
result = writer.write_all(narrated, report, "2026-03-25")
log(f"✓ Output written")
log(f"  JSONL: {result['jsonl']}")
log(f"  Report: {result['report']}")
log(f"  Events: {result['event_count']}")

# 8. Verify files exist
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

# 9. List all output files
for d in ['audit_logs', 'audit_reports', 'snapshots']:
    files = os.listdir(d)
    log(f"{d}: {len(files)} file(s)")
    for f in files:
        fp = os.path.join(d, f)
        log(f"  {f} ({os.path.getsize(fp)} bytes)")

# Write results
with open('_test_results.txt', 'w') as f:
    f.write('\n'.join(results))
    f.write('\n\nALL TESTS COMPLETE\n')
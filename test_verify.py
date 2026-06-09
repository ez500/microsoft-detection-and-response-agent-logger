"""Quick verification script for the MXDR logging system."""
import json
import os
import sys

# Change to the MXDR_logging directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

from pathlib import Path
from config import Config
from events import (
    AuditEvent, EventType, Severity,
    make_alert_ingestion_event,
    make_pipeline_start_event,
    make_rule_triggered_event,
    make_narrative_report_event,
    make_volume_spike_event,
)
from scanner import ArtifactScanner
from narrator import LLMNarrator
from writer import AuditLogWriter

print("=" * 60)
print("MXDR_logging System Verification")
print("=" * 60)

# 1. Config
print("\n[1] Config loading...")
config = Config.from_env()
config.agent_root = Path("C:/Users/eugen/Projects/MXDR_analysis_agent")
print(f"    Agent root: {config.agent_root}")
print(f"    API key present: {bool(config.gemini_api_key)}")
print(f"    Log dir: {config.log_dir}")

# 2. Event creation
print("\n[2] Event creation...")
events = [
    make_pipeline_start_event("2026-03-25"),
    make_alert_ingestion_event(1056, 12, 36, 998, 10, 47, "packet.json"),
    make_rule_triggered_event("CR-003", [{"alert_count": 7, "incidents": [134059]}], "rules.yaml", 0.87),
    make_narrative_report_event("mxdr_analysis/2026-03-25.md", 3500, 
        ["Overall Distribution", "Severity", "Hourly Distribution", "Per-Category Analysis"],
        "report.md"),
    make_volume_spike_event("Discovery", 11, 5.0, 7.5, "stats.jsonl"),
]
print(f"    Created {len(events)} events")
for e in events:
    print(f"      [{e.severity.value}] {e.event_type.value}: {e.raw_snippet[:80]}")

# 3. Scanner with available artifacts
print("\n[3] Scanner (dry run with existing artifacts)...")
scanner = ArtifactScanner(config, "2026-03-25")
scanned = scanner.scan_all()
print(f"    Scanned {len(scanned)} events from artifacts")

# 4. Narrator (template fallback)
print("\n[4] Narrator (template fallback)...")
narrator = LLMNarrator(config)
print(f"    LLM available: {narrator.available}")
narrated = narrator.narrate_events(events)
for e in narrated:
    narrative_preview = e.llm_narrative[:100] if e.llm_narrative else "(empty)"
    print(f"    [{e.event_type.value}] {narrative_preview}...")

# 5. Report generation
print("\n[5] Report generation...")
report = narrator.generate_report(events, "2026-03-25")
print(f"    Report length: {len(report)} chars")
print(f"    First 200 chars:")
print(f"    {report[:200]}")

# 6. Writer output
print("\n[6] Writer output...")
writer = AuditLogWriter(config)
result = writer.write_all(events, report, "2026-03-25")
print(f"    JSONL: {result['jsonl']}")
print(f"    Report: {result['report']}")
print(f"    Events: {result['event_count']}")

# Verify files
for key in ['jsonl', 'report']:
    path = result[key]
    exists = os.path.isfile(path)
    size = os.path.getsize(path) if exists else 0
    print(f"    {key}: exists={exists}, size={size} bytes")

print("\n" + "=" * 60)
print("VERIFICATION COMPLETE")
print("=" * 60)
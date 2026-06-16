# MXDR Logging System

An **LLM-based audit logging system** that observes, parses, and processes every action and insight from the [MXDR analysis agent](https://github.com/eugen-moniree/MXDR_analysis_agent) pipeline. It produces maximally verbose, human-readable audit logs enhanced by various LLM API interpretation.

## Features

- **Automated artifact scanning** — reads all analysis agent output artifacts (analysis packets, narrative reports, correlation rules, watchlists, false negatives, daily stats)
- **17+ event types** — ingestion, correlation rule hits, watchlist alerts, clustering, SOC queue prioritization, anomaly detection, volume spikes, rule confidence changes, and more
- **LLM-enhanced narration** — An LLM transforms structured events into verbose, plain-English audit narratives
- **Graceful fallback** — template-based narratives when the API is unavailable
- **Dual output formats**:
  - `audit_logs/YYYY-MM-DD.jsonl` — machine-parseable structured JSONL log
  - `audit_reports/YYYY-MM-DD.md` — human-readable Markdown audit report
- **Snapshot-based diffing** — detects changes in correlation rules, watchlists, and false negatives between runs
- **Anomaly detection** — volume spike alerts via 7-day rolling average comparison, new alert title detection

## Architecture

```
MXDR_analysis_agent (existing, unmodified)
    │
    ├── data/analysis_packets/YYYY-MM-DD.json    ─┐
    ├── mxdr_analysis/YYYY-MM-DD.md               │
    ├── rules/correlation_rules.yaml               ├──►  ArtifactScanner
    ├── rules/watchlist.yaml                       │       │
    ├── data/daily_stats.jsonl                    ─┘       │
    └── ...other artifacts...                              │
                                                           ▼
                                                    List[AuditEvent]
                                                           │
                                                           ▼
                                                     LLMNarrator (LLM)
                                                           │
                                                     ┌─────┴─────┐
                                                     ▼           ▼
                                               JSONL Log    Markdown Report
                                           (audit_logs/)  (audit_reports/)

                            ┌─────────────────────────────────────────┐
                            │          AgentTrace Framework           │
                            │  (Runtime Instrumentation & Tracing)    │
                            │                                         │
                            │  ┌─────────────────────────────────┐    │
                            │  │   TraceSchema (Unified Envelope) │    │
                            │  │   ├── Operational Surface       │    │
                            │  │   ├── Cognitive Surface         │    │
                            │  │   └── Contextual Surface        │    │
                            │  └─────────────────────────────────┘    │
                            │                                         │
                            │  ┌────────────┐  ┌──────────────────┐   │
                            │  │  ALogger   │  │  OTel Exporter   │   │
                            │  │  (JSONL)   │  │  (OTLP/gRPC)     │   │
                            │  └────────────┘  └──────────────────┘   │
                            │                                         │
                            │  trace_logs/traces.jsonl (dual-path)    │
                            └─────────────────────────────────────────┘
```

## Installation

```bash
# 1. Clone and enter the project
cd MXDR_logging

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure your environment
#    Edit .env and set your LLM API key
#    CLAUDE_API_KEY=your-api-key-here    # or GEMINI_API_KEY=
#    MXDR_AGENT_ROOT=C:\Users\eugen\Projects\MXDR_analysis_agent
```

## Usage

```bash
# Process a specific date (requires LLM API key in .env)
python main.py --date 2026-03-25

# Process yesterday (for cron/automation)
python main.py --yesterday

# Skip LLM narration (use template fallback — no API key needed)
python main.py --date 2026-03-25 --skip-llm

# Dry run — extract events but don't write output files
python main.py --date 2026-03-25 --dry-run

# Override agent root path
python main.py --date 2026-03-25 --agent-root /path/to/MXDR_analysis_agent

# Verbose mode — shows skipped/missing artifact messages
python main.py --date 2026-03-25 --verbose
```

## Event Types

| Category | Events |
|---|---|
| **Ingestion** | `ALERTS_INGESTED` — alerts downloaded from Microsoft Graph Security API |
| **Pre-Analysis** | `ANALYSIS_PACKET_GENERATED` — correlation rules executed, packet written |
| **Correlation Rules** | `RULE_TRIGGERED` / `RULE_NOT_TRIGGERED` — per-rule hit detection |
| **Watchlist** | `WATCHLIST_HIT` — entity matched a watchlist entry |
| **Clustering** | `CLUSTERS_GENERATED` — suspicious alert clusters identified |
| **Prioritization** | `SOC_QUEUE_GENERATED` — priority-sorted SOC action items |
| **Reporting** | `NARRATIVE_REPORT_WRITTEN` — daily analysis report produced |
| **Rule Updates** | `RULE_CONFIDENCE_UPDATED` / `RULE_RETIRED` — confidence scoring |
| **Watchlist Updates** | `WATCHLIST_UPDATED` — additions, modifications, expirations |
| **False Negatives** | `FALSE_NEGATIVE_RECORDED` — new confirmed false negatives |
| **Statistics** | `DAILY_STATS_APPENDED` — daily summary stats appended |
| **Anomaly Detection** | `NEW_ALERT_TITLE_DETECTED` / `VOLUME_SPIKE_DETECTED` |
| **Entity Insights** | `HIGH_RISK_ENTITY_OBSERVED` — entity across multiple high-severity incidents |
| **Pipeline Metadata** | `PIPELINE_RUN_STARTED` / `PIPELINE_RUN_COMPLETED` |

## Output Structure

```
MXDR_logging/
├── audit_logs/                # Structured JSONL event logs
│   └── YYYY-MM-DD.jsonl       # Append-only, one JSON object per line
├── audit_reports/             # Human-readable Markdown audit reports
│   └── YYYY-MM-DD.md          # Full LLM-narrated report with sections
├── snapshots/                 # Cached state for diff-based detection
│   ├── correlation_rules.yaml
│   ├── watchlist.yaml
│   └── false_negatives.yaml
├── main.py                    # CLI entry point
├── config.py                  # Configuration management
├── events.py                  # Event taxonomy (17+ event types)
├── scanner.py                 # ArtifactScanner
├── narrator.py                # LLMNarrator (LLM + fallback)
├── writer.py                  # AuditLogWriter
├── .env                       # Environment configuration
├── requirements.txt           # Python dependencies
└── README.md                  # This file
```

## Sample Audit Report

The generated Markdown report includes:

```markdown
# 🔍 MXDR Audit Log — 2026-03-25

**Pipeline Run**: Completed in 4m 35s
**Total Events**: 24

## Executive Summary
The daily MXDR analysis pipeline processed 1,056 security alerts
across 47 distinct incidents. The correlation engine detected activity
matching 3 correlation rules (CR-003, CR-004, CR-005)...

## Event Timeline

### ℹ️ ALERTS_INGESTED
1,056 alerts were successfully downloaded from the Microsoft Graph
Security API. Severity breakdown: 12 high, 36 medium, 998 low, 10 informational.

### 🟡 RULE_TRIGGERED — CR-003
Correlation rule CR-003 (Single IP → 5+ distinct users within 48h)
matched IP address 203.0.113.45 with 7 distinct user accounts...

## Correlation Rule Activity
| Rule | Status | Hits | Confidence |
|------|--------|------|------------|
| CR-001 | No Hit | 0 | 0.85 |
| CR-003 | TRIGGERED | 1 | 0.87 |

## Statistics Dashboard
- Total alerts: 1,056 | Non-noisy alerts: 48
- Suspicious clusters: 6 | P1 items: 2
```

## AgentTrace Integration

The system includes [AgentTrace](https://arxiv.org/pdf/2602.10133v1), a structured logging framework for agent system observability that provides runtime instrumentation across three surfaces.

### Three-Surface Taxonomy

| Surface | What It Captures | Example |
|---|---|---|
| **Operational** | Method calls, arguments, return values, timing | `scan_all()` start/complete events with duration |
| **Cognitive** | LLM interactions — prompts, completions, thinking segments | LLM prompt/response pairs with token counts |
| **Contextual** | External I/O — file reads/writes, API calls | JSONL write operations, narrative report output |

### How AgentTrace Works

AgentTrace instruments the existing `ArtifactScanner`, `LLMNator`, and `AuditLogWriter` classes at runtime using Python decorators — **no modifications to agent code are required**:

1. `@instrument(agent_name="ClassName")` decorator wraps each public method
2. Each method call emits **start** and **complete/error** operational events with trace/span IDs
3. LLM responses are scanned for cognitive segments: `<thinking>` tags, `<plan>` tags, JSON fields, Chain-of-Thought reasoning
4. File I/O operations are automatically captured as contextual events
5. All events flow to both JSONL (local) and OTel (remote) backends simultaneously

### Enabling AgentTrace

```python
# In main.py (already integrated)
import agent_trace
agent_trace.init(
    agent_name="MXDR_agent",
    log_dir=Path("trace_logs"),
    enable_otel=True,                    # Enable OTel export
    otel_endpoint="http://localhost:4317", # Jaeger/Tempo endpoint
)
```

Or via environment variables:
```env
ENABLE_OTEL=true
OTEL_ENDPOINT=http://localhost:4317
```

### OpenTelemetry Integration

When enabled, AgentTrace exports trace events as OTel spans to any compatible backend (Jaeger, Tempo, Grafana):

```bash
# Start with OTel export
python main.py --date 2026-03-25

# Traces will be available at http://localhost:16686 (Jaeger UI)
```

### Trace Log Output

All trace events are written to `trace_logs/traces.jsonl`:
```json
{
  "event_id": "a1b2c3d4e5f6",
  "surface": "operational",
  "trace_id": "abc123...",
  "span_id": "def456...",
  "agent_name": "MXDR_agent",
  "body": {"method": "scan_all", "status": "complete", "duration_ms": 1250.3},
  "timestamp": "2026-03-25T12:00:00.000Z"
}
```

## Integration with MXDR Analysis Agent

For automated daily use, add this step to `run_daily_analysis.sh` after the analysis agent completes:

```bash
# After step 3 (analysis agent completes)
echo "[4/4] Generating audit log..."
cd /path/to/MXDR_logging
python main.py --yesterday --agent-root /path/to/MXDR_analysis_agent
```

## License

MIT

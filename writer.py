"""
writer.py — AuditLogWriter for the MXDR logging system.

Writes structured audit events to two output formats:
1. JSONL log file (machine-parseable, append-only)
2. Markdown audit report (human-readable, LLM-enhanced)
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from agent_trace import get_logger, instrument
from config import Config
from events import AuditEvent, make_pipeline_complete_event

logger = logging.getLogger(__name__)


@instrument(agent_name="AuditLogWriter")
class AuditLogWriter:
    """
    Writes audit events to JSONL and Markdown output files.

    The JSONL log is append-only and machine-parseable with one JSON object
    per line. The Markdown report is the primary human-readable deliverable
    with full LLM-enhanced narratives.
    """

    def __init__(self, config: Config):
        self.config = config
        config.ensure_output_dirs()

    # ── JSONL output ──────────────────────────────────────────────────────

    def write_jsonl(self, events: List[AuditEvent], date_str: str) -> Path:
        """
        Write events to an append-only JSONL file.

        Returns the path to the written file.
        """
        out_path = self.config.log_dir / f"{date_str}.jsonl"

        # Record contextual event for file I/O
        alogger = get_logger()
        if alogger:
            alogger.record_contextual(
                operation="file_write",
                source=str(out_path),
                query_summary=f"Writing {len(events)} events",
                byte_count=0,
            )

        # Open in append mode so multiple runs can add to the same day
        with open(out_path, "a", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event.to_dict(), default=str) + "\n")

        # Record completion with actual byte count
        if alogger:
            byte_count = out_path.stat().st_size if out_path.exists() else 0
            alogger.record_contextual(
                operation="file_write_complete",
                source=str(out_path),
                response_summary=f"Wrote {len(events)} events ({byte_count} bytes)",
                byte_count=byte_count,
            )

        return out_path

    # ── Markdown report output ────────────────────────────────────────────

    def write_report(
        self,
        report_content: str,
        date_str: str,
    ) -> Path:
        """
        Write the full Markdown audit report.

        Args:
            report_content: The complete Markdown report string (from LLMNarrator).
            date_str: Date string in YYYY-MM-DD format.

        Returns:
            Path to the written report file.
        """
        out_path = self.config.report_dir / f"{date_str}.md"

        # Record contextual event for report write
        alogger = get_logger()
        if alogger:
            alogger.record_contextual(
                operation="file_write",
                source=str(out_path),
                query_summary=f"Writing Markdown report ({len(report_content)} chars)",
                byte_count=len(report_content.encode("utf-8")),
            )

        out_path.write_text(report_content, encoding="utf-8")

        if alogger:
            byte_count = out_path.stat().st_size if out_path.exists() else 0
            alogger.record_contextual(
                operation="file_write_complete",
                source=str(out_path),
                response_summary=f"Report written ({byte_count} bytes)",
                byte_count=byte_count,
            )

        return out_path

    # ── Convenience: write both outputs ───────────────────────────────────

    def write_all(
        self,
        events: List[AuditEvent],
        report_content: str,
        date_str: str,
        start_time: Optional[datetime] = None,
    ) -> dict:
        """
        Write all output formats and append a PIPELINE_RUN_COMPLETED event.

        Args:
            events: List of audit events (will be written to JSONL).
            report_content: The Markdown audit report content.
            date_str: Date string in YYYY-MM-DD format.
            start_time: Optional pipeline start time for duration calculation.

        Returns:
            Dict with paths to written files: {'jsonl': Path, 'report': Path}.
        """
        # Write JSONL log
        jsonl_path = self.write_jsonl(events, date_str)

        # Write Markdown report
        report_path = self.write_report(report_content, date_str)

        # Compute duration if start_time provided
        duration = 0.0
        if start_time:
            duration = (datetime.utcnow() - start_time).total_seconds()

        # Create and write the completion event
        complete_event = make_pipeline_complete_event(
            date_str=date_str,
            duration_seconds=duration,
            event_count=len(events),
            status="completed",
        )

        # Append completion event to JSONL
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(complete_event.to_dict(), default=str) + "\n")

        return {
            "jsonl": str(jsonl_path),
            "report": str(report_path),
            "event_count": len(events),
            "duration_seconds": round(duration, 1),
        }
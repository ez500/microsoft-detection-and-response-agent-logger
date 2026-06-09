"""
writer.py — AuditLogWriter for the MXDR logging system.

Writes structured audit events to two output formats:
1. JSONL log file (machine-parseable, append-only)
2. Markdown audit report (human-readable, LLM-enhanced)
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from config import Config
from events import AuditEvent, make_pipeline_complete_event


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

        # Open in append mode so multiple runs can add to the same day
        with open(out_path, "a", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event.to_dict(), default=str) + "\n")

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
        out_path.write_text(report_content, encoding="utf-8")
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
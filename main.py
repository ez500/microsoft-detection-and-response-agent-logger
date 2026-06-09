"""
main.py — CLI entry point for the MXDR logging system.

Usage:
    python -m MXDR_logging.main --date 2026-03-25
    python -m MXDR_logging.main --yesterday
    python -m MXDR_logging.main --date 2026-03-25 --dry-run
    python -m MXDR_logging.main --date 2026-03-25 --agent-root /path/to/MXDR_analysis_agent

Orchestrates: ArtifactScanner → LLMNarrator → AuditLogWriter
"""

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from config import Config
from narrator import LLMNarrator
from scanner import ArtifactScanner
from writer import AuditLogWriter


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="MXDR LLM-based Audit Logging System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m MXDR_logging.main --date 2026-03-25
  python -m MXDR_logging.main --yesterday
  python -m MXDR_logging.main --date 2026-03-25 --dry-run
  python -m MXDR_logging.main --date 2026-03-25 --agent-root /path/to/agent
        """,
    )

    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument(
        "--date",
        help="Target date in YYYY-MM-DD format",
    )
    date_group.add_argument(
        "--yesterday",
        action="store_true",
        help="Process yesterday's date",
    )

    parser.add_argument(
        "--agent-root",
        help="Path to MXDR_analysis_agent root directory "
             "(overrides .env MXDR_AGENT_ROOT)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract events but skip LLM narration and file writing",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output (show skipped files)",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Skip LLM narration entirely (use template fallback)",
    )
    return parser.parse_args(argv)


def resolve_date(args: argparse.Namespace) -> str:
    """Resolve the target date string from args."""
    if args.yesterday:
        return (date.today() - timedelta(days=1)).isoformat()
    return args.date  # type: ignore


def main(argv: Optional[list] = None) -> int:
    """
    Main entry point.

    Returns:
        0 on success, 1 on failure.
    """
    args = parse_args(argv)
    start_time = datetime.utcnow()

    # ── Load configuration ────────────────────────────────────────────────
    config = Config.from_env()

    # Override agent root if provided on CLI
    if args.agent_root:
        config.agent_root = Path(args.agent_root)

    if args.verbose:
        config.verbose = True

    target_date = resolve_date(args)

    print(f"🔍 MXDR Audit Logger — {target_date}")
    print(f"   Agent root: {config.agent_root}")
    print(f"   Dry run:    {args.dry_run}")
    print(f"   Skip LLM:   {args.skip_llm}")
    print()

    # ── Step 1: Scan artifacts ────────────────────────────────────────────
    print("[1/3] Scanning analysis artifacts...")
    scanner = ArtifactScanner(config, target_date)
    events = scanner.scan_all()
    print(f"       → {len(events)} events extracted")

    if not events:
        print("       ⚠ No events found. Are the artifact paths correct?")
        print(f"       Expected analysis packet at: {config.artifact('packet') / f'{target_date}.json'}")

    # ── Step 2: Narrate events ────────────────────────────────────────────
    print("[2/3] Narrating events...")
    narrator = LLMNarrator(config)

    if args.skip_llm or args.dry_run:
        print(f"       → Using template narration "
              f"({'dry run' if args.dry_run else '--skip-llm set'})")
        for event in events:
            event.llm_narrative = narrator._template_narrative(event)
    else:
        if narrator.available:
            print(f"       → Using Gemini ({config.llm_model})")
        else:
            print(f"       → Gemini unavailable, using template fallback")
        events = narrator.narrate_events(events)

    print(f"       → {len(events)} events narrated")

    # ── Step 3: Generate report and write output ──────────────────────────
    print("[3/3] Generating report and writing output...")

    if args.dry_run:
        print("       → DRY RUN — skipping file output")
        print()
        print("=== Events (dry run) ===")
        for event in events:
            print(f"  [{event.severity.value}] {event.event_type.value}: {event.raw_snippet[:120]}")
        print()
        elapsed = (datetime.utcnow() - start_time).total_seconds()
        print(f"Done. {len(events)} events in {elapsed:.1f}s (dry run, no files written)")
        return 0

    # Generate the Markdown report
    report_content = narrator.generate_report(events, target_date)

    # Write all outputs
    writer = AuditLogWriter(config)
    result = writer.write_all(
        events=events,
        report_content=report_content,
        date_str=target_date,
        start_time=start_time,  # use the actual pipeline start time
    )

    # Override with actual timing
    elapsed = (datetime.utcnow() - start_time).total_seconds()
    result["duration_seconds"] = round(elapsed, 1)

    print()
    print(f"✅ Audit logging complete for {target_date}")
    print(f"   Events logged:  {result['event_count']}")
    print(f"   Duration:       {result['duration_seconds']}s")
    print(f"   JSONL log:      {result['jsonl']}")
    print(f"   Markdown report:{result['report']}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())

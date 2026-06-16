"""
narrator.py — LLMNarrator for the MXDR logging system.

Uses an LLM (Claude or Gemini) to transform structured audit events into
verbose, human-readable narrative audit reports. Falls back to
template-based narration if the API is unavailable.
"""

import json
import logging
import textwrap
from datetime import datetime
from typing import Dict, List, Optional

from agent_trace import get_logger, instrument
from config import Config
from events import AuditEvent, EventType, Severity

logger = logging.getLogger(__name__)


@instrument(agent_name="LLMNarrator", methods=["narrate_events", "generate_report"])
class LLMNarrator:
    """
    Enhances structured audit events with LLM-generated verbose narratives
    and produces a full Markdown audit report.

    Supports multiple LLM backends (Claude, Gemini, etc.).
    Falls back to template-based narration if the API cannot be reached.
    """

    def __init__(self, config: Config):
        self.config = config
        self._model = None
        self._llm_provider: Optional[str] = None  # "gemini" | "claude" | None

        # Initialize whichever LLM is configured
        self._init_llm()

    def _init_llm(self):
        """Initialize the LLM model — prefers Claude if both keys are set."""
        claude_api_key = self.config.claude_api_key
        gemini_api_key = self.config.gemini_api_key

        # Prefer Claude if its key is present
        if claude_api_key:
            try:
                import anthropic
                self._model = anthropic.Anthropic(api_key=claude_api_key)
                self._llm_provider = "claude"
                print(f"[narrator] Claude initialized (model: {self.config.claude_model})")
                return
            except Exception as e:
                print(f"[narrator] WARNING: Failed to initialize Claude: {e}")
                self._model = None
                self._llm_provider = None

        # Fall back to Gemini
        if gemini_api_key:
            try:
                import google.generativeai as genai
                from google.generativeai import GenerationConfig

                genai.configure(api_key=gemini_api_key)

                self._model = genai.GenerativeModel(
                    model_name=self.config.gemini_model,
                    generation_config=GenerationConfig(
                        temperature=self.config.llm_temperature,
                        max_output_tokens=self.config.llm_max_output_tokens,
                    ),
                )
                self._llm_provider = "gemini"
                print(f"[narrator] Gemini initialized (model: {self.config.gemini_model})")
            except Exception as e:
                print(f"[narrator] WARNING: Failed to initialize Gemini: {e}")
                self._model = None
                self._llm_provider = None

    @property
    def available(self) -> bool:
        """Check if the LLM is available for narration."""
        return self._model is not None

    @property
    def active_model_name(self) -> str:
        """Return the name of the model currently in use."""
        if self._llm_provider == "claude":
            return self.config.claude_model
        elif self._llm_provider == "gemini":
            return self.config.gemini_model
        return "template-fallback"

    # Public API

    def narrate_events(self, events: List[AuditEvent]) -> List[AuditEvent]:
        """
        Enhance a list of audit events with LLM-generated narratives.

        If the LLM is available, events are batched into a single prompt
        for efficient processing. Otherwise, template-based fallback is used.
        """
        if self.available and events:
            try:
                return self._llm_narrate(events)
            except Exception as e:
                print(f"[narrator] LLM narration failed: {e}. Using fallback.")

        # Fallback: template-based narration
        for event in events:
            event.llm_narrative = LLMNarrator.template_narrative(event)
        return events

    def generate_report(self, events: List[AuditEvent], date_str: str) -> str:
        """
        Generate a complete Markdown audit report from events.

        If the LLM is available, uses it to produce a rich narrative report.
        Otherwise produces a structured template-based report.
        """
        if self.available and events:
            try:
                return self._llm_generate_report(events, date_str)
            except Exception as e:
                print(f"[narrator] LLM report generation failed: {e}. Using fallback.")

        return self._template_report(events, date_str)

    # LLM narration
    @staticmethod
    def _build_event_descriptions(events: List[AuditEvent]) -> List[str]:
        """Build a list of event description strings for the prompt."""
        descriptions = []
        for i, event in enumerate(events):
            descriptions.append(
                f"[EVENT {i}]\n"
                f"Type: {event.event_type.value}\n"
                f"Severity: {event.severity.value}\n"
                f"Timestamp: {event.timestamp}\n"
                f"Source: {event.source_file}\n"
                f"Details: {json.dumps(event.details, indent=2)}\n"
                f"Raw: {event.raw_snippet}\n"
            )
        return descriptions

    def _build_narration_prompt(self, events: List[AuditEvent]) -> str:
        """Build the event narration prompt shared across providers."""
        event_descriptions = self._build_event_descriptions(events)

        return textwrap.dedent(f"""\
        You are an expert MXDR (Microsoft Extended Detection and Response) audit analyst.
        Your job is to produce verbose, plain-English narratives for security audit events.

        For each event below, write 2-4 sentences explaining:
        - WHAT happened (in plain English, not just the event type name)
        - WHY it matters from a security perspective
        - WHAT the data shows (specific numbers, entities, patterns)
        - Any CONTEXT that helps a human reader understand the significance

        Be specific, thorough, and avoid generic statements. Use the exact numbers
        and entities from the event details.

        Events to narrate:
        ---
        {chr(10).join(event_descriptions)}
        ---

        Respond with exactly {len(events)} narratives, one per line, in order.
        Each line should be prefixed with "NARRATIVE_i:" where i is the event index.
        """)

    @staticmethod
    def _parse_narratives(text: str) -> Dict[int, str]:
        """Parse NARRATIVE_i: lines from the LLM response text."""
        narratives: Dict[int, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("NARRATIVE_"):
                try:
                    prefix, rest = line.split(":", 1)
                    idx = int(prefix.replace("NARRATIVE_", ""))
                    narratives[idx] = rest.strip()
                except (ValueError, IndexError):
                    continue
        return narratives

    def _llm_narrate(self, events: List[AuditEvent]) -> List[AuditEvent]:
        """Use the active LLM to generate verbose narratives for each event."""
        # Record cognitive event for LLM call
        alogger = get_logger()
        if alogger:
            alogger.record_cognitive(
                model=self.active_model_name,
                prompt_summary=f"Narrating {len(events)} events",
                completion_summary="pending",
                temperature=self.config.llm_temperature,
            )

        prompt = self._build_narration_prompt(events)

        # Dispatch by provider
        if self._llm_provider == "claude":
            text = self._claude_complete(prompt)
        else:
            text = self._gemini_complete(prompt)

        # Record cognitive event for LLM completion with cognitive extraction
        if alogger:
            thought_segments = []
            import re
            thinking_matches = re.findall(r"<thinking>(.*?)</thinking>", text, re.DOTALL)
            for segment in thinking_matches:
                thought_segments.append(segment.strip())
            alogger.record_cognitive(
                model=self.active_model_name,
                thought="\n".join(thought_segments) if thought_segments else None,
                completion_summary=text[:200],
                total_tokens=len(text.split()),
                temperature=self.config.llm_temperature,
            )

        # Parse the response
        narratives = self._parse_narratives(text)

        # Apply narratives to events
        for i, event in enumerate(events):
            if i in narratives:
                event.llm_narrative = narratives[i]
            else:
                event.llm_narrative = LLMNarrator.template_narrative(event)

        return events

    def _llm_generate_report(self, events: List[AuditEvent], date_str: str) -> str:
        """Use the active LLM to generate a full Markdown audit report."""
        # Record cognitive event for report generation
        alogger = get_logger()
        if alogger:
            alogger.record_cognitive(
                model=self.active_model_name,
                plan=f"Generate comprehensive audit report for {date_str}",
                prompt_summary=f"Report generation with {len(events)} events",
                temperature=self.config.llm_temperature,
            )

        event_summaries = []
        for event in events:
            event_summaries.append(
                f"- [{event.severity.value}] {event.event_type.value} "
                f"({event.timestamp}): {event.raw_snippet}"
            )

        prompt = textwrap.dedent(f"""\
        You are an expert MXDR audit report writer. Generate a comprehensive,
        highly verbose, human-readable Markdown audit report for the daily
        MXDR analysis pipeline run on {date_str}.

        The report must include these sections:

        1. **Executive Summary** — High-level overview of the day's pipeline run,
           total events, key findings, and overall security posture.

        2. **Pipeline Timeline** — Chronological log of all events with detailed
           narration. Each event entry should include: timestamp, event type,
           severity, and a multi-sentence plain-English explanation of what
           happened and why it matters.

        3. **Correlation Rule Activity** — Summary table of which rules triggered,
           how many hits each had, and what the hits mean.

        4. **Entity Intelligence** — Notable entities (IPs, users, devices, files)
           that appeared across multiple incidents, with context on their activity.

        5. **Anomaly Report** — Any spikes, new alert types, or unusual patterns
           detected in this run.

        6. **Statistics Dashboard** — Key metrics in a clean format: alert counts,
           severity breakdown, cluster counts, SOC queue prioritization.

        Use the following event data as your source material:

        {chr(10).join(event_summaries)}

        Write in a professional, detailed style suitable for SOC managers and
        senior analysts. Maximize verbosity — every event deserves thorough
        explanation. Use Markdown formatting with tables, bullet lists, and
        code blocks for metrics.
        """)

        # Dispatch by provider
        if self._llm_provider == "claude":
            text = self._claude_complete(prompt)
        else:
            text = self._gemini_complete(prompt)

        # Record cognitive event for completion
        if alogger:
            alogger.record_cognitive(
                model=self.active_model_name,
                completion_summary=f"Report generated: {len(text)} chars",
                total_tokens=len(text.split()),
                temperature=self.config.llm_temperature,
                reflection="Report contains executive summary, timeline, correlation activity, "
                           "entity intelligence, anomalies, and statistics",
            )

        return text

    # Provider-specific API calls
    def _gemini_complete(self, prompt: str) -> str:
        """Send a prompt to Gemini and return the response text."""
        response = self._model.generate_content(prompt)
        return response.text

    def _claude_complete(self, prompt: str) -> str:
        """Send a prompt to Claude and return the response text."""
        response = self._model.messages.create(
            model=self.config.claude_model,
            max_tokens=self.config.llm_max_output_tokens,
            temperature=self.config.llm_temperature,
            messages=[
                {"role": "user", "content": prompt}
            ],
        )
        # Extract text from the response content blocks
        text_parts = []
        for block in response.content:
            if hasattr(block, 'text'):
                text_parts.append(block.text)
            elif isinstance(block, dict) and block.get('type') == 'text':
                text_parts.append(block.get('text', ''))
        return "\n".join(text_parts)

    # Template fallback

    @staticmethod
    def template_narrative(event: AuditEvent) -> str:
        """Generate a template-based narrative for a given event."""
        event_type = event.event_type.value if isinstance(event.event_type, EventType) else str(event.event_type)
        severity = event.severity.value if isinstance(event.severity, Severity) else str(event.severity)

        narratives = {
            EventType.ALERTS_INGESTED: (
                f"[{severity}] {event_type}: The analysis pipeline successfully ingested "
                f"{event.details.get('total', '?')} "
                f"security alerts from the Microsoft Graph Security API. "
                f"Severity breakdown: {event.details.get('high', 0)} high, "
                f"{event.details.get('medium', 0)} medium, "
                f"{event.details.get('low', 0)} low, "
                f"{event.details.get('informational', 0)} informational. "
                f"These alerts span {event.details.get('incidents', '?')} distinct incidents."
            ),
            EventType.ANALYSIS_PACKET_GENERATED: (
                f"[{severity}] {event_type}: The pre-analysis engine processed the ingested alerts "
                f"and generated a structured analysis packet. "
                f"Of the 8 correlation rules, "
                f"{len(event.details.get('rules_triggered', {}))} triggered with hits, "
                f"while {len(event.details.get('rules_not_triggered', []))} had no matches. "
                f"The watchlist check identified {event.details.get('watchlist_hit_count', 0)} hits, "
                f"and {event.details.get('notable_alert_count', 0)} non-noisy alerts "
                f"were flagged for deeper analysis."
            ),
            EventType.RULE_TRIGGERED: (
                f"[{severity}] {event_type}: Correlation rule {event.details.get('rule_id', '?')} triggered "
                f"{event.details.get('hit_count', 0)} time(s) during this analysis run. "
                f"This rule detects specific patterns of suspicious activity "
                f"and each hit represents a potential security concern requiring investigation. "
                f"Confidence: {event.details.get('confidence', 'N/A')}."
            ),
            EventType.RULE_NOT_TRIGGERED: (
                f"[{severity}] {event_type}: Correlation rule {event.details.get('rule_id', '?')} was evaluated "
                f"against the day's alerts but did not find any matching patterns. "
                f"This is normal and expected — not every rule activates every day. "
                f"Continued monitoring is required as threat actor behaviors change."
            ),
            EventType.WATCHLIST_HIT: (
                f"[{severity}] {event_type}: A watchlisted entity was detected in active alerts: "
                f"{event.details.get('entity_type', '?')} = "
                f"`{event.details.get('entity_value', '?')}`. "
                f"Reason for watchlisting: {event.details.get('watchlist_reason', 'N/A')}. "
                f"This entity appeared in incident {event.details.get('incident_id', '?')} "
                f"with alert title '{event.details.get('alert_title', '?')}'. "
                f"Watchlist hits warrant elevated attention as these entities "
                f"have been previously identified as suspicious."
            ),
            EventType.CLUSTERS_GENERATED: (
                f"[{severity}] {event_type}: The clustering engine identified {event.details.get('cluster_count', 0)} "
                f"suspicious alert clusters from the day's analysis. "
                f"Priority breakdown: {event.details.get('p1_count', 0)} P1 "
                f"(investigate today), {event.details.get('p2_count', 0)} P2 "
                f"(investigate this week), {event.details.get('p3_count', 0)} P3 (monitor). "
                f"Clusters represent groups of related alerts that may indicate "
                f"coordinated attack campaigns rather than isolated events."
            ),
            EventType.SOC_QUEUE_GENERATED: (
                f"[{severity}] {event_type}: The prioritization engine ranked {event.details.get('total_items', 0)} "
                f"SOC action items by urgency. "
                f"Critical (P1): {event.details.get('p1_count', 0)}, "
                f"High (P2): {event.details.get('p2_count', 0)}, "
                f"Low (P3): {event.details.get('p3_count', 0)}. "
                f"Top items requiring immediate attention: "
                f"{', '.join(event.details.get('top_items', []))[:200]}."
            ),
            EventType.NARRATIVE_REPORT_WRITTEN: (
                f"[{severity}] {event_type}: The LLM-generated narrative analysis report was written to "
                f"`{event.details.get('report_path', '?')}`. "
                f"The report contains {event.details.get('word_count', 0)} words "
                f"across {len(event.details.get('sections', []))} sections: "
                f"{', '.join(event.details.get('sections', []))}. "
                f"This report provides the primary human-readable output "
                f"for SOC analysts to review the day's findings."
            ),
            EventType.RULE_CONFIDENCE_UPDATED: (
                f"[{severity}] {event_type}: Correlation rule {event.details.get('rule_id', '?')} confidence "
                f"was updated from {event.details.get('old_confidence', '?')} to "
                f"{event.details.get('new_confidence', '?')} "
                f"(delta: {event.details.get('delta', 0):+g}). "
                f"This adjustment reflects {event.details.get('hit_count', 0)} total hits "
                f"and the rule's last trigger event on "
                f"{event.details.get('last_triggered', 'N/A')}. "
                f"Confidence scoring helps prioritize which rules to trust."
            ),
            EventType.RULE_RETIRED: (
                f"[{severity}] {event_type}: ALERT: Correlation rule {event.details.get('rule_id', '?')} has been "
                f"RETIRED because its confidence dropped to "
                f"{event.details.get('confidence', 0)} — below the 0.20 threshold. "
                f"This rule will no longer trigger on new alerts until its confidence "
                f"is reset or the rule definition is updated. Retired rules should "
                f"be reviewed to determine if they need re-tuning."
            ),
            EventType.WATCHLIST_UPDATED: (
                f"[{severity}] {event_type}: The watchlist was modified in this analysis run: "
                f"{event.details.get('additions', 0)} new entries were added, "
                f"{event.details.get('modifications', 0)} existing entries were modified, "
                f"and {event.details.get('expirations', 0)} entries expired. "
                f"Watchlist changes reflect the agent's adaptive learning "
                f"about which entities are associated with malicious activity."
            ),
            EventType.FALSE_NEGATIVE_RECORDED: (
                f"[{severity}] {event_type}: A new confirmed false negative was recorded: "
                f"`{json.dumps(event.details)[:200]}`. "
                f"False negatives represent incidents or alerts that the "
                f"correlation engine failed to flag but were later identified "
                f"as security-relevant. These entries help improve rule accuracy."
            ),
            EventType.DAILY_STATS_APPENDED: (
                f"[{severity}] {event_type}: Daily summary statistics were appended to the historical record. "
                f"Stats: {json.dumps(event.details)[:200]}. "
                f"This data feeds trend analysis and anomaly detection for "
                f"identifying shifts in the security alert landscape."
            ),
            EventType.VOLUME_SPIKE_DETECTED: (
                f"[{severity}] {event_type}: ANOMALY DETECTED: Alert volume for category "
                f"'{event.details.get('category', '?')}' spiked to "
                f"{event.details.get('current_count', 0)} alerts, "
                f"far exceeding the 7-day rolling average of "
                f"{event.details.get('rolling_avg_7d', 0)} "
                f"(threshold: {event.details.get('threshold', 0)}, "
                f"deviation: {event.details.get('deviation_pct', 0)}%). "
                f"This significant deviation warrants investigation to determine "
                f"whether it represents a real attack surge or a benign event."
            ),
            EventType.NEW_ALERT_TITLE_DETECTED: (
                f"[{severity}] {event_type}: A new alert title was detected that has not appeared "
                f"in the prior 7 days: '{event.details.get('title', '?')}'. "
                f"New alert types may indicate changes in the threat landscape, "
                f"new detection rules deployed by Microsoft, or novel attack "
                f"techniques being used against the organization."
            ),
            EventType.HIGH_RISK_ENTITY_OBSERVED: (
                f"[{severity}] {event_type}: High-risk entity observed: "
                f"{event.details.get('entity_type', '?')} = "
                f"`{event.details.get('entity_value', '?')}` "
                f"appeared in {event.details.get('incident_count', 0)} "
                f"{event.details.get('severity', '?')}-severity incidents. "
                f"Entities that appear across multiple high-severity incidents "
                f"are prime candidates for watchlisting and closer monitoring."
            ),
            EventType.PIPELINE_RUN_STARTED: (
                f"[{severity}] {event_type}: The MXDR daily analysis pipeline began processing "
                f"for date {event.details.get('date', '?')}. "
                f"This marks the start of the automated security analysis "
                f"workflow including alert ingestion, correlation, "
                f"clustering, prioritization, and reporting."
            ),
            EventType.PIPELINE_RUN_COMPLETED: (
                f"[{severity}] {event_type}: The MXDR daily analysis pipeline completed successfully "
                f"for {event.details.get('date', '?')} in "
                f"{event.details.get('duration_seconds', 0):.1f} seconds, "
                f"producing {event.details.get('event_count', 0)} audit events. "
                f"Status: {event.details.get('status', 'completed')}. "
                f"All artifacts have been scanned and logged."
            ),
        }

        template = narratives.get(
            event.event_type if isinstance(event.event_type, EventType) else None,
            LLMNarrator._generic_narrative(event),
        )
        return template

    @staticmethod
    def _generic_narrative(event: AuditEvent) -> str:
        """Fallback narrative for event types without a specific template."""
        return (
            f"Event '{event.event_type.value if isinstance(event.event_type, EventType) else event.event_type}' "
            f"occurred with {event.severity.value if isinstance(event.severity, Severity) else event.severity} "
            f"severity. "
            f"Details: {json.dumps(event.details)[:300]}. "
            f"This event was recorded from source: {event.source_file}."
        )

    # Template report

    @staticmethod
    def _template_report(events: List[AuditEvent], date_str: str) -> str:
        """Generate a structured template-based Markdown report without LLM."""
        lines = [
            f"# 🔍 MXDR Audit Log — {date_str}",
            "",
            f"**Generated At**: {datetime.now().isoformat()}Z",
            f"**Total Events**: {len(events)}",
            "",
            "---",
            "",
            "## Executive Summary",
            "",
            f"The MXDR daily analysis pipeline for {date_str} processed "
            f"a total of **{len(events)}** audit events across all stages. "
            "Below is the complete chronological event record.",
            "",
            "## Event Timeline",
            "",
        ]

        # Group events by type for summary stats
        from collections import Counter
        type_counts = Counter(e.event_type.value for e in events)
        severity_counts = Counter(e.severity.value for e in events)

        lines.append("### Event Type Summary")
        lines.append("")
        lines.append("| Event Type | Count |")
        lines.append("|------------|-------|")
        for etype, count in type_counts.most_common():
            lines.append(f"| {etype} | {count} |")
        lines.append("")
        lines.append("### Severity Breakdown")
        lines.append("")
        lines.append("| Severity | Count |")
        lines.append("|----------|-------|")
        for sev in ("CRITICAL", "WARNING", "INFO"):
            lines.append(f"| {sev} | {severity_counts.get(sev, 0)} |")
        lines.append("")

        # Detailed event log
        lines.append("---")
        lines.append("## Detailed Event Log")
        lines.append("")

        for i, event in enumerate(events, 1):
            event_type = event.event_type.value if isinstance(event.event_type, EventType) else str(event.event_type)
            severity = event.severity.value if isinstance(event.severity, Severity) else str(event.severity)
            severity_icon = {"CRITICAL": "🔴", "WARNING": "🟡", "INFO": "ℹ️"}.get(severity, "•")

            lines.append(f"### {i}. {severity_icon} {event_type}")
            lines.append("")
            lines.append(f"| Field | Value |")
            lines.append(f"|-------|-------|")
            lines.append(f"| **Event ID** | `{event.event_id}` |")
            lines.append(f"| **Timestamp** | {event.timestamp} |")
            lines.append(f"| **Severity** | {severity} |")
            lines.append(f"| **Source** | `{event.source_file}` |")
            lines.append("")
            lines.append(f"**Narrative**:")
            lines.append("")
            narrative = event.llm_narrative or LLMNarrator.template_narrative(event)
            lines.append(narrative)
            lines.append("")
            lines.append(f"**Raw Snippet**:")
            lines.append("")
            lines.append(f"> {event.raw_snippet}")
            lines.append("")
            lines.append(f"**Details**:")
            lines.append("")
            lines.append(f"```json")
            lines.append(json.dumps(event.details, indent=2))
            lines.append(f"```")
            lines.append("")

        return "\n".join(lines)

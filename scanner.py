"""
scanner.py — ArtifactScanner for the MXDR logging system.

Reads and parses the MXDR analysis agent's output artifacts for a given date,
producing structured AuditEvent objects. Each artifact type (analysis packet,
narrative report, correlation rules, etc.) has its own scanning logic.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import yaml

from agent_trace import get_logger, instrument
from config import Config
from events import (
    AuditEvent,
    make_alert_ingestion_event,
    make_packet_generated_event,
    make_rule_triggered_event,
    make_rule_not_triggered_event,
    make_watchlist_hit_event,
    make_clusters_generated_event,
    make_soc_queue_generated_event,
    make_narrative_report_event,
    make_rule_confidence_updated_event,
    make_rule_retired_event,
    make_watchlist_updated_event,
    make_false_negative_event,
    make_daily_stats_event,
    make_new_title_event,
    make_volume_spike_event,
    make_high_risk_entity_event,
    make_pipeline_start_event,
)

logger = logging.getLogger(__name__)


@instrument(agent_name="ArtifactScanner")
class ArtifactScanner:
    """
    Scans MXDR analysis agent output artifacts for a given date and produces
    structured AuditEvent objects.

    Gracefully handles missing files — returns empty lists for artifacts that
    don't exist rather than raising errors.
    """

    def __init__(self, config: Config, target_date: str):
        """
        Args:
            config: System configuration with agent root and output paths.
            target_date: Date string in YYYY-MM-DD format.
        """
        self.config = config
        self.target_date = target_date
        self.events: List[AuditEvent] = []

        # Snapshot path for diff-based change detection
        self._snapshot_dir = config.snapshot_dir

    # Main entry point

    def scan_all(self) -> List[AuditEvent]:
        """Scan all available artifacts and return a sorted event list."""
        self.events = []
        self.events.append(make_pipeline_start_event(self.target_date))

        self._scan_analysis_packet()
        self._scan_narrative_report()
        self._scan_clusters()
        self._scan_soc_queue()
        self._scan_correlation_rules()
        self._scan_watchlist()
        self._scan_false_negatives()
        self._scan_daily_stats()

        # Sort by timestamp
        self.events.sort(key=lambda e: e.timestamp)

        # Record contextual span for scan completion
        alogger = get_logger()
        if alogger:
            alogger.record_contextual(
                operation="scan_complete",
                source=f"ArtifactScanner/{self.target_date}",
                response_summary=f"{len(self.events)} events scanned",
                byte_count=0,
            )

        return self.events

    # Analysis packet

    def _scan_analysis_packet(self):
        """Parse the analysis packet JSON and generate ingestion + rule events."""
        packet_path: Path = (
            self.config.artifact("packet") / f"{self.target_date}.json"
        )
        if not packet_path.exists():
            if self.config.verbose:
                print(f"[scanner] SKIP: packet not found at {packet_path}")
            return

        with open(packet_path) as f:
            packet = json.load(f)

        source = str(packet_path)

        # 1. Ingestion event (from stats)
        stats = packet.get("stats", {})
        self.events.append(make_alert_ingestion_event(
            total=stats.get("total", 0),
            high=stats.get("high", 0),
            medium=stats.get("medium", 0),
            low=stats.get("low", 0),
            info=stats.get("informational", 0),
            incidents=stats.get("incidents", 0),
            source=source,
        ))

        # 2. Packet generated event
        rule_hits = packet.get("rule_hits", {})
        watchlist_hits = packet.get("watchlist_hits", [])
        notable_alerts = packet.get("notable_alerts", [])
        self.events.append(make_packet_generated_event(
            date_str=self.target_date,
            stats=stats,
            rule_hits=rule_hits,
            watchlist_hits=len(watchlist_hits),
            notable_count=len(notable_alerts),
            source=source,
        ))

        # 3. Per-rule triggered/not-triggered events
        for rule_id, hits in rule_hits.items():
            if hits:
                self.events.append(make_rule_triggered_event(rule_id, hits, source=source))
            else:
                self.events.append(make_rule_not_triggered_event(rule_id, source=source))

        # 4. Per-watchlist-hit events
        for wh in watchlist_hits:
            self.events.append(make_watchlist_hit_event(
                entity_type=wh.get("matched_entity_type", "unknown"),
                entity_value=wh.get("matched_entity_value", "?"),
                reason=wh.get("watchlist_reason", ""),
                incident_id=str(wh.get("incident_id", "")),
                title=wh.get("title", ""),
                source=source,
            ))

        # 5. High-risk entity detection
        entity_incident_map: Dict[str, Dict[str, Any]] = {}
        for alert in notable_alerts:
            for entity in alert.get("entities", []):
                key = f"{entity.get('type', '?')}:{entity.get('value', '?')}"
                if key not in entity_incident_map:
                    entity_incident_map[key] = {
                        "type": entity.get("type", "?"),
                        "value": entity.get("value", "?"),
                        "incidents": set(),
                        "severity": alert.get("severity", "low"),
                    }
                entity_incident_map[key]["incidents"].add(str(alert.get("incident_id", "")))

        for key, info in entity_incident_map.items():
            incident_count = len(info["incidents"])
            if incident_count >= 2 and info["severity"] in ("high", "medium"):
                self.events.append(make_high_risk_entity_event(
                    entity_type=info["type"],
                    entity_value=info["value"],
                    incident_count=incident_count,
                    severity=info["severity"],
                    source=source,
                ))

    # Narrative report

    def _scan_narrative_report(self):
        """Parse the markdown narrative report and extract section info."""
        report_path: Path = (
            self.config.artifact("narrative") / f"{self.target_date}.md"
        )
        if not report_path.exists():
            if self.config.verbose:
                print(f"[scanner] SKIP: narrative report not found at {report_path}")
            return

        content = report_path.read_text(encoding="utf-8")
        source = str(report_path)

        # Extract section headers (## headings)
        sections: List[str] = []
        for line in content.splitlines():
            line_stripped = line.strip()
            if line_stripped.startswith("## ") and not line_stripped.startswith("###"):
                sections.append(line_stripped.lstrip("## ").strip())

        word_count = len(content.split())

        self.events.append(make_narrative_report_event(
            report_path=source,
            word_count=word_count,
            sections=sections,
            source=source,
        ))

        # New title detection: any line with **bold** alert titles or "New finding"
        new_finding_prefixes = ["**new finding**", "**new alert", "*new"]
        for line in content.splitlines():
            lower = line.lower().strip()
            if any(lower.startswith(p) for p in new_finding_prefixes):
                # Extract the title from context
                # Lines like "New finding — XYZ detected" or "**New alert title**: ..."
                title_text = line.strip().lstrip("*").strip()
                if "—" in title_text:
                    title_text = title_text.split("—", 1)[1].strip()
                self.events.append(make_new_title_event(
                    title=title_text,
                    source=source,
                ))

    # Clusters

    def _scan_clusters(self):
        """Parse clusters YAML file."""
        cluster_path: Path = (
            self.config.artifact("clusters") / f"{self.target_date}.yaml"
        )
        if not cluster_path.exists():
            if self.config.verbose:
                print(f"[scanner] SKIP: clusters not found at {cluster_path}")
            return

        with open(cluster_path) as f:
            data = yaml.safe_load(f)

        source = str(cluster_path)
        clusters = data if isinstance(data, list) else data.get("clusters", [data])

        self.events.append(make_clusters_generated_event(clusters, source=source))

    # SOC queue

    def _scan_soc_queue(self):
        """Parse SOC queue YAML file."""
        queue_path: Path = (
            self.config.artifact("soc_queue") / f"{self.target_date}.yaml"
        )
        if not queue_path.exists():
            if self.config.verbose:
                print(f"[scanner] SKIP: SOC queue not found at {queue_path}")
            return

        with open(queue_path) as f:
            data = yaml.safe_load(f)

        source = str(queue_path)
        items = data if isinstance(data, list) else data.get("items", [data])

        self.events.append(make_soc_queue_generated_event(items, source=source))

    # Correlation rules

    def _scan_correlation_rules(self):
        """
        Parse correlation_rules.yaml and compare with a cached snapshot to
        detect confidence changes and retirements.
        """
        rules_path: Path = self.config.artifact("correlation_rules")
        if not rules_path.exists():
            if self.config.verbose:
                print(f"[scanner] SKIP: correlation rules not found at {rules_path}")
            return

        with open(rules_path) as f:
            rules_data = yaml.safe_load(f)

        source = str(rules_path)
        rules = rules_data if isinstance(rules_data, dict) else {"rules": rules_data}
        current_rules = rules.get("rules", [rules])

        # Load snapshot
        snapshot_path = self._snapshot_dir / "correlation_rules.yaml"
        previous_rules: List[Dict] = self._load_snapshot(snapshot_path) or []

        # Build lookup by rule_id
        current_map: Dict[str, Dict] = {}
        for r in current_rules:
            rid = r.get("id", "")
            if rid:
                current_map[rid] = r

        previous_map: Dict[str, Dict] = {}
        for r in previous_rules:
            rid = r.get("id", "")
            if rid:
                previous_map[rid] = r

        # Detect changes
        for rid, current in current_map.items():
            prev = previous_map.get(rid)
            if prev is None:
                # New rule, no history to compare
                continue

            old_conf = cast(float, prev.get("confidence", 0.5))
            new_conf = cast(float, current.get("confidence", 0.5))

            if abs(new_conf - old_conf) > 0.001:
                self.events.append(make_rule_confidence_updated_event(
                    rule_id=rid,
                    old_confidence=old_conf,
                    new_confidence=new_conf,
                    hit_count=cast(int, current.get("hit_count", 0)),
                    last_triggered=cast(Optional[str], current.get("last_triggered")),
                    source=source,
                ))

            if new_conf < 0.20 <= old_conf:
                self.events.append(make_rule_retired_event(
                    rule_id=rid,
                    confidence=new_conf,
                    source=source,
                ))

        # Save current state as snapshot for next run
        self._save_snapshot(snapshot_path, current_rules)

    # Watchlist

    def _scan_watchlist(self):
        """
        Parse watchlist.yaml and compare with cached snapshot to detect
        additions, modifications, and expirations.
        """
        wl_path: Path = self.config.artifact("watchlist")
        if not wl_path.exists():
            if self.config.verbose:
                print(f"[scanner] SKIP: watchlist not found at {wl_path}")
            return

        with open(wl_path) as f:
            wl_data = yaml.safe_load(f)

        source = str(wl_path)
        current_entries = wl_data.get("watchlist", [])

        snapshot_path = self._snapshot_dir / "watchlist.yaml"
        previous_entries: List[Dict] = self._load_snapshot(snapshot_path) or []

        # Build lookup keys
        def _entry_key(e: Dict) -> str:
            return f"{e.get('type', '')}:{e.get('value', '')}"

        current_keys = {_entry_key(e): e for e in current_entries}
        previous_keys = {_entry_key(e): e for e in previous_entries}

        additions = []
        modifications = []
        expirations = []

        for key, entry in current_keys.items():
            if key not in previous_keys:
                additions.append(entry)
            else:
                prev = previous_keys[key]
                if entry.get("status") == "expired" and prev.get("status") != "expired":
                    expirations.append(entry)
                elif entry != prev:
                    modifications.append({
                        "key": key,
                        "old": prev,
                        "new": entry,
                    })

        if additions or modifications or expirations:
            self.events.append(make_watchlist_updated_event(
                additions=additions,
                modifications=modifications,
                expirations=expirations,
                source=source,
            ))

        # Save snapshot
        self._save_snapshot(snapshot_path, current_entries)

    # False negatives──

    def _scan_false_negatives(self):
        """
        Parse false_negatives.yaml and compare with cached snapshot to
        detect new entries.
        """
        fn_path: Path = self.config.artifact("false_negs")
        if not fn_path.exists():
            if self.config.verbose:
                print(f"[scanner] SKIP: false negatives not found at {fn_path}")
            return

        with open(fn_path) as f:
            fn_data = yaml.safe_load(f)

        source = str(fn_path)
        current_entries = fn_data if isinstance(fn_data, list) else fn_data.get("false_negatives", [])

        snapshot_path = self._snapshot_dir / "false_negatives.yaml"
        previous_entries: List[Dict] = self._load_snapshot(snapshot_path) or []

        previous_count = len(previous_entries)
        for i, entry in enumerate(current_entries):
            if i >= previous_count:
                self.events.append(make_false_negative_event(entry, source=source))

        # Save snapshot
        self._save_snapshot(snapshot_path, current_entries)

    # Daily stats──────

    def _scan_daily_stats(self):
        """
        Parse daily_stats.jsonl for the latest line and detect volume spikes
        by comparing with historical entries.
        """
        stats_path: Path = self.config.artifact("daily_stats")
        if not stats_path.exists():
            if self.config.verbose:
                print(f"[scanner] SKIP: daily stats not found at {stats_path}")
            return

        lines = stats_path.read_text().strip().splitlines()
        if not lines:
            return

        source = str(stats_path)

        # Parse all historical stats
        parsed_stats: List[Dict] = []
        for line in lines:
            try:
                parsed_stats.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if not parsed_stats:
            return

        # Latest entry
        latest = parsed_stats[-1]
        self.events.append(make_daily_stats_event(latest, source=source))

        # Volume spike detection: compare category counts vs 7-day rolling avg
        if len(parsed_stats) > 1:
            historical = parsed_stats[:-1]  # all but latest
            recent_7 = historical[-7:] if len(historical) >= 7 else historical

            # Compute rolling averages per category
            category_counts: Dict[str, List[int]] = {}
            for entry in recent_7:
                cats = entry.get("categories", entry.get("category_counts", {}))
                if isinstance(cats, dict):
                    for cat, count in cats.items():
                        if cat not in category_counts:
                            category_counts[cat] = []
                        category_counts[cat].append(count)

            # Check latest against averages
            latest_cats = latest.get("categories", latest.get("category_counts", {}))
            if isinstance(latest_cats, dict):
                for cat, current_count in latest_cats.items():
                    if isinstance(current_count, (int, float)) and cat in category_counts:
                        history = category_counts[cat]
                        avg_count = sum(history) / len(history)
                        threshold = avg_count * 1.5  # 50% above avg = spike

                        if current_count > threshold and avg_count > 1:
                            self.events.append(make_volume_spike_event(
                                category=cat,
                                current_count=int(current_count),
                                avg_count=avg_count,
                                threshold=threshold,
                                source=source,
                            ))

            # New title detection: compare categories with historical
            latest_titles = set(latest.get("top_titles", latest.get("titles", [])))
            historical_titles: set = set()
            for entry in historical:
                titles = entry.get("top_titles", entry.get("titles", []))
                if isinstance(titles, list):
                    historical_titles.update(titles)
            if isinstance(latest_titles, set):
                for title in latest_titles:
                    if title not in historical_titles:
                        self.events.append(make_new_title_event(
                            title=str(title),
                            source=source,
                        ))

    # Snapshot helpers
    @staticmethod
    def _load_snapshot(path: Path) -> Optional[List[Dict]]:
        """Load a cached snapshot from disk."""
        if not path.exists():
            return None
        with open(path) as f:
            data = yaml.safe_load(f)
        if isinstance(data, list):
            return data
        return None

    @staticmethod
    def _save_snapshot(path: Path, data: List[Dict]):
        """Save a snapshot to disk for future diff comparison."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False)

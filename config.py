"""
config.py — Configuration management for the MXDR logging system.

Loads environment variables from .env, sets default paths for agent artifacts
and log outputs, and provides a centralized Config dataclass.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


@dataclass
class Config:
    """Central configuration for the MXDR logging system."""

    # ── LLM settings ──────────────────────────────────────────────────────
    gemini_api_key: str = ""
    llm_model: str = "gemini-2.0-flash"
    llm_temperature: float = 0.7
    llm_max_output_tokens: int = 8192

    # ── Agent root path ────────────────────────────────────────────────────
    agent_root: Path = Path.home() / "Projects" / "MXDR_analysis_agent"

    # ── Log output paths (relative to MXDR_logging/) ──────────────────────
    log_dir: Path = Path("audit_logs")
    report_dir: Path = Path("audit_reports")
    snapshot_dir: Path = Path("snapshots")

    # ── AgentTrace / Telemetry ────────────────────────────────────────────
    enable_otel: bool = False
    otel_endpoint: str = "http://localhost:4317"
    trace_log_dir: Path = Path("trace_logs")
    trace_agent_name: str = "MXDR_agent"

    # ── Verbosity ──────────────────────────────────────────────────────────
    verbose: bool = False

    # ── Derived artifact paths (computed) ──────────────────────────────────
    _artifact_paths: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self._artifact_paths = {
            "packet":        self.agent_root / "data" / "analysis_packets",
            "clusters":      self.agent_root / "data" / "clusters",
            "soc_queue":     self.agent_root / "data" / "soc_queue",
            "false_negs":    self.agent_root / "data" / "false_negatives.yaml",
            "daily_stats":   self.agent_root / "data" / "daily_stats.jsonl",
            "narrative":     self.agent_root / "mxdr_analysis",
            "correlation_rules": self.agent_root / "rules" / "correlation_rules.yaml",
            "watchlist":     self.agent_root / "rules" / "watchlist.yaml",
        }

    def artifact(self, name: str) -> Path:
        """Get a resolved artifact path by key name."""
        return self._artifact_paths.get(name, Path(""))

    @classmethod
    def from_env(cls, env_path: Optional[Path] = None) -> "Config":
        """Load configuration from .env file and environment variables."""
        load_dotenv(dotenv_path=env_path or Path(".env"))

        agent_root_str = os.getenv(
            "MXDR_AGENT_ROOT",
            str(Path.home() / "Projects" / "MXDR_analysis_agent"),
        )

        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", "gemini-2.0-flash"),
            agent_root=Path(agent_root_str),
            enable_otel=os.getenv("ENABLE_OTEL", "").lower() in ("1", "true", "yes"),
            otel_endpoint=os.getenv("OTEL_ENDPOINT", "http://localhost:4317"),
            verbose=os.getenv("VERBOSE", "").lower() in ("1", "true", "yes"),
        )

    def ensure_output_dirs(self):
        """Create all output directories if they don't exist."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.trace_log_dir.mkdir(parents=True, exist_ok=True)

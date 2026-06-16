"""
trace_schema.py — AgentTrace Trace Envelope Schema

Implements the unified envelope schema defined in AgentTrace:
  L(S : E : C) → R

Where:
  S = surface (OPERATIONAL, COGNITIVE, or CONTEXTUAL)
  E = event content
  C = metadata context
  R = structured record satisfying consistency, causality, fidelity, interoperability

Each log event includes:
  • UUID identifier
  • surface type
  • trace ID and span ID
  • parent span ID for nesting
  • precise UTC timestamp
  • agent name
  • event body (structured per surface)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class TraceSurface(str, Enum):
    """The three observability surfaces defined in AgentTrace."""

    OPERATIONAL = "operational"
    COGNITIVE = "cognitive"
    CONTEXTUAL = "contextual"


class TraceLevel(str, Enum):
    """Severity/level for trace events."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class SpanKind(str, Enum):
    """Kind of span, analogous to OpenTelemetry SpanKind."""

    INTERNAL = "INTERNAL"
    CLIENT = "CLIENT"
    SERVER = "SERVER"
    PRODUCER = "PRODUCER"
    CONSUMER = "CONSUMER"


@dataclass
class TraceEvent:
    """
    Unified trace event envelope conforming to the AgentTrace schema.

    Every event across all three surfaces shares this common structure.
    """

    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    surface: TraceSurface = TraceSurface.OPERATIONAL
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    agent_name: str = ""
    level: TraceLevel = TraceLevel.INFO
    span_kind: SpanKind = SpanKind.INTERNAL
    body: Dict[str, Any] = field(default_factory=dict)
    duration_ms: Optional[float] = None
    error: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict for JSON / OTel export."""
        return {
            "event_id": self.event_id,
            "surface": self.surface.value,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "timestamp": self.timestamp,
            "agent_name": self.agent_name,
            "level": self.level.value,
            "span_kind": self.span_kind.value,
            "body": self.body,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TraceEvent":
        """Deserialize from a plain dict."""
        return cls(
            event_id=d.get("event_id", uuid.uuid4().hex),
            surface=TraceSurface(d.get("surface", "operational")),
            trace_id=d.get("trace_id", ""),
            span_id=d.get("span_id", ""),
            parent_span_id=d.get("parent_span_id"),
            timestamp=d.get("timestamp", ""),
            agent_name=d.get("agent_name", ""),
            level=TraceLevel(d.get("level", "INFO")),
            span_kind=SpanKind(d.get("span_kind", "INTERNAL")),
            body=d.get("body", {}),
            duration_ms=d.get("duration_ms"),
            error=d.get("error"),
            tags=d.get("tags", {}),
        )

    @property
    def is_error(self) -> bool:
        """Check if this event represents an error."""
        return self.level == TraceLevel.ERROR or self.error is not None


# Operational surface body builders


def make_operational_body(
    method: str,
    status: str,
    args_summary: Optional[str] = None,
    result_summary: Optional[str] = None,
    duration_ms: Optional[float] = None,
    **extra,
) -> Dict[str, Any]:
    """Build an operational surface event body."""
    body: Dict[str, Any] = {
        "method": method,
        "status": status,
    }
    if args_summary is not None:
        body["args_summary"] = args_summary
    if result_summary is not None:
        body["result_summary"] = result_summary
    if duration_ms is not None:
        body["duration_ms"] = duration_ms
    body.update(extra)
    return body


# Cognitive surface body builders


def make_cognitive_body(
    thought: Optional[str] = None,
    plan: Optional[str] = None,
    reflection: Optional[str] = None,
    model: Optional[str] = None,
    prompt_summary: Optional[str] = None,
    completion_summary: Optional[str] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    **extra,
) -> Dict[str, Any]:
    """Build a cognitive surface event body."""
    body: Dict[str, Any] = {}
    if thought is not None:
        body["thought"] = thought
    if plan is not None:
        body["plan"] = plan
    if reflection is not None:
        body["reflection"] = reflection
    if model is not None:
        body["model"] = model
    if prompt_summary is not None:
        body["prompt_summary"] = prompt_summary
    if completion_summary is not None:
        body["completion_summary"] = completion_summary
    if prompt_tokens is not None:
        body["prompt_tokens"] = prompt_tokens
    if completion_tokens is not None:
        body["completion_tokens"] = completion_tokens
    if total_tokens is not None:
        body["total_tokens"] = total_tokens
    if temperature is not None:
        body["temperature"] = temperature
    body.update(extra)
    return body


# Contextual surface body builders
def make_contextual_body(
    operation: str,
    source: str,
    query_summary: Optional[str] = None,
    response_summary: Optional[str] = None,
    byte_count: Optional[int] = None,
    row_count: Optional[int] = None,
    status_code: Optional[int] = None,
    **extra,
) -> Dict[str, Any]:
    """Build a contextual surface event body (I/O operations)."""
    body: Dict[str, Any] = {
        "operation": operation,
        "source": source,
    }
    if query_summary is not None:
        body["query_summary"] = query_summary
    if response_summary is not None:
        body["response_summary"] = response_summary
    if byte_count is not None:
        body["byte_count"] = byte_count
    if row_count is not None:
        body["row_count"] = row_count
    if status_code is not None:
        body["status_code"] = status_code
    body.update(extra)
    return body

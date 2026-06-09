"""
otel_exporter.py — OpenTelemetry Export Bridge for AgentTrace

Converts TraceEvent objects to OpenTelemetry spans and exports them
via OTel batch span processor. Gracefully degrades to local JSONL
when OTel is unavailable or export fails.

Design follows AgentTrace §Implementation: OpenTelemetry Export and
Auto-Instrumentation, with defensive attribute population and fallback.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from trace_schema import TraceEvent, TraceSurface, SpanKind

logger = logging.getLogger(__name__)

# ── Lazy OTel imports (optional dependency) ──────────────────────────────────

_OTEL_AVAILABLE = False
try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.trace import SpanKind as OTelSpanKind
    from opentelemetry.trace.status import Status, StatusCode

    _OTEL_AVAILABLE = True
except ImportError:
    otel_trace = None  # type: ignore
    TracerProvider = None  # type: ignore
    BatchSpanProcessor = None  # type: ignore
    SimpleSpanProcessor = None  # type: ignore
    OTLPSpanExporter = None  # type: ignore
    OTelSpanKind = None  # type: ignore
    Status = None  # type: ignore
    StatusCode = None  # type: ignore


# ── Surface → OTel span name mapping ────────────────────────────────────────


def _span_name_from_event(event: TraceEvent) -> str:
    """Derive a human-readable span name from a trace event."""
    if event.surface == TraceSurface.OPERATIONAL:
        method = event.body.get("method", "unknown")
        status = event.body.get("status", "")
        return f"{event.agent_name}.{method}.{status}" if status else f"{event.agent_name}.{method}"
    elif event.surface == TraceSurface.COGNITIVE:
        return f"{event.agent_name}.llm"
    elif event.surface == TraceSurface.CONTEXTUAL:
        op = event.body.get("operation", "io")
        source = event.body.get("source", "unknown")
        return f"{event.agent_name}.{op}.{source}"
    return f"{event.agent_name}.event"


# ── Surface → OTel SpanKind mapping ─────────────────────────────────────────


def _otel_span_kind(kind: SpanKind):
    """Map AgentTrace SpanKind to OTel SpanKind."""
    if OTelSpanKind is None:
        return None
    mapping = {
        SpanKind.INTERNAL: OTelSpanKind.INTERNAL,
        SpanKind.CLIENT: OTelSpanKind.CLIENT,
        SpanKind.SERVER: OTelSpanKind.SERVER,
        SpanKind.PRODUCER: OTelSpanKind.PRODUCER,
        SpanKind.CONSUMER: OTelSpanKind.CONSUMER,
    }
    return mapping.get(kind, OTelSpanKind.INTERNAL)


# ── Defensive attribute serialization ────────────────────────────────────────


def _safe_set_attribute(span, key: str, value: Any):
    """Set a span attribute with defensive type checking."""
    if value is None:
        return
    if isinstance(value, (str, bool, int, float)):
        span.set_attribute(key, value)
    elif isinstance(value, dict):
        span.set_attribute(key, json.dumps(value, default=str))
    else:
        span.set_attribute(key, str(value))


# ── OTel Span Exporter ──────────────────────────────────────────────────────


class OTelExporter:
    """
    Converts AgentTrace TraceEvents to OpenTelemetry spans and exports them.

    Graceful degradation: if OTel is not installed or export fails, all
    methods are no-ops. The caller is responsible for fallback to JSONL.
    """

    def __init__(
        self,
        service_name: str = "MXDR_logging",
        endpoint: Optional[str] = None,
        use_batch: bool = True,
    ):
        self._tracer = None
        self._provider = None
        self._processor = None
        self._enabled = False
        self._service_name = service_name

        if not _OTEL_AVAILABLE:
            logger.info("OpenTelemetry not installed — OTel export disabled")
            return

        try:
            resource = Resource.create({"service.name": service_name})
            provider_class = TracerProvider
            if provider_class is None:
                return
            self._provider = provider_class(resource=resource)

            # Configure exporter
            if endpoint and OTLPSpanExporter is not None:
                span_exporter = OTLPSpanExporter(endpoint=endpoint)
            else:
                # Use a console exporter as fallback (for development)
                from opentelemetry.sdk.trace.export import ConsoleSpanExporter
                span_exporter = ConsoleSpanExporter()

            # Span processor
            if use_batch and BatchSpanProcessor is not None:
                self._processor = BatchSpanProcessor(span_exporter)
            elif SimpleSpanProcessor is not None:
                self._processor = SimpleSpanProcessor(span_exporter)

            if self._processor is not None:
                self._provider.add_span_processor(self._processor)

            otel_trace.set_tracer_provider(self._provider)

            tracer_provider = otel_trace.get_tracer_provider() if otel_trace else None
            self._tracer = tracer_provider.get_tracer(service_name) if tracer_provider else None
            self._enabled = self._tracer is not None

            logger.info(f"OTel exporter initialized (endpoint={endpoint or 'console'})")
        except Exception as e:
            logger.warning(f"Failed to initialize OTel exporter: {e}")
            self._enabled = False

    @property
    def enabled(self) -> bool:
        """Check if OTel export is available and configured."""
        return self._enabled

    def export_event(self, event: TraceEvent) -> bool:
        """
        Export a single TraceEvent as an OTel span.

        Returns True if the event was exported, False otherwise (caller
        should fall back to JSONL).
        """
        if not self._enabled or self._tracer is None:
            return False

        try:
            span_name = _span_name_from_event(event)
            otel_kind = _otel_span_kind(event.span_kind)

            # Start a new span
            span = self._tracer.start_span(
                name=span_name,
                kind=otel_kind,
                attributes={
                    "event_id": event.event_id,
                    "surface": event.surface.value,
                    "agent_name": event.agent_name,
                    "level": event.level.value,
                },
            )

            # Add all body fields as attributes
            for key, value in event.body.items():
                _safe_set_attribute(span, f"body.{key}", value)

            # Add tags
            for key, value in event.tags.items():
                _safe_set_attribute(span, f"tag.{key}", value)

            # Set duration if available
            if event.duration_ms is not None:
                span.set_attribute("duration_ms", event.duration_ms)

            # Set error status
            if event.is_error:
                if Status is not None and StatusCode is not None:
                    span.set_status(Status(StatusCode.ERROR, event.error or "unknown error"))

            span.end()

            return True
        except Exception as e:
            logger.warning(f"Failed to export OTel span: {e}")
            return False

    def export_events(self, events: List[TraceEvent]) -> int:
        """
        Export multiple TraceEvents as OTel spans.

        Returns the number of successfully exported events.
        """
        count = 0
        for event in events:
            if self.export_event(event):
                count += 1
        return count

    def shutdown(self):
        """Shutdown the OTel exporter gracefully."""
        if self._provider is not None:
            try:
                self._provider.shutdown()
            except Exception:
                pass

    def force_flush(self):
        """Force flush any pending spans."""
        if self._provider is not None:
            try:
                self._provider.force_flush()
            except Exception:
                pass
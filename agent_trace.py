"""
agent_trace.py — Core AgentTrace Instrumentation Framework

Implements the runtime instrumentation system defined in AgentTrace:

  Algorithm 1: Runtime Instrumentation Wrapper
  - Injects decorator-based wrappers around target methods
  - Captures operational (start/complete/error) events
  - Extracts cognitive segments from LLM responses
  - Propagates trace/span IDs via contextvars
  - Dual-path storage: JSONL + OTel

Design goals:
  - Non-intrusiveness (no agent code changes needed)
  - Low overhead (2 events per call on success path)
  - Graceful degradation (OTel failures → JSONL fallback)
  - Composability (schema-consistent across all surfaces)
"""

from __future__ import annotations

import functools
import json
import logging
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, TypeVar

from otel_exporter import OTelExporter
from trace_schema import (
    SchemaValidationError,
    SpanKind,
    TraceEvent,
    TraceLevel,
    TraceSurface,
    make_cognitive_body,
    make_contextual_body,
    make_operational_body,
)

logger = logging.getLogger(__name__)

# Context propagation (thread-safe)

_current_trace_id: ContextVar[str] = ContextVar("_current_trace_id", default="")
_current_span_id: ContextVar[str] = ContextVar("_current_span_id", default="")
_current_agent_name: ContextVar[str] = ContextVar("_current_agent_name", default="")

F = TypeVar("F", bound=Callable[..., Any])


# Trace context helpers


class TraceContext:
    """Manages trace/span ID propagation using contextvars."""

    @staticmethod
    def new_trace_id() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def new_span_id() -> str:
        return uuid.uuid4().hex[:16]

    @staticmethod
    def get_current_trace_id() -> str:
        return _current_trace_id.get()

    @staticmethod
    def get_current_span_id() -> str:
        return _current_span_id.get()

    @staticmethod
    def get_current_agent_name() -> str:
        return _current_agent_name.get()

    @staticmethod
    def set_trace_context(
        trace_id: str, span_id: str, agent_name: str = ""
    ):
        _current_trace_id.set(trace_id)
        _current_span_id.set(span_id)
        if agent_name:
            _current_agent_name.set(agent_name)

    @staticmethod
    def reset():
        _current_trace_id.set("")
        _current_span_id.set("")

    @staticmethod
    @contextmanager
    def span_context(
        trace_id: str, span_id: str, agent_name: str = ""
    ):
        """Context manager for nesting spans."""
        parent_span = _current_span_id.get()
        _current_trace_id.set(trace_id)
        _current_span_id.set(span_id)
        if agent_name:
            _current_agent_name.set(agent_name)
        try:
            yield
        finally:
            _current_span_id.set(parent_span)


# ALogger: Central Logging Interface


class ALogger:
    """
    AgentTrace Logger — records surface-specific events.

    Each record is written to JSONL and optionally exported to OTel.
    The ALogger is the central logging sink referenced in Algorithm 1.
    """

    def __init__(
        self,
        agent_name: str = "agent",
        log_dir: Optional[Path] = None,
        otel_exporter: Optional[OTelExporter] = None,
        mirror_contextual_to_jsonl: bool = False,
    ):
        self.agent_name = agent_name
        self.log_dir = log_dir
        self.otel_exporter = otel_exporter
        # Per AgentTrace §Log Schema and Local Sinks:
        # "Operational and cognitive events are persisted to JSONL, while contextual
        # events are primarily exported via OTel with optional file mirroring for
        # offline workflows."
        self.mirror_contextual_to_jsonl = mirror_contextual_to_jsonl
        self._events_written: int = 0
        self._jsonl_path: Optional[Path] = None

        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)
            # Use a combined trace log for all surfaces
            self._jsonl_path = log_dir / "traces.jsonl"

    # Recording methods──────────

    def record_operational(
        self,
        method: str,
        status: str,
        trace_id: str = "",
        span_id: str = "",
        parent_span_id: Optional[str] = None,
        args_summary: Optional[str] = None,
        result_summary: Optional[str] = None,
        duration_ms: Optional[float] = None,
        level: TraceLevel = TraceLevel.INFO,
        error: Optional[str] = None,
        **extra,
    ) -> TraceEvent:
        """Record an operational surface event."""
        body = make_operational_body(
            method=method,
            status=status,
            args_summary=args_summary,
            result_summary=result_summary,
            duration_ms=duration_ms,
            **extra,
        )
        event = TraceEvent(
            surface=TraceSurface.OPERATIONAL,
            trace_id=trace_id or TraceContext.get_current_trace_id(),
            span_id=span_id or TraceContext.new_span_id(),
            parent_span_id=parent_span_id or TraceContext.get_current_span_id() or None,
            agent_name=self.agent_name,
            level=level,
            span_kind=SpanKind.INTERNAL,
            body=body,
            duration_ms=duration_ms,
            error=error,
        )
        self._write_event(event)
        return event

    def record_operational_start(
        self,
        method: str,
        trace_id: str = "",
        span_id: str = "",
        args_summary: Optional[str] = None,
        **extra,
    ) -> str:
        """
        Record an operational START event and return the span_id.
        Use this at the beginning of an instrumented method.
        """
        span_id = span_id or TraceContext.new_span_id()
        self.record_operational(
            method=method,
            status="start",
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=TraceContext.get_current_span_id() or None,
            args_summary=args_summary,
            level=TraceLevel.INFO,
            **extra,
        )
        return span_id

    def record_operational_complete(
        self,
        method: str,
        span_id: str,
        duration_ms: float,
        result_summary: Optional[str] = None,
        **extra,
    ):
        """Record an operational COMPLETE event."""
        self.record_operational(
            method=method,
            status="complete",
            trace_id=TraceContext.get_current_trace_id(),
            span_id=span_id,
            parent_span_id=TraceContext.get_current_span_id() or None,
            result_summary=result_summary,
            duration_ms=duration_ms,
            level=TraceLevel.INFO,
            **extra,
        )

    def record_operational_error(
        self,
        method: str,
        span_id: str,
        duration_ms: float,
        error: str,
        **extra,
    ):
        """Record an operational ERROR event."""
        self.record_operational(
            method=method,
            status="error",
            trace_id=TraceContext.get_current_trace_id(),
            span_id=span_id,
            parent_span_id=TraceContext.get_current_span_id() or None,
            duration_ms=duration_ms,
            error=error,
            level=TraceLevel.ERROR,
            **extra,
        )

    def record_cognitive(
        self,
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
        trace_id: str = "",
        span_id: str = "",
        **extra,
    ) -> TraceEvent:
        """Record a cognitive surface event (LLM interaction)."""
        body = make_cognitive_body(
            thought=thought,
            plan=plan,
            reflection=reflection,
            model=model,
            prompt_summary=prompt_summary,
            completion_summary=completion_summary,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            temperature=temperature,
            **extra,
        )
        event = TraceEvent(
            surface=TraceSurface.COGNITIVE,
            trace_id=trace_id or TraceContext.get_current_trace_id(),
            span_id=span_id or TraceContext.new_span_id(),
            parent_span_id=TraceContext.get_current_span_id() or None,
            agent_name=self.agent_name,
            level=TraceLevel.INFO,
            span_kind=SpanKind.CLIENT,
            body=body,
        )
        self._write_event(event)
        return event

    def record_contextual(
        self,
        operation: str,
        source: str,
        query_summary: Optional[str] = None,
        response_summary: Optional[str] = None,
        byte_count: Optional[int] = None,
        row_count: Optional[int] = None,
        status_code: Optional[int] = None,
        trace_id: str = "",
        span_id: str = "",
        **extra,
    ) -> TraceEvent:
        """Record a contextual surface event (external I/O)."""
        body = make_contextual_body(
            operation=operation,
            source=source,
            query_summary=query_summary,
            response_summary=response_summary,
            byte_count=byte_count,
            row_count=row_count,
            status_code=status_code,
            **extra,
        )
        event = TraceEvent(
            surface=TraceSurface.CONTEXTUAL,
            trace_id=trace_id or TraceContext.get_current_trace_id(),
            span_id=span_id or TraceContext.new_span_id(),
            parent_span_id=TraceContext.get_current_span_id() or None,
            agent_name=self.agent_name,
            level=TraceLevel.INFO,
            span_kind=SpanKind.CLIENT,
            body=body,
        )
        self._write_event(event)
        return event

    # Internal write logic───────

    def _write_event(self, event: TraceEvent):
        """
        Write a trace event using the dual-path strategy from AgentTrace
        §Log Schema and Local Sinks:

          * Operational and cognitive → JSONL (always) + OTel (if enabled)
          * Contextual               → OTel (primary)  + JSONL (optional mirror)

        OTel export failures degrade gracefully to JSONL (per §Implementation:
        "Exporter failures gracefully degrade to local JSONL").
        """
        self._events_written += 1

        # AgentTrace §Implementation: "All records are validated at emission
        # time against the schema to maintain consistency."
        try:
            event.validate()
        except SchemaValidationError as e:
            logger.warning(f"Dropping schema-invalid trace event: {e}")
            return

        is_contextual = event.surface == TraceSurface.CONTEXTUAL
        otel_active = self.otel_exporter is not None and self.otel_exporter.enabled
        otel_succeeded = False

        # 1. Export to OTel first (primary path for contextual; secondary for others)
        if otel_active:
            try:
                otel_succeeded = bool(self.otel_exporter.export_event(event))
            except Exception as e:
                logger.debug(f"OTel export failed (non-critical): {e}")
                otel_succeeded = False

        # 2. Write to JSONL according to the surface routing policy
        should_write_jsonl = (
            self._jsonl_path is not None
            and (
                not is_contextual                       # op/cog: always JSONL
                or self.mirror_contextual_to_jsonl      # contextual: only if mirroring
                or not otel_succeeded                   # graceful degradation
            )
        )
        if should_write_jsonl:
            try:
                with open(self._jsonl_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event.to_dict(), default=str) + "\n")
            except OSError as e:
                logger.warning(f"Failed to write trace event to JSONL: {e}")

    @property
    def events_written(self) -> int:
        return self._events_written


# Global logger instance─────────────

_logger_instance: Optional[ALogger] = None


def get_logger() -> Optional[ALogger]:
    """Get the global ALogger instance."""
    return _logger_instance


# Init───────────────────────────────


def init(
    agent_name: str = "MXDR_agent",
    log_dir: Optional[Path] = None,
    enable_otel: bool = True,
    otel_endpoint: Optional[str] = None,
    service_name: str = "MXDR_logging",
    verbose: bool = False,
    auto_instrument: bool = True,
    mirror_contextual_to_jsonl: bool = False,
):
    """
    Initialize AgentTrace instrumentation.

    Per AgentTrace §Python Module Layout and Initialization:
      "Initialization loads configuration, prepares append-only JSONL outputs,
       and, when enabled, activates OTel auto-instrumentation for common I/O
       libraries."

    Args:
        agent_name: Name of the agent being traced.
        log_dir: Directory for trace JSONL output. Defaults to "trace_logs/".
        enable_otel: Whether to enable OpenTelemetry export.
        otel_endpoint: OTLP gRPC endpoint (e.g., "http://localhost:4317").
        service_name: OTel service name.
        verbose: Enable verbose logging.
        auto_instrument: When OTel is enabled, activate auto-instrumentation
            for HTTP / DB / cache libraries to capture contextual I/O spans
            (per AgentTrace §Contextual Surface).
        mirror_contextual_to_jsonl: When True, also write contextual events
            to JSONL (paper: "optional file mirroring for offline workflows").
    """
    global _logger_instance

    if verbose:
        logging.basicConfig(level=logging.INFO)

    if log_dir is None:
        log_dir = Path("trace_logs")

    log_dir.mkdir(parents=True, exist_ok=True)

    # Initialize OTel exporter if enabled
    otel_exporter = None
    if enable_otel:
        try:
            otel_exporter = OTelExporter(
                service_name=service_name,
                endpoint=otel_endpoint,
            )
        except Exception as e:
            logger.warning(f"Failed to initialize OTel exporter: {e}")
            otel_exporter = None

        # Activate auto-instrumentation hooks for contextual surface
        # (HTTP, SQL, cache libs). See AgentTrace §Contextual Surface.
        if otel_exporter is not None and otel_exporter.enabled and auto_instrument:
            _activate_auto_instrumentation(verbose=verbose)

    _logger_instance = ALogger(
        agent_name=agent_name,
        log_dir=log_dir,
        otel_exporter=otel_exporter,
        mirror_contextual_to_jsonl=mirror_contextual_to_jsonl,
    )

    logger.info(
        f"AgentTrace initialized: agent={agent_name}, "
        f"log_dir={log_dir}, otel={enable_otel}, "
        f"auto_instrument={auto_instrument and enable_otel}"
    )


def _activate_auto_instrumentation(verbose: bool = False) -> List[str]:
    """
    Attempt to activate OpenTelemetry auto-instrumentation for common I/O
    libraries (requests, urllib3, sqlalchemy, redis). Missing instrumentations
    are silently skipped — graceful degradation per AgentTrace §Engineering
    Considerations.

    Returns the list of successfully activated instrumentations.
    """
    activated: List[str] = []
    instrumentations = [
        ("opentelemetry.instrumentation.requests", "RequestsInstrumentor"),
        ("opentelemetry.instrumentation.urllib3", "URLLib3Instrumentor"),
        ("opentelemetry.instrumentation.urllib", "URLLibInstrumentor"),
        ("opentelemetry.instrumentation.sqlalchemy", "SQLAlchemyInstrumentor"),
        ("opentelemetry.instrumentation.redis", "RedisInstrumentor"),
    ]
    for module_path, class_name in instrumentations:
        try:
            mod = __import__(module_path, fromlist=[class_name])
            instrumentor_cls = getattr(mod, class_name)
            instrumentor_cls().instrument()
            activated.append(class_name)
            if verbose:
                logger.info(f"AgentTrace auto-instrumentation: {class_name} activated")
        except Exception as e:
            logger.debug(f"AgentTrace auto-instrumentation: skipped {class_name} ({e})")
    return activated


def shutdown():
    """Shutdown AgentTrace, flushing any pending exports."""
    global _logger_instance
    if _logger_instance is not None:
        if _logger_instance.otel_exporter is not None:
            try:
                _logger_instance.otel_exporter.force_flush()
                _logger_instance.otel_exporter.shutdown()
            except Exception:
                pass
    _logger_instance = None
    TraceContext.reset()


# Instrumentation decorator──────────


def instrument(
    name: Optional[str] = None,
    methods: Optional[Sequence[str]] = None,
    agent_name: Optional[str] = None,
):
    """
    Decorator that instruments a class's methods for runtime tracing.

    This implements Algorithm 1 from AgentTrace:
    1. For each public method, install an in-place wrapper
    2. The wrapper emits start/complete/error events
    3. Preserves original function signatures
    4. Cognitive trace extraction occurs on LLM completions

    Can be applied at:
      - Class level: @instrument() on a class to trace all public methods
      - Method level: @instrument() on individual methods

    Args:
        name: Optional override for the agent name.
        methods: Optional allowlist of method names to instrument.
                 If None, all public methods are instrumented.
        agent_name: Name for the agent in trace events.

    Returns:
        Decorated class or function.
    """

    def class_decorator(cls):
        """Instrument all public methods of a class."""
        target_methods = _select_methods(cls, methods)
        for method_name in target_methods:
            original_method = getattr(cls, method_name)
            if callable(original_method) and not method_name.startswith("_"):
                instrumented = _make_wrapper(
                    original_method,
                    agent_name or cls.__name__,
                    method_name,
                )
                setattr(cls, method_name, instrumented)
        return cls

    def function_decorator(func):
        """Instrument a single function."""
        return _make_wrapper(func, agent_name or func.__qualname__, func.__name__)

    # If used without parentheses, `name` will be the decorated class
    if callable(name):
        return class_decorator(name)

    return class_decorator


def instrument_agent(
    obj: Any,
    name: Optional[str] = None,
    methods: Optional[Sequence[str]] = None,
) -> Any:
    """
    Instrument an already-constructed agent *instance* at runtime.

    This is the API named in AgentTrace §Python Module Layout:
      ``instrument_agent(obj, name, methods=None)`` —
      "wraps selected public callables for runtime tracing."

    It mirrors Algorithm 1 (input: agent instance A, name n, optional allowlist
    M) by installing per-instance wrappers on bound methods of ``obj``.

    Args:
        obj: The agent instance to instrument (in place).
        name: Logical agent name used in trace events. Defaults to class name.
        methods: Optional allowlist of public method names. If None, every
                 public instance method is wrapped.

    Returns:
        The same ``obj`` instance, now instrumented.
    """
    agent_name = name or type(obj).__name__
    target_methods = _select_methods(type(obj), methods)
    for method_name in target_methods:
        bound = getattr(obj, method_name, None)
        if bound is None or not callable(bound) or method_name.startswith("_"):
            continue
        # Idempotency: skip if this bound method is already wrapped.
        if getattr(bound, "__agenttrace_instrumented__", False):
            continue
        wrapped = _make_wrapper(bound, agent_name, method_name)
        try:
            setattr(wrapped, "__agenttrace_instrumented__", True)
        except (AttributeError, TypeError):
            pass
        try:
            setattr(obj, method_name, wrapped)
        except (AttributeError, TypeError) as e:
            # Instances using __slots__ may forbid attribute assignment; skip.
            logger.debug(f"instrument_agent: skipped {method_name} ({e})")
    return obj


def _select_methods(cls, allowlist: Optional[Sequence[str]] = None) -> List[str]:
    """Select public methods to instrument from a class."""
    if allowlist is not None:
        return [m for m in allowlist if hasattr(cls, m) and callable(getattr(cls, m))]
    return [
        m
        for m in dir(cls)
        if not m.startswith("_")
        and callable(getattr(cls, m))
        and not m.startswith("__")
    ]


def _make_wrapper(
    func: Callable[..., Any],
    agent_name: str,
    method_name: str,
) -> Callable[..., Any]:
    """
    Create an in-place wrapper that emits trace events (Algorithm 1).

    The wrapper:
    1. Generates or propagates trace_id and span_id
    2. Records an operational START event
    3. Calls the original function
    4. Attempts cognitive extraction from the result
    5. Records an operational COMPLETE or ERROR event
    6. Returns the original result (preserving semantics)
    """
    func_name = f"{agent_name}.{method_name}"

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        alogger = get_logger()
        if alogger is None:
            return func(*args, **kwargs)

        # 1. Generate trace/span IDs
        trace_id = TraceContext.get_current_trace_id() or TraceContext.new_trace_id()
        span_id = TraceContext.new_span_id()
        parent_span_id = TraceContext.get_current_span_id() or None

        # 2. Record START event
        # Per AgentTrace §Operational Surface, start/complete events are
        # enriched with "argument count, result type, and execution duration".
        args_summary = _summarize_args(args, kwargs)
        alogger.record_operational_start(
            method=method_name,
            trace_id=trace_id,
            span_id=span_id,
            args_summary=args_summary,
            args_count=len(args) + len(kwargs),
        )

        # Set context for nested spans
        TraceContext.set_trace_context(trace_id, span_id, agent_name)
        t0 = time.monotonic()

        try:
            # 3. Call the original function: y ← f(x)
            result = func(*args, **kwargs)

            # 4. Cognitive extraction (Algorithm 1, line 10):
            #    (y, θ) ← maybe_extract_cognitive(y)
            # Paper: returns the cleaned answer and logs the segment as a
            # cognitive event; otherwise, results pass through unmodified.
            result, _cognitive_extracted = _maybe_extract_cognitive(
                result, agent_name, trace_id, span_id
            )

            # 5. Record COMPLETE event
            duration_ms = (time.monotonic() - t0) * 1000
            result_summary = _summarize_value(result)
            alogger.record_operational_complete(
                method=method_name,
                span_id=span_id,
                duration_ms=duration_ms,
                result_summary=result_summary,
                result_type=type(result).__name__,
            )

            # Restore parent context
            if parent_span_id:
                TraceContext.set_trace_context(trace_id, parent_span_id, agent_name)
            else:
                TraceContext.reset()

            return result

        except Exception as e:
            # 5. Record ERROR event
            duration_ms = (time.monotonic() - t0) * 1000
            alogger.record_operational_error(
                method=method_name,
                span_id=span_id,
                duration_ms=duration_ms,
                error=repr(e),
            )

            # Restore parent context
            if parent_span_id:
                TraceContext.set_trace_context(trace_id, parent_span_id, agent_name)
            else:
                TraceContext.reset()

            # Re-raise to preserve semantics
            raise

    # Mark the wrapper so instrument_agent() can detect double-instrumentation.
    try:
        setattr(wrapper, "__agenttrace_instrumented__", True)
    except (AttributeError, TypeError):
        pass
    return wrapper


# Cognitive extraction───────────────


def _maybe_extract_cognitive(
    result: Any,
    agent_name: str,
    trace_id: str,
    span_id: str,
) -> Tuple[Any, bool]:
    """
    Algorithm 1, line 10:  (y, θ) ← maybe_extract_cognitive(y)

    Per AgentTrace §Cognitive trace extraction:
      "When completions include a delimited reasoning segment, AgentTrace
       returns the cleaned answer and logs the segment as a cognitive event;
       otherwise, results pass through unmodified."

    Supports:
      - Strings containing <thinking>...</thinking> / <plan>...</plan> tags
        (returns the string with these segments stripped)
      - Dicts with 'plan', 'reflection', 'thought' keys
      - Objects with a `.text` attribute (LLM responses) — segments logged,
        original object returned unmodified
      - Objects with a `.candidates` attribute (LLM SDK responses)

    Returns (possibly_cleaned_result, extracted_flag).
    """
    alogger = get_logger()
    if alogger is None:
        return result, False

    # Strings: extract cognitive segments AND strip them from the returned value
    if isinstance(result, str):
        extracted = _extract_cognitive_from_text(
            result, alogger, agent_name, trace_id, span_id
        )
        cleaned = _strip_cognitive_markers(result) if extracted else result
        return cleaned, extracted

    # Dicts: record cognitive fields once; pass dict through unmodified
    if isinstance(result, dict):
        extracted = False
        if any(k in result for k in ("plan", "reflection", "thought", "reasoning")):
            alogger.record_cognitive(
                thought=result.get("thought") or result.get("reasoning"),
                plan=result.get("plan"),
                reflection=result.get("reflection"),
                trace_id=trace_id,
                span_id=span_id,
                source="dict_field",
            )
            extracted = True
        return result, extracted

    # Objects with `.text` or LLM SDK `.candidates`
    text: Optional[str] = None
    if hasattr(result, "text") and isinstance(getattr(result, "text", None), str):
        text = result.text
    elif hasattr(result, "candidates"):
        try:
            for candidate in result.candidates:
                if hasattr(candidate, "content") and hasattr(candidate.content, "parts"):
                    text = " ".join(
                        p.text for p in candidate.content.parts if hasattr(p, "text")
                    )
        except Exception:
            text = None

    if text:
        extracted = _extract_cognitive_from_text(
            text, alogger, agent_name, trace_id, span_id
        )
        # Do not mutate complex response objects — pass through.
        return result, extracted

    return result, False


def _strip_cognitive_markers(text: str) -> str:
    """
    Remove <thinking>...</thinking> and <plan>...</plan> delimited segments
    from a string so the cleaned answer can be returned to the caller
    (cf. AgentTrace §Cognitive trace extraction).
    """
    cleaned = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<plan>.*?</plan>", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


def _extract_cognitive_from_text(
    text: str,
    alogger: ALogger,
    agent_name: str,
    trace_id: str,
    span_id: str,
) -> bool:
    """
    Extract cognitive segments from text using marker-based pattern detection,
    XML tag parsing, and JSON field extraction (per AgentTrace §Cognitive Surface).
    """
    extracted = False

    # 1. Marker-based: <thinking>...</thinking> tags
    thinking_segments = _extract_xml_tags(text, "thinking")
    for segment in thinking_segments:
        alogger.record_cognitive(
            thought=segment,
            trace_id=trace_id,
            span_id=span_id,
            extraction_method="thinking_tag",
        )
        extracted = True

    # 2. Marker-based: <plan>...</plan> tags
    plan_segments = _extract_xml_tags(text, "plan")
    for segment in plan_segments:
        alogger.record_cognitive(
            plan=segment,
            trace_id=trace_id,
            span_id=span_id,
            extraction_method="plan_tag",
        )
        extracted = True

    # 3. JSON field extraction: look for { "plan": ... } or { "reflection": ... }
    json_cognitive = _extract_json_cognitive(text)
    if json_cognitive:
        alogger.record_cognitive(
            thought=json_cognitive.get("thought"),
            plan=json_cognitive.get("plan"),
            reflection=json_cognitive.get("reflection"),
            trace_id=trace_id,
            span_id=span_id,
            extraction_method="json_field",
        )
        extracted = True

    # 4. Chain-of-Thought: detect numbered reasoning steps
    cot_steps = _extract_cot_steps(text)
    if cot_steps:
        alogger.record_cognitive(
            thought=cot_steps,
            trace_id=trace_id,
            span_id=span_id,
            extraction_method="cot_steps",
        )
        extracted = True

    return extracted


def _extract_xml_tags(text: str, tag: str) -> List[str]:
    """Extract content from XML-like tags: <tag>content</tag>."""
    pattern = rf"<{tag}>(.*?)</{tag}>"
    matches = re.findall(pattern, text, re.DOTALL)
    return [m.strip() for m in matches if m.strip()]


def _extract_json_cognitive(text: str) -> Dict[str, Any]:
    """
    Extract cognitive fields from JSON blocks in text.
    Looks for patterns like { "plan": "...", "reflection": "..." }
    """
    # Find JSON-like blocks
    json_pattern = r"\{[^{}]*\}"
    matches = re.findall(json_pattern, text, re.DOTALL)
    result = {}
    for match in matches:
        try:
            data = json.loads(match)
            if isinstance(data, dict):
                for key in ("plan", "reflection", "thought", "reasoning"):
                    if key in data:
                        result[key] = str(data[key])
        except (json.JSONDecodeError, ValueError):
            continue
    return result


def _extract_cot_steps(text: str) -> Optional[str]:
    """Extract Chain-of-Thought reasoning steps (numbered steps)."""
    # Look for "Step 1:", "Step 2:", etc.
    step_pattern = r"(?:^|\n)\s*(?:Step|Reasoning)\s*\d+\s*[:.].*?(?=\n\s*(?:Step|Reasoning)\s*\d+\s*[:.]|\n\n|\Z)"
    matches = re.findall(step_pattern, text, re.DOTALL | re.IGNORECASE)
    if matches:
        return "\n".join(m.strip() for m in matches)
    return None


# Value summarization helpers


def _summarize_args(args: tuple, kwargs: dict) -> str:
    """Create a concise summary of function arguments."""
    parts = []
    for i, arg in enumerate(args[:5]):  # Limit to first 5 positional args
        parts.append(f"arg{i}={_summarize_value(arg)}")
    for key, value in list(kwargs.items())[:5]:  # Limit to 5 keyword args
        parts.append(f"{key}={_summarize_value(value)}")
    return ", ".join(parts) if parts else ""


def _summarize_value(value: Any) -> str:
    """Create a short string summary of a value."""
    if value is None:
        return "None"
    if isinstance(value, (str, bytes)):
        s = str(value)
        return f"'{s[:80]}{'...' if len(s) > 80 else ''}'"
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return f"[{type(value).__name__}:{len(value)} items]"
    if isinstance(value, dict):
        return f"[dict:{len(value)} keys]"
    if hasattr(value, "__class__"):
        return f"[{value.__class__.__name__}]"
    return str(value)[:100]

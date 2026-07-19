"""The AgentArgus ``Tracer`` — a thin OO wrapper over ``opentelemetry-sdk``.

Design (spec §6.2):
*   **Encapsulation pillar.** All OTel provider/exporter/processor wiring is
    private to this class. Nothing outside ``observability/`` imports
    ``opentelemetry`` directly — callers depend only on ``Tracer.span`` and the
    ``TracerSeam`` protocol from ``agents/seams.py``.
*   **Abstraction pillar.** We depend on OTel's ``SpanExporter`` interface, not a
    concrete backend. The exporter is chosen from config: ``memory`` (tests),
    ``console`` (dev), ``otlp`` (Jaeger / any OTLP collector, optional extra).
*   **Per-run span collector.** A private ``SpanProcessor`` captures every
    finished span into a per-``trace_id`` buffer *in addition to* whatever
    exporter is active — so spans reach Jaeger AND ``RunResult.spans`` at once.
    ``Agent.arun`` drains the buffer for the current run.

The OTel span/trace ids (W3C standard, 32-/16-hex) are the source of truth for
correlation — ``Agent`` adopts the tracer's trace id when a real tracer is
active, falling back to a uuid4 only when tracing is off (``NullTracer``).
"""

# NOTE: no ``from __future__ import annotations`` needed here (no @overload
# site), but kept off for consistency with the rest of observability/.

import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import format_span_id, format_trace_id

from agentargus.core import Span
from agentargus.logging import get_logger

__all__ = ["Tracer", "CollectorProcessor"]

_logger = get_logger("observability.tracer")

# OTel timestamps are integer nanoseconds; our Span uses float seconds.
_NS_PER_S = 1_000_000_000


def _readable_to_span(raw: ReadableSpan) -> Span:
    """Convert an OTel ``ReadableSpan`` into our immutable ``Span`` dataclass."""
    ctx = raw.get_span_context()
    parent_id = format_span_id(raw.parent.span_id) if raw.parent else None
    span_id = format_span_id(ctx.span_id) if ctx is not None else ""
    return Span(
        name=raw.name,
        span_id=span_id,
        start_time=(raw.start_time or 0) / _NS_PER_S,
        end_time=(raw.end_time or 0) / _NS_PER_S,
        attributes=dict(raw.attributes or {}),
        parent_id=parent_id,
    )


class CollectorProcessor(SpanProcessor):
    """A span processor that buffers finished spans keyed by trace id.

    This is how spans get onto ``RunResult.spans`` without disabling export:
    it runs alongside the real exporter's processor. Buffers are drained (and
    cleared) per run to bound memory.
    """

    def __init__(self) -> None:
        self._by_trace: dict[str, list[Span]] = defaultdict(list)

    def on_end(self, span: ReadableSpan) -> None:
        ctx = span.get_span_context()
        if ctx is None:  # pragma: no cover - defensive
            return
        trace_hex = format_trace_id(ctx.trace_id)
        self._by_trace[trace_hex].append(_readable_to_span(span))

    def drain(self, trace_id: str) -> tuple[Span, ...]:
        """Return and remove all spans collected for ``trace_id`` (start-ordered)."""
        spans = self._by_trace.pop(trace_id, [])
        spans.sort(key=lambda s: s.start_time)
        return tuple(spans)

    # SpanProcessor requires these; we have nothing to flush/shutdown.
    def shutdown(self) -> None:  # pragma: no cover - trivial
        self._by_trace.clear()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:  # pragma: no cover
        return True


def _make_exporter(kind: str) -> SpanExporter:
    """Select a span exporter by name (encapsulated; the only place this lives)."""
    if kind == "memory":
        return InMemorySpanExporter()
    if kind == "console":
        return ConsoleSpanExporter()
    if kind == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError(
                "The 'otlp' tracer exporter requires the optional dependency. "
                "Install it with:  pip install 'agentargus[otlp]'"
            ) from exc
        exporter: SpanExporter = OTLPSpanExporter()
        return exporter
    raise ValueError(f"Unknown tracer exporter {kind!r}; expected 'memory', 'console', or 'otlp'.")


class Tracer:
    """OO wrapper over the OTel SDK. Satisfies the ``TracerSeam`` protocol."""

    def __init__(self, exporter: str = "memory", service_name: str = "agentargus") -> None:
        self._provider = TracerProvider()
        self._exporter = _make_exporter(exporter)
        # Export processor (to console/memory/OTLP) + our collector processor.
        self._provider.add_span_processor(SimpleSpanProcessor(self._exporter))
        self._collector = CollectorProcessor()
        self._provider.add_span_processor(self._collector)
        self._otel = self._provider.get_tracer(service_name)
        self._exporter_kind = exporter
        _logger.debug("tracer initialised exporter=%s", exporter)

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Any]:
        """Open a span as the current span; yields the live OTel span."""
        with self._otel.start_as_current_span(name, attributes=attributes) as sp:
            yield sp

    def current_trace_id(self) -> str | None:
        """Return the active span's trace id (32-hex), or None if no span is active."""
        from opentelemetry import trace as _trace

        ctx = _trace.get_current_span().get_span_context()
        if not ctx.is_valid:
            return None
        return format_trace_id(ctx.trace_id)

    def collect(self, trace_id: str) -> tuple[Span, ...]:
        """Drain the spans recorded for ``trace_id`` into ``RunResult`` form."""
        return self._collector.drain(trace_id)

    def traced(self, name: str | None = None, **attributes: Any) -> Any:
        """Decorator that runs the wrapped function inside a span."""

        def decorator(fn: Any) -> Any:
            span_name = name or fn.__name__

            import functools
            import inspect

            if inspect.iscoroutinefunction(fn):

                @functools.wraps(fn)
                async def awrapper(*args: Any, **kwargs: Any) -> Any:
                    with self.span(span_name, **attributes):
                        return await fn(*args, **kwargs)

                return awrapper

            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                with self.span(span_name, **attributes):
                    return fn(*args, **kwargs)

            return wrapper

        return decorator


def _now() -> float:  # pragma: no cover - kept for symmetry / future use
    return time.time()

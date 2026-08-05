"""Telemetry — durable, exportable observability for the event stream.

The event stream (`events.py`) is the source of truth; this module gives it
production-grade sinks:

  * **`JsonlSink`** writes events as JSON lines to a file or stream — a durable,
    grep-able, stdlib-only audit/observability log. The default production sink.
  * **`otel_sink(...)`** bridges the stream to OpenTelemetry *if* the optional
    `opentelemetry-api` package is installed, turning each event into a span
    event. Without it, you get a clear error pointing to `JsonlSink`.

OpenTelemetry is strictly optional — the self-contained promise holds.
"""
from __future__ import annotations

import json
import sys
from typing import Optional

from .events import CallbackSink, Event, EventSink


class JsonlSink(EventSink):
    """Write each event as one JSON line (to a path or an open stream)."""

    def __init__(self, path: Optional[str] = None, stream=None):
        self._owns = False
        if stream is not None:
            self._stream = stream
        elif path is not None:
            from pathlib import Path

            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._stream = open(path, "a", encoding="utf-8")
            self._owns = True
        else:
            self._stream = sys.stdout

    def emit(self, event: Event) -> None:
        try:
            self._stream.write(json.dumps(event.to_dict()) + "\n")
            self._stream.flush()
        except Exception:
            pass  # observability must never break the run

    def close(self) -> None:
        if self._owns:
            try:
                self._stream.close()
            except Exception:
                pass


def otel_available() -> bool:
    try:
        import opentelemetry.trace  # noqa: F401
        return True
    except Exception:
        return False


def otel_sink(tracer=None) -> CallbackSink:
    """Bridge events to OpenTelemetry spans (requires `opentelemetry-api`).

    Each Autarch event becomes a span event on the current span (or a short
    span if a tracer is provided). Raises a clear error if OTel isn't installed.
    """
    if not otel_available():
        raise RuntimeError(
            "OpenTelemetry is not installed. Either `pip install opentelemetry-api` "
            "or use JsonlSink for stdlib-only observability."
        )
    from opentelemetry import trace

    tracer = tracer or trace.get_tracer("autarch")

    def forward(event: Event) -> None:
        span = trace.get_current_span()
        try:
            span.add_event(event.kind, attributes={
                f"autarch.{k}": (v if isinstance(v, (str, int, float, bool)) else json.dumps(v))
                for k, v in event.data.items()
            })
        except Exception:
            pass

    return CallbackSink(forward)

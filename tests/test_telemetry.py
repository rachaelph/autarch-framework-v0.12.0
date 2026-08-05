"""Telemetry tests — JSONL sink and optional OTel bridge."""
import io
import json

from autarch import Agent, JsonlSink, capability, otel_available
from autarch.events import Event, RUN_START


def test_jsonl_sink_writes_lines():
    buf = io.StringIO()
    sink = JsonlSink(stream=buf)
    sink.emit(Event(RUN_START, "run_1", {"intent": "x"}))
    sink.emit(Event("run.complete", "run_1"))
    lines = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    assert lines[0]["kind"] == RUN_START
    assert lines[0]["data"]["intent"] == "x"
    assert lines[1]["kind"] == "run.complete"


def test_jsonl_sink_to_file(tmp_path):
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path=str(path))
    sink.emit(Event(RUN_START, "run_1"))
    sink.close()
    assert path.exists()
    assert json.loads(path.read_text().strip())["kind"] == RUN_START


def test_agent_streams_to_jsonl(tmp_path):
    buf = io.StringIO()
    agent = Agent(
        intent="create a.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path, events=JsonlSink(stream=buf),
    )
    agent.run()
    kinds = [json.loads(line)["kind"] for line in buf.getvalue().splitlines() if line.strip()]
    assert RUN_START in kinds
    assert "run.complete" in kinds


def test_jsonl_sink_never_raises_on_bad_stream():
    class Bad:
        def write(self, _):
            raise IOError("disk full")

        def flush(self):
            pass

    JsonlSink(stream=Bad()).emit(Event("k", "run_1"))  # swallowed


def test_otel_sink_behavior():
    # If OTel is installed, otel_sink() returns a usable sink; otherwise it raises
    # a clear error. Either way it never silently misbehaves.
    from autarch import otel_sink

    if otel_available():
        sink = otel_sink()
        sink.emit(Event("k", "run_1"))  # no current span -> no-op, no raise
    else:
        import pytest

        with pytest.raises(RuntimeError):
            otel_sink()

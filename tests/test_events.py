"""Structured event stream tests."""
from autarch import Agent, ListSink, NullSink, capability
from autarch.events import (
    ACTION_EXECUTED,
    DELIBERATION_COMPLETE,
    GATE_CHECKED,
    RUN_COMPLETE,
    RUN_START,
    CallbackSink,
    Event,
    emit,
)


def test_null_sink_is_noop():
    NullSink().emit(Event("x", "run_1"))  # must not raise


def test_list_sink_collects():
    sink = ListSink()
    sink.emit(Event(RUN_START, "run_1", {"a": 1}))
    sink.emit(Event(RUN_COMPLETE, "run_1"))
    assert sink.kinds() == [RUN_START, RUN_COMPLETE]
    assert sink.of_kind(RUN_START)[0].data["a"] == 1


def test_callback_sink_forwards():
    seen = []
    CallbackSink(seen.append).emit(Event("k", "run_1"))
    assert seen[0].kind == "k"


def test_emit_never_raises_on_broken_sink():
    class Broken:
        def emit(self, event):
            raise RuntimeError("boom")

    emit(Broken(), "k", "run_1")  # swallowed, no exception


def test_agent_emits_full_lifecycle(tmp_path):
    sink = ListSink()
    agent = Agent(
        intent="create a.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path, events=sink,
    )
    agent.run()
    kinds = sink.kinds()
    for expected in (RUN_START, DELIBERATION_COMPLETE, GATE_CHECKED, ACTION_EXECUTED, RUN_COMPLETE):
        assert expected in kinds
    # run.start is first; a terminal event is last.
    assert kinds[0] == RUN_START
    assert kinds[-1] in (RUN_COMPLETE, "run.blocked")


def test_default_sink_is_null(tmp_path):
    agent = Agent(
        intent="create a.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path,
    )
    assert isinstance(agent.events, NullSink)

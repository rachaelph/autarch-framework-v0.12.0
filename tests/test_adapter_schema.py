"""Adapter schema + param normalization — making free-form model output line up
with typed adapters (the gap real models exposed)."""
from autarch import Agent, capability
from autarch.adapters.filesystem import FileSystemAdapter
from autarch.contracts import Action
from autarch.council.deliberation import Council
from autarch.intelligence.mock import MockProvider


def test_filesystem_declares_schema():
    schema = FileSystemAdapter(root="./sandbox/_t").schema()
    assert "path" in schema["file.write"]
    assert "content" in schema["file.write"]
    assert "dest" in schema["file.move"]


def test_normalize_filename_synonym(tmp_path):
    adapter = FileSystemAdapter(root=tmp_path)
    params = adapter.normalize_params("file.write", {"filename": "a.txt", "text": "hi"})
    assert params["path"] == "a.txt"
    assert params["content"] == "hi"


def test_execute_tolerates_synonyms(tmp_path):
    adapter = FileSystemAdapter(root=tmp_path)
    # A real model used 'filename' + 'text' instead of 'path' + 'content'.
    result = adapter.execute(Action("file.write", {"filename": "note.txt", "text": "hello"}))
    assert result.ok is True
    assert (tmp_path / "note.txt").read_text() == "hello"


def test_canonical_params_take_precedence(tmp_path):
    adapter = FileSystemAdapter(root=tmp_path)
    params = adapter.normalize_params("file.write", {"path": "real.txt", "filename": "decoy.txt"})
    assert params["path"] == "real.txt"


def test_council_renders_schema_in_prompt():
    council = Council(
        [MockProvider()],
        capabilities=["file.write"],
        schemas={"file.write": {"path": "string", "content": "string"}},
    )
    block = council._capabilities_block()
    assert "file.write" in block
    assert "path" in block and "content" in block


def test_agent_normalizes_before_gate_and_executes(tmp_path):
    # Simulate a model that proposes synonym params; the action must still gate
    # and execute with canonical params recorded.
    import json

    provider = MockProvider(name="synmodel", scripted={
        "ROLE: PROPOSER": json.dumps(
            {"capability": "file.write", "params": {"filename": "out.txt", "text": "hi"}, "rationale": "r"}
        ),
        "ROLE: CHALLENGER": json.dumps({"verdict": "approve", "reasons": "ok"}),
    })
    agent = Agent(
        intent="write a file",
        council=[provider],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path,
    )
    result = agent.run()
    assert result.executed is True
    assert (tmp_path / "out.txt").read_text() == "hi"
    # The audit record shows canonical params, not the synonyms.
    record = agent.memory.get(result.why_id)
    assert record.params.get("path") == "out.txt"

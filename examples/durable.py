"""Durable, resumable execution + a structured event stream.

Enterprise agents must survive crashes without repeating side effects, and every
step must be observable. This shows: a run records each lifecycle step to a
durable journal and emits typed events; resuming a completed run returns its
recorded outcome WITHOUT re-executing the action.

Run from the repo root:
    python examples/durable.py
"""
import shutil
from pathlib import Path

from autarch import Agent, ListSink, RunJournal, capability
from autarch.adapters.base import Adapter
from autarch.contracts import ActionResult


class CountingFiles(Adapter):
    """A file adapter that counts how many times it actually writes."""

    name = "counting-files"

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.writes = 0

    def capabilities(self):
        return ["file.write", "file.read", "file.move", "file.delete"]

    def execute(self, action):
        if action.capability != "file.write":
            return ActionResult(False, error="only writes in this demo")
        self.writes += 1
        target = self.root / action.params.get("path", "out.txt")
        target.write_text(action.params.get("content", ""))
        return ActionResult(True, output=f"wrote {target.name} (write #{self.writes})")


def banner(title):
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def main():
    ws = Path("./sandbox/_durable")
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)

    journal = RunJournal(ws / ".autarch" / "runs.db")
    sink = ListSink()
    adapter = CountingFiles(ws)

    banner("1) Run with a durable journal + event stream")
    agent = Agent(
        intent="create report.txt that says quarterly",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=ws, adapters=[adapter], run_id="run_demo", journal=journal, events=sink,
    )
    result = agent.run()
    print(f"  executed: {result.executed}  why: {result.why_id}")
    print(f"  side-effect writes: {adapter.writes}")
    print(f"  events: {' -> '.join(sink.kinds())}")
    state = journal.get("run_demo")
    print(f"  journal: status={state.status} step={state.step} why={state.why_id}")

    banner("2) The process 'crashes' and an orchestrator restarts")
    print("  A brand-new Agent + adapter resumes the SAME run id...")
    fresh_adapter = CountingFiles(ws)
    resumed = Agent(
        intent="create report.txt that says quarterly",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=ws, adapters=[fresh_adapter], journal=journal,
    ).resume("run_demo")
    print(f"  resumed executed: {resumed.executed}  why: {resumed.why_id}")
    print(f"  same outcome as before: {resumed.why_id == result.why_id}")
    print(f"  NEW adapter's writes: {fresh_adapter.writes}  (0 = side effect NOT repeated)")

    banner("3) Crash recovery — find runs that never finished")
    journal.start("orphan_run", "an intent that never completed")
    pending = [s.run_id for s in journal.unfinished()]
    print(f"  unfinished runs awaiting recovery: {pending}")

    journal.close()
    print("\nGoverned AND durable: it never does the same thing twice, and you can see every step.")


if __name__ == "__main__":
    main()

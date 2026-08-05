"""Describe an image — GOVERNED and SIGNED by autarch (vision, not text-only).

autarch's model seam now accepts images: a vision-capable provider implements ``complete_vision``
(Azure OpenAI + the MAF bridge both do). This example wraps a vision call as a GOVERNED capability
so the picture is described under the exact same guarantees as any autarch action:

  * the agent is granted ONLY ``vision.describe`` (proven it cannot write, delete, or reach the
    filesystem beyond the tool);
  * the describe call is enacted through the kernel and SIGNED in the tamper-evident ledger;
  * the model call is metered (tokens + cost) like every other.

Usage:
    python examples/vision.py "C:/path/to/image.png" --model azure:gpt-5.4
    python examples/vision.py "https://example.com/pic.jpg" --model azure:gpt-5.4

Without a vision-capable model configured it still runs, but the provider degrades to a text
completion (it can't actually see the image) — proving the seam is backward compatible.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from autarch import Agent, ImageRef, Invariant, capability, get_usage_meter
from autarch.adapters.tool import ToolAdapter
from autarch.intelligence.factory import build_provider


def banner(title: str) -> None:
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


def parse_args(argv):
    src, model, rest = None, "azure:gpt-5.4", list(argv)
    if "--model" in rest:
        i = rest.index("--model")
        model = rest[i + 1]
        del rest[i : i + 2]
    if rest:
        src = rest[0]
    return src, model


_PROMPT = "Describe this image in detail: what it shows, any text, notable objects, and its likely purpose."
_SYSTEM = "You are a precise visual analyst. Describe only what is actually visible."


def main() -> int:
    src, model = parse_args(sys.argv[1:])
    if not src:
        print('usage: python examples/vision.py "<image path or url>" [--model azure:gpt-5.4]')
        return 2

    image = ImageRef.from_url(src) if src.lower().startswith(("http://", "https://")) else ImageRef.from_path(src)
    if image.path and not Path(image.path).exists():
        print(f"file not found: {image.path}")
        return 2

    provider = build_provider(model)
    get_usage_meter().reset()

    banner(f"VISION — describe an image, governed by autarch  ({model})")
    print(f"  provider supports vision: {provider.supports_vision()}")
    if not provider.supports_vision():
        print("  (this provider is text-only; it will answer from the prompt without seeing the image)")

    # The governed capability: describing the image. The agent is granted ONLY this.
    def describe(path_or_url: str) -> str:
        return provider.complete_vision(_PROMPT, [image], system=_SYSTEM)

    workspace = tempfile.mkdtemp(prefix="autarch_vision_")
    agent = Agent(
        intent=f"describe image {src}",
        adapters=[ToolAdapter({"describe": describe}, namespace="vision")],
        grants=[capability("vision.describe")],  # nothing else — by construction
        workspace=workspace,
    )
    report = agent.guarantee([Invariant.forbid("file.write"), Invariant.forbid("file.delete")])
    print(f"  guarantee — agent can never write or delete: {report.all_hold}")

    result = agent.enact("vision.describe", {"path_or_url": src})
    if not result.executed or result.result is None or not result.result.ok:
        print(f"  describe was blocked/failed: {result.result.error if result.result else 'no result'}")
        return 1

    print(f"  signed why-record: {result.why_id}")
    print(f"  provenance verifies: {agent.memory.verify_provenance(result.why_id)}")

    banner("DESCRIPTION")
    print(str(result.result.output).strip() or "(empty)")

    tot = get_usage_meter().totals()
    if tot["calls"]:
        print(f"\n  model calls: {tot['calls']}  |  input tokens: {tot['prompt_tokens']:,}  |  "
              f"output tokens: {tot['completion_tokens']:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

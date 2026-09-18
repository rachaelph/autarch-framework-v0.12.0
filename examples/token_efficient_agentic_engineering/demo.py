"""Command-line entry point for the token-efficient engineering benchmark."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from autarch.intelligence.factory import build_provider

from .engine import (
    DeterministicEngineeringProvider,
    TokenBudgetExceeded,
    TokenEfficientEngineering,
    load_knowledge,
)

HERE = Path(__file__).resolve().parent
DEFAULT_WORKSPACE = Path("sandbox") / "token-efficient-demo"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare fixed full-context agents with a routed, budgeted agent runtime."
    )
    parser.add_argument(
        "--intent",
        help="Engineering request. Defaults to sample_issue.md.",
    )
    parser.add_argument(
        "--provider",
        default="demo",
        help="demo (offline), ollama:<model>, openai:<model>, azure:<deployment>, etc.",
    )
    parser.add_argument("--budget", type=int, default=12000, help="Optimized admission budget.")
    parser.add_argument(
        "--context-budget", type=int, default=2400, help="Total specialist evidence budget."
    )
    parser.add_argument("--max-parallel", type=int, default=4)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--json", action="store_true", help="Print the machine-readable report.")
    return parser


def _provider(spec: str):
    return DeterministicEngineeringProvider() if spec == "demo" else build_provider(spec)


def _print_summary(report) -> None:
    baseline = report.baseline
    optimized = report.optimized
    print("\nTOKEN-EFFICIENT AGENTIC ENGINEERING")
    print("=" * 72)
    print("{:<24} {:>16} {:>16}".format("Metric", "Baseline", "Optimized"))
    print("-" * 72)
    rows = [
        ("Dynamically used skills", len(baseline.selected_skills), len(optimized.selected_skills)),
        ("Exposed tools", len(baseline.exposed_tools), len(optimized.exposed_tools)),
        ("Evidence tokens", baseline.context_tokens, optimized.context_tokens),
        ("Model calls", baseline.model_calls, optimized.model_calls),
        ("Prompt tokens", baseline.prompt_tokens, optimized.prompt_tokens),
        ("Total tokens", baseline.total_tokens, optimized.total_tokens),
        ("Citation score", "{:.0%}".format(baseline.citation_score), "{:.0%}".format(optimized.citation_score)),
        ("Section coverage", "{:.0%}".format(baseline.section_score), "{:.0%}".format(optimized.section_score)),
    ]
    for label, before, after in rows:
        print("{:<24} {:>16} {:>16}".format(label, before, after))
    print("-" * 72)
    print("Token reduction: {:.1f}%".format(report.token_reduction_percent))
    print("Tool reduction:  {:.1f}%".format(report.tool_reduction_percent))
    print("Quality preserved: {}".format(report.to_dict()["improvement"]["quality_preserved"]))
    print("Selected skills: {}".format(", ".join(optimized.selected_skills)))
    print("Selected evidence: {}".format(", ".join(optimized.selected_sources)))
    print("\nOPTIMIZED PLAN\n" + "-" * 72)
    print(optimized.output)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.budget < 1000:
        raise SystemExit("--budget must be at least 1000 tokens")
    if args.context_budget < 100:
        raise SystemExit("--context-budget must be at least 100 tokens")
    intent = args.intent or (HERE / "sample_issue.md").read_text(encoding="utf-8")
    args.workspace.mkdir(parents=True, exist_ok=True)
    app = TokenEfficientEngineering(
        _provider(args.provider),
        load_knowledge(HERE / "knowledge.json"),
        workspace=args.workspace,
        max_parallel=args.max_parallel,
    )
    try:
        report = app.compare(
            intent,
            optimized_budget=args.budget,
            context_budget=args.context_budget,
        )
    except TokenBudgetExceeded as exc:
        raise SystemExit("Run was safely refused: {}".format(exc)) from exc

    payload = report.to_dict()
    report_path = args.workspace / "comparison.json"
    plan_path = args.workspace / "optimized-plan.md"
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    plan_path.write_text(report.optimized.output + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(report)
        print("\nArtifacts: {} and {}".format(report_path, plan_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

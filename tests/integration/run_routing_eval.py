"""Run the routing-eval corpus against `claude -p` and report results.

Each prompt declares which ce-lite layer (and optionally which persona) it
expects to fire. The runner spawns claude, parses stream-json, and asserts
the verdict matches expectation.

Designed for compressed dogfood — answers "do all the routing surfaces work
in my actual environment?" in ~20-40 minutes instead of 2 weeks of casual
use.

Usage:

    # Full corpus, realistic mode, 1 rep per case
    nix develop --command python tests/integration/run_routing_eval.py

    # Pick a subset by id-prefix
    python tests/integration/run_routing_eval.py --filter sec-

    # Multiple reps for stability (each rep is a fresh claude -p)
    python tests/integration/run_routing_eval.py --reps 3

    # Lite mode for faster, cheaper runs (less realistic)
    python tests/integration/run_routing_eval.py --mode lite

    # Parallelism (default: 4 workers)
    python tests/integration/run_routing_eval.py --workers 8

Cost: realistic mode runs ~80k context per call. With ~14 cases × 1 rep
≈ $0.70 quota equivalent. Reps multiply linearly. Lite mode is roughly
10× cheaper.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

# Allow running this script directly without an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.claude_runner import run_claude_p  # noqa: E402
from lib.stream_classifier import Layer  # noqa: E402

try:
    import yaml  # type: ignore
except ImportError:
    print(
        "Missing dependency 'pyyaml'. Run inside `nix develop` or install with "
        "`pip install pyyaml`.",
        file=sys.stderr,
    )
    sys.exit(2)


@dataclass
class Case:
    id: str
    mode: str  # "autonomous" | "explicit"
    prompt: str
    accepted_layers: list[str]
    accepted_personas: Optional[list[str]] = None


@dataclass
class CaseResult:
    case_id: str
    rep: int
    expected_layers: list[str]
    expected_personas: Optional[list[str]]
    actual_layer: str
    actual_persona: Optional[str]
    passed: bool
    duration_seconds: float
    timed_out: bool
    intermediate_layers: list[str]
    error: Optional[str] = None


def load_corpus(path: Path) -> list[Case]:
    raw = yaml.safe_load(path.read_text())
    return [Case(**c) for c in raw["cases"]]


def evaluate_case(
    case: Case, rep: int, mode: str, model: str, timeout: int
) -> CaseResult:
    short_circuit = case.accepted_layers != ["none"]
    # For negative cases we need to wait long enough to confirm Claude
    # didn't quietly invoke a specialist — short-circuiting on first non-ce
    # tool would be a false-pass. Cap at the timeout instead.
    res = run_claude_p(
        case.prompt,
        mode=mode,
        model=model,
        timeout=timeout,
        short_circuit=short_circuit,
    )

    actual_layer = res.verdict.layer.value
    actual_persona = res.verdict.persona

    if case.accepted_layers == ["none"]:
        # Pass iff no ce-lite layer fired during the entire turn.
        ce_lite_layers = {
            Layer.META_AGENT.value,
            Layer.META_SKILL.value,
            Layer.PANEL.value,
            Layer.WRAPPER.value,
        }
        passed = not any(
            layer.value in ce_lite_layers for layer in res.intermediate_layers
        )
    else:
        passed = actual_layer in case.accepted_layers
        if passed and case.accepted_personas:
            passed = (
                actual_persona in case.accepted_personas if actual_persona else False
            )

    return CaseResult(
        case_id=case.id,
        rep=rep,
        expected_layers=case.accepted_layers,
        expected_personas=case.accepted_personas,
        actual_layer=actual_layer,
        actual_persona=actual_persona,
        passed=passed,
        duration_seconds=res.duration_seconds,
        timed_out=res.timed_out,
        intermediate_layers=[layer.value for layer in res.intermediate_layers],
        error=res.error,
    )


def report(results: list[CaseResult], by_case_id: dict[str, Case]) -> int:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    rate = passed / total * 100 if total else 0.0
    duration_total = sum(r.duration_seconds for r in results)

    print()
    print("=" * 70)
    print("ce-lite Tier 3 routing eval")
    print("=" * 70)
    print()

    # Group by case for the per-case summary
    by_case: dict[str, list[CaseResult]] = {}
    for r in results:
        by_case.setdefault(r.case_id, []).append(r)

    for case_id in sorted(by_case):
        reps = by_case[case_id]
        case = by_case_id[case_id]
        rep_passed = sum(1 for r in reps if r.passed)
        status_icon = (
            "✅" if rep_passed == len(reps) else ("❌" if rep_passed == 0 else "⚠️ ")
        )
        layers_seen = sorted({r.actual_layer for r in reps})
        personas_seen = sorted({r.actual_persona for r in reps if r.actual_persona})
        rep_str = f"{rep_passed}/{len(reps)}"
        prompt_preview = textwrap.shorten(
            case.prompt.strip(), width=70, placeholder="…"
        )
        print(
            f"  {status_icon} [{case_id}] {rep_str} → layer={','.join(layers_seen)}"
            + (f" persona={','.join(personas_seen)}" if personas_seen else "")
        )
        print(f"     prompt: {prompt_preview}")
        if rep_passed < len(reps):
            print(
                f"     expected: layer in {case.accepted_layers}"
                + (
                    f", persona in {case.accepted_personas}"
                    if case.accepted_personas
                    else ""
                )
            )
        print()

    print("=" * 70)
    print(
        f"Total: {passed}/{total} pass ({rate:.0f}%) — {duration_total:.1f}s wall-clock"
    )
    print("=" * 70)

    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default="tests/integration/prompts.yaml")
    parser.add_argument(
        "--filter", default=None, help="only run cases whose id starts with this prefix"
    )
    parser.add_argument("--mode", choices=["realistic", "lite"], default="realistic")
    parser.add_argument("--model", default="claude-opus-4-7")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument(
        "--timeout",
        type=int,
        default=90,
        help="seconds per claude -p call (default 90)",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--results", default=None, help="optional JSONL path to dump per-case results"
    )
    args = parser.parse_args()

    corpus_path = Path(args.corpus).resolve()
    if not corpus_path.is_file():
        print(f"Corpus not found: {corpus_path}", file=sys.stderr)
        return 2

    cases = load_corpus(corpus_path)
    if args.filter:
        cases = [c for c in cases if c.id.startswith(args.filter)]
    if not cases:
        print("No cases match the filter.", file=sys.stderr)
        return 2

    by_case_id = {c.id: c for c in cases}
    print(
        f"Running {len(cases)} cases × {args.reps} reps "
        f"({len(cases) * args.reps} total) in {args.mode} mode "
        f"with {args.workers} workers",
        file=sys.stderr,
    )

    results: list[CaseResult] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = []
        for case in cases:
            for rep in range(args.reps):
                futures.append(
                    ex.submit(
                        evaluate_case, case, rep, args.mode, args.model, args.timeout
                    )
                )
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                r = fut.result()
            except Exception as e:
                print(f"  [{i}/{len(futures)}] worker error: {e}", file=sys.stderr)
                continue
            tag = "✅" if r.passed else "❌"
            print(
                f"  [{i}/{len(futures)}] {tag} {r.case_id} rep={r.rep} "
                f"({r.duration_seconds:.1f}s) → {r.actual_layer}",
                file=sys.stderr,
            )
            results.append(r)

    if args.results:
        Path(args.results).write_text(
            "\n".join(json.dumps(asdict(r)) for r in results) + "\n"
        )

    return report(results, by_case_id)


if __name__ == "__main__":
    sys.exit(main())

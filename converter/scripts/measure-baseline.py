#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""A/B measure idle-context cost: upstream CE vs ce-lite vs bare.

The `generate_wrappers.py` docstring claims ~71k tokens of idle context savings
from ce-lite's deferred-loading design, conditional on Claude Code loading agent
bodies into the registration slot. This script answers the open question
empirically by running `claude -p` against three isolated config dirs and
reading `usage.input_tokens` from the JSON envelope.

Invocation pattern follows ~/.claude/memory/claude_p_headless_subscription.md:
NO `--system-prompt` (we want the default Claude Code system prompt loaded
so plugin descriptions show up in idle context), NO `--setting-sources ""`
(we want plugin install state loaded), but the cheap-and-quiet env vars on
to keep auto-memory / cron / CLAUDE.md walking out of the measurement.

The three measurements:

  bare:        no plugin installed                  → harness-only baseline
  upstream-ce: only EveryInc/compound-engineering   → upstream's idle cost
  ce-lite:     only ak2k/ce-lite                    → ce-lite's idle cost

Deltas:

  upstream-ce - bare        = upstream's plugin idle cost
  ce-lite     - bare        = ce-lite's plugin idle cost
  upstream-ce - ce-lite     = the savings ce-lite delivers

Run with one of:

  python converter/scripts/measure-baseline.py
      # uses the default CLAUDE_CONFIG_DIR paths below

  python converter/scripts/measure-baseline.py \\
      --bare ~/.claude-isolate-bare \\
      --upstream-ce ~/.claude-isolate-upstream-ce \\
      --ce-lite ~/.claude-isolate-ce-lite

ONE-TIME SETUP (per config dir, manual):

  # 1. Create the three isolated config dirs
  mkdir -p ~/.claude-isolate-bare ~/.claude-isolate-upstream-ce ~/.claude-isolate-ce-lite

  # 2. In a fresh Claude Code session, point CLAUDE_CONFIG_DIR at each one in turn
  #    and install the appropriate plugin:
  #
  #    CLAUDE_CONFIG_DIR=~/.claude-isolate-upstream-ce claude
  #      /plugins marketplace add github:EveryInc/compound-engineering-plugin
  #      /plugins install compound-engineering@compound-engineering-plugin
  #      exit
  #
  #    CLAUDE_CONFIG_DIR=~/.claude-isolate-ce-lite claude
  #      /plugins marketplace add github:ak2k/ce-lite
  #      /plugins install ce-lite@ce-lite
  #      exit
  #
  #    (~/.claude-isolate-bare stays empty)
  #
  # 3. Re-run this script. Measurements should be reproducible across runs
  #    (within a cache window — see claude_p_headless_subscription.md).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

USER_PROMPT = "Return exactly the text: ok"
MAX_BUDGET_USD = 0.10  # cheap per call; ~3 runs × $0.05 = ~$0.15 quota total
MODEL = "claude-sonnet-4-6"  # full ID — short alias inherits parent context mode


@dataclass
class Measurement:
    label: str
    config_dir: Path
    input_tokens: int | None
    cache_creation_tokens: int
    cache_read_tokens: int
    error: str | None


def measure(label: str, config_dir: Path) -> Measurement:
    """One claude -p invocation against an isolated CLAUDE_CONFIG_DIR."""
    if not config_dir.is_dir():
        return Measurement(
            label, config_dir, None, 0, 0, f"config dir missing: {config_dir}"
        )

    env = os.environ.copy()
    env.update(
        {
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
            "CLAUDE_CODE_DISABLE_CRON": "1",
            "CLAUDE_CODE_DISABLE_AUTO_UPDATE": "1",
            "CLAUDE_CODE_DISABLE_TELEMETRY": "1",
        }
    )
    cmd = [
        "claude",
        "-p",
        USER_PROMPT,
        "--model",
        MODEL,
        "--output-format",
        "json",
        "--no-session-persistence",
        "--max-budget-usd",
        str(MAX_BUDGET_USD),
    ]

    # Run from /tmp so no project CLAUDE.md gets picked up.
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=env, cwd="/tmp", timeout=90
        )
    except subprocess.TimeoutExpired:
        return Measurement(label, config_dir, None, 0, 0, "timeout")

    if result.returncode != 0:
        return Measurement(
            label,
            config_dir,
            None,
            0,
            0,
            f"claude -p exit {result.returncode}: {result.stderr[:200]}",
        )

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return Measurement(label, config_dir, None, 0, 0, f"bad JSON: {exc}")

    usage = envelope.get("usage", {})
    return Measurement(
        label=label,
        config_dir=config_dir,
        input_tokens=usage.get("input_tokens"),
        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        error=None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    home = Path.home()
    parser.add_argument(
        "--bare",
        type=Path,
        default=home / ".claude-isolate-bare",
        help="Isolated CLAUDE_CONFIG_DIR with NO plugins installed",
    )
    parser.add_argument(
        "--upstream-ce",
        type=Path,
        default=home / ".claude-isolate-upstream-ce",
        help="Isolated CLAUDE_CONFIG_DIR with ONLY upstream CE installed",
    )
    parser.add_argument(
        "--ce-lite",
        type=Path,
        default=home / ".claude-isolate-ce-lite",
        help="Isolated CLAUDE_CONFIG_DIR with ONLY ak2k/ce-lite installed",
    )
    args = parser.parse_args()

    runs = [
        measure("bare", args.bare),
        measure("upstream-ce", args.upstream_ce),
        measure("ce-lite", args.ce_lite),
    ]

    print()
    print("=" * 78)
    print(
        f"{'variant':<14} {'input_tokens':>15} {'cache_creation':>15} {'cache_read':>15}"
    )
    print("-" * 78)
    for r in runs:
        if r.error:
            print(f"{r.label:<14} ERROR: {r.error}")
            continue
        print(
            f"{r.label:<14} {str(r.input_tokens):>15} "
            f"{r.cache_creation_tokens:>15,} {r.cache_read_tokens:>15,}"
        )
    print("=" * 78)
    print()

    by_label = {r.label: r for r in runs if r.error is None}
    if {"bare", "upstream-ce", "ce-lite"}.issubset(by_label.keys()):
        bare = by_label["bare"].input_tokens
        up = by_label["upstream-ce"].input_tokens
        ce = by_label["ce-lite"].input_tokens
        if all(v is not None for v in (bare, up, ce)):
            print("Deltas (input_tokens):")
            print(
                f"  upstream-ce - bare     = {up - bare:>8,}   (upstream's plugin idle cost)"
            )
            print(
                f"  ce-lite     - bare     = {ce - bare:>8,}   (ce-lite's plugin idle cost)"
            )
            print(
                f"  upstream-ce - ce-lite  = {up - ce:>8,}   (savings ce-lite delivers)"
            )
            print()
            print("Caveats:")
            print("  - input_tokens here is what claude -p reports for the round-trip;")
            print(
                "    cache_creation_tokens better reflects what the model SEES as new"
            )
            print("    context on a cold call. Re-run within the cache window to see")
            print("    cache_read_tokens dominate.")
            print("  - The default Claude Code system prompt (which loads plugin")
            print("    descriptions) is what we want measured — that's why this script")
            print(
                "    intentionally does NOT pass --system-prompt or --setting-sources ''."
            )

    has_error = any(r.error for r in runs)
    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())

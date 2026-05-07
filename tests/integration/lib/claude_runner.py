"""Spawn `claude -p` and collect routing/behavior signals from the stream.

Single-purpose runner that:
  1. Invokes `claude -p` with stream-json output
  2. Parses events as they arrive
  3. Short-circuits on the first ce-lite routing tool_use (saves quota by not
     waiting for the full turn to complete after the routing decision is made)
  4. Returns a structured Verdict

Two modes:
  - `realistic`: run with the user's full Claude Code env. Measures what
    actually happens when a real prompt is typed in the user's session.
    Closest to dogfood; expensive context (~80k per call).
  - `lite`: run with --setting-sources "" + CLAUDE_CODE_DISABLE_* env vars
    (per ~/.claude/memory/claude_p_headless_subscription.md). Uses a tmpdir
    cwd so no project .claude/ contaminates. Cheaper but doesn't see the
    user's other plugins, so it's a measure of ce-lite's intrinsic routing
    rather than its real-environment routing. Good for fast iteration once
    realistic confirms the basics.

The default is `realistic` because the dogfood-replacement question we're
answering is "does this work in MY environment", not "does this work in
isolation." Lite mode is for diagnostic when realistic shows surprises.
"""

from __future__ import annotations

import os
import select
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .stream_classifier import Layer, Verdict, classify_event


@dataclass
class RunResult:
    verdict: Verdict
    duration_seconds: float
    timed_out: bool
    raw_events_seen: int
    error: Optional[str] = None
    intermediate_layers: list[Layer] = field(default_factory=list)
    # All the non-NONE layers that fired during the turn, in order. Useful
    # for diagnosing cases where Claude tried something then redirected.


def run_claude_p(
    prompt: str,
    *,
    mode: str = "realistic",
    model: str = "claude-opus-4-7",
    timeout: int = 90,
    short_circuit: bool = True,
) -> RunResult:
    """Spawn `claude -p` and return the routing verdict.

    Args:
      prompt: the user message to test.
      mode: "realistic" (full env) or "lite" (stripped context).
      model: passed through to `--model`.
      timeout: per-call ceiling in seconds. Hard kill if exceeded.
      short_circuit: if True, return as soon as the first ce-lite-routing
        tool_use is observed (saves quota). If False, run to completion.
        Use False for behavior testing (need full output) or for negative
        cases where we need to confirm NOTHING fires.
    """
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--model", model,
    ]
    env = {**os.environ}
    env.pop("CLAUDECODE", None)  # allow nesting subprocess inside Claude session

    cwd_ctx: Optional[tempfile.TemporaryDirectory] = None
    cwd_path: Optional[str] = None

    if mode == "lite":
        cmd.extend(["--setting-sources", ""])
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
        env["CLAUDE_CODE_DISABLE_CRON"] = "1"
        cwd_ctx = tempfile.TemporaryDirectory(prefix="ce-lite-eval-")
        cwd_path = cwd_ctx.name
    elif mode == "realistic":
        # Use a tmpdir as CWD anyway, so test doesn't write project files.
        # User-level settings still load (we want them to — that's the point
        # of "realistic"), but project-level .claude/ is empty.
        cwd_ctx = tempfile.TemporaryDirectory(prefix="ce-lite-eval-")
        cwd_path = cwd_ctx.name
    else:
        raise ValueError(f"unknown mode {mode!r}; want 'realistic' or 'lite'")

    start = time.monotonic()
    timed_out = False
    error: Optional[str] = None
    final_verdict = Verdict(layer=Layer.NONE, persona=None, raw_tool=None, raw_input=None)
    intermediates: list[Layer] = []
    events_seen = 0

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd_path,
            env=env,
            text=False,
        )
    except FileNotFoundError as e:
        if cwd_ctx:
            cwd_ctx.cleanup()
        return RunResult(
            verdict=final_verdict,
            duration_seconds=0.0,
            timed_out=False,
            raw_events_seen=0,
            error=f"claude CLI not found: {e}",
        )

    buffer = b""
    try:
        while True:
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                timed_out = True
                break

            if proc.poll() is not None:
                # Process exited; drain remaining output.
                try:
                    rest = proc.stdout.read()  # type: ignore[union-attr]
                except Exception:
                    rest = b""
                if rest:
                    buffer += rest
                break

            ready, _, _ = select.select([proc.stdout], [], [], 1.0)
            if not ready:
                continue
            try:
                chunk = os.read(proc.stdout.fileno(), 8192)  # type: ignore[union-attr]
            except OSError:
                break
            if not chunk:
                continue
            buffer += chunk

            # Process complete lines.
            while b"\n" in buffer:
                line_b, buffer = buffer.split(b"\n", 1)
                try:
                    line = line_b.decode("utf-8", errors="replace")
                except Exception:
                    continue
                events_seen += 1
                v = classify_event(line)
                if v is None:
                    continue
                if v.layer != Layer.NONE:
                    intermediates.append(v.layer)
                    final_verdict = v
                    if short_circuit:
                        # Got a ce-lite layer match; we have our answer.
                        raise _StopReading
                else:
                    # NONE means a non-ce-lite tool fired (Read/Bash/etc.).
                    # Track it so we can tell "Claude did the work himself"
                    # apart from "Claude never ran any tool at all".
                    intermediates.append(Layer.NONE)
    except _StopReading:
        pass
    finally:
        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        if cwd_ctx:
            cwd_ctx.cleanup()

    duration = time.monotonic() - start
    return RunResult(
        verdict=final_verdict,
        duration_seconds=duration,
        timed_out=timed_out,
        raw_events_seen=events_seen,
        error=error,
        intermediate_layers=intermediates,
    )


class _StopReading(Exception):
    """Internal sentinel to break out of the read loop on short-circuit."""
    pass

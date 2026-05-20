"""Classify which ce-lite layer fires from `claude -p --output-format stream-json` events.

Tier 3 has four routing surfaces. Each shows up differently in the stream:

  - `ce-specialist` agent → tool_use(name=Task, input.subagent_type="ce-specialist")
  - `ce-ask-<persona>` wrappers → tool_use(name=Skill, input.skill="ce-ask-<persona>")
  - `ce-ask` meta-skill → tool_use(name=Skill, input.skill="ce-ask")
  - `ce-ask-panel` → tool_use(name=Skill, input.skill="ce-ask-panel")

Anything else (Read, Bash, Grep, etc.) is "did the work directly without
specialist" — what we explicitly want for negative test cases.

We short-circuit on the first relevant tool_use event rather than waiting for
the full turn to finish — the routing decision is made by then, and waiting
for completion costs quota for output we don't need.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Layer(Enum):
    NONE = "none"  # no specialist fired (Claude handled directly)
    META_AGENT = "ce-specialist"  # registered agent route
    META_SKILL = "ce-ask"  # discovery skill route
    PANEL = "ce-ask-panel"  # parallel multi-persona
    WRAPPER = "ce-ask-wrapper"  # one of the 49 per-persona wrappers
    UNKNOWN = "unknown"  # tool_use happened but didn't match any expected pattern


@dataclass
class Verdict:
    layer: Layer
    persona: Optional[str]  # for META_AGENT / WRAPPER, which persona was selected
    raw_tool: Optional[str]  # the actual tool name that fired (Task / Skill / etc.)
    raw_input: Optional[dict]  # the tool input — useful for diagnostics


def classify_event(line: str) -> Optional[Verdict]:
    """Parse one stream-json line. Return Verdict if it's a routing-relevant
    tool_use start; None otherwise.

    We look at `content_block_start` events with `type=tool_use` — those mark
    the moment Claude commits to a routing decision. Subsequent
    `content_block_delta` events on the same block fill in the input
    progressively, but the tool name + subagent_type is set at start time.
    """
    line = line.strip()
    if not line:
        return None
    try:
        evt = json.loads(line)
    except json.JSONDecodeError:
        return None

    # claude-code stream-json wraps the SDK event in a `stream_event` envelope
    if evt.get("type") == "stream_event":
        inner = evt.get("event", {})
        if inner.get("type") != "content_block_start":
            return None
        block = inner.get("content_block", {})
        if block.get("type") != "tool_use":
            return None
        tool_name = block.get("name", "")
        tool_input = block.get("input", {}) or {}
        return _classify_tool_use(tool_name, tool_input)

    return None


def _classify_tool_use(tool_name: str, tool_input: dict) -> Verdict:
    if tool_name in ("Task", "Agent"):
        subagent = (tool_input.get("subagent_type") or "").strip()
        if subagent == "ce-specialist":
            # The persona is named in the prompt body via persona=<name>;
            # we surface it for diagnostics if present.
            prompt = tool_input.get("prompt", "")
            persona = _extract_persona_arg(prompt)
            return Verdict(
                layer=Layer.META_AGENT,
                persona=persona,
                raw_tool=tool_name,
                raw_input=tool_input,
            )
        # Some other subagent_type (general-purpose, etc.) — not a ce-lite route
        return Verdict(
            layer=Layer.NONE,
            persona=None,
            raw_tool=tool_name,
            raw_input=tool_input,
        )

    if tool_name == "Skill":
        skill_name = (tool_input.get("skill") or tool_input.get("name") or "").strip()
        if skill_name == "ce-ask":
            return Verdict(
                layer=Layer.META_SKILL,
                persona=None,
                raw_tool=tool_name,
                raw_input=tool_input,
            )
        if skill_name == "ce-ask-panel":
            return Verdict(
                layer=Layer.PANEL,
                persona=None,
                raw_tool=tool_name,
                raw_input=tool_input,
            )
        if skill_name.startswith("ce-ask-"):
            persona = "ce-" + skill_name[len("ce-ask-") :]
            return Verdict(
                layer=Layer.WRAPPER,
                persona=persona,
                raw_tool=tool_name,
                raw_input=tool_input,
            )

    # Some other tool fired (Read, Bash, Grep, Edit, etc.) — Claude is doing
    # the work directly. From our routing-evaluation perspective, that's "no
    # specialist fired" — return NONE so the caller knows to keep watching.
    # Note: this is NOT a terminal verdict; the caller should ignore NONE
    # results unless the stream ends without ever producing a layer match.
    return Verdict(
        layer=Layer.NONE,
        persona=None,
        raw_tool=tool_name,
        raw_input=tool_input,
    )


_PERSONA_ARG_RE = None  # lazy init to avoid module-level regex import


def _extract_persona_arg(prompt: str) -> Optional[str]:
    """Extract `persona=<name>` from a meta-agent prompt, if present.

    Format is convention-only: the meta-agent's body asks Claude to encode
    persona selection as `persona=<name>` in the prompt it passes to Task.
    Tolerant of quotes and trailing punctuation.
    """
    global _PERSONA_ARG_RE
    if _PERSONA_ARG_RE is None:
        import re

        _PERSONA_ARG_RE = re.compile(
            r'persona\s*=\s*["\']?(ce-[a-z][a-z0-9-]*)["\']?',
            re.IGNORECASE,
        )
    if not prompt:
        return None
    m = _PERSONA_ARG_RE.search(prompt)
    return m.group(1) if m else None

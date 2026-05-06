"""Regex patterns for detecting CE agent name references in skill prose.

The pattern is the entry point for fail-loud detection: any token in a skill
file that matches `AGENT_REFERENCE_RE` is checked against the manifest of
extracted agents. A match that's NOT in the manifest is a stray mention,
flagged by rewrite.py and (if confirmed as a dispatch site) failed by
validate.py.

The shape: `ce-<words>-<persona-suffix>` where persona-suffix is one of the
agent role suffixes upstream CE uses. Skill names (ce-code-review,
ce-brainstorm, ce-plan, ce-work, etc.) end in verb-like roots and are excluded.

If upstream introduces a new agent role suffix, extend the suffix list below.
That's the maintenance vector: every CE upstream release that adds a brand-new
suffix breaks the build until the suffix is added here.
"""

from __future__ import annotations

import re

# Persona suffixes observed in CE v3.6.1 (49 agents). Sorted for diff stability.
PERSONA_SUFFIXES: list[str] = sorted([
    "agent",
    "analyst",
    "analyzer",
    "detector",
    "expert",
    "guardian",
    "historian",
    "hunter",
    "iterator",
    "oracle",
    "researcher",
    "resolver",
    "reviewer",
    "sentinel",
    "specialist",
    "strategist",
    "sync",
    "writer",
])

# `ce-` + at least one mid-segment + `-` + persona-suffix, on a word boundary.
# Examples that match: ce-security-reviewer, ce-data-integrity-guardian,
# ce-figma-design-sync, ce-deployment-verification-agent.
# Examples that don't: ce-code-review, ce-brainstorm, ce-plan (skill names).
AGENT_REFERENCE_RE: re.Pattern[str] = re.compile(
    # Greedy `*` so that names containing one suffix as a middle segment
    # (e.g. `ce-scope-guardian-reviewer` — `guardian` is a suffix) match the
    # longest valid persona name. Non-greedy `*?` would prematurely match
    # `ce-scope-guardian` and miss the real `-reviewer` tail.
    r"\bce-[a-z][a-z0-9-]*-(?:" + "|".join(PERSONA_SUFFIXES) + r")\b"
)

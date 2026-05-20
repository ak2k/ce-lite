"""Rewrite orchestrator skills to use lightweight-delegation dispatch.

Each SKILL.md that mentions any agent name from the manifest gets a uniform
"dispatch protocol" preamble inserted after its frontmatter. The preamble tells
the runtime how to load the relocated prompt files and pass them to a
general-purpose Agent dispatch instead of looking up `subagent_type: ce-*`.

The skill body itself is not modified — agent name references in tables,
descriptive prose, and status messages remain as documentation. Only the
dispatch protocol changes.

Idempotent: detects the preamble marker and skips re-insertion.

Fails loud if any orchestrator skill references an agent-shaped token (matches
DISPATCH_PATTERNS) whose name is not in the manifest. That's the upstream-drift
detector — when CE introduces a new agent the converter doesn't know about,
the build breaks here rather than silently shipping.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from DISPATCH_PATTERNS import AGENT_REFERENCE_RE

PREAMBLE_MARKER_BEGIN = "<!-- ce-lite: dispatch protocol begin -->"
PREAMBLE_MARKER_END = "<!-- ce-lite: dispatch protocol end -->"

PREAMBLE = f"""\
{PREAMBLE_MARKER_BEGIN}

> **ce-lite dispatch protocol.** Persona names referenced below
> (`ce-security-reviewer`, `ce-correctness-reviewer`,
> `ce-learnings-researcher`, …) are NOT registered subagent types in this
> variant — they're data files at `references/agent-prompts/<name>.md`. To
> dispatch one:
>
> 1. Run `ce-lite-persona <persona-name> --body` via Bash. The resolver is
>    on PATH (this plugin's `bin/` is exported by Claude Code) and prints
>    the persona's full role prompt to stdout. Non-zero exit means an
>    unknown persona or partial install — the resolver's stderr explains;
>    surface it and stop. Do not silently fall back to a different persona.
>
> 2. Spawn an `Agent` (or your harness's equivalent) with `subagent_type:
>    "general-purpose"` and a meaningful
>    `description: "<persona-name>: <one-line task summary>"` so traces
>    stay readable. The prompt is the resolver output + this skill's
>    existing context bundle (intent, diff, base, file list, etc.) +
>    output schema, in that order.
>
> 3. Apply any dispatch-time options the skill specifies for the original
>    named agent (model override, parallel-scheduler limits, etc.). Tool
>    constraints are advisory in this variant — pass them inline in the
>    dispatched prompt.
>
> 4. **Do not** call `Agent({{subagent_type: "ce-<name>"}})` — those
>    registrations don't exist in this variant.
>
> Persona names elsewhere in this skill (descriptive prose, tables, status
> messages) are documentation; only dispatch sites change.

{PREAMBLE_MARKER_END}
"""

# Frontmatter at the top of a SKILL.md is fenced by --- lines.
FRONTMATTER_FENCE_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class StrayMention:
    file: Path
    name: str
    line: int


def load_manifest(dist_dir: Path) -> set[str]:
    manifest_path = dist_dir / "references" / "agent-prompts" / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {a["name"] for a in data["agents"]}


def find_agent_references(text: str) -> list[tuple[str, int]]:
    """Return [(name, line_number), ...] for every agent-shaped token in text."""
    refs: list[tuple[str, int]] = []
    for line_num, line in enumerate(text.splitlines(), start=1):
        for m in AGENT_REFERENCE_RE.finditer(line):
            refs.append((m.group(0), line_num))
    return refs


def insert_preamble(skill_text: str) -> str:
    """Insert the ce-lite preamble after the frontmatter. Idempotent."""
    if PREAMBLE_MARKER_BEGIN in skill_text:
        return skill_text  # already converted

    fm_match = FRONTMATTER_FENCE_RE.match(skill_text)
    if not fm_match:
        # No frontmatter — should not happen for a real SKILL.md
        raise ValueError("SKILL.md has no frontmatter; refusing to rewrite")

    fm_end = fm_match.end()
    return skill_text[:fm_end] + "\n" + PREAMBLE + "\n" + skill_text[fm_end:]


def rewrite_skill(skill_path: Path, manifest_names: set[str]) -> tuple[bool, list[StrayMention]]:
    """Rewrite one SKILL.md if it references any manifest agent.

    Returns (was_modified, stray_mentions). stray_mentions are agent-shaped
    tokens that don't appear in the manifest — caller decides whether to fail.
    """
    text = skill_path.read_text(encoding="utf-8")
    refs = find_agent_references(text)

    referenced_names = {name for name, _ in refs}
    strays: list[StrayMention] = [
        StrayMention(file=skill_path, name=name, line=line)
        for name, line in refs
        if name not in manifest_names
    ]

    has_known_refs = bool(referenced_names & manifest_names)
    if not has_known_refs:
        return False, strays

    new_text = insert_preamble(text)
    if new_text != text:
        skill_path.write_text(new_text, encoding="utf-8")
        return True, strays
    return False, strays


def main(dist_dir: str) -> int:
    dist_path = Path(dist_dir).resolve()
    skills_dir = dist_path / "skills"
    if not skills_dir.is_dir():
        print(f"error: no skills/ directory at {skills_dir}", file=sys.stderr)
        return 1

    manifest_names = load_manifest(dist_path)
    print(f"manifest has {len(manifest_names)} agents", file=sys.stderr)

    modified_count = 0
    skipped_count = 0
    all_strays: list[StrayMention] = []

    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        modified, strays = rewrite_skill(skill_md, manifest_names)
        all_strays.extend(strays)
        if modified:
            modified_count += 1
            print(f"  rewrote {skill_md.relative_to(dist_path)}", file=sys.stderr)
        else:
            skipped_count += 1

    print(
        f"rewrite.py: {modified_count} skills rewritten, "
        f"{skipped_count} skills skipped (no agent references)",
        file=sys.stderr,
    )

    if all_strays:
        print(
            "\n*** STRAY AGENT MENTIONS — names matching the agent-reference "
            "pattern that are NOT in the manifest:",
            file=sys.stderr,
        )
        # Deduplicate by (file, name) for compact output, but keep first line.
        seen: set[tuple[str, str]] = set()
        unique_strays: list[StrayMention] = []
        for s in all_strays:
            key = (str(s.file), s.name)
            if key not in seen:
                seen.add(key)
                unique_strays.append(s)
        for s in unique_strays:
            rel = s.file.relative_to(dist_path)
            print(f"  {rel}:{s.line}: {s.name}", file=sys.stderr)
        print(
            "\nThese references could indicate (a) upstream typo / stale ref "
            "(no action needed beyond noting), (b) a new agent type the "
            "converter doesn't know about (extend AGENT_REFERENCE_RE in "
            "DISPATCH_PATTERNS.py if the suffix is missing), or (c) a bug in "
            "the agent-name extraction. validate.py will fail the build if "
            "these turn out to be real dispatch sites.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: rewrite.py <dist-dir>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))

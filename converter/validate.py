"""Structural validation suite for converted plugin output.

Run after extract.py + rewrite.py. Asserts:

1. dist/ has no agents/ directory (we killed the persistent registrations).
2. .claude-plugin/plugin.json parses and has required fields.
3. references/agent-prompts/manifest.json count matches actual prompt-file count.
4. No orphan prompt files (every file in agent-prompts/ is in the manifest).
5. No orphan manifest entries (every manifest entry has a real file).
6. No prompt file is empty or absurdly short.
7. Every SKILL.md has valid frontmatter.
8. Every orchestrator skill (mentions agents from manifest) has the dispatch
   preamble inserted — this is the "fail loud if rewrite.py missed a skill" check.
9. No stray agent references — every token matching AGENT_REFERENCE_RE in any
   SKILL.md must be in the manifest. Strays here indicate either upstream-drift
   (new agent type whose name shape isn't covered by the regex) or bug in
   extraction. Either way, the build fails until investigated.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from DISPATCH_PATTERNS import AGENT_REFERENCE_RE
from rewrite import PREAMBLE_MARKER_BEGIN

FRONTMATTER_FENCE_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
MIN_PROMPT_BYTES = 200  # smallest known agent prompt is ~1KB; 200 catches truncation


class ValidationError(Exception):
    pass


def fail(msg: str) -> None:
    raise ValidationError(msg)


def check_no_agents_dir(dist: Path) -> None:
    agents_dir = dist / "agents"
    if agents_dir.exists():
        fail(
            f"dist/agents/ exists ({agents_dir}); ce-lite must not ship persistent "
            f"agent registrations. extract.py should remove this dir."
        )


def check_plugin_json(dist: Path) -> dict:
    plugin_json = dist / ".claude-plugin" / "plugin.json"
    if not plugin_json.is_file():
        fail(f"missing {plugin_json}")
    try:
        data = json.loads(plugin_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{plugin_json}: invalid JSON: {exc}")
    for key in ("name", "version", "description"):
        if key not in data:
            fail(f"{plugin_json}: missing required key {key!r}")
    if not data["name"].startswith("ce-lite"):
        fail(
            f"{plugin_json}: 'name' is {data['name']!r}, expected to start with 'ce-lite'"
        )
    return data


def check_manifest(dist: Path) -> set[str]:
    manifest_path = dist / "references" / "agent-prompts" / "manifest.json"
    if not manifest_path.is_file():
        fail(f"missing {manifest_path}")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{manifest_path}: invalid JSON: {exc}")
    if data.get("schema_version") != 1:
        fail(f"{manifest_path}: unexpected schema_version {data.get('schema_version')!r}")

    agents = data.get("agents") or []
    if not agents:
        fail(f"{manifest_path}: empty agents list")

    names_in_manifest: set[str] = set()
    prompt_dir = dist / "references" / "agent-prompts"
    for entry in agents:
        name = entry.get("name")
        if not name:
            fail(f"{manifest_path}: agent entry missing 'name'")
        if name in names_in_manifest:
            fail(f"{manifest_path}: duplicate agent name {name!r}")
        names_in_manifest.add(name)

        prompt_path = entry.get("prompt_path")
        if not prompt_path:
            fail(f"{manifest_path}: agent {name!r} missing 'prompt_path'")
        prompt_file = dist / prompt_path
        if not prompt_file.is_file():
            fail(f"{manifest_path}: agent {name!r} prompt_path does not exist: {prompt_file}")

        size = prompt_file.stat().st_size
        if size < MIN_PROMPT_BYTES:
            fail(
                f"{prompt_file}: only {size} bytes — prompt looks empty/truncated "
                f"(min expected: {MIN_PROMPT_BYTES})"
            )

    actual_files = sorted(prompt_dir.glob("*.md"))
    actual_names = {p.stem for p in actual_files}
    if actual_names != names_in_manifest:
        only_in_files = actual_names - names_in_manifest
        only_in_manifest = names_in_manifest - actual_names
        msg = []
        if only_in_files:
            msg.append(f"orphan files (in dir, not in manifest): {sorted(only_in_files)}")
        if only_in_manifest:
            msg.append(f"orphan manifest entries (in manifest, no file): {sorted(only_in_manifest)}")
        fail(f"{manifest_path}: manifest/file mismatch: {'; '.join(msg)}")

    return names_in_manifest


def check_skills(dist: Path, manifest_names: set[str]) -> None:
    skills_dir = dist / "skills"
    if not skills_dir.is_dir():
        fail(f"missing {skills_dir}")

    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")

        if not FRONTMATTER_FENCE_RE.match(text):
            fail(f"{skill_md}: missing or malformed frontmatter")

        # Strays: agent-shaped tokens not in manifest
        strays_found: dict[str, int] = {}
        for line_num, line in enumerate(text.splitlines(), start=1):
            for m in AGENT_REFERENCE_RE.finditer(line):
                name = m.group(0)
                if name not in manifest_names:
                    strays_found.setdefault(name, line_num)
        if strays_found:
            details = "; ".join(f"{name} (line {ln})" for name, ln in strays_found.items())
            fail(
                f"{skill_md.relative_to(dist)}: stray agent references not in manifest: "
                f"{details}\n  Either upstream introduced a new persona-suffix shape "
                f"(extend PERSONA_SUFFIXES in DISPATCH_PATTERNS.py), or the extraction "
                f"missed an agent file."
            )

        # Orchestrator detection: skill mentions any manifest name → must have preamble
        # (excluding the preamble's own example mentions, which all contain manifest names)
        # We do this AFTER stray check so failures are attributed correctly.
        text_below_preamble = text
        if PREAMBLE_MARKER_BEGIN in text:
            # Strip the preamble for the orchestrator-detection check, so we
            # only count agent mentions in the actual skill body.
            from rewrite import PREAMBLE_MARKER_END

            begin = text.index(PREAMBLE_MARKER_BEGIN)
            end = text.index(PREAMBLE_MARKER_END) + len(PREAMBLE_MARKER_END)
            text_below_preamble = text[:begin] + text[end:]

        body_mentions = {
            m.group(0)
            for line in text_below_preamble.splitlines()
            for m in AGENT_REFERENCE_RE.finditer(line)
        }
        is_orchestrator = bool(body_mentions & manifest_names)

        has_preamble = PREAMBLE_MARKER_BEGIN in text
        if is_orchestrator and not has_preamble:
            fail(
                f"{skill_md.relative_to(dist)}: orchestrator skill (mentions "
                f"{sorted(body_mentions & manifest_names)[:3]}...) is missing the "
                f"ce-lite dispatch preamble. rewrite.py should have inserted it."
            )


def main(dist_dir: str) -> int:
    dist = Path(dist_dir).resolve()

    print(f"validate.py: dist={dist}", file=sys.stderr)

    try:
        check_no_agents_dir(dist)
        plugin_data = check_plugin_json(dist)
        print(f"  plugin: name={plugin_data['name']} version={plugin_data['version']}", file=sys.stderr)

        manifest_names = check_manifest(dist)
        print(f"  manifest: {len(manifest_names)} agents, all prompt files present", file=sys.stderr)

        check_skills(dist, manifest_names)
        print(f"  skills: all SKILL.md files structurally valid; orchestrators have preambles", file=sys.stderr)

    except ValidationError as exc:
        print(f"\n*** VALIDATION FAILED ***\n{exc}", file=sys.stderr)
        return 1

    print("validate.py: all checks passed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: validate.py <dist-dir>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))

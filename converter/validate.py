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

import argparse
import json
import re
import sys
from pathlib import Path

from DISPATCH_PATTERNS import AGENT_REFERENCE_RE
from extract import find_plugin_root, parse_frontmatter
from rewrite import PREAMBLE_MARKER_BEGIN, PREAMBLE_MARKER_END

FRONTMATTER_FENCE_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
MIN_PROMPT_BYTES = 200  # smallest known agent prompt is ~1KB; 200 catches truncation


class ValidationError(Exception):
    pass


def fail(msg: str) -> None:
    raise ValidationError(msg)


def check_agents_dir(dist: Path) -> None:
    """Allow only the ce-specialist meta-agent in dist/agents/.

    ce-lite v1 stripped all 49 individual persistent agent registrations to
    save ~58.8k idle tokens. Phase B.5 introduces a single allowed registration:
    `ce-specialist`, a ~2k-token router agent that internally dispatches to any
    persona based on the prompt. This restores autonomous-routing capability
    (the 0%-recall ceiling we measured on per-skill routing) at 30× the cost
    efficiency of upstream's design — well within the spirit of the original
    "minimal registrations" thesis.

    Anything other than ce-specialist.agent.md in dist/agents/ is a regression.
    """
    agents_dir = dist / "agents"
    if not agents_dir.exists():
        # Pre-Phase-B.5 dist or v1: no agents at all. Acceptable.
        return

    allowed = {"ce-specialist.agent.md"}
    actual = {p.name for p in agents_dir.iterdir() if p.is_file()}
    extras = actual - allowed
    if extras:
        fail(
            f"dist/agents/ contains agents beyond the allowed meta-agent: "
            f"{sorted(extras)}. ce-lite registers only ce-specialist as a "
            f"single router agent (Phase B.5 of Tier 3). extract.py should "
            f"strip everything else; generate_wrappers.py emits ce-specialist."
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


def collect_skill_names(skills_dir: Path) -> set[str]:
    """Collect every SKILL.md's `name:` field into a set.

    Skills can reference other skills by slash-command (e.g., a wrapper's body
    pointing at `/ce-ask-panel`). The strays check must know these are real
    skill identities, not unrecognized agent references — otherwise any
    cross-skill reference whose name happens to match the agent-suffix shape
    would falsely fail the build.
    """
    names: set[str] = set()
    for skill_md in skills_dir.glob("*/SKILL.md"):
        text = skill_md.read_text(encoding="utf-8")
        fm_match = FRONTMATTER_FENCE_RE.match(text)
        if not fm_match:
            continue
        try:
            fm, _ = parse_frontmatter(text)
        except ValueError:
            continue
        if name := fm.get("name"):
            names.add(name)
    return names


def check_skills(dist: Path, manifest_names: set[str]) -> None:
    skills_dir = dist / "skills"
    if not skills_dir.is_dir():
        fail(f"missing {skills_dir}")

    skill_names = collect_skill_names(skills_dir)
    allowed_names = manifest_names | skill_names

    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")

        fm_match = FRONTMATTER_FENCE_RE.match(text)
        if not fm_match:
            fail(f"{skill_md}: missing or malformed frontmatter")

        # Skip frontmatter when scanning for agent references — frontmatter
        # carries the skill's own name and metadata, not dispatch language.
        # Agent references that matter live in the body.
        fm_line_count = fm_match.group(0).count("\n")
        body_text = text[fm_match.end():]

        # Strays: agent-shaped tokens not in manifest or in any sibling skill's name
        strays_found: dict[str, int] = {}
        for body_line_idx, line in enumerate(body_text.splitlines()):
            line_num = fm_line_count + body_line_idx + 1
            for m in AGENT_REFERENCE_RE.finditer(line):
                name = m.group(0)
                if name not in allowed_names:
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
        # Operate on body_text (already frontmatter-stripped above).
        # Generated wrapper skills (Tier 3) are exempt — they carry their own
        # complete dispatch instructions in the body and don't need the
        # orchestrator preamble. They self-identify via the wrapper marker.
        is_generated_wrapper = "Generated by ce-lite converter" in body_text
        if is_generated_wrapper:
            continue

        text_below_preamble = body_text
        if PREAMBLE_MARKER_BEGIN in body_text:
            # Strip the preamble for the orchestrator-detection check, so we
            # only count agent mentions in the actual skill body.
            from rewrite import PREAMBLE_MARKER_END

            begin = body_text.index(PREAMBLE_MARKER_BEGIN)
            end = body_text.index(PREAMBLE_MARKER_END) + len(PREAMBLE_MARKER_END)
            text_below_preamble = body_text[:begin] + body_text[end:]

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


def normalize_body(text: str) -> str:
    """Trailing-whitespace normalization for body-equivalence checks.

    extract.py rstrips the body and adds a single trailing newline, so direct
    byte-for-byte comparison won't match. We strip both inputs the same way.
    """
    return text.rstrip() + "\n"


def check_round_trip(dist: Path, upstream: Path, manifest_names: set[str]) -> None:
    """Verify byte-equivalence between upstream and dist for content-bearing files.

    1. Agent body: upstream agents/<name>.agent.md body == dist references/agent-prompts/<name>.md
    2. Skill body: upstream skills/<name>/SKILL.md body == dist skills/<name>/SKILL.md body
       (where dist's body excludes the inserted ce-lite preamble if present).
    """
    plugin_root = find_plugin_root(upstream)

    # 1. Agent body equivalence
    upstream_agents = plugin_root / "agents"
    dist_prompts = dist / "references" / "agent-prompts"
    for name in sorted(manifest_names):
        upstream_file = upstream_agents / f"{name}.agent.md"
        dist_file = dist_prompts / f"{name}.md"
        if not upstream_file.is_file():
            fail(f"round-trip: upstream missing {upstream_file}")

        upstream_text = upstream_file.read_text(encoding="utf-8")
        try:
            _, upstream_body = parse_frontmatter(upstream_text)
        except ValueError as exc:
            fail(f"round-trip: cannot parse upstream {upstream_file}: {exc}")

        dist_body = dist_file.read_text(encoding="utf-8")

        if normalize_body(upstream_body) != normalize_body(dist_body):
            fail(
                f"round-trip: agent body mismatch for {name!r}\n"
                f"  upstream: {upstream_file}\n"
                f"  dist:     {dist_file}\n"
                f"  bodies differ after trailing-whitespace normalization"
            )

    # 2. Skill body equivalence (preamble-aware)
    upstream_skills = plugin_root / "skills"
    dist_skills = dist / "skills"
    for upstream_skill in sorted(upstream_skills.glob("*/SKILL.md")):
        skill_name = upstream_skill.parent.name
        dist_skill = dist_skills / skill_name / "SKILL.md"
        if not dist_skill.is_file():
            fail(f"round-trip: dist missing {dist_skill}")

        upstream_text = upstream_skill.read_text(encoding="utf-8")
        dist_text = dist_skill.read_text(encoding="utf-8")

        # Strip the ce-lite preamble (if present) from dist for fair comparison.
        if PREAMBLE_MARKER_BEGIN in dist_text:
            begin = dist_text.index(PREAMBLE_MARKER_BEGIN)
            end = dist_text.index(PREAMBLE_MARKER_END) + len(PREAMBLE_MARKER_END)
            # Also consume the surrounding blank-line padding rewrite.py adds:
            # "\n" + PREAMBLE + "\n" — drop one newline before begin and one
            # after end if they're whitespace-only padding.
            stripped = dist_text[:begin] + dist_text[end:]
            # Collapse the blank padding: one \n before, one \n after.
            stripped = re.sub(r"\n\n+\n", "\n\n", stripped, count=1)
        else:
            stripped = dist_text

        if upstream_text != stripped:
            # More forgiving comparison — allow trailing-whitespace drift on the
            # last line. If it still doesn't match, fail with line-level details.
            if normalize_body(upstream_text) == normalize_body(stripped):
                continue
            fail(
                f"round-trip: skill body mismatch for {skill_name!r}\n"
                f"  upstream: {upstream_skill}\n"
                f"  dist:     {dist_skill}\n"
                f"  body differs after preamble strip"
            )


def check_metadata_files_unchanged(dist: Path, upstream: Path) -> None:
    """Files copied verbatim by extract.py should match upstream byte-for-byte.

    Excludes .claude-plugin/plugin.json (intentionally rewritten).
    """
    plugin_root = find_plugin_root(upstream)
    skip = {".claude-plugin", "agents", "skills"}
    for entry in plugin_root.iterdir():
        if entry.name in skip:
            continue
        dist_entry = dist / entry.name
        if not dist_entry.exists():
            fail(f"round-trip: dist missing {dist_entry} (upstream had it)")
        if entry.is_file():
            if entry.read_bytes() != dist_entry.read_bytes():
                fail(f"round-trip: {entry.name} differs from upstream — extract.py mangled it?")


def main(dist_dir: str, upstream_dir: str | None = None) -> int:
    dist = Path(dist_dir).resolve()

    print(f"validate.py: dist={dist}", file=sys.stderr)

    try:
        check_agents_dir(dist)
        plugin_data = check_plugin_json(dist)
        print(f"  plugin: name={plugin_data['name']} version={plugin_data['version']}", file=sys.stderr)

        manifest_names = check_manifest(dist)
        print(f"  manifest: {len(manifest_names)} agents, all prompt files present", file=sys.stderr)

        check_skills(dist, manifest_names)
        print(f"  skills: all SKILL.md files structurally valid; orchestrators have preambles", file=sys.stderr)

        if upstream_dir:
            upstream = Path(upstream_dir).resolve()
            print(f"  round-trip: comparing dist against upstream={upstream}", file=sys.stderr)
            check_round_trip(dist, upstream, manifest_names)
            print(f"    agent bodies + skill bodies byte-equivalent", file=sys.stderr)
            check_metadata_files_unchanged(dist, upstream)
            print(f"    metadata files unchanged from upstream", file=sys.stderr)

    except ValidationError as exc:
        print(f"\n*** VALIDATION FAILED ***\n{exc}", file=sys.stderr)
        return 1

    print("validate.py: all checks passed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist_dir")
    parser.add_argument(
        "--upstream",
        help="upstream CE checkout to round-trip against (enables byte-equivalence checks)",
    )
    args = parser.parse_args()
    sys.exit(main(args.dist_dir, args.upstream))

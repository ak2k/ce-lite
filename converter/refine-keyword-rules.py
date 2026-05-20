#!/usr/bin/env python3
"""Pass B: refine UserPromptSubmit hook keyword rules per persona via `claude -p`.

Pass A (generate_wrappers.derive_keyword_rule) produces a deterministic
baseline keyword set from the manifest description. This script refines
that baseline by asking a Claude model to produce 5-10 high-precision
trigger keywords/phrases per persona, plus a one-line dispatch phrasing
message.

Output: `converter/overrides/persona-keywords.yaml`. Existing entries are
merged (not overwritten unless `--force` is given). The generator
(`render_hook_rules`) reads this YAML at build time and overlays it on
top of Pass A defaults.

Not a CI step — preserves the "no LLM in CI" invariant. Run locally
before cutting a release; commit the resulting YAML alongside the
converter changes.

Usage:
  python converter/refine-keyword-rules.py                # all personas
  python converter/refine-keyword-rules.py --filter sec   # subset (substring)
  python converter/refine-keyword-rules.py --dry-run      # plan only, no API calls
  python converter/refine-keyword-rules.py --workers 4    # parallelism (default 4)
  python converter/refine-keyword-rules.py --skip-existing  # don't re-refine

Cost: roughly $0.001-0.005 per persona at haiku tier (~$0.05-0.20 for all 49).
Latency: ~3-10s per call; with 4 workers, ~3-5 min wall clock for all 49.

Invocation pattern follows ~/.claude/memory/claude_p_headless_subscription.md:
lightweight `claude -p` with `--system-prompt`, `--setting-sources ""`,
JSON-schema-constrained structured output, and the
CLAUDE_CODE_DISABLE_* env vars to keep context minimal.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"
OVERRIDES_PATH = REPO_ROOT / "converter" / "overrides" / "persona-keywords.yaml"
PROMPT_BODY_CAP = 3000  # chars; keeps input bounded across diverse persona prompts


SYSTEM_PROMPT = """You are a precise keyword extractor for a slash-command auto-suggestion hook in the ce-lite Claude Code plugin.

When a user submits a prompt to Claude Code, a hook scans the prompt text for keywords and suggests invoking the corresponding specialist persona slash command (e.g., `/ce-lite:ce-ask-security-sentinel`). For each persona you analyze, produce:

  - 5-10 trigger keywords/phrases (case-insensitive substring match against the user's prompt)
  - A one-line dispatch phrasing message that suggests the slash command
  - A one-sentence rationale (for transcript review)

QUALITY RULES (strict):

1. Keywords should fire on REAL prompts a user might type when they need this specialist's expertise. Think: what would the user say?

2. Mix single words and phrases. Multi-word phrases (>= 2 words) are higher precision — favor them.

3. Single words must be >= 6 characters OR all-uppercase acronyms (the matcher uses case-insensitive substring; short generic words like "user", "auth", "code", "data" cause false positives via greedy substring matches).

4. Prefer specific domain terminology over generic English. Examples:
   - GOOD: "OWASP", "race condition", "off-by-one", "N+1 query", "hardcoded secret"
   - BAD: "security audit", "concurrency", "edge case", "performance", "secrets"

5. Avoid words that appear in almost every prompt: "code", "review", "check", "analyze", "implement", "fix", "help", "look at".

6. Keywords should be DISTINCTIVE to this persona. Don't include keywords that would also fire other personas in the manifest (the user will be told you have the full list as context).

PHRASING RULES:

The phrasing line is what gets injected as a system reminder when a keyword fires. Format:
  "<2-6 word framing of the prompt's flavor>. Consider `/ce-lite:<wrapper-name>` for <1-line of what the persona does>."

Example: "The prompt looks security-flavored. Consider running `/ce-lite:ce-ask-security-sentinel` (or invoking the wrapper via Skill) for a focused security review by a specialist grounded in OWASP / injection / authn-authz / secrets-handling expertise."

Keep phrasing under 280 characters.

RETURN the result strictly matching the supplied JSON schema. Do not add commentary outside the JSON."""


SCHEMA = {
    "type": "object",
    "properties": {
        "keywords": {
            "type": "array",
            "minItems": 5,
            "maxItems": 10,
            "items": {"type": "string", "minLength": 2, "maxLength": 60},
        },
        "phrasing": {
            "type": "string",
            "minLength": 20,
            "maxLength": 400,
        },
        "rationale": {
            "type": "string",
            "minLength": 10,
            "maxLength": 300,
        },
    },
    "required": ["keywords", "phrasing", "rationale"],
    "additionalProperties": False,
}


@dataclass
class PersonaInput:
    name: str
    description: str
    prompt_body: str


@dataclass
class RefinedRule:
    persona: str
    keywords: list[str]
    phrasing: str
    rationale: str


def load_personas(dist: Path) -> list[PersonaInput]:
    """Load persona manifest + prompt bodies for refinement input."""
    manifest_path = dist / "references" / "agent-prompts" / "manifest.json"
    if not manifest_path.is_file():
        sys.exit(f"missing manifest at {manifest_path}; run extract.py first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    out: list[PersonaInput] = []
    for a in manifest["agents"]:
        body_path = dist / a["prompt_path"]
        body = body_path.read_text(encoding="utf-8") if body_path.is_file() else ""
        out.append(
            PersonaInput(
                name=a["name"],
                description=a["description"],
                prompt_body=body[:PROMPT_BODY_CAP],
            )
        )
    return out


def refine_persona(persona: PersonaInput, model: str) -> RefinedRule | None:
    """Call `claude -p` for one persona; return the refined rule or None on error.

    None signals "skip this persona" — the caller continues with others.
    """
    user_input = (
        f"Persona: {persona.name}\n\n"
        f"Manifest description:\n{persona.description}\n\n"
        f"Persona prompt body (first {PROMPT_BODY_CAP} chars):\n"
        f"{persona.prompt_body}\n\n"
        f"Return refined keywords + phrasing + rationale per the schema."
    )

    env = os.environ.copy()
    env.update(
        {
            "CLAUDE_CODE_DISABLE_AUTO_UPDATE": "1",
            "CLAUDE_CODE_DISABLE_TELEMETRY": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
    )

    cmd = [
        "claude",
        "-p",
        user_input,
        "--model",
        model,
        "--setting-sources",
        "",
        "--system-prompt",
        SYSTEM_PROMPT,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(SCHEMA),
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=env, timeout=90
        )
    except subprocess.TimeoutExpired:
        print(f"  [{persona.name}] TIMEOUT", file=sys.stderr)
        return None
    except FileNotFoundError:
        sys.exit(
            "claude CLI not on PATH. Install Claude Code or run inside an env "
            "that exports it."
        )

    if result.returncode != 0:
        print(
            f"  [{persona.name}] claude -p exit {result.returncode}: "
            f"{result.stderr[:300]}",
            file=sys.stderr,
        )
        return None

    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(
            f"  [{persona.name}] could not parse stdout as JSON: {exc}",
            file=sys.stderr,
        )
        return None

    # `claude -p --json-schema` returns structured output under
    # `structured_output` (NOT `result`), per
    # ~/.claude/memory/claude_p_headless_subscription.md.
    data = response.get("structured_output") or {}
    if not isinstance(data, dict) or not data.get("keywords"):
        print(
            f"  [{persona.name}] response missing structured_output.keywords: "
            f"{str(response)[:200]}",
            file=sys.stderr,
        )
        return None

    return RefinedRule(
        persona=persona.name,
        keywords=[k.strip() for k in data["keywords"] if isinstance(k, str)],
        phrasing=str(data.get("phrasing", "")).strip(),
        rationale=str(data.get("rationale", "")).strip(),
    )


def merge_and_write(
    refined: list[RefinedRule],
    existing: dict,
    out_path: Path,
    *,
    force: bool,
) -> tuple[int, int]:
    """Merge refined rules into existing overrides; return (added, skipped)."""
    added = 0
    skipped = 0
    merged = dict(existing)
    for rule in refined:
        if not force and rule.persona in existing:
            skipped += 1
            continue
        merged[rule.persona] = {
            "keywords": rule.keywords,
            "phrasing": rule.phrasing,
            "_rationale": rule.rationale,
        }
        added += 1

    header = (
        "# Pass B overrides for skill-rules.json\n"
        "#\n"
        "# Format: one top-level key per persona (canonical name with ce- prefix).\n"
        "# Each entry can provide `keywords` (list) and/or `phrasing` (string) to\n"
        "# replace the Pass A defaults derived from the manifest description.\n"
        "# The `_rationale` field is for transcript review and is ignored at\n"
        "# build time (see generate_wrappers.load_keyword_overrides).\n"
        "#\n"
        "# Anything not listed here keeps Pass A's auto-derived rule, which is\n"
        "# built from the persona's manifest description + name (see\n"
        "# derive_keyword_rule in converter/generate_wrappers.py).\n"
        "#\n"
        "# Maintenance: refine via `converter/refine-keyword-rules.py`\n"
        "# (claude -p driven, run pre-release, output committed). Not a CI step —\n"
        "# the 'no LLM in CI' invariant holds.\n"
        "#\n"
    )
    body = yaml.safe_dump(
        merged, default_flow_style=False, sort_keys=True, width=80, allow_unicode=True
    )
    out_path.write_text(header + body, encoding="utf-8")
    return added, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--filter",
        default=None,
        help="only refine personas whose name contains this substring",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan; don't invoke claude -p",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="parallel claude -p calls (default 4)",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help=(
            "claude model to use (default claude-sonnet-4-6 — keyword "
            "extraction needs judgment, not just pattern-matching; sonnet "
            "adheres reliably to the multi-rule system prompt where haiku "
            "misses ~30% of edge cases). Pass `claude-haiku-4-5-20251001` "
            "for cheaper runs. Use FULL model IDs — the short aliases "
            "(`sonnet`, `haiku`) inherit context mode from the parent "
            "session, which forces a 429 when the parent uses 1M context "
            "without paid usage credits."
        ),
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="skip personas already in overrides yaml",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing overrides for refined personas",
    )
    parser.add_argument(
        "--out",
        default=str(OVERRIDES_PATH),
        help="output yaml path",
    )
    args = parser.parse_args()

    personas = load_personas(DIST_DIR)
    if args.filter:
        personas = [p for p in personas if args.filter in p.name]

    out_path = Path(args.out)
    # `existing` for the merge step reads from the OUTPUT path (may be a
    # tmp file). For `--skip-existing` we always check the CANONICAL
    # overrides file — otherwise a trial run with `--out /tmp/foo.yaml`
    # would re-refine personas that already have curated entries in the
    # real overrides file.
    existing = {}
    if out_path.is_file():
        existing = yaml.safe_load(out_path.read_text(encoding="utf-8")) or {}

    canonical_existing = {}
    if OVERRIDES_PATH.is_file():
        canonical_existing = (
            yaml.safe_load(OVERRIDES_PATH.read_text(encoding="utf-8")) or {}
        )

    if args.skip_existing:
        personas = [p for p in personas if p.name not in canonical_existing]

    print(
        f"refine-keyword-rules.py: {len(personas)} personas "
        f"(model={args.model}, workers={args.workers}, dry_run={args.dry_run})",
        file=sys.stderr,
    )

    if args.dry_run:
        for p in personas:
            print(f"  would refine: {p.name}", file=sys.stderr)
        return 0

    refined: list[RefinedRule] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(refine_persona, p, args.model): p for p in personas}
        for future in as_completed(futures):
            p = futures[future]
            result = future.result()
            if result:
                refined.append(result)
                print(
                    f"  ✓ {result.persona}: {len(result.keywords)} keywords",
                    file=sys.stderr,
                )
            else:
                print(f"  ✗ {p.name}: skipped (see error above)", file=sys.stderr)

    added, skipped = merge_and_write(refined, existing, out_path, force=args.force)
    print(
        f"Wrote {out_path} ({added} added, {skipped} skipped because already present "
        f"— use --force to overwrite)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

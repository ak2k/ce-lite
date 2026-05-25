"""Unit tests for the converter pipeline.

Covers the trickiest pieces — the ones where regressions would silently produce
wrong output instead of failing loudly:

- parse_frontmatter: malformed input handling, multi-line values, edge cases
- AGENT_REFERENCE_RE: greedy matching, skill-name vs agent-name disambiguation
- insert_preamble: idempotency, frontmatter-required, positioning
- find_plugin_root: nested vs flat layouts

Round-trip + structural checks are in validate.py and exercised by CI against
the live upstream tag — no need to duplicate them here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

CONVERTER_DIR = Path(__file__).resolve().parent.parent / "converter"
sys.path.insert(0, str(CONVERTER_DIR))

from DISPATCH_PATTERNS import AGENT_REFERENCE_RE  # noqa: E402
from extract import (  # noqa: E402
    extract_agents,
    find_plugin_root,
    parse_frontmatter,
)
from rewrite import (  # noqa: E402
    PREAMBLE_MARKER_BEGIN,
    PREAMBLE_MARKER_END,
    insert_preamble,
)


# -------- extract_agents: discovery across the v3.8.4 agent-file rename --------


def _write_agent(agents_dir: Path, filename: str, name: str, body: str) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / filename).write_text(
        f"---\nname: {name}\ndescription: d\nmodel: inherit\ntools: Read, Grep\n---\n{body}\n",
        encoding="utf-8",
    )


def test_extract_agents_v384_plain_md(tmp_path):
    """v3.8.4 renamed agents <name>.agent.md → <name>.md; extract must find them."""
    root = tmp_path / "plugin"
    _write_agent(root / "agents", "ce-foo.md", "ce-foo", "FOO BODY")
    records = extract_agents(root)
    assert [r.name for r in records] == ["ce-foo"]
    assert records[0].body.strip() == "FOO BODY"
    assert records[0].source_path == "agents/ce-foo.md"


def test_extract_agents_legacy_agent_md_still_works(tmp_path):
    """The ≤v3.8.3 <name>.agent.md layout must remain supported."""
    root = tmp_path / "plugin"
    _write_agent(root / "agents", "ce-bar.agent.md", "ce-bar", "BAR BODY")
    records = extract_agents(root)
    assert [r.name for r in records] == ["ce-bar"]
    assert records[0].source_path == "agents/ce-bar.agent.md"


def test_extract_agents_rejects_filename_name_mismatch(tmp_path):
    """Filename must match the frontmatter name under an accepted suffix."""
    root = tmp_path / "plugin"
    _write_agent(root / "agents", "ce-foo.md", "ce-different", "BODY")
    with pytest.raises(ValueError, match="does not match frontmatter name"):
        extract_agents(root)


# -------- parse_frontmatter --------


def test_parse_frontmatter_simple():
    text = '---\nname: foo\ndescription: "bar"\n---\nbody\n'
    fm, body = parse_frontmatter(text)
    assert fm["name"] == "foo"
    assert fm["description"] == "bar"  # quotes stripped
    assert body == "body\n"


def test_parse_frontmatter_with_model_and_tools():
    text = "---\nname: ce-x\nmodel: inherit\ntools: Read, Grep, Bash\n---\nbody"
    fm, body = parse_frontmatter(text)
    assert fm["name"] == "ce-x"
    assert fm["model"] == "inherit"
    assert fm["tools"] == "Read, Grep, Bash"


def test_parse_frontmatter_single_quotes():
    text = "---\nname: 'quoted'\n---\nbody"
    fm, _ = parse_frontmatter(text)
    assert fm["name"] == "quoted"


def test_parse_frontmatter_indented_continuation():
    # description spans two lines; line 2 is indented (continuation)
    text = "---\nname: foo\ndescription: line one\n  line two\n---\nbody"
    fm, _ = parse_frontmatter(text)
    assert "line one" in fm["description"]
    assert "line two" in fm["description"]


def test_parse_frontmatter_missing_fences():
    with pytest.raises(ValueError, match="frontmatter"):
        parse_frontmatter("just a body, no frontmatter\n")


def test_parse_frontmatter_only_opening_fence():
    with pytest.raises(ValueError, match="frontmatter"):
        parse_frontmatter("---\nname: foo\nbody without closing fence")


def test_parse_frontmatter_preserves_body_horizontal_rules():
    """Body-level `---` rules must not be confused with frontmatter fences."""
    text = "---\nname: foo\n---\nintro\n\n---\n\nsection\n"
    _, body = parse_frontmatter(text)
    assert "---" in body  # body-level horizontal rule preserved
    assert body.startswith("intro")


# -------- AGENT_REFERENCE_RE --------


@pytest.mark.parametrize(
    "name",
    [
        "ce-security-sentinel",
        "ce-security-reviewer",
        "ce-kieran-python-reviewer",
        "ce-scope-guardian-reviewer",  # contains "guardian" suffix mid-name
        "ce-data-integrity-guardian",
        "ce-figma-design-sync",
        "ce-deployment-verification-agent",
        "ce-pr-comment-resolver",
        "ce-architecture-strategist",
        "ce-issue-intelligence-analyst",
        "ce-spec-flow-analyzer",
        "ce-session-historian",
        "ce-best-practices-researcher",
        "ce-design-iterator",
        "ce-ankane-readme-writer",
        "ce-pattern-recognition-specialist",
    ],
)
def test_agent_reference_matches_real_names(name):
    """Every real CE agent name must be detected by the pattern."""
    matches = AGENT_REFERENCE_RE.findall(name)
    assert matches == [name], f"expected [{name!r}], got {matches!r}"


@pytest.mark.parametrize(
    "non_agent",
    [
        "ce-code-review",  # skill, not agent (no persona suffix)
        "ce-brainstorm",
        "ce-plan",
        "ce-work",
        "ce-compound",
        "ce-setup",
        "ce-debug",
        "ce-commit",
        "the-reviewer",  # missing ce- prefix
        "reviewer",
        "ce-",  # too short
    ],
)
def test_agent_reference_does_not_match_non_agents(non_agent):
    matches = AGENT_REFERENCE_RE.findall(non_agent)
    assert matches == [], f"expected no match, got {matches!r}"


def test_agent_reference_greedy_in_compound_name():
    """ce-scope-guardian-reviewer must match WHOLE, not prefix-only.

    This is the regression the non-greedy `*?` introduced — the old regex
    matched `ce-scope-guardian` (a real persona suffix mid-name) and missed
    `-reviewer` at the end.
    """
    text = "Spawn ce-scope-guardian-reviewer for this PR."
    matches = AGENT_REFERENCE_RE.findall(text)
    assert matches == ["ce-scope-guardian-reviewer"]


def test_agent_reference_word_boundary():
    """Pattern must respect word boundaries to avoid partial matches."""
    # `\b` matches at letter↔dash transitions, so `ce-security-reviewer`
    # would match because there's a word boundary at end of `reviewer` (letter)
    # and start of `-` (non-word). Test the realistic case: name in prose.
    text2 = "When dispatching `ce-security-reviewer`, pass diff context."
    matches2 = AGENT_REFERENCE_RE.findall(text2)
    assert "ce-security-reviewer" in matches2


# -------- insert_preamble --------


def test_insert_preamble_after_frontmatter():
    skill = "---\nname: ce-x\n---\n# Heading\n\nBody.\n"
    out = insert_preamble(skill)
    assert PREAMBLE_MARKER_BEGIN in out
    assert PREAMBLE_MARKER_END in out
    # Frontmatter must be preserved at the very top
    assert out.startswith("---\nname: ce-x\n---\n")
    # Original body must follow the preamble
    body_idx = out.index("# Heading")
    preamble_end_idx = out.index(PREAMBLE_MARKER_END)
    assert body_idx > preamble_end_idx


def test_insert_preamble_idempotent():
    skill = "---\nname: ce-x\n---\nBody.\n"
    once = insert_preamble(skill)
    twice = insert_preamble(once)
    assert once == twice


def test_insert_preamble_refuses_without_frontmatter():
    with pytest.raises(ValueError, match="frontmatter"):
        insert_preamble("body without frontmatter\n")


def test_insert_preamble_preserves_body_byte_for_byte():
    body = "# Title\n\nLine 1\n\n---\n\nLine 2 after horizontal rule\n"
    skill = "---\nname: ce-x\n---\n" + body
    out = insert_preamble(skill)
    end_marker_pos = out.index(PREAMBLE_MARKER_END) + len(PREAMBLE_MARKER_END)
    after_preamble = out[end_marker_pos:].lstrip("\n")
    assert after_preamble == body.lstrip("\n")


# -------- find_plugin_root --------


def test_find_plugin_root_nested(tmp_path: Path):
    """v3.x layout: plugin under plugins/compound-engineering/."""
    nested = tmp_path / "plugins" / "compound-engineering"
    (nested / ".claude-plugin").mkdir(parents=True)
    assert find_plugin_root(tmp_path) == nested


def test_find_plugin_root_flat(tmp_path: Path):
    """Older layout: .claude-plugin at upstream root."""
    (tmp_path / ".claude-plugin").mkdir()
    assert find_plugin_root(tmp_path) == tmp_path


def test_find_plugin_root_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        find_plugin_root(tmp_path)


# -------- generate_wrappers (Tier 3) --------

from generate_wrappers import (  # noqa: E402
    Persona,
    load_overrides,
    passA_description,
    render_wrapper,
    wrapper_name,
)


def test_wrapper_name_strips_ce_prefix():
    """ce-X-reviewer -> ce-ask-X-reviewer (not ce-ask-ce-X-reviewer)."""
    assert wrapper_name("ce-security-sentinel") == "ce-ask-security-sentinel"
    assert (
        wrapper_name("ce-architecture-strategist") == "ce-ask-architecture-strategist"
    )


def test_wrapper_name_passes_through_non_ce():
    """Names without ce- prefix get plain ce-ask- prepended."""
    assert wrapper_name("custom-reviewer") == "ce-ask-custom-reviewer"


def test_passA_strips_conditional_prefix():
    """'Conditional X persona, selected when Y' -> 'Use when Y'."""
    desc = (
        "Conditional code-review persona, selected when the diff is large or "
        "touches auth. Constructs failure scenarios."
    )
    out = passA_description(desc)
    assert out.startswith("Use when the diff is large")
    assert "Constructs failure scenarios" in out
    assert "Conditional" not in out


def test_passA_passthrough_already_action_oriented():
    """Descriptions already starting with 'Use when' or action verbs pass through."""
    desc = "Reviews code for security issues. Use when reviewing auth code."
    assert passA_description(desc) == desc


def test_passA_handles_capitalised_conditional():
    """The regex is case-insensitive; mixed-case 'Conditional' still matches."""
    desc = "CONDITIONAL document persona, selected when document is large. Foo."
    assert passA_description(desc).startswith("Use when document is large")


def test_render_wrapper_includes_canonical_name_not_wrapper_name():
    """Tier 4 future-proofing: body must reference canonical persona name."""
    p = Persona(
        name="ce-security-sentinel",
        description="Security reviews.",
        model="inherit",
        tools=["Read", "Grep"],
        prompt_path="references/agent-prompts/ce-security-sentinel.md",
    )
    out = render_wrapper(p, "Use when reviewing security.")
    assert "ce-security-sentinel" in out
    # Wrapper name appears in the frontmatter `name:` field, that's it
    assert out.count("ce-ask-security-sentinel") == 1


def test_render_wrapper_includes_generated_marker():
    """Round-trip cleanup hook: wrappers self-identify as generated."""
    p = Persona(
        name="ce-x",
        description="x",
        model="inherit",
        tools=None,
        prompt_path="references/agent-prompts/ce-x.md",
    )
    out = render_wrapper(p, "x")
    assert "Generated by ce-lite converter" in out


def test_render_wrapper_includes_trace_tag():
    """Inline debug metadata for grep-on-demand."""
    p = Persona(
        name="ce-x",
        description="x",
        model="inherit",
        tools=None,
        prompt_path="references/agent-prompts/ce-x.md",
    )
    out = render_wrapper(p, "x")
    assert "[ce-persona=ce-x via=ce-ask-direct]" in out


def test_render_wrapper_delegates_to_resolver_prefix():
    """Phase B.10: tool-restriction preamble moves into the resolver's --prefix
    output. The SKILL.md body just calls ce-lite-persona <name> --prefix and
    concatenates with the task. Wrapper body must NOT embed the tool list
    inline (that'd duplicate the resolver's job and break on null tools)."""
    p = Persona(
        name="ce-x",
        description="x",
        model="inherit",
        tools=["Read", "Grep", "Glob"],
        prompt_path="references/agent-prompts/ce-x.md",
    )
    out = render_wrapper(p, "x")
    # Resolver is called with --prefix
    assert "ce-lite-persona ce-x --prefix" in out
    # Tool list NOT inline in wrapper body (resolver emits it at runtime)
    assert "tools=[Read, Grep, Glob]" not in out
    # Body still references tool restriction conceptually (so reader knows
    # the resolver is doing it)
    assert "tool-restriction" in out.lower() or "tool restriction" in out.lower()


def test_render_wrapper_frontmatter_quotes_special_characters():
    """description field uses json.dumps so embedded quotes don't break YAML."""
    p = Persona(
        name="ce-x",
        description='Has "quotes" and: colons',
        model="inherit",
        tools=None,
        prompt_path="references/agent-prompts/ce-x.md",
    )
    out = render_wrapper(p, 'Has "quotes" and: colons')
    # First few lines should parse as valid YAML frontmatter
    fm_block = out.split("---\n", 2)[1]
    # json.dumps escapes inner quotes; that's a valid JSON string and YAML accepts it
    assert '"Has \\"quotes\\" and: colons"' in fm_block


def test_load_overrides_missing_returns_empty(tmp_path: Path):
    """No overrides file -> empty dict (Pass A applies to everything)."""
    assert load_overrides(tmp_path) == {}


def test_load_overrides_inline_scalar(tmp_path: Path):
    """Single-line `key: value` form."""
    (tmp_path / "overrides").mkdir()
    (tmp_path / "overrides" / "persona-descriptions.yaml").write_text(
        'ce-security-sentinel: "Use when X"\nce-other: Plain value\n'
    )
    out = load_overrides(tmp_path)
    assert out["ce-security-sentinel"] == "Use when X"
    assert out["ce-other"] == "Plain value"


def test_load_overrides_block_scalar(tmp_path: Path):
    """Multi-line `key: |\n  body` form for descriptions that span lines."""
    (tmp_path / "overrides").mkdir()
    (tmp_path / "overrides" / "persona-descriptions.yaml").write_text(
        "ce-x: |\n  Use when reviewing X.\n  Focuses on Y.\nce-y: inline\n"
    )
    out = load_overrides(tmp_path)
    assert out["ce-x"] == "Use when reviewing X.\nFocuses on Y."
    assert out["ce-y"] == "inline"


def test_load_overrides_skips_comments(tmp_path: Path):
    """Lines starting with `#` at top level are ignored."""
    (tmp_path / "overrides").mkdir()
    (tmp_path / "overrides" / "persona-descriptions.yaml").write_text(
        "# header comment\nce-x: value\n# trailing comment\n"
    )
    assert load_overrides(tmp_path) == {"ce-x": "value"}


# -------- ce-ask-panel meta-skill --------

from generate_wrappers import PANEL_DESCRIPTION, render_panel  # noqa: E402


def test_render_panel_static_template_ignores_persona_count():
    """Panel template doesn't enumerate personas — manifest is the runtime source.

    Embedding 49 persona names would force regeneration on every upstream
    bump just to update a list Claude can read from manifest.json directly.
    """
    a = render_panel([])
    b = render_panel(
        [
            Persona(
                name="ce-x",
                description="d",
                model="inherit",
                tools=None,
                prompt_path="references/agent-prompts/ce-x.md",
            ),
        ]
    )
    assert a == b


def test_render_panel_includes_generated_marker():
    out = render_panel([])
    assert "Generated by ce-lite converter" in out


def test_render_panel_dispatches_with_panel_via_tag():
    """Panel skill instructs the resolver to record via=ce-ask-panel in the
    trace tag.

    Phase B.10: the trace tag is emitted by the resolver (not inline in the
    SKILL.md body) — the wrapper just passes `--via ce-ask-panel`. via=ce-ask-direct
    may still appear in documentation prose contrasting the two values.
    """
    out = render_panel([])
    assert "--via ce-ask-panel" in out


def test_render_panel_argument_hint_in_frontmatter():
    """argument-hint surfaces in slash-command UX for users + Claude."""
    out = render_panel([])
    fm_block = out.split("---\n", 2)[1]
    assert "argument-hint:" in fm_block
    assert "<persona1>,<persona2>" in fm_block


def test_render_panel_directs_users_to_canonical_names():
    """Validation step requires canonical names, not wrapper names."""
    out = render_panel([])
    assert "canonical" in out.lower()
    assert "ce-security-sentinel" in out  # an example canonical name
    assert "NOT" in out  # explicit "NOT the ce-ask-* wrapper names" guidance


def test_render_panel_description_is_substantive():
    """PANEL_DESCRIPTION is the routing surface — must convey use case clearly."""
    assert len(PANEL_DESCRIPTION) > 100
    assert "parallel" in PANEL_DESCRIPTION.lower()
    assert "comma-separated" in PANEL_DESCRIPTION.lower()


# -------- meta-skill ce-ask (Phase B.5) — meta-agent removed in B.7 --------

import json as _json  # noqa: E402  # local alias to avoid clashing with module-level imports

from generate_wrappers import (  # noqa: E402
    HAIKU_CONFIG,
    META_SKILL_DESCRIPTION,
    render_hook_config,
    render_hook_rules,
    render_hook_script,
    render_meta_skill,
)


def test_render_meta_skill_argument_hint():
    """meta-skill takes optional persona + optional task."""
    out = render_meta_skill()
    fm_block = out.split("---\n", 2)[1]
    assert "argument-hint:" in fm_block
    assert "persona-name" in fm_block
    assert "task context" in fm_block


def test_render_meta_skill_describes_three_modes():
    """no-args → catalog; persona-only → role; persona+task → dispatch."""
    out = render_meta_skill()
    assert "no args" in out.lower() or "/ce-ask`" in out
    assert "persona's role" in out.lower() or "role definition" in out.lower()
    assert "dispatch" in out.lower()


def test_render_meta_skill_dynamic_catalog():
    """Catalog read at runtime — body is stable across upstream bumps.

    A handful of example persona names in prose is fine (they help readers);
    what matters is that the FULL catalog isn't enumerated (which would force
    regeneration on every upstream persona-list change). Heuristic: if the
    body mentions more than 5 distinct ce-* persona names, the catalog is
    being baked in inline.
    """
    out = render_meta_skill()
    assert "manifest.json" in out
    distinct_persona_names = set(AGENT_REFERENCE_RE.findall(out))
    assert len(distinct_persona_names) <= 5, (
        f"meta-skill body looks like it bakes in the catalog ({len(distinct_persona_names)} "
        f"persona names): {sorted(distinct_persona_names)}"
    )


def test_render_meta_skill_includes_marker():
    out = render_meta_skill()
    assert "Generated by ce-lite converter" in out


def test_render_meta_skill_via_tag_distinguishes():
    """Meta-skill instructs the resolver to record via=ce-ask-meta in the
    trace tag (passed as --via ce-ask-meta on the resolver call)."""
    out = render_meta_skill()
    assert "--via ce-ask-meta" in out


def test_meta_skill_description_substantive():
    """Routing surface — must convey the three modes clearly."""
    assert len(META_SKILL_DESCRIPTION) > 200
    assert "discover" in META_SKILL_DESCRIPTION.lower()
    assert "dispatch" in META_SKILL_DESCRIPTION.lower()


def test_three_routing_layers_have_distinct_via_tags():
    """Sanity: the three routing surfaces pass distinct --via values to the
    resolver. Phase B.10 moved the trace tag emission into the resolver; the
    SKILL.md bodies just pass `--via <source>` to record it."""
    p = Persona(
        name="ce-x",
        description="x",
        model="inherit",
        tools=None,
        prompt_path="references/agent-prompts/ce-x.md",
    )
    # Direct wrapper either passes --via ce-ask-direct or relies on the
    # resolver default (which is ce-ask-direct). Both forms are valid; just
    # verify the source is named in the body.
    direct_body = render_wrapper(p, "x")
    via_direct = "ce-ask-direct" in direct_body
    via_panel = "--via ce-ask-panel" in render_panel([])
    via_meta = "--via ce-ask-meta" in render_meta_skill()
    assert via_direct and via_panel and via_meta


# -------- UserPromptSubmit hook (Phase B.7) --------


def test_hook_config_has_userpromptsubmit():
    """The hook entry-point Claude Code looks for must be present."""
    spec = _json.loads(render_hook_config())
    assert "hooks" in spec
    assert "UserPromptSubmit" in spec["hooks"]
    handlers = spec["hooks"]["UserPromptSubmit"]
    assert isinstance(handlers, list) and len(handlers) >= 1


def test_hook_config_uses_plugin_root_variable():
    """${CLAUDE_PLUGIN_ROOT} is the only portable way to point at the script."""
    spec = _json.loads(render_hook_config())
    cmd = spec["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert "${CLAUDE_PLUGIN_ROOT}" in cmd
    assert "auto_suggest.py" in cmd


def test_hook_rules_have_keywords_persona_phrasing():
    """Rule schema — keyword/persona/phrasing — is the contract the hook script reads."""
    rules = _json.loads(render_hook_rules())["rules"]
    assert len(rules) >= 5  # we ship 6 default rules; 5 is a safety floor
    for rule in rules:
        assert "keywords" in rule and isinstance(rule["keywords"], list)
        assert rule["keywords"], "rule with no keywords is dead weight"
        assert "persona" in rule and rule["persona"].startswith("ce-")
        assert "phrasing" in rule and rule["phrasing"].strip()


def test_hook_rules_reference_real_personas():
    """Rules must point at canonical persona names so dispatch resolves."""
    rules = _json.loads(render_hook_rules())["rules"]
    targeted = {r["persona"] for r in rules}
    # High-leverage personas worth covering — if any are missing from default
    # rules, the design is leaving obvious wins on the table.
    expected_coverage = {
        "ce-security-sentinel",
        "ce-architecture-strategist",
        "ce-code-simplicity-reviewer",
    }
    missing = expected_coverage - targeted
    assert not missing, f"default rules missing high-leverage personas: {missing}"


def test_hook_rules_phrasing_mentions_slash_command():
    """The injected suggestion needs to give Claude an actionable command."""
    rules = _json.loads(render_hook_rules())["rules"]
    for rule in rules:
        assert "/ce-" in rule["phrasing"], (
            f"rule for {rule['persona']} doesn't suggest a slash command "
            f"in its phrasing: {rule['phrasing']!r}"
        )


def test_hook_script_is_executable_python():
    """Script starts with shebang + uses stdlib only (no external deps)."""
    src = render_hook_script()
    assert src.startswith("#!/usr/bin/env python3")
    # No imports of pyyaml / requests / external libs — must be stdlib-only.
    forbidden = ["import yaml", "import requests", "from anthropic", "import anthropic"]
    for f in forbidden:
        assert f not in src, f"hook script imports {f!r} — must be stdlib-only"


def test_hook_script_handles_empty_payload_silently():
    """Empty / malformed prompt → silent exit (don't break user prompt processing)."""
    src = render_hook_script()
    # The script must early-return on empty/invalid input — verify by string
    # search since exec'ing the hook needs an active Claude session context.
    assert "return 0" in src
    # And critically — the silent-exit paths handle the common error cases.
    assert "JSONDecodeError" in src
    assert "if not isinstance(user_prompt, str)" in src or "not user_prompt" in src


def test_render_hook_rules_emits_haiku_config_block():
    """The config block sources from the HAIKU_CONFIG constant, unchanged."""
    rendered = _json.loads(render_hook_rules())
    assert rendered["config"] == HAIKU_CONFIG


def test_render_hook_rules_covers_all_personas():
    """Pass A generates a rule per persona in the manifest; Pass B may override
    keywords/phrasing but doesn't remove rules. Total rule count == persona count."""
    from generate_wrappers import load_manifest

    rendered = _json.loads(render_hook_rules())
    personas = load_manifest(Path(__file__).resolve().parent.parent / "dist")
    assert len(rendered["rules"]) == len(personas), (
        f"expected one rule per persona; got {len(rendered['rules'])} rules "
        f"for {len(personas)} personas"
    )
    rule_personas = {r["persona"] for r in rendered["rules"]}
    manifest_personas = {p.name for p in personas}
    assert rule_personas == manifest_personas, (
        f"rule personas don't match manifest: "
        f"missing={manifest_personas - rule_personas}, "
        f"extra={rule_personas - manifest_personas}"
    )


# -------- hook script live runtime tests (Phase B.7) --------
#
# render_hook_script tests above prove we generate the right SOURCE.
# These tests pipe synthetic payloads into the actual generated script and
# assert on output JSON — catching regressions in the runtime behaviour
# (parsing, matching, error handling) that source-string assertions miss.

import subprocess as _subprocess  # noqa: E402
import tempfile as _tempfile  # noqa: E402


def _run_hook_with(prompt: str, rules: dict | None = None) -> tuple[int, str, str]:
    """Run the generated hook script against `prompt`, optionally with custom rules.

    Writes script + rules to a tmpdir, pipes a UserPromptSubmit-shaped payload
    on stdin, returns (exit_code, stdout, stderr).
    """
    from generate_wrappers import render_hook_rules, render_hook_script

    with _tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        script_path = tmp_path / "auto_suggest.py"
        rules_path = tmp_path / "skill-rules.json"
        script_path.write_text(render_hook_script(), encoding="utf-8")
        if rules is None:
            rules_path.write_text(render_hook_rules(), encoding="utf-8")
        else:
            rules_path.write_text(_json.dumps(rules), encoding="utf-8")

        proc = _subprocess.run(
            ["python3", str(script_path)],
            input=_json.dumps({"prompt": prompt}).encode("utf-8"),
            capture_output=True,
            timeout=10,
        )
        return proc.returncode, proc.stdout.decode(), proc.stderr.decode()


def test_hook_runtime_security_keyword_fires():
    rc, out, err = _run_hook_with("audit users.py for OWASP Top 10 issues")
    assert rc == 0, f"non-zero exit: stderr={err!r}"
    response = _json.loads(out)
    # additionalContext must be inside hookSpecificOutput per the hook spec
    # (https://code.claude.com/docs/en/hooks). Top-level additionalContext
    # is silently ignored by Claude Code.
    assert "hookSpecificOutput" in response
    spec = response["hookSpecificOutput"]
    assert spec.get("hookEventName") == "UserPromptSubmit"
    assert "ce-ask-security-sentinel" in spec.get("additionalContext", "")


def test_hook_runtime_architecture_keyword_fires():
    rc, out, err = _run_hook_with("factor out the duplicated controller logic")
    assert rc == 0
    response = _json.loads(out)
    assert (
        "ce-ask-architecture-strategist"
        in (response["hookSpecificOutput"]["additionalContext"])
    )


def test_hook_runtime_emits_correct_envelope_shape():
    """Regression guard: response MUST nest additionalContext under
    hookSpecificOutput with hookEventName=UserPromptSubmit.

    Phase B.7's first implementation got this wrong (additionalContext at
    top level) — Claude silently ignored it and the integration eval
    showed 0 behavior change. The bug went undetected until we wired up
    the canonical docs check. This test prevents regression.
    """
    rc, out, _ = _run_hook_with("audit for security issues")
    response = _json.loads(out)
    # Top-level fields: only hookSpecificOutput should be present
    assert "additionalContext" not in response, (
        "additionalContext at top level is silently ignored by Claude Code; "
        "it must be nested inside hookSpecificOutput"
    )
    assert "hookSpecificOutput" in response
    spec = response["hookSpecificOutput"]
    assert spec["hookEventName"] == "UserPromptSubmit"
    assert isinstance(spec["additionalContext"], str)
    assert spec["additionalContext"].strip()


def test_hook_runtime_no_match_silent():
    rc, out, err = _run_hook_with("the test suite is taking 12 minutes")
    assert rc == 0
    assert out == "", f"expected silent exit; got stdout={out!r}"


def test_hook_runtime_empty_prompt_silent():
    rc, out, err = _run_hook_with("")
    assert rc == 0
    assert out == ""


def test_hook_runtime_handles_malformed_json_gracefully():
    """Pipe garbage on stdin — must not crash or block the user prompt."""
    from generate_wrappers import render_hook_script

    with _tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "auto_suggest.py"
        script_path.write_text(render_hook_script(), encoding="utf-8")
        proc = _subprocess.run(
            ["python3", str(script_path)],
            input=b"not valid json at all { malformed",
            capture_output=True,
            timeout=10,
        )
    assert proc.returncode == 0  # silent exit, not crash
    assert proc.stdout == b""


def test_hook_runtime_caps_at_three_suggestions():
    """If a prompt matches many rules, output bounds to MAX_SUGGESTIONS=3."""
    rules = {
        "rules": [
            {"keywords": ["x"], "persona": f"ce-test-{i}", "phrasing": f"test {i}"}
            for i in range(10)
        ]
    }
    rc, out, err = _run_hook_with("xxx", rules=rules)
    assert rc == 0
    response = _json.loads(out)
    suggestions_block = response["hookSpecificOutput"]["additionalContext"]
    assert suggestions_block.count("- test ") <= 3, (
        f"expected ≤3 suggestions; got: {suggestions_block!r}"
    )


def test_hook_runtime_case_insensitive_match():
    rc, out, _ = _run_hook_with("AUDIT FOR OWASP ISSUES")
    response = _json.loads(out)
    assert (
        "ce-ask-security-sentinel"
        in (response["hookSpecificOutput"]["additionalContext"])
    )


def test_hook_runtime_works_when_rules_missing():
    """If skill-rules.json doesn't exist, hook must silent-exit (not crash)."""
    from generate_wrappers import render_hook_script

    with _tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "auto_suggest.py"
        script_path.write_text(render_hook_script(), encoding="utf-8")
        # Note: no skill-rules.json written
        proc = _subprocess.run(
            ["python3", str(script_path)],
            input=_json.dumps({"prompt": "audit for security"}).encode(),
            capture_output=True,
            timeout=10,
        )
    assert proc.returncode == 0
    assert proc.stdout == b""  # silent — no rules to match


# -------- Haiku intent classifier wiring (Phase B.8) --------
#
# We don't call real Haiku in tests (burns quota; non-deterministic). We test
# the WIRING — that the script imports/structures the call correctly, that the
# config gates the path, and that failure paths fall through silently.


def test_default_config_has_haiku_disabled():
    """Haiku is opt-in. Default config must not auto-enable it."""
    from generate_wrappers import HAIKU_CONFIG

    cfg = HAIKU_CONFIG["haiku_classifier"]
    assert cfg["enabled"] is False


def test_default_config_haiku_caps_budget_and_timeout():
    """Sane defaults for cost/latency. Bounds the worst case."""
    from generate_wrappers import HAIKU_CONFIG

    cfg = HAIKU_CONFIG["haiku_classifier"]
    assert 0 < cfg["max_budget_usd"] <= 0.05  # don't accidentally spend $5/prompt
    assert 0 < cfg["timeout_seconds"] <= 30  # bound latency


def test_hook_script_imports_subprocess_for_haiku():
    """Haiku path uses subprocess.run; without it, classifier can't shell out."""
    src = render_hook_script()
    assert "import subprocess" in src
    assert "subprocess.run" in src


def test_hook_script_uses_lightweight_claude_p_flags():
    """Haiku call must use the no-context-bloat flag combo from
    ~/.claude/memory/claude_p_headless_subscription.md."""
    src = render_hook_script()
    # Each of these flags is non-negotiable for the lightweight pattern;
    # missing any of them means the classifier call will load full env
    # context, blowing budget and confusing the routing classifier.
    assert "--setting-sources" in src
    assert "--no-session-persistence" in src
    assert "--disable-slash-commands" in src
    assert "--system-prompt" in src
    assert "--json-schema" in src
    assert "--max-budget-usd" in src
    assert "CLAUDE_CODE_DISABLE_AUTO_MEMORY" in src
    assert "CLAUDE_CODE_DISABLE_CLAUDE_MDS" in src


def test_hook_script_haiku_silent_on_failure():
    """Haiku call failure paths (timeout, non-zero exit, malformed JSON,
    low confidence) must all return None and let the script silent-exit
    rather than crash or fabricate suggestions."""
    src = render_hook_script()
    # The function returns None on all known failure modes
    assert "TimeoutExpired" in src
    assert "JSONDecodeError" in src
    # Confidence floor enforcement
    assert "min_confidence" in src
    # Disabled path (config.enabled = False) returns None
    assert 'if not cfg.get("enabled")' in src


def test_hook_script_haiku_uses_structured_output():
    """The script reads `structured_output` from the JSON envelope, not
    `result` (per claude_p_headless_subscription.md trap section)."""
    src = render_hook_script()
    assert '"structured_output"' in src
    assert "structured_output" in src


def test_hook_runtime_haiku_disabled_falls_through():
    """When config.haiku_classifier.enabled=False (default) AND no keyword
    match, hook must silent-exit. NEVER calls claude -p in this state."""
    rules = {
        "config": {"haiku_classifier": {"enabled": False}},
        "rules": [
            {"keywords": ["xyz"], "persona": "ce-test", "phrasing": "no match"},
        ],
    }
    rc, out, _ = _run_hook_with("totally unrelated prompt", rules=rules)
    assert rc == 0
    assert out == ""  # silent — no keyword match, Haiku disabled


def test_hook_runtime_keyword_match_short_circuits_haiku():
    """When keyword match fires, Haiku is NOT called (cheap path wins).

    We can't directly verify "Haiku not called" without mocking, but we can
    verify a keyword-match prompt produces a result quickly (<2s) — well
    under the Haiku timeout — and contains the keyword-rule's phrasing
    (not a Haiku-shaped fallback).
    """
    import time as _time

    rules = {
        "config": {"haiku_classifier": {"enabled": True, "timeout_seconds": 10}},
        "rules": [
            {
                "keywords": ["security"],
                "persona": "ce-security-sentinel",
                "phrasing": "KEYWORD_PATH_MARKER",
            },
        ],
    }
    t0 = _time.monotonic()
    rc, out, _ = _run_hook_with("security review please", rules=rules)
    elapsed = _time.monotonic() - t0
    assert rc == 0
    response = _json.loads(out)
    assert "KEYWORD_PATH_MARKER" in response["hookSpecificOutput"]["additionalContext"]
    assert elapsed < 2.0, (
        f"keyword-match took {elapsed:.1f}s — Haiku may have been called"
    )


# -------- bin/ce-lite-persona resolver shim (Phase B.9) --------
#
# The resolver replaces per-skill Glob/find discovery scaffolding. Wrappers
# and orchestrator preambles both invoke `ce-lite-persona <name> --body`
# instead of embedding ~500 tokens of discovery prose. These tests cover
# both the rendered source (string assertions on the template) AND live
# runtime behaviour (write the script to a tmpdir with a fake manifest and
# exercise it).

from generate_wrappers import render_persona_resolver  # noqa: E402


def test_resolver_has_shebang():
    src = render_persona_resolver()
    assert src.startswith("#!/usr/bin/env python3")


def test_resolver_is_stdlib_only():
    """Hooks ship stdlib-only too — same constraint applies."""
    src = render_persona_resolver()
    forbidden = ["import yaml", "import requests", "from anthropic", "import anthropic"]
    for f in forbidden:
        assert f not in src, f"resolver imports {f!r} — must be stdlib-only"


def test_resolver_honours_claude_plugin_root_env():
    """${CLAUDE_PLUGIN_ROOT} is the canonical plugin-root reference; resolver
    must prefer it when set so hook-spawned subprocesses don't re-walk."""
    src = render_persona_resolver()
    assert "CLAUDE_PLUGIN_ROOT" in src


def test_resolver_walks_up_as_fallback():
    """When the env var isn't set (model's Bash environment outside a skill
    context), the resolver walks up from its own location."""
    src = render_persona_resolver()
    assert "__file__" in src
    assert ".claude-plugin" in src


def test_resolver_advertises_subcommands_in_docstring():
    """Discoverability — `--help` should surface the same flags the SKILL.md
    bodies reference, so a future converter regen can't silently drift."""
    src = render_persona_resolver()
    for flag in ["--body", "--path", "--list", "--diagnose"]:
        assert flag in src, f"resolver source missing flag advertisement: {flag}"


def _run_resolver(
    tmp: Path, args: list[str], extra_env: dict | None = None
) -> tuple[int, str, str]:
    """Write resolver to a tmpdir with a fake manifest + prompt and invoke it."""
    import subprocess as _subp

    plugin_root = tmp / "plugin"
    first_call = not plugin_root.exists()
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"x","version":"1"}'
    )
    prompts_dir = plugin_root / "references" / "agent-prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    if first_call:
        # Subsequent calls within the same test (e.g. simulating "prompt
        # deleted after install") must NOT re-bootstrap the fixture.
        (prompts_dir / "ce-security-sentinel.md").write_text("security persona body\n")
    if first_call:
        manifest = {
            "schema_version": 1,
            "agent_count": 1,
            "agents": [
                {
                    "name": "ce-security-sentinel",
                    "description": "Use when reviewing security.",
                    "model": "inherit",
                    "tools": ["Read", "Grep"],
                    "prompt_path": "references/agent-prompts/ce-security-sentinel.md",
                }
            ],
        }
        (prompts_dir / "manifest.json").write_text(_json.dumps(manifest))

    bin_dir = plugin_root / "bin"
    bin_dir.mkdir(exist_ok=True)
    resolver = bin_dir / "ce-lite-persona"
    resolver.write_text(render_persona_resolver(), encoding="utf-8")
    resolver.chmod(0o755)

    env = {**os.environ}
    if extra_env:
        env.update(extra_env)
    proc = _subp.run(
        ["python3", str(resolver), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


# os import for _run_resolver
import os  # noqa: E402


def test_resolver_runtime_body_returns_prompt_contents(tmp_path: Path):
    rc, out, err = _run_resolver(tmp_path, ["ce-security-sentinel", "--body"])
    assert rc == 0, f"stderr={err!r}"
    assert out == "security persona body\n"


def test_resolver_runtime_path_returns_absolute_path(tmp_path: Path):
    rc, out, err = _run_resolver(tmp_path, ["ce-security-sentinel", "--path"])
    assert rc == 0, f"stderr={err!r}"
    resolved = Path(out.strip())
    assert resolved.is_absolute()
    assert resolved.name == "ce-security-sentinel.md"


def test_resolver_runtime_accepts_bare_persona_name(tmp_path: Path):
    """`security-sentinel` resolves to `ce-security-sentinel`."""
    rc, out, _ = _run_resolver(tmp_path, ["security-sentinel", "--body"])
    assert rc == 0
    assert "security persona body" in out


def test_resolver_runtime_list_emits_catalog(tmp_path: Path):
    rc, out, _ = _run_resolver(tmp_path, ["--list"])
    assert rc == 0
    assert "ce-security-sentinel\t" in out
    assert "Use when reviewing security." in out


def test_resolver_runtime_diagnose_reports_ok(tmp_path: Path):
    rc, out, _ = _run_resolver(tmp_path, ["--diagnose"])
    assert rc == 0
    assert "personas:    1" in out
    assert "status:      ok" in out


def test_resolver_runtime_unknown_persona_lists_known(tmp_path: Path):
    rc, out, err = _run_resolver(tmp_path, ["ce-bogus-reviewer", "--body"])
    assert rc != 0
    # Error message must list known personas so the caller can correct typos
    assert "unknown persona" in err.lower()
    assert "ce-security-sentinel" in err
    # And NOTHING was written to stdout (callers grep stderr for the reason)
    assert out == ""


def test_resolver_runtime_diagnose_flags_missing_prompts(tmp_path: Path):
    """Delete a prompt file after install — diagnose must spot it."""
    rc1, _, _ = _run_resolver(tmp_path, ["--diagnose"])
    assert rc1 == 0
    # Remove the prompt file and re-run
    (
        tmp_path / "plugin" / "references" / "agent-prompts" / "ce-security-sentinel.md"
    ).unlink()
    rc2, _, err = _run_resolver(tmp_path, ["--diagnose"])
    assert rc2 != 0
    assert "ce-security-sentinel" in err


def test_resolver_runtime_honours_claude_plugin_root(tmp_path: Path):
    """When $CLAUDE_PLUGIN_ROOT is set, resolver uses it instead of walking up."""
    rc, out, _ = _run_resolver(
        tmp_path,
        ["ce-security-sentinel", "--body"],
        extra_env={"CLAUDE_PLUGIN_ROOT": str(tmp_path / "plugin")},
    )
    assert rc == 0
    assert "security persona body" in out


# -------- wrapper templates reference the resolver shim --------


def test_render_wrapper_invokes_resolver_prefix():
    """Phase B.10: wrappers call --prefix (not --body) — emits full prompt
    prefix (body + trace tag + tool restriction) in one resolver call. Saves
    ~15 lines of inline preamble per wrapper body."""
    from generate_wrappers import Persona, render_wrapper

    p = Persona(
        name="ce-security-sentinel",
        description="Security reviews.",
        model="inherit",
        tools=["Read", "Grep"],
        prompt_path="references/agent-prompts/ce-security-sentinel.md",
    )
    out = render_wrapper(p, "Use when reviewing security.")
    assert "ce-lite-persona ce-security-sentinel --prefix" in out
    # No glob/find scaffolding left over
    assert ".claude/plugins/cache" not in out
    assert "find ~/" not in out


def test_render_panel_invokes_resolver_prefix():
    """Panel skill uses --prefix with --via ce-ask-panel so trace tags
    distinguish panel dispatch from direct."""
    from generate_wrappers import render_panel

    out = render_panel([])
    assert "ce-lite-persona" in out
    assert "--prefix" in out
    assert "--via ce-ask-panel" in out
    assert ".claude/plugins/cache" not in out


def test_render_meta_skill_invokes_resolver():
    """Meta-skill (`/ce-ask`) uses --list (mode 1) and --prefix --via
    ce-ask-meta (mode 3 dispatch)."""
    from generate_wrappers import render_meta_skill

    out = render_meta_skill()
    assert "ce-lite-persona --list" in out
    assert "--prefix" in out
    assert "--via ce-ask-meta" in out
    assert ".claude/plugins/cache" not in out


def test_rewrite_preamble_references_resolver_prefix():
    """Orchestrator preamble teaches dispatch via --prefix with an
    orchestrator-specific --via tag, NOT the legacy inline preamble."""
    from rewrite import PREAMBLE

    assert "ce-lite-persona" in PREAMBLE
    assert "--prefix" in PREAMBLE
    assert "general-purpose" in PREAMBLE  # still the default dispatch type
    # Legacy Glob/find scaffolding must be gone
    assert ".claude/plugins/cache" not in PREAMBLE
    assert "find ~/" not in PREAMBLE


def test_rewrite_preamble_keeps_meaningful_description_guidance():
    """The 'general-purpose' trace label is mitigated by a meaningful
    Agent.description; preamble must teach that pattern."""
    from rewrite import PREAMBLE

    assert "description" in PREAMBLE.lower()
    assert "trace" in PREAMBLE.lower() or "readable" in PREAMBLE.lower()


# -------- --prefix and --via runtime behavior (Phase B.10) --------


def test_resolver_runtime_prefix_emits_body_plus_preamble(tmp_path: Path):
    rc, out, err = _run_resolver(tmp_path, ["ce-security-sentinel", "--prefix"])
    assert rc == 0, f"stderr={err!r}"
    # Body is included verbatim
    assert "security persona body" in out
    # Trace tag is emitted with default --via=ce-ask-direct
    assert "[ce-persona=ce-security-sentinel via=ce-ask-direct]" in out
    # Tool-restriction self-policing preamble appears
    assert "tools=[Read, Grep]" in out
    assert "model=inherit" in out
    assert "stop and explain why" in out


def test_resolver_runtime_prefix_honours_via_override(tmp_path: Path):
    rc, out, err = _run_resolver(
        tmp_path, ["ce-security-sentinel", "--prefix", "--via", "ce-code-review"]
    )
    assert rc == 0, f"stderr={err!r}"
    assert "[ce-persona=ce-security-sentinel via=ce-code-review]" in out
    # Default tag must NOT appear when --via overrides
    assert "via=ce-ask-direct" not in out


def test_resolver_runtime_prefix_rejects_unknown_via(tmp_path: Path):
    """Defence against typos: --via is a closed set."""
    rc, out, err = _run_resolver(
        tmp_path, ["ce-security-sentinel", "--prefix", "--via", "ce-fake-source"]
    )
    assert rc != 0
    assert "unknown" in err.lower() and "via" in err.lower()
    assert "ce-ask-direct" in err  # error message lists known sources


def test_resolver_runtime_prefix_safe_for_arbitrary_task_content(tmp_path: Path):
    """The task NEVER passes through argv on a --prefix call; only the
    persona name and dispatch source do. This test documents the
    quote-safety property by verifying that --prefix takes no task arg."""
    # Even if we tried to pass a task as an extra positional, argparse rejects
    rc, _, err = _run_resolver(
        tmp_path,
        ["ce-security-sentinel", "--prefix", "unexpected-extra-arg"],
    )
    # argparse exit code is 2 on unrecognized args; we want non-zero either way
    assert rc != 0
    assert "unrecognized" in err.lower() or "arguments" in err.lower()


def test_resolver_runtime_prefix_handles_null_tools(tmp_path: Path):
    """Persona with no tool restriction in manifest -> 'tools=[any]' in prefix output."""
    # Override the manifest to a persona with tools=None
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"x","version":"1"}'
    )
    prompts_dir = plugin_root / "references" / "agent-prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "ce-no-tools-persona.md").write_text("body text\n")
    manifest = {
        "schema_version": 1,
        "agent_count": 1,
        "agents": [
            {
                "name": "ce-no-tools-persona",
                "description": "x",
                "model": "inherit",
                "tools": None,
                "prompt_path": "references/agent-prompts/ce-no-tools-persona.md",
            }
        ],
    }
    (prompts_dir / "manifest.json").write_text(_json.dumps(manifest))
    bin_dir = plugin_root / "bin"
    bin_dir.mkdir(exist_ok=True)
    resolver = bin_dir / "ce-lite-persona"
    resolver.write_text(render_persona_resolver(), encoding="utf-8")
    resolver.chmod(0o755)
    import subprocess as _subp

    proc = _subp.run(
        ["python3", str(resolver), "ce-no-tools-persona", "--prefix"],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=10,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    assert "tools=[any]" in proc.stdout


# -------- lite_suffix_from_git (auto -lite.N bump) --------
#
# Lets converter-only changes ship as `/plugin update`-visible bumps without
# waiting for an upstream EveryInc release. N = converter-touching commits
# since the last commit that set .last-processed to the current upstream
# tag. Cross-upstream bumps reset N to 0 → bare '-lite'.

import subprocess as _suff_subp  # noqa: E402

from extract import lite_suffix_from_git  # noqa: E402


def _init_repo(repo: Path) -> None:
    """Initialise a minimal git repo for suffix tests."""
    _suff_subp.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _suff_subp.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    _suff_subp.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    _suff_subp.run(
        ["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True
    )


def _commit(repo: Path, path: str, content: str, msg: str) -> None:
    full = repo / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    _suff_subp.run(["git", "-C", str(repo), "add", path], check=True)
    _suff_subp.run(["git", "-C", str(repo), "commit", "-q", "-m", msg], check=True)


def test_lite_suffix_no_last_processed_returns_bare(tmp_path: Path):
    """First-ever conversion: .last-processed doesn't exist yet."""
    _init_repo(tmp_path)
    _commit(tmp_path, "README.md", "x", "initial")
    assert lite_suffix_from_git(tmp_path, "compound-engineering-v3.8.3") == "-lite"


def test_lite_suffix_cross_upstream_returns_bare(tmp_path: Path):
    """Bumping to a different upstream tag → N resets to 0."""
    _init_repo(tmp_path)
    _commit(tmp_path, ".last-processed", "compound-engineering-v3.8.1\n", "v3.8.1")
    _commit(tmp_path, "converter/extract.py", "x", "converter change")
    # Now extracting against v3.8.3 (different from .last-processed's v3.8.1)
    # → bare suffix, N reset
    assert lite_suffix_from_git(tmp_path, "compound-engineering-v3.8.3") == "-lite"


def test_lite_suffix_same_upstream_no_converter_commits_returns_bare(tmp_path: Path):
    """Re-running publish-dist for the same upstream with no converter
    changes since the last bump → '-lite' (N=0)."""
    _init_repo(tmp_path)
    _commit(tmp_path, ".last-processed", "compound-engineering-v3.8.3\n", "v3.8.3")
    assert lite_suffix_from_git(tmp_path, "compound-engineering-v3.8.3") == "-lite"


def test_lite_suffix_counts_converter_commits_since_last_bump(tmp_path: Path):
    """N counts only converter-touching commits since .last-processed
    was last set to the current upstream tag."""
    _init_repo(tmp_path)
    _commit(tmp_path, ".last-processed", "compound-engineering-v3.8.3\n", "v3.8.3 bump")
    _commit(tmp_path, "converter/extract.py", "x", "converter change 1")
    _commit(tmp_path, "README.md", "y", "non-converter change")
    _commit(tmp_path, "converter/rewrite.py", "z", "converter change 2")
    assert lite_suffix_from_git(tmp_path, "compound-engineering-v3.8.3") == "-lite.2"


def test_lite_suffix_dist_only_commits_dont_count(tmp_path: Path):
    """Commits touching only dist/ (regenerated output) don't bump N —
    only converter/ commits do."""
    _init_repo(tmp_path)
    _commit(tmp_path, ".last-processed", "compound-engineering-v3.8.3\n", "v3.8.3")
    _commit(tmp_path, "dist/foo.md", "x", "regen dist")
    _commit(tmp_path, "dist/bar.md", "y", "regen dist 2")
    assert lite_suffix_from_git(tmp_path, "compound-engineering-v3.8.3") == "-lite"


def test_lite_suffix_falls_back_outside_git_repo(tmp_path: Path):
    """tmp_path is not a git repo → bare '-lite' (no crash)."""
    (tmp_path / ".last-processed").write_text("compound-engineering-v3.8.3\n")
    assert lite_suffix_from_git(tmp_path, "compound-engineering-v3.8.3") == "-lite"


def test_lite_suffix_empty_upstream_tag(tmp_path: Path):
    """Defensive: empty/missing upstream_tag arg → bare '-lite'."""
    _init_repo(tmp_path)
    _commit(tmp_path, ".last-processed", "compound-engineering-v3.8.3\n", "v3.8.3")
    assert lite_suffix_from_git(tmp_path, "") == "-lite"


# -------- cross-corpus validators (validate.py) --------

from validate import (  # noqa: E402
    ValidationError,
    check_dispatch_sources_cross_corpus,
    check_hook_rules_cross_corpus,
)


def _make_dist_with_hooks_and_manifest(
    tmp_path: Path,
    manifest_personas: list[str],
    rule_personas: list[str],
) -> Path:
    dist = tmp_path / "dist"
    (dist / "references" / "agent-prompts").mkdir(parents=True)
    (dist / "hooks").mkdir()
    (dist / "skills").mkdir()
    (dist / "references" / "agent-prompts" / "manifest.json").write_text(
        _json.dumps(
            {
                "schema_version": 1,
                "upstream_tag": "test",
                "agent_count": len(manifest_personas),
                "agents": [
                    {
                        "name": n,
                        "description": f"desc for {n}",
                        "model": "inherit",
                        "tools": None,
                        "prompt_path": f"references/agent-prompts/{n}.md",
                        "upstream_source": f"agents/{n}.agent.md",
                    }
                    for n in manifest_personas
                ],
            }
        )
    )
    (dist / "hooks" / "skill-rules.json").write_text(
        _json.dumps(
            {
                "config": {"haiku_classifier": {"enabled": False}},
                "rules": [
                    {"persona": p, "keywords": ["x"], "phrasing": "x"}
                    for p in rule_personas
                ],
            }
        )
    )
    return dist


def test_check_hook_rules_cross_corpus_passes_when_all_personas_in_manifest(
    tmp_path: Path,
):
    dist = _make_dist_with_hooks_and_manifest(
        tmp_path,
        manifest_personas=["ce-a-reviewer", "ce-b-reviewer", "ce-c-reviewer"],
        rule_personas=["ce-a-reviewer", "ce-b-reviewer"],
    )
    check_hook_rules_cross_corpus(dist)  # no raise


def test_check_hook_rules_cross_corpus_fails_on_unknown_persona(tmp_path: Path):
    dist = _make_dist_with_hooks_and_manifest(
        tmp_path,
        manifest_personas=["ce-a-reviewer"],
        rule_personas=["ce-a-reviewer", "ce-typo-reviewer"],
    )
    with pytest.raises(ValidationError, match="ce-typo-reviewer"):
        check_hook_rules_cross_corpus(dist)


def test_check_hook_rules_cross_corpus_skips_when_no_hooks_dir(tmp_path: Path):
    """Pre-B.7 dist (no hooks/) shouldn't fail this check."""
    dist = tmp_path / "dist"
    (dist / "references" / "agent-prompts").mkdir(parents=True)
    (dist / "references" / "agent-prompts" / "manifest.json").write_text(
        _json.dumps({"agents": []})
    )
    check_hook_rules_cross_corpus(dist)  # no raise — file absent → skip


def _make_dist_with_resolver(
    tmp_path: Path,
    dispatch_sources: list[str],
    via_used_in_skills: list[tuple[str, str]],  # (skill_name, via_value)
) -> Path:
    """Build a tempdir with converter/resources/ce-lite-persona + dist/skills/.

    The resolver source contains a synthetic DISPATCH_SOURCES set; skills
    contain `--via X` references that may or may not be in that set.
    """
    repo_root = tmp_path
    (repo_root / "converter" / "resources").mkdir(parents=True)
    resolver = repo_root / "converter" / "resources" / "ce-lite-persona"
    sources_literal = ",\n    ".join(f'"{s}"' for s in dispatch_sources)
    resolver.write_text(
        f"#!/usr/bin/env python3\nDISPATCH_SOURCES = {{\n    {sources_literal}\n}}\n"
    )

    dist = repo_root / "dist"
    (dist / "skills").mkdir(parents=True)
    for skill_name, via in via_used_in_skills:
        skill_dir = dist / "skills" / skill_name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill_name}\n---\nUse `ce-lite-persona X --prefix --via {via}`.\n"
        )
    return dist


def test_check_dispatch_sources_cross_corpus_passes_when_all_via_in_set(
    tmp_path: Path,
):
    dist = _make_dist_with_resolver(
        tmp_path,
        dispatch_sources=["ce-ask-direct", "ce-code-review", "ce-ask-panel"],
        via_used_in_skills=[
            ("ce-code-review", "ce-code-review"),
            ("ce-ask", "ce-ask-direct"),
        ],
    )
    check_dispatch_sources_cross_corpus(dist)


def test_check_dispatch_sources_cross_corpus_fails_on_unknown_via(tmp_path: Path):
    dist = _make_dist_with_resolver(
        tmp_path,
        dispatch_sources=["ce-ask-direct", "ce-code-review"],
        via_used_in_skills=[
            ("ce-bad", "ce-mystery-source"),
        ],
    )
    with pytest.raises(ValidationError, match="ce-mystery-source"):
        check_dispatch_sources_cross_corpus(dist)


def test_check_dispatch_sources_cross_corpus_skips_when_no_resolver_source(
    tmp_path: Path,
):
    """validate.py run against an isolated dist (e.g., tempdir without sibling
    converter/) should skip this check rather than fail."""
    dist = tmp_path / "dist"
    (dist / "skills" / "ce-x").mkdir(parents=True)
    (dist / "skills" / "ce-x" / "SKILL.md").write_text(
        "---\nname: ce-x\n---\nUses --via ce-anywhere\n"
    )
    check_dispatch_sources_cross_corpus(dist)  # no raise

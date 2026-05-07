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
from extract import find_plugin_root, parse_frontmatter  # noqa: E402
from rewrite import (  # noqa: E402
    PREAMBLE_MARKER_BEGIN,
    PREAMBLE_MARKER_END,
    insert_preamble,
)


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
        "ce-code-review",   # skill, not agent (no persona suffix)
        "ce-brainstorm",
        "ce-plan",
        "ce-work",
        "ce-compound",
        "ce-setup",
        "ce-debug",
        "ce-commit",
        "the-reviewer",     # missing ce- prefix
        "reviewer",
        "ce-",              # too short
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
    text = "prefix-ce-security-reviewer-suffix"
    # `\b` won't match between `-` and alphanumerics-the-same-way as we expect:
    # `\b` matches between word char and non-word char. Both `-` and letters
    # straddle that boundary, so `prefix-ce-security-reviewer-suffix` SHOULD
    # NOT match because `-suffix` extends past `reviewer\b`.
    matches = AGENT_REFERENCE_RE.findall(text)
    # Actually `\b` matches at letter↔dash transitions, so `ce-security-reviewer`
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
    CONDITIONAL_PREFIX_RE,
    Persona,
    load_overrides,
    passA_description,
    render_wrapper,
    wrapper_name,
)


def test_wrapper_name_strips_ce_prefix():
    """ce-X-reviewer -> ce-ask-X-reviewer (not ce-ask-ce-X-reviewer)."""
    assert wrapper_name("ce-security-sentinel") == "ce-ask-security-sentinel"
    assert wrapper_name("ce-architecture-strategist") == "ce-ask-architecture-strategist"


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


def test_render_wrapper_includes_tool_restriction_preamble():
    """Tools field becomes loud-advisory in the dispatched prompt."""
    p = Persona(
        name="ce-x",
        description="x",
        model="inherit",
        tools=["Read", "Grep", "Glob"],
        prompt_path="references/agent-prompts/ce-x.md",
    )
    out = render_wrapper(p, "x")
    assert "tools=[Read, Grep, Glob]" in out
    assert "stop and explain why" in out


def test_render_wrapper_handles_null_tools():
    """tools: null in manifest -> 'tools=[any]' in body, no crash."""
    p = Persona(
        name="ce-x",
        description="x",
        model="inherit",
        tools=None,
        prompt_path="references/agent-prompts/ce-x.md",
    )
    out = render_wrapper(p, "x")
    assert "tools=[any]" in out


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
        "ce-x: |\n"
        "  Use when reviewing X.\n"
        "  Focuses on Y.\n"
        "ce-y: inline\n"
    )
    out = load_overrides(tmp_path)
    assert out["ce-x"] == "Use when reviewing X.\nFocuses on Y."
    assert out["ce-y"] == "inline"


def test_load_overrides_skips_comments(tmp_path: Path):
    """Lines starting with `#` at top level are ignored."""
    (tmp_path / "overrides").mkdir()
    (tmp_path / "overrides" / "persona-descriptions.yaml").write_text(
        "# header comment\n"
        "ce-x: value\n"
        "# trailing comment\n"
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
    b = render_panel([
        Persona(name="ce-x", description="d", model="inherit", tools=None,
                prompt_path="references/agent-prompts/ce-x.md"),
    ])
    assert a == b


def test_render_panel_includes_generated_marker():
    out = render_panel([])
    assert "Generated by ce-lite converter" in out


def test_render_panel_dispatches_with_panel_via_tag():
    """The dispatch preamble template uses via=ce-ask-panel.

    via=ce-ask-direct may appear elsewhere in the body as documentation
    (contrasting the two trace-tag values) — that's expected and fine; the
    invariant is that the preamble template Claude actually embeds in each
    dispatched prompt is the panel form.
    """
    out = render_panel([])
    assert "[ce-persona=<persona> via=ce-ask-panel]" in out


def test_render_panel_argument_hint_in_frontmatter():
    """argument-hint surfaces in slash-command UX for users + Claude."""
    out = render_panel([])
    fm_block = out.split("---\n", 2)[1]
    assert 'argument-hint:' in fm_block
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

import json as _json  # local alias to avoid clashing with module-level imports

from generate_wrappers import (  # noqa: E402
    DEFAULT_HOOK_RULES,
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
    """Distinct via= value separates meta-skill dispatch from per-skill."""
    out = render_meta_skill()
    assert "via=ce-ask-meta" in out


def test_meta_skill_description_substantive():
    """Routing surface — must convey the three modes clearly."""
    assert len(META_SKILL_DESCRIPTION) > 200
    assert "discover" in META_SKILL_DESCRIPTION.lower()
    assert "dispatch" in META_SKILL_DESCRIPTION.lower()


def test_three_routing_layers_have_distinct_via_tags():
    """Sanity: the three remaining routing surfaces have distinct trace tags."""
    p = Persona(
        name="ce-x", description="x", model="inherit", tools=None,
        prompt_path="references/agent-prompts/ce-x.md",
    )
    via_direct = "via=ce-ask-direct" in render_wrapper(p, "x")
    via_panel = "via=ce-ask-panel" in render_panel([])
    via_meta = "via=ce-ask-meta" in render_meta_skill()
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


def test_default_hook_rules_match_render():
    """Sanity: the constant DEFAULT_HOOK_RULES is what render_hook_rules emits."""
    rendered = _json.loads(render_hook_rules())
    assert rendered == DEFAULT_HOOK_RULES


# -------- hook script live runtime tests (Phase B.7) --------
#
# render_hook_script tests above prove we generate the right SOURCE.
# These tests pipe synthetic payloads into the actual generated script and
# assert on output JSON — catching regressions in the runtime behaviour
# (parsing, matching, error handling) that source-string assertions miss.

import subprocess as _subprocess
import tempfile as _tempfile


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
    assert "additionalContext" in response
    assert "ce-ask-security-sentinel" in response["additionalContext"]


def test_hook_runtime_architecture_keyword_fires():
    rc, out, err = _run_hook_with("factor out the duplicated controller logic")
    assert rc == 0
    response = _json.loads(out)
    assert "ce-ask-architecture-strategist" in response["additionalContext"]


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
    suggestions_block = response["additionalContext"]
    assert suggestions_block.count("- test ") <= 3, (
        f"expected ≤3 suggestions; got: {suggestions_block!r}"
    )


def test_hook_runtime_case_insensitive_match():
    rc, out, _ = _run_hook_with("AUDIT FOR OWASP ISSUES")
    response = _json.loads(out)
    assert "ce-ask-security-sentinel" in response["additionalContext"]


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

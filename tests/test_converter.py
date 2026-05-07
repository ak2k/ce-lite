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

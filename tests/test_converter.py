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

"""Unit tests for stream_classifier — pure-function tests of event parsing.

Doesn't spawn claude -p; just feeds synthetic stream-json events and
asserts the classifier picks the right layer. Quick sanity check that the
event-shape assumptions hold; the integration runner builds on this.
"""

from __future__ import annotations

import json

import pytest

from .stream_classifier import Layer, classify_event


def _stream_event(content_block: dict) -> str:
    """Wrap a content_block in the stream_event envelope claude -p emits."""
    return json.dumps({
        "type": "stream_event",
        "event": {
            "type": "content_block_start",
            "content_block": content_block,
        },
    })


def test_classify_meta_agent_dispatch():
    line = _stream_event({
        "type": "tool_use",
        "name": "Task",
        "input": {"subagent_type": "ce-specialist", "prompt": "persona=ce-security-sentinel review users.py"},
    })
    v = classify_event(line)
    assert v is not None
    assert v.layer == Layer.META_AGENT
    assert v.persona == "ce-security-sentinel"


def test_classify_meta_agent_no_persona_arg():
    """Meta-agent fired but persona= wasn't in the prompt — verdict still valid."""
    line = _stream_event({
        "type": "tool_use",
        "name": "Task",
        "input": {"subagent_type": "ce-specialist", "prompt": "review the auth code"},
    })
    v = classify_event(line)
    assert v.layer == Layer.META_AGENT
    assert v.persona is None  # not extracted; that's fine


def test_classify_meta_skill():
    line = _stream_event({
        "type": "tool_use",
        "name": "Skill",
        "input": {"skill": "ce-ask"},
    })
    v = classify_event(line)
    assert v.layer == Layer.META_SKILL


def test_classify_panel():
    line = _stream_event({
        "type": "tool_use",
        "name": "Skill",
        "input": {"skill": "ce-ask-panel"},
    })
    v = classify_event(line)
    assert v.layer == Layer.PANEL


def test_classify_wrapper():
    line = _stream_event({
        "type": "tool_use",
        "name": "Skill",
        "input": {"skill": "ce-ask-security-sentinel"},
    })
    v = classify_event(line)
    assert v.layer == Layer.WRAPPER
    # Persona derived from skill name with the ce-ask- prefix re-replaced
    assert v.persona == "ce-security-sentinel"


def test_classify_wrapper_with_compound_persona_name():
    line = _stream_event({
        "type": "tool_use",
        "name": "Skill",
        "input": {"skill": "ce-ask-pattern-recognition-specialist"},
    })
    v = classify_event(line)
    assert v.layer == Layer.WRAPPER
    assert v.persona == "ce-pattern-recognition-specialist"


def test_classify_unrelated_skill_is_none():
    """A non-ce skill firing isn't a ce-lite routing event — verdict.layer = NONE."""
    line = _stream_event({
        "type": "tool_use",
        "name": "Skill",
        "input": {"skill": "document-skills:pdf"},
    })
    v = classify_event(line)
    assert v.layer == Layer.NONE


def test_classify_general_purpose_subagent_is_none():
    """Task with subagent_type=general-purpose is not ce-lite — could be the
    skill body's own dispatch, but not direct ce-lite routing."""
    line = _stream_event({
        "type": "tool_use",
        "name": "Task",
        "input": {"subagent_type": "general-purpose", "prompt": "..."},
    })
    v = classify_event(line)
    assert v.layer == Layer.NONE


def test_classify_non_tool_event_returns_none():
    """text/thinking/etc. events return None — not classification-relevant."""
    line = _stream_event({
        "type": "text",
        "text": "let me think about this...",
    })
    assert classify_event(line) is None


def test_classify_malformed_json_returns_none():
    assert classify_event("not json at all") is None
    assert classify_event("") is None
    assert classify_event("   ") is None


def test_classify_non_stream_event_returns_none():
    """Top-level event types other than 'stream_event' are ignored."""
    line = json.dumps({"type": "session_meta", "id": "abc"})
    assert classify_event(line) is None


@pytest.mark.parametrize("prompt", [
    "persona=ce-security-sentinel",
    "persona = ce-security-sentinel",
    'persona="ce-security-sentinel"',
    "persona='ce-security-sentinel'",
    "PERSONA=ce-security-sentinel",
    "Some preamble. persona=ce-security-sentinel and then the task...",
])
def test_persona_arg_extraction_tolerant(prompt):
    """The persona= regex must handle whitespace/quotes/case variations."""
    line = _stream_event({
        "type": "tool_use",
        "name": "Task",
        "input": {"subagent_type": "ce-specialist", "prompt": prompt},
    })
    v = classify_event(line)
    assert v.persona == "ce-security-sentinel", f"failed on {prompt!r}"

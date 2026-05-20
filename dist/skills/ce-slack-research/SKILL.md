---
name: ce-slack-research
description: "Search Slack for interpreted organizational context -- decisions, constraints, and discussion arcs -- and produce a synthesized research digest with cross-cutting analysis. Use when the user says 'search slack for', 'what did we discuss about', 'slack context for', or 'what does the team think about'. Differs from slack:find-discussions, which returns raw message results without synthesis."
---


<!-- ce-lite: dispatch protocol begin -->

> **ce-lite dispatch protocol.** Persona names referenced below
> (`ce-security-reviewer`, `ce-correctness-reviewer`,
> `ce-learnings-researcher`, …) are NOT registered subagent types in this
> variant — they're data files. To dispatch one:
>
> 1. Run `ce-lite-persona <persona-name> --prefix --via ce-code-review`
>    via Bash (substitute the current orchestrator name for `ce-code-review`
>    if different). The resolver is on PATH (this plugin's `bin/` is
>    exported by Claude Code) and emits the full prompt prefix: persona
>    body, trace tag, and tool-restriction self-policing preamble. Non-zero
>    exit means the resolver hit an error; surface stderr and stop.
>
> 2. Spawn `Agent` with `subagent_type: "general-purpose"` and a meaningful
>    `description: "<persona-name>: <one-line task summary>"` so traces
>    stay readable. The Agent's `prompt` parameter is the resolver output
>    from step 1, a blank line, then this skill's existing context bundle
>    (intent, diff, base, file list, etc.) and output schema.
>
> 3. **Do not** call `Agent({subagent_type: "ce-<name>"})` — those
>    registrations don't exist in this variant.
>
> Persona names elsewhere in this skill (descriptive prose, tables, status
> messages) are documentation; only dispatch sites change.

<!-- ce-lite: dispatch protocol end -->

# /ce-slack-research

Search Slack for organizational context and receive an interpreted research digest.

## Usage

```
/ce-slack-research [topic or question]
/ce-slack-research
```

## Examples

```
/ce-slack-research free trial
/ce-slack-research What did we say about free trial recently?
/ce-slack-research free trial in #proj-reverse-trial
/ce-slack-research onboarding flow after:2026-03-01
```

The input can be a keyword, a natural language question, or include Slack search modifiers like channel hints (`in:#channel`) and date filters (`after:YYYY-MM-DD`). The agent extracts the topic and formulates searches from whatever form the input takes.

## Execution

If no argument is provided, ask what topic to research. Use the platform's blocking question tool: `AskUserQuestion` in Claude Code (call `ToolSearch` with `select:AskUserQuestion` first if its schema isn't loaded), `request_user_input` in Codex, `ask_user` in Gemini, `ask_user` in Pi (requires the `pi-ask-user` extension). Fall back to asking in plain text only when no blocking tool exists in the harness or the call errors (e.g., Codex edit modes) — not because a schema load is required. Never silently skip the question.

Dispatch `ce-slack-researcher` with the user's topic as the task prompt. Omit the `mode` parameter so the user's configured permission settings apply.

The agent handles everything from here -- Slack MCP discovery, search execution, thread reads, and synthesis. It returns a digest with:

- **Workspace identifier** so the user can verify the correct Slack instance was searched
- **Research-value assessment** (high / moderate / low / none) with justification
- **Findings organized by topic** with source channels and dates
- **Cross-cutting analysis** surfacing patterns across findings

If the agent reports that Slack is unavailable (MCP not connected or auth expired), relay the message to the user. Do not attempt alternative research methods.

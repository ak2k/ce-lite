---
name: ce-slack-research
description: "Search Slack for interpreted organizational context -- decisions, constraints, and discussion arcs that shape the current task. Produces a research digest with cross-cutting analysis and research-value assessment, not raw message lists. Use when searching Slack for context during planning, brainstorming, or any task where organizational knowledge matters. Trigger phrases: 'search slack for', 'what did we discuss about', 'slack context for', 'organizational context about', 'what does the team think about', 'any slack discussions on'. Differs from slack:find-discussions which returns individual message results without synthesis."
---


<!-- ce-lite: dispatch protocol begin -->

> **ce-lite dispatch protocol.** This skill ships in the lightweight variant of
> compound-engineering. The persistent agent registrations have been removed;
> specialist persona prompts now live as data files **inside this plugin's
> install directory** under `references/agent-prompts/<name>.md` (catalogued
> in `references/agent-prompts/manifest.json`).
>
> **Locating the persona prompts.** The plugin's install path varies, and a
> bare `references/agent-prompts/<name>.md` resolves relative to the user's
> project — usually wrong. To find the correct path, use Glob with one of
> these patterns (try in order; first non-empty result wins):
>
> 1. `**/.claude/plugins/cache/ce-lite/*/references/agent-prompts/<name>.md`
>    rooted at `~`
> 2. `**/.claude/plugins/cache/*/ce-lite/*/references/agent-prompts/<name>.md`
>    rooted at `~`
> 3. As a fallback, run `find ~/.claude/plugins/cache -name '<name>.md' -path
>    '*/agent-prompts/*' 2>/dev/null | head -1` via Bash.
>
> Cache the discovered plugin root (the directory containing
> `.claude-plugin/plugin.json`) for subsequent persona lookups in this turn —
> all personas live under the same root.
>
> Wherever this skill describes spawning a CE persona by name (e.g.
> `ce-security-reviewer`, `ce-correctness-reviewer`, `ce-learnings-researcher`),
> dispatch as follows:
>
> 1. Read the persona's prompt body from the resolved
>    `<plugin-root>/references/agent-prompts/<name>.md`.
> 2. Spawn an `Agent` (or your harness's equivalent) with `subagent_type:
>    "general-purpose"`. The persona prompt body becomes the prompt prefix; the
>    skill's existing context bundle (intent, diff, base, file list, etc.) and
>    output schema follow as before.
> 3. Apply all dispatch-time options the skill specifies for the original named
>    agent (model override, tools allowlist, parallel-scheduler limits, etc.).
> 4. **Do not** call `Agent({subagent_type: "ce-<name>"})` — those
>    registrations do not exist in this variant. (The single allowed
>    registration is `ce-specialist`, a router agent that internally selects
>    a persona — orchestrator skills generally don't need to call it
>    directly; they read persona prompts and dispatch via general-purpose.)
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

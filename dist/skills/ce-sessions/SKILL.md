---
name: ce-sessions
description: "Search and ask questions about your coding agent session history. Use when asking what you worked on, what was tried before, how a problem was investigated across sessions, what happened recently, or any question about past agent sessions. Also use when the user references prior sessions, previous attempts, or past investigations — even without saying 'sessions' explicitly."
---


<!-- ce-lite: dispatch protocol begin -->

> **ce-lite dispatch protocol.** This skill ships in the lightweight variant of
> compound-engineering. The persistent agent registrations have been removed;
> specialist persona prompts now live as data files at
> `references/agent-prompts/<name>.md` (catalogued in
> `references/agent-prompts/manifest.json`).
>
> Wherever this skill describes spawning a CE persona by name (e.g.
> `ce-security-reviewer`, `ce-correctness-reviewer`, `ce-learnings-researcher`),
> dispatch as follows:
>
> 1. Read the persona's prompt body from `references/agent-prompts/<name>.md`
>    (paths are listed in `manifest.json`).
> 2. Spawn an `Agent` (or your harness's equivalent) with `subagent_type:
>    "general-purpose"`. The persona prompt body becomes the prompt prefix; the
>    skill's existing context bundle (intent, diff, base, file list, etc.) and
>    output schema follow as before.
> 3. Apply all dispatch-time options the skill specifies for the original named
>    agent (model override, tools allowlist, parallel-scheduler limits, etc.).
> 4. **Do not** call `Agent({subagent_type: "ce-<name>"})` — those
>    registrations do not exist in this variant.
>
> Persona names elsewhere in this skill (descriptive prose, tables, status
> messages) are documentation; only dispatch sites change.

<!-- ce-lite: dispatch protocol end -->

# /ce-sessions

Search your session history.

## Usage

```
/ce-sessions [question or topic]
/ce-sessions
```

## Pre-resolved context

**Git branch (pre-resolved):** !`git rev-parse --abbrev-ref HEAD 2>/dev/null || true`

If the line above resolved to a plain branch name (like `feat/my-branch`), pass it to the agent. If it still contains a backtick command string or is empty, it did not resolve — omit it and let the agent derive it at runtime.

## Execution

If no argument is provided, ask what the user wants to know about their session history. Use the platform's blocking question tool: `AskUserQuestion` in Claude Code (call `ToolSearch` with `select:AskUserQuestion` first if its schema isn't loaded), `request_user_input` in Codex, `ask_user` in Gemini, `ask_user` in Pi (requires the `pi-ask-user` extension). Fall back to asking in plain text only when no blocking tool exists in the harness or the call errors (e.g., Codex edit modes) — not because a schema load is required. Never silently skip the question.

Dispatch `ce-session-historian` with the user's question as the task prompt. Omit the `mode` parameter so the user's configured permission settings apply. Include in the dispatch prompt:

- The user's question
- The current working directory
- The repo name and git branch from pre-resolved context (only if they resolved to plain values — do not pass literal command strings)

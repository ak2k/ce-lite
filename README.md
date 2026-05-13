# ce-lite

Lightweight-delegation variant of [compound-engineering-plugin](https://github.com/EveryInc/compound-engineering-plugin).

Drops the persistent agent registrations (~58.8k tokens baseline on Opus 4.7 1M-context) and converts orchestrator commands to spawn `Task` subagents inline. Specialist prompts move to `references/agent-prompts/`. Same expertise, no idle context cost.

## Status

**Phase 1 — scaffolding.** Converter not yet implemented. See the design doc in [ak2k/nix-config:docs/plans/ce-lite-converter.md](https://github.com/ak2k/nix-config/blob/main/docs/plans/ce-lite-converter.md).

Tracking: [`work-nrxg`](https://github.com/ak2k/work/issues) on the private bd workspace.

## Install (planned, after Phase 4)

```text
/plugins marketplace add github:ak2k/ce-lite
/plugins install ce-lite@ce-lite
```

During trial, slash commands are namespaced under `/ce-lite:*` so the original `compound-engineering` plugin can be installed alongside for direct comparison. Before the upstream PR is filed, the live build will switch to `/ce:*` (drop-in replacement).

## Trade-offs vs. upstream CE

| | Upstream CE | ce-lite |
|---|---|---|
| Baseline context cost | ~58.8k tokens (29 persistent agents) | ~30 tokens (slash-command frontmatter only) |
| Specialist coverage | Full | Full (prompts moved to `references/agent-prompts/`) |
| Proactive specialist invocation | ✅ Any conversation can pull `security-sentinel` | ❌ Specialists only fire via `/ce-lite:review` |
| Maintenance | Anthropic / EveryInc | Auto-regenerated daily from upstream by GH Action |

## Build pipeline

1. **`converter/extract.py`** — read `agents/*.agent.md` from upstream CE, relocate bodies to `dist/references/agent-prompts/<name>.md`, build `manifest.json`, copy non-agent files (`skills/`, `.claude-plugin/`, etc.) through.
2. **`converter/rewrite.py`** — insert the ce-lite dispatch protocol preamble into every orchestrator SKILL.md that mentions a manifest agent. **Fail loud** on unrecognized agent mentions.
3. **`converter/generate_wrappers.py`** — emit 49 `ce-ask-<persona>` wrapper skills + the `ce-ask` discovery meta-skill + `ce-ask-panel` + the `UserPromptSubmit` keyword-routing hook.
4. **`converter/generate_commands.py`** — emit `dist/commands/<name>.md` slash-command wrappers, one per skill. Without these the `/ce-plan`, `/ce-work`, `/ce-ask-*` entry points only appear in autocomplete; submit doesn't reliably dispatch.
5. **`converter/validate.py`** — structural assertions over the output (no orphan refs, every Task call points at a real prompt file, manifest count matches file count, every skill has a matching command wrapper, etc.).
6. **`nix flake check`** — formatting, action lint, unit tests.
7. **GH Action** — daily cron polls upstream tag; if newer, runs converter; opens PR if all green; fails red if any step breaks.

No LLM in the CI loop. All transforms deterministic.

## Why not just use Anthropic's `pr-review-toolkit` or `code-review`?

Both are excellent and ~6× cheaper than CE. Choose them if you don't need CE's opinionated personas (DHH-Rails-reviewer, kieran-Python-reviewer, etc.) or specialists like `architecture-strategist`, `data-integrity-guardian`, `performance-oracle`.

ce-lite exists for users who want CE's *coverage* at near-zero baseline cost.

## License

Inherits from [EveryInc/compound-engineering-plugin](https://github.com/EveryInc/compound-engineering-plugin/blob/main/LICENSE).

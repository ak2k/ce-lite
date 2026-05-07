# ce-lite Tier 3 integration eval

Compressed-dogfood harness. Replaces ~2 weeks of casual usage observation
with ~20–40 minutes of automated test runs that exercise every routing
surface (49 wrappers, panel, meta-skill, meta-agent).

## What it answers

**Q1 (this harness): does each routing layer fire when expected?**

Each prompt in `prompts.yaml` declares an expected layer (or `none` for
negative cases) and optionally a specific persona. The runner spawns
`claude -p`, parses `stream-json` events, and classifies the routing
decision based on which `tool_use` event Claude emits (Task with
`subagent_type=ce-specialist`, Skill with `name=ce-ask-*`, etc.).

Q2 (behavior — does the persona produce specialist-shaped output?) and
Q3 (comparative — does ce-lite beat baseline Claude?) are **not yet
implemented**. Add them as separate runners under this directory if/when
the basic routing signal proves stable.

## Running

```bash
# Inside the ce-lite repo
nix develop --command python tests/integration/run_routing_eval.py
```

Common arguments:

| Flag | Default | Notes |
|---|---|---|
| `--filter <prefix>` | none | Only run cases whose id starts with this prefix (`sec-`, `neg-`, `explicit-`, etc.) |
| `--mode realistic\|lite` | realistic | `realistic` runs in user's full Claude env (~80k context per call). `lite` strips context via `--setting-sources ""` + `CLAUDE_CODE_DISABLE_*` env vars (~5k context, cheaper, but doesn't reflect real-environment routing). |
| `--reps N` | 1 | Stability check; runs each case N times. Routing has stochastic noise — 3 reps is a reasonable signal for borderline cases. |
| `--workers N` | 4 | Concurrent `claude -p` invocations. Tune based on quota tolerance. |
| `--timeout N` | 90 | Per-call seconds. Negative cases need to run to completion (no short-circuit), so this caps how long we wait to confirm "nothing fired." |
| `--results <path>` | — | Optional JSONL dump for offline analysis. |
| `--model <id>` | `claude-opus-4-7` | Pass-through to `claude -p --model`. |

## Cost guide (realistic mode, Opus, single rep)

| Run shape | ~Time | ~Quota | Use when |
|---|---|---|---|
| `--filter sec- --reps 1` (4 cases) | 3–5 min | $0.20 | iterating on security-prompt corpus |
| Full corpus, 1 rep (14 cases) | 8–15 min | $0.70 | full structural sweep |
| Full corpus, 3 reps (42 calls) | 25–40 min | $2.10 | regression suite for upstream-bump release gate |

`lite` mode is roughly 10× cheaper but trades realism for speed.

## Reading the report

Each case prints:

```
  ✅ [sec-001-mass-assignment] 3/3 → layer=ce-specialist persona=ce-security-sentinel
     prompt: audit api/v2/users.py for OWASP Top 10 stuff please. especially…
```

- `✅ 3/3` — all 3 reps passed (matched expected layer + persona)
- `❌ 0/3` — never fired the expected layer
- `⚠️ 1/3` — flaky; sometimes fires sometimes doesn't (worth investigating)

For failing cases the report shows what was expected vs what fired.

## Corpus design notes

Cases are grouped by id prefix:

- `sec-` / `arch-` / `simp-` — autonomous-routing positive cases.
  Should fire `ce-specialist` (the meta-agent) or one of the specific
  per-persona wrappers. Both are valid because the convention is "either
  works."
- `explicit-` — slash-command invocations. Must fire the named layer.
- `neg-` — should NOT fire any specialist (Claude handles directly).
- `discover-` — `/ce-ask` discovery flows.

Adding cases: append to `prompts.yaml`. Keep prompts realistic (file
paths, casual phrasing, mix of casing/typos like a real session). Each
case needs at least `id`, `mode`, `prompt`, `accepted_layers`. Optional
`accepted_personas` pins which specific persona must fire.

## Known limitations

- **`claude -p` is not a faithful proxy for interactive Claude Code.**
  This is the big one. Empirical evidence (Phases B.6 + B.7 eval runs):
  - Autonomous-routing prompts: 0/4 wrappers fire even with the full
    Tier 3 stack installed and visible.
  - Explicit slash-command prompts (`/ce-ask-X`): 0/4 dispatch.
    `claude -p` appears to treat slash commands as literal task text,
    confirmed by [issue #837](https://github.com/anthropics/claude-code/issues/837).
  - `UserPromptSubmit` hooks (Phase B.7+): not observed firing in
    `claude -p`. The eval can't distinguish "hook didn't fire" from
    "hook fired but had no effect."
  - Negative cases (no specialist should fire): 4/4 always pass —
    consistent with the hypothesis that `claude -p` simply doesn't
    exercise routing surfaces beyond direct tool calls.

  **Use this harness for structural / regression checks**, NOT for
  validating that wrappers actually fire on real prompts. Real
  interactive dogfood is the only validator for the routing paths.

- **Realistic mode loads the user's full skill set.** Other plugins
  compete for routing attention; results aren't fully isolated. Lite
  mode is the cleaner measurement but doesn't see ce-lite-against-real-
  competitors.
- **Stream-json parsing relies on the event shape.** If Claude Code
  changes its event format the classifier may miss routing decisions
  (will look like a recall regression). Sanity-check by running one
  case with `--results /tmp/out.jsonl` and reviewing actual_layer
  classifications.
- **Behavior + comparative aren't implemented.** This harness only
  answers "did the right layer fire?", not "did the persona produce
  good output?" or "is ce-lite better than vanilla Claude?".
- **Routing has stochastic noise.** Expect non-deterministic results
  on borderline cases. `--reps 3+` for stability, `--reps 1` for fast
  signal.

## What this harness IS good for

Despite the above, the harness still earns its keep:

- **Negative-case regression detection**: confirms the corpus's
  not-a-review prompts continue to produce no specialist tool_use.
  If a future change accidentally makes ce-lite over-trigger, this
  will catch it.
- **Structural well-formedness of the dist**: spawning `claude -p`
  with the plugin installed exercises the load path. If the plugin
  fails to install, claude crashes, or any of our generated files are
  malformed, the eval would surface it.
- **Path-resolution sanity**: wrappers reference plugin-internal
  paths via Glob discovery. If those break, claude -p's stream events
  would show different / errored behavior even if the routing layer
  itself isn't measured.

The harness's findings on the routing-path layer should be read as
**necessary-but-not-sufficient**: passing here doesn't prove
interactive use will work, but failing here likely indicates a
structural problem worth investigating.

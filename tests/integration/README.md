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

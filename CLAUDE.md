# ce-lite

Lightweight-delegation variant of upstream [`EveryInc/compound-engineering-plugin`](https://github.com/EveryInc/compound-engineering-plugin):
this repo's converter (`converter/*.py`) reads upstream's `agents/*.agent.md`
files and emits a derived plugin in `dist/` with persistent agent
registrations stripped (~2k idle tokens per registered agent — ~88k on
upstream v3.8.4, measured via `/context`), specialist
prompts relocated to `references/agent-prompts/`, and orchestrator skills
rewritten to dispatch via a `bin/ce-lite-persona` resolver shim.

## The pipeline

```
upstream-checkout/             converter/                       dist/
   plugins/CE/agents/   ───►   extract.py            ───►   references/agent-prompts/
   plugins/CE/skills/   ───►   rewrite.py            ───►   skills/ (preamble-rewritten)
                               generate_wrappers.py  ───►   skills/ce-ask-*, bin/ce-lite-persona, hooks/
                               generate_commands.py  ───►   commands/
                               validate.py           ───►   (gate — no output)
```

All stages are deterministic; no LLM in CI. `validate.py` includes a
`--upstream` mode that asserts agent bodies and skill bodies are
byte-equivalent between upstream and dist (round-trip property).

## File locations

- `converter/*.py` — the four pipeline stages + `validate.py`. Detailed
  contributor notes in [`converter/CLAUDE.md`](converter/CLAUDE.md).
- `dist/` — generated. **Don't hand-edit.** Regenerate by re-running the
  pipeline (see "Release flow" below). The only hand-edited file inside
  `dist/` is by accident, which `validate.py` will catch on round-trip.
- `dist/CLAUDE.md`, `dist/AGENTS.md` — **upstream-pristine**, copied
  through by `extract.py`. These describe how to edit *upstream
  EveryInc/compound-engineering-plugin*, NOT how to work on ce-lite. They
  ship to end users as part of the plugin. Read them for context on
  upstream conventions, not for guidance on this repo's contributor flow.
- `.last-processed` — single line, the upstream tag the current `dist/`
  was extracted from (e.g. `compound-engineering-v3.8.3`). The
  `lite_suffix_from_git` versioning scheme keys off changes to this file.
- `tests/test_converter.py` — pytest suite, fast (<2s).
- `tests/integration/` — opt-in, quota-spending routing probe driven by
  `claude -p`; run via `nix run .#integration-eval`. Headless `-p` routing is
  noisy/unreliable — treat output as a rough signal, not a passing/failing
  test. NOT part of `nix flake check`.
- `flake.nix` — `nix flake check` runs format + actionlint + pytest.
  `nix run .#integration-eval` for the quota-spending eval.

## Release flow

`plugin.json`'s `version` field is what Claude Code reads for
`/plugin update`. The converter derives it from the upstream version +
the `lite_suffix_from_git`-computed suffix:

- Cross-upstream bump → `<upstream>-lite`
- Same upstream, N converter-touching commits since the last
  `.last-processed` change → `<upstream>-lite.N`
- Workflow/test/docs-only commits don't bump N

**To ship a converter-only change:**

```sh
gh workflow run publish-dist --field upstream_tag=$(cat .last-processed)
```

This opens an auto-PR with the regenerated `dist/` and a bumped version.
Merge it → users get the bump via `/plugin update`. No manual version
edits.

**To pick up a new upstream release:**

`upstream-watch.yml` cron runs daily at 06:17 UTC and triggers
`publish-dist` automatically when `EveryInc/compound-engineering-plugin`
tags a new release. Manual dispatch with a specific tag is the same
mechanism.

## Workflow pitfalls

- `peter-evans/create-pull-request` with the default `GITHUB_TOKEN` is
  intentionally blocked from triggering downstream workflows on the PR
  it opens (GitHub anti-recursion safeguard). So `ci.yml` doesn't run
  on auto-PRs from `publish-dist`. The publish-dist workflow already
  runs the same checks (pytest + `validate.py --upstream` + nix flake
  check) before opening the PR — those *are* the validation signal.
- `publish-dist` requires `fetch-depth: 0` on the main checkout so
  `lite_suffix_from_git` can compute N. Shallow clone (default
  `fetch-depth: 1`) silently falls back to bare `-lite`. Fixed in
  `.github/workflows/publish-dist.yml`; don't remove that override.
- Stacked PRs: merging the parent with `--delete-branch` auto-closes the
  child and disables `gh pr edit --base` retargeting on the closed PR.
  Retarget all dependents to `main` BEFORE merging the parent.

## Adding a new dispatch source

`ce-lite-persona --via <source>` validates against a closed
`DISPATCH_SOURCES` set in `converter/generate_wrappers.py::render_persona_resolver`.
When you add a new orchestrator that dispatches personas, add its name
to that set in the same PR.

## Adding a new persona-suffix

`rewrite.py` fails loudly if upstream introduces an agent name whose
suffix isn't in `converter/DISPATCH_PATTERNS.PERSONA_SUFFIXES`. Extend
that list and regenerate.

## Local dev

```sh
nix develop                           # devshell
nix develop -c pytest tests/          # unit tests
nix flake check                       # format + actionlint + pytest
nix run .#integration-eval            # quota-spending routing eval (opt-in)
```

Full pipeline against the pinned upstream:

```sh
TAG=$(cat .last-processed)
WORK=$(mktemp -d)
git clone --depth 1 --branch "$TAG" \
  https://github.com/EveryInc/compound-engineering-plugin.git \
  "$WORK/upstream"
nix develop -c bash -c "
  python converter/extract.py '$WORK/upstream' '$WORK/dist' '$TAG'
  python converter/rewrite.py '$WORK/dist'
  python converter/generate_wrappers.py '$WORK/dist'
  python converter/generate_commands.py '$WORK/dist'
  python converter/validate.py '$WORK/dist' --upstream '$WORK/upstream'
"
```

# Converter contributor notes

The four-stage pipeline (`extract.py` → `rewrite.py` → `generate_wrappers.py` →
`generate_commands.py`) is deterministic; `validate.py` is the structural
gate. Tests cover every behaviour-load-bearing piece — if you touch a
template, run `nix develop -c python -m pytest tests/` before pushing.

## Shipping a converter-only change

`plugin.json`'s `version` field is what Claude Code's `/plugin update` flow
keys off. The converter derives that field from upstream's version + a
`-lite[.N]` suffix computed by `extract.lite_suffix_from_git`:

- **Cross-upstream bump** (different upstream tag than `.last-processed`): N
  resets, version is `<upstream>-lite`.
- **Same upstream, N converter-touching commits since the last
  `.last-processed` change**: version is `<upstream>-lite.N`.

So when you make a converter-only change between upstream releases:

1. Merge your converter PR to `main` (the usual review flow).
2. Manually trigger `publish-dist` against the current upstream tag:
   ```
   gh workflow run publish-dist --field upstream_tag=$(cat .last-processed)
   ```
3. That opens a PR with the regenerated `dist/`. `plugin.json`'s version
   will have bumped to `<upstream>-lite.<count>` where count is your new
   converter commits since the last bump.
4. Merge that PR. Users on `<upstream>-lite[.N-1]` get the bump via
   `/plugin update`.

No manual version-field editing is needed — the suffix is git-derived.

## Adding a new dispatch source for the resolver

`ce-lite-persona --via <source>` validates `source` against
`DISPATCH_SOURCES` (a closed set in `generate_wrappers.render_persona_resolver`).
When you add a new orchestrator that dispatches personas, add its name to
that set in the same PR. The resolver fails loud on unknown `--via` values,
so a missing entry surfaces at first runtime, not silently.

## Upstream-drift detector

`rewrite.py` is the build-time canary for upstream introducing new
agent-name shapes. If `validate.py` reports "stray agent references not in
manifest," `DISPATCH_PATTERNS.PERSONA_SUFFIXES` is missing the new suffix —
extend it, regenerate, rerun.

## Workflow shortcuts

- `nix flake check` — format, actionlint, all unit tests (same as CI).
- Re-run the full pipeline locally:
  ```
  rm -rf /tmp/ce-lite-regen && mkdir /tmp/ce-lite-regen && cd /tmp/ce-lite-regen
  git clone --depth 1 --branch $(cat ~/src/ak2k/ce-lite/.last-processed) \
    https://github.com/EveryInc/compound-engineering-plugin.git upstream-checkout
  nix develop ~/src/ak2k/ce-lite -c bash -c '
    python ~/src/ak2k/ce-lite/converter/extract.py upstream-checkout dist-new "$(cat ~/src/ak2k/ce-lite/.last-processed)"
    python ~/src/ak2k/ce-lite/converter/rewrite.py dist-new
    python ~/src/ak2k/ce-lite/converter/generate_wrappers.py dist-new
    python ~/src/ak2k/ce-lite/converter/generate_commands.py dist-new
    python ~/src/ak2k/ce-lite/converter/validate.py dist-new --upstream upstream-checkout
  '
  ```

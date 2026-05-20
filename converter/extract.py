"""Extract agent prompts from upstream CE.

For CE v3.x layout:
  upstream/plugins/compound-engineering/
    ├── .claude-plugin/plugin.json
    ├── agents/<name>.agent.md       ← these get extracted out
    ├── skills/<name>/SKILL.md       ← copied as-is (rewrite.py prepends a preamble)
    └── ... (other top-level files)

After extract.py:
  dist/
    ├── .claude-plugin/plugin.json   (name rewritten to ce-lite-* during trial)
    ├── references/agent-prompts/
    │   ├── manifest.json
    │   └── <name>.md                (just the agent body, no frontmatter)
    └── skills/<name>/SKILL.md       (untouched here)
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Frontmatter is fenced with "---" lines at the top of each agent .md file.
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)

# Agent files in v3.x are named "<name>.agent.md" inside agents/.
AGENT_FILE_SUFFIX = ".agent.md"

# How we rename the plugin during trial. Switch to "ce" before filing upstream PR.
LITE_NAME = "ce-lite"
LITE_NAMESPACE_PREFIX = "ce-lite"  # affects plugin.json and slash-command rename


@dataclass(frozen=True)
class AgentRecord:
    name: str
    description: str
    body: str
    model: str | None
    tools: list[str] | None
    source_path: str  # relative to the upstream plugin root


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Raise ValueError if frontmatter missing/malformed."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("missing or malformed frontmatter (expected '---' fences)")

    raw_yaml = m.group(1)
    body = m.group(2)

    # Light-touch YAML parser: we only need to extract a small known set of keys
    # (name, description, model, tools). Avoid pulling in PyYAML to keep
    # converter dependencies minimal — the agent frontmatter shape is stable.
    fm: dict = {}
    current_key: str | None = None
    for line in raw_yaml.splitlines():
        if not line.strip():
            continue
        # Top-level "key: value" or "key:" (followed by indented continuation)
        if not line.startswith(" ") and not line.startswith("\t"):
            if ":" not in line:
                raise ValueError(f"malformed frontmatter line: {line!r}")
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            # Strip surrounding quotes if any
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            fm[key] = value
            current_key = key
        else:
            # Indented continuation — append to current key as a string
            if current_key is None:
                raise ValueError(f"unexpected indented frontmatter line: {line!r}")
            fm[current_key] = (fm.get(current_key) or "") + " " + line.strip()

    return fm, body


def parse_tools(tools_value: str | None) -> list[str] | None:
    if not tools_value:
        return None
    # CE uses "Read, Grep, Glob, Bash" comma-separated form
    return [t.strip() for t in tools_value.split(",") if t.strip()]


def find_plugin_root(upstream_dir: Path) -> Path:
    """Locate <upstream>/plugins/compound-engineering/ (or upstream itself if it's the plugin root)."""
    candidate = upstream_dir / "plugins" / "compound-engineering"
    if (candidate / ".claude-plugin").is_dir():
        return candidate
    if (upstream_dir / ".claude-plugin").is_dir():
        return upstream_dir
    raise FileNotFoundError(
        f"could not locate plugin root under {upstream_dir} "
        f"(expected .claude-plugin/ at upstream root or plugins/compound-engineering/)"
    )


def extract_agents(plugin_root: Path) -> list[AgentRecord]:
    agents_dir = plugin_root / "agents"
    if not agents_dir.is_dir():
        raise FileNotFoundError(f"no agents/ directory at {agents_dir}")

    records: list[AgentRecord] = []
    for path in sorted(agents_dir.glob(f"*{AGENT_FILE_SUFFIX}")):
        text = path.read_text(encoding="utf-8")
        try:
            fm, body = parse_frontmatter(text)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc

        name = fm.get("name")
        if not name:
            raise ValueError(f"{path}: agent has no 'name' in frontmatter")

        # Sanity: filename should match name
        expected_filename = f"{name}{AGENT_FILE_SUFFIX}"
        if path.name != expected_filename:
            raise ValueError(
                f"{path}: filename does not match name (expected {expected_filename!r})"
            )

        records.append(
            AgentRecord(
                name=name,
                description=fm.get("description", ""),
                body=body.strip() + "\n",
                model=fm.get("model"),
                tools=parse_tools(fm.get("tools")),
                source_path=str(path.relative_to(plugin_root)),
            )
        )
    return records


def write_relocated_prompts(records: list[AgentRecord], dist_dir: Path) -> None:
    out_dir = dist_dir / "references" / "agent-prompts"
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in records:
        (out_dir / f"{r.name}.md").write_text(r.body, encoding="utf-8")


def write_manifest(
    records: list[AgentRecord],
    dist_dir: Path,
    upstream_tag: str | None,
) -> None:
    manifest = {
        "schema_version": 1,
        "upstream_tag": upstream_tag,
        "agent_count": len(records),
        "agents": [
            {
                "name": r.name,
                "description": r.description,
                "model": r.model,
                "tools": r.tools,
                "prompt_path": f"references/agent-prompts/{r.name}.md",
                "upstream_source": r.source_path,
            }
            for r in records
        ],
    }
    out = dist_dir / "references" / "agent-prompts" / "manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def copy_non_agent_files(plugin_root: Path, dist_dir: Path) -> None:
    """Copy everything from the plugin root EXCEPT agents/ to dist/."""
    for entry in plugin_root.iterdir():
        if entry.name == "agents":
            continue
        dest = dist_dir / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, dest)


def lite_suffix_from_git(repo_root: Path, new_upstream_tag: str) -> str:
    """Compute the lite version suffix from git history.

    Returns `'-lite'` when this conversion is the first for `new_upstream_tag`
    (i.e., the prior `.last-processed` content differs, or none exists), and
    `'-lite.N'` otherwise — where N is the number of commits touching
    `converter/` since the commit that last set `.last-processed` to the
    current upstream tag.

    Lets converter-only changes ship as `/plugin update`-visible bumps
    (`3.8.3-lite` → `3.8.3-lite.1` → `3.8.3-lite.2`, …) by re-running
    `publish-dist` against the unchanged upstream tag. When the upstream
    tag actually changes, N resets to 0 → bare `-lite`.

    Falls back to bare `-lite` on any error (no git, no history, not a
    repo) so the converter still produces a valid version string in
    weird invocation contexts (eval'd in tests, ad-hoc local runs).
    """
    if not new_upstream_tag:
        return "-lite"
    last_processed = repo_root / ".last-processed"
    if not last_processed.is_file():
        return "-lite"
    if last_processed.read_text(encoding="utf-8").strip() != new_upstream_tag:
        # Cross-upstream bump → reset counter.
        return "-lite"
    try:
        last_bump = subprocess.check_output(
            ["git", "-C", str(repo_root), "log", "-n", "1",
             "--format=%H", "--", ".last-processed"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        if not last_bump:
            return "-lite"
        count = int(subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-list", "--count",
             f"{last_bump}..HEAD", "--", "converter/"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return "-lite"
    return "-lite" if count == 0 else f"-lite.{count}"


def rewrite_plugin_json(
    dist_dir: Path,
    upstream_tag: str | None,
    repo_root: Path | None = None,
) -> None:
    """Rename the plugin to ce-lite-<...> in .claude-plugin/plugin.json.

    Version suffix is derived from git history via `lite_suffix_from_git`
    so converter-only changes can ship as `-lite.N` bumps between upstream
    releases. `repo_root` defaults to this script's parent's parent (the
    ce-lite checkout root); tests override it.

    Codex/Cursor manifests left untouched for parity for now (they aren't the
    Claude Code install path, and namespace concerns differ per platform).
    """
    plugin_json = dist_dir / ".claude-plugin" / "plugin.json"
    if not plugin_json.is_file():
        raise FileNotFoundError(f"missing {plugin_json}")
    data = json.loads(plugin_json.read_text(encoding="utf-8"))
    upstream_version = data.get("version", "0.0.0")
    data["name"] = LITE_NAME
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent
    suffix = lite_suffix_from_git(repo_root, upstream_tag or "")
    data["version"] = f"{upstream_version}{suffix}"
    data["description"] = (
        "Lightweight-delegation variant of compound-engineering. "
        "Agent registrations removed; specialist prompts loaded on demand from "
        "references/agent-prompts/. See https://github.com/ak2k/ce-lite."
    )
    data["homepage"] = "https://github.com/ak2k/ce-lite"
    data["repository"] = "https://github.com/ak2k/ce-lite"
    if upstream_tag:
        data.setdefault("ce_lite", {})["upstream_tag"] = upstream_tag
    plugin_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main(upstream_dir: str, dist_dir: str, upstream_tag: str | None = None) -> int:
    upstream_path = Path(upstream_dir).resolve()
    dist_path = Path(dist_dir).resolve()

    if dist_path.exists():
        # Always start from a clean dist/ so deletions in upstream propagate
        shutil.rmtree(dist_path)
    dist_path.mkdir(parents=True)

    plugin_root = find_plugin_root(upstream_path)

    print(f"plugin root: {plugin_root}", file=sys.stderr)
    print(f"dist root:   {dist_path}", file=sys.stderr)

    print("extracting agents...", file=sys.stderr)
    records = extract_agents(plugin_root)
    print(f"  found {len(records)} agents", file=sys.stderr)

    print("relocating prompts to references/agent-prompts/...", file=sys.stderr)
    write_relocated_prompts(records, dist_path)
    write_manifest(records, dist_path, upstream_tag)

    print("copying non-agent plugin files...", file=sys.stderr)
    copy_non_agent_files(plugin_root, dist_path)

    print("rewriting .claude-plugin/plugin.json...", file=sys.stderr)
    rewrite_plugin_json(dist_path, upstream_tag)

    print("extract.py: done", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print(
            "usage: extract.py <upstream-dir> <dist-dir> [upstream-tag]",
            file=sys.stderr,
        )
        sys.exit(2)
    tag = sys.argv[3] if len(sys.argv) == 4 else None
    sys.exit(main(sys.argv[1], sys.argv[2], tag))

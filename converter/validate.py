"""Structural validation suite for converted plugin output.

Asserts:
- No unrecognized agent-name mentions in commands.
- Every Task() call references a real prompt file.
- All Markdown parses.
- All YAML frontmatter is valid.
- plugin.json schema valid.
- No prompt file empty/truncated.
- Manifest count matches prompt-file count; no orphans.

Phase 3: implement.
"""

from __future__ import annotations

import sys


def main(dist_dir: str) -> int:
    raise NotImplementedError("validate.py: implement in Phase 3")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: validate.py <dist-dir>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))

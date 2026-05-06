"""Extract agent prompts from upstream CE.

Reads agents/**/<name>.md from an upstream CE checkout, writes each body
(without frontmatter) to dist/references/agent-prompts/<name>.md, and
builds a manifest mapping agent names to file paths.

Phase 2: implement.
"""

from __future__ import annotations

import sys


def main(upstream_dir: str, dist_dir: str) -> int:
    raise NotImplementedError("extract.py: implement in Phase 2")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: extract.py <upstream-dir> <dist-dir>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))

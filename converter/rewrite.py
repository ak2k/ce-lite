"""Rewrite CE orchestrator commands to use lightweight-delegation Task spawns.

Pattern-based regex transform of commands/ce/*.md. Replaces dispatch mentions
of agent names (per the manifest) with explicit Task call boilerplate that
reads the relocated prompt file at runtime.

Fails loud on any unrecognized agent mention. No silent skips.

Phase 2: implement.
"""

from __future__ import annotations

import sys


def main(dist_dir: str) -> int:
    raise NotImplementedError("rewrite.py: implement in Phase 2")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: rewrite.py <dist-dir>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))

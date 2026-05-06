"""Whitelist of dispatch-prose patterns recognized by rewrite.py.

Each pattern is a compiled regex that captures an `agent` group naming the
specialist to invoke. After replacement, rewrite.py performs a stray-mention
scan: any remaining `<known-agent-name>` token in the rewritten file is a
build failure (either a new prose pattern needs to be added here, or the
mention is documentation that needs explicit annotation).

Initial set seeded from upstream CE @ <pinned-tag>; extend over time as
upstream's prose drifts.

Phase 2: populate.
"""

from __future__ import annotations

import re

# Each entry: (regex, description for debug output on failure).
# The regex MUST capture a named group `agent` containing the specialist name.
DISPATCH_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Phase 2: populate against current upstream CE checkout.
]

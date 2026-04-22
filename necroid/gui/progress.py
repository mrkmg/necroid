"""Parser for the `==> step N/M: <text>` progress lines emitted by the CLI.

The `N/M:` prefix is optional; the CLI uses the bare `==> <text>` form for
one-off status markers too. Friendly label lookup is delegated to
`constants.STEP_FRIENDLY` (first substring hit wins).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .constants import STEP_FRIENDLY


STEP_RE = re.compile(r"^==>\s+(?:step\s+(\d+)/(\d+):\s+)?(.+)$")


@dataclass(frozen=True)
class StepLine:
    idx: Optional[int]       # step number if `step N/M:` was present
    total: Optional[int]
    raw_text: str            # the trailing text from the step line (used for STEP_FRIENDLY lookup)
    friendly: str            # human-facing label; falls back to raw_text


def parse_step_line(line: str) -> Optional[StepLine]:
    """Parse a single line of CLI output. Returns a `StepLine` if the line is
    a `==>` progress marker, else None.

    Matches the previous inline regex + lookup in `ModderApp._parse_status_line`
    and `ModderApp._cmd_busy_headline`.
    """
    m = STEP_RE.match(line.rstrip())
    if not m:
        return None
    idx = int(m.group(1)) if m.group(1) else None
    total = int(m.group(2)) if m.group(2) else None
    raw = m.group(3).strip()
    friendly = raw
    for key, label in STEP_FRIENDLY.items():
        if key in raw:
            friendly = label
            break
    return StepLine(idx=idx, total=total, raw_text=raw, friendly=friendly)

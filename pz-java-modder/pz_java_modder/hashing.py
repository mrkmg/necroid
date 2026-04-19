"""SHA256 helpers. Hex output is uppercase to match the legacy PS state format."""
from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 64 * 1024


def file_sha256(path: Path) -> str | None:
    """SHA256 of a file (uppercase hex) or None if missing."""
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fp:
        while True:
            buf = fp.read(_CHUNK)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest().upper()


def string_sha256(text: str) -> str:
    """SHA256 of a UTF-8 string (lowercase hex — matches PS `Get-StringHash256`)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

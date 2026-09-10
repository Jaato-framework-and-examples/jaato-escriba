"""What was left uncurated last time.

Nothing here talks to the SDK: it looks at the store on disk and counts.
It lives outside `run_escriba.py` for the same reason `voice.py` does —
the driver is meant to read as two sessions and three `ask` calls, not as
plumbing.

WHY COUNT RAW MEMORIES INSTEAD OF KEEPING A FLAG.  A "last session ended
cleanly" flag has to be written on open and cleared on close, and it
drifts on its own: a `kill -9` in between leaves it lying.  Raw memories
ARE the condition rather than a signal of it — if any exist, nobody has
judged them yet, however the previous session ended.

And it catches a case a flag cannot see: the curator judges eight at a
time by design, so a session that ended cleanly can still leave a
backlog.  Nothing failed there, and there is still work pending.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import yaml

#: Where a forgotten store goes.  NOT deleted: moved.  Erasing a second
#: brain is not an operation anyone should have to get right first time,
#: and a directory rename costs nothing next to the alternative.
ATTIC = Path(".jaato/forgotten")

#: Where the store path comes from.  Read from the profile rather than
#: duplicated here, so that two places never decide the same thing: change
#: `storage_path` and this follows it.
PROFILE = Path(".jaato/profiles/_base_escriba.yaml")


def _store_root(workspace: Path) -> Path:
    """The store directory, resolved the way the framework resolves it.

    `MemoryStorage` accepts a `.jsonl` path for backwards compatibility
    but treats it as a ROOT: `x.jsonl` becomes the directory `x/` holding
    `raw/` and `curated.jsonl` (storage.py:250-270, jaato#912).  That rule
    is mirrored here so the count does not depend on how the path happens
    to be spelled in the profile.
    """
    profile = yaml.safe_load((workspace / PROFILE).read_text())
    declared = profile["plugin_configs"]["memory"]["storage_path"]
    p = workspace / declared
    return p.parent / p.stem if p.suffix == ".jsonl" else p


def uncurated_count(workspace: Path) -> int:
    """How many memories are waiting for the curator.  0 if no store yet."""
    raw = _store_root(workspace) / "raw"
    return len(list(raw.glob("*.json"))) if raw.is_dir() else 0


def counts(workspace: Path) -> dict:
    """What the store holds right now: raw and curated."""
    root = _store_root(workspace)
    raw = root / "raw"
    curated = root / "curated.jsonl"
    return {
        "raw": len(list(raw.glob("*.json"))) if raw.is_dir() else 0,
        "curated": sum(1 for line in curated.read_text().splitlines()
                       if line.strip()) if curated.is_file() else 0,
    }


def forget(workspace: Path) -> Path | None:
    """Move the whole store aside. Returns where it went, or None if empty.

    A move rather than a delete, and the returned path is printed, so
    "start from scratch" stays a decision the person can walk back from.
    """
    root = _store_root(workspace)
    if not root.exists():
        return None
    where = workspace / ATTIC / datetime.now().strftime("%Y%m%d_%H%M%S") / root.name
    where.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(root), str(where))
    return where

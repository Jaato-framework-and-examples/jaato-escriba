"""Keeping the audio archive inside two limits that answer to nobody else.

TWO NUMBERS, TWO AUTHORITIES.  A deployment is bounded by a disk and by a
retention policy, and neither knows about the other:

    the FLOOR    `record_keeping:` in the profile — how long records must
                 be kept (EU AI Act Arts. 12, 19, 26(6)).  Declared per
                 profile, inherited most-restrictive-wins, and written
                 where the model cannot reach it: `.jaato/profiles/` is on
                 the AppArmor write-deny list.
    the CEILING  one integer in a root-owned file outside the workspace
                 root — how many megabytes of recordings one person may
                 hold.  The sysadmin's number, not the regulation's.

They can disagree.  When the ceiling cannot be met without deleting
something the floor requires keeping, THAT IS REPORTED AND NOTHING IS
DELETED: a housekeeping pass that silently wins against a declared
retention is the exact failure Art. 19(1) is about, and a full disk is a
problem a person can be told about.  So this exits non-zero and names the
workspace instead of resolving it.

WHERE THE FLOOR COMES FROM, and why not from the profile file.  The clocks
that govern a recording are the RESOLVED ones — after `inherits:` and
most-restrictive-wins — and profile resolution lives in jaato-server, not
in the SDK (`jaato_sdk` ships no `discover_profiles`; `explain` has to
`--connect` to the daemon, and its SDK-side CLI cannot even name a profile
inside a set).  Re-implementing the merge here would be a second statement
of the framework's rule that drifts from the first.

So the archive carries its own clocks: at the first turn the driver copies
`profile_snapshot.record_keeping` out of the session record the daemon
sealed — post-merge, by construction — into the manifest's policy row.
This module reads manifests and nothing else.  Three properties fall out:
it needs no daemon, it needs nothing but the stdlib, and an archive says
which policy was live WHEN IT WAS RECORDED rather than inheriting
whatever is live when it is swept.

WHAT IS PROTECTED IS WHAT WAS DECLARED.  `record_keeping` absent means
"delete removes everything, exactly as before" — the framework's own
words — so this module invents no protection where none was declared.
A clock of 0 is "keep until deleted" and never expires.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import archive as _archive
import workspace as _workspace

#: The policy row's discriminator in the manifest.  `stage` is the
#: framework's own word for which kind of record a row is (every ledger
#: row carries one), so a reader that knows the ledger knows this.
POLICY_STAGE = "policy"

#: What the archive's own directory name means.  Parsed rather than read
#: off the filesystem: an mtime moves when a file is copied, and the
#: question "how old is this conversation" must not depend on whether
#: somebody rsynced the workspace.  The format belongs to the module that
#: MAKES the name, so it is borrowed rather than restated.
STAMP = _archive.STAMP


class BudgetUnreadable(RuntimeError):
    """The ceiling could not be read, so nothing may be pruned.

    Deliberately fatal.  A missing budget file with a built-in default
    would mean that the day the config goes astray, every archive on the
    host quietly shrinks to a number nobody chose.
    """


def ceiling(path: Path) -> int:
    """The per-person ceiling in BYTES, from a file holding megabytes.

    One integer, because the file is edited by a person under pressure and
    a format with sections is a format with a syntax error in it.  Blank
    lines and `#` comments are allowed; anything else is refused by name.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BudgetUnreadable(f"{path}: {exc.strerror or exc}") from exc
    lines = [l.strip() for l in text.splitlines()]
    values = [l for l in lines if l and not l.startswith("#")]
    if len(values) != 1:
        raise BudgetUnreadable(
            f"{path}: expected one integer (megabytes per person), "
            f"found {len(values)} values")
    try:
        megabytes = int(values[0])
    except ValueError as exc:
        raise BudgetUnreadable(f"{path}: {values[0]!r} is not an integer") from exc
    if megabytes <= 0:
        raise BudgetUnreadable(f"{path}: {megabytes} is not a usable ceiling")
    return megabytes * 1024 * 1024


@dataclass
class Clocks:
    """The resolved retention a recording was made under.

    THREE STATES, AND THE SWEEP TREATS EACH DIFFERENTLY:

        unresolved  the sealed snapshot could not be read.  Nothing is
                    known, so nothing may go — not even for the ceiling.
        declared    a clock says how long this is kept.  Inside it, the
                    floor stands; past it, the expiry pass removes it.
        undeclared  the snapshot was read and the profile declares no
                    retention.  The framework's own reading applies —
                    "delete removes everything, exactly as before" — so
                    nothing promised to keep this and the CEILING may
                    take it.  The expiry pass still may not: there is no
                    clock to have run out.

    Collapsing `unresolved` into `undeclared` is how a sweep deletes an
    archive it was merely unable to ask about, so they stay apart.
    """
    retention_days: Optional[int] = None
    conversation_retention_days: Optional[int] = None
    resolved: bool = False

    @classmethod
    def from_row(cls, row: dict) -> "Clocks":
        keeping = row.get("record_keeping")
        if not isinstance(keeping, dict):
            return cls(resolved=bool(row.get("resolved")))
        return cls(retention_days=keeping.get("retention_days"),
                   conversation_retention_days=keeping.get(
                       "conversation_retention_days"),
                   resolved=bool(row.get("resolved")))

    @staticmethod
    def expired(days: Optional[int], age: timedelta) -> bool:
        """Has a DECLARED clock run out?

        An undeclared clock never expires — there was no promise to come
        to an end.  A clock of 0 is "keep until deleted" and never
        expires either, and the framework is explicit that 0 cannot win
        the most-restrictive merge, so it is always a deliberate forever.
        """
        if days is None or days == 0:
            return False
        return age >= timedelta(days=days)

    @staticmethod
    def protects(days: Optional[int], age: timedelta) -> bool:
        """Is something this old still inside a DECLARED clock?

        The negation of `expired` only for a declared one: undeclared is
        neither expired nor protected, which is exactly the state the
        ceiling is allowed to act on and the expiry pass is not.
        """
        if days is None:
            return False
        return days == 0 or age < timedelta(days=days)


@dataclass
class Removal:
    """One thing that went, and when, so the archive stays honest."""
    path: str
    bytes: int
    reason: str


@dataclass
class Report:
    """What one person's archive looked like, and what was done to it."""
    workspace: Path
    held: int = 0                      # bytes before
    freed: int = 0
    removals: List[Removal] = field(default_factory=list)
    #: Archives whose manifest declares no policy row at all. Never
    #: pruned, always named: this module does not delete evidence whose
    #: governing clock it could not read.
    undeclared: List[str] = field(default_factory=list)
    #: Set when the ceiling cannot be met without deleting inside a
    #: declared floor. The whole reason this runs under a person's eye.
    conflict: Optional[str] = None

    @property
    def remaining(self) -> int:
        return self.held - self.freed


def _archives(ws: Path) -> List[Tuple[datetime, Path]]:
    """Every recorded conversation in a workspace, oldest first."""
    root = ws / _archive.ROOT
    found = []
    for d in root.iterdir() if root.is_dir() else ():
        if not d.is_dir():
            continue
        # `<stamp>` or `<stamp>_<n>`, the second being two conversations
        # opened in one second.  Splitting the suffix off matters more
        # than it looks: an unparsed name is passed over as "not ours",
        # so a real archive would go unswept and unreported.
        stem = d.name.rsplit("_", 1)[0] if d.name.count("_") == 2 else d.name
        try:
            found.append((datetime.strptime(stem, STAMP), d))
        except ValueError:
            continue        # not an archive directory; not ours to touch
    return sorted(found)


def _clocks(archive_dir: Path) -> Optional[Clocks]:
    """The policy row of one archive, or None when it has none."""
    manifest = archive_dir / "manifest.jsonl"
    if not manifest.is_file():
        return None
    with manifest.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("stage") == POLICY_STAGE:
                return Clocks.from_row(row)
    return None


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _recordings(archive_dir: Path) -> List[Path]:
    """The media of one archive, model speech first.

    Model speech goes before the person's own voice because the archive is
    ~93 % model speech by construction — inbound is MP3 at 32 kbit/s and
    outbound is s16le PCM — so dropping it reclaims the most bytes for the
    least evidence, and what it says is recoverable from the transcript the
    manifest keeps.  This is a TIE-BREAK inside what the floor already
    allows to go, never a reason to go past the floor.
    """
    out = sorted(archive_dir.glob("out_*"))
    inn = sorted(archive_dir.glob("in_*"))
    return out + inn


def _tomb(path: Path, record: dict) -> None:
    """Append one tombstone.

    ONE FILE PER PERSON, at the root of `audio/`, never inside the
    archive a removal came from — an archive's own tombstone goes with it
    the day the archive goes, which would delete the record of the
    pruning along with the pruned thing.  It also gives an auditor one
    file to read instead of a hunt through directories, some of which no
    longer exist.
    """
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def prune(ws: Path, limit: int, now: Optional[datetime] = None,
          dry_run: bool = False) -> Report:
    """Bring one workspace's recordings inside `limit`, floor permitting.

    Two passes, and the order between them is the whole design:

      1. EXPIRY.  What a declared clock no longer covers goes, whether or
         not the person is over the ceiling.  Retention is not a budget:
         a deployment that declared 30 days does not get to keep 29 of
         them because there happens to be room.
      2. CEILING.  Only then, and only into what no declared clock
         protects.  If that is not enough, the shortfall is REPORTED and
         nothing more is deleted.
    """
    now = now or datetime.now()
    report = Report(workspace=ws)
    found = _archives(ws)
    audio_root = ws / _archive.ROOT
    # `audio/` AS A WHOLE, the tombstone file included: a total that
    # leaves something out is not the number the disk answers to, and
    # `freed` is then guaranteed to be bytes that `held` counted.
    report.held = _size(audio_root)

    def remove(target: Path, reason: str) -> None:
        size = _size(target)
        report.removals.append(Removal(
            path=str(target.relative_to(ws)), bytes=size, reason=reason))
        report.freed += size
        if dry_run:
            return
        if target.is_dir():
            for f in sorted(target.rglob("*"), reverse=True):
                f.unlink() if f.is_file() or f.is_symlink() else f.rmdir()
            target.rmdir()
        else:
            target.unlink()
        _tomb(audio_root / "pruned.jsonl",
              {"at": now.isoformat(timespec="seconds"),
                     "removed": str(target.relative_to(ws)),
                     "bytes": size, "reason": reason})

    # An archive whose policy row is missing or unresolved is passed over
    # ENTIRELY, by both passes, and named in the report instead.  The one
    # irreversible thing here is deletion, so not knowing the floor is a
    # reason to keep.
    judged = []
    for stamp, d in found:
        clocks = _clocks(d)
        if clocks is None or not clocks.resolved:
            report.undeclared.append(str(d.relative_to(ws)))
            continue
        judged.append((stamp, d, clocks))

    # ---- pass one: what its own declared retention no longer covers
    for stamp, d, clocks in judged:
        age = now - stamp
        if clocks.expired(clocks.conversation_retention_days, age):
            for media in _recordings(d):
                remove(media, "conversation_retention_days")
        # The manifest is the audit record and outlives the recordings it
        # names.  The whole archive goes only once ITS clock has run out
        # and there is nothing left of the conversation to name.
        if (clocks.expired(clocks.retention_days, age)
                and not _recordings(d)):
            remove(d, "retention_days")

    # ---- pass two: the ceiling, which may not reach inside the floor
    protected = []
    for stamp, d, clocks in judged:
        if not d.exists():
            continue
        age = now - stamp
        if clocks.protects(clocks.conversation_retention_days, age):
            protected.append(f"{d.name} (conversation_retention_days="
                             f"{clocks.conversation_retention_days})")
            continue
        for media in _recordings(d):
            if report.remaining <= limit:
                break
            remove(media, "ceiling")
        # A manifest is never removed for the ceiling: it is the cheap
        # half — bytes are ~93 % model speech by construction — so
        # deleting the record to save disk would trade the evidence that
        # costs nothing for the evidence that costs everything.

    if report.remaining > limit:
        report.conflict = (
            f"{report.remaining} bytes held, ceiling {limit}: "
            + (f"{len(protected)} archive(s) inside a declared floor — "
               + ", ".join(protected[:4])
               if protected else
               "nothing left that the ceiling is allowed to remove"))
    return report


def sweep(root: Path, limit: int, now: Optional[datetime] = None,
          dry_run: bool = False) -> List[Report]:
    """Every workspace under one root, each judged on its own clocks."""
    if not root.is_dir():
        raise BudgetUnreadable(f"{root} is not a directory")
    out = []
    for person in sorted(root.iterdir()):
        ws = person / "workspace"
        if (ws / ".jaato").is_dir():
            out.append(prune(ws, limit, now=now, dry_run=dry_run))
    return out


def _render(reports: List[Report], limit: int) -> List[str]:
    lines = [f"ceiling {limit // (1024 * 1024)} MB per person"]
    for r in reports:
        lines.append(
            f"{r.workspace.parent.name}: held {r.held} freed {r.freed} "
            f"remaining {r.remaining}"
            + (f" — {len(r.removals)} removal(s)" if r.removals else ""))
        for u in r.undeclared:
            lines.append(f"  UNDECLARED {u}: no policy row, not pruned")
        if r.conflict:
            lines.append(f"  CONFLICT {r.conflict}")
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m housekeeping",
        description="Prune each person's audio archive to the ceiling, "
                    "without reaching inside a declared retention floor.")
    p.add_argument("--root", default=str(_workspace.DEFAULT_ROOT),
                   help="the workspace root (default: %(default)s)")
    p.add_argument("--budget-file", required=True, metavar="PATH",
                   help="file holding ONE integer: megabytes per person")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would go, remove nothing")
    args = p.parse_args(argv)

    try:
        limit = ceiling(Path(args.budget_file))
        reports = sweep(Path(args.root), limit, dry_run=args.dry_run)
    except BudgetUnreadable as exc:
        print(f"housekeeping: {exc}", file=sys.stderr)
        return 2

    for line in _render(reports, limit):
        print(line)
    # Non-zero when a person has to look: either a floor stands in the way
    # of the ceiling, or an archive carries no clock to judge it by.
    return 1 if any(r.conflict or r.undeclared for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())

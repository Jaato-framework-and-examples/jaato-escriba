"""The ceiling, the floor, and what happens where they disagree.

Everything here is destructive by nature, so the properties asserted are
the ones whose failure is unrecoverable: a floor that does not hold, an
archive deleted because its policy could not be read, a manifest thrown
away to reclaim bytes it does not occupy.  None of those raises on its
own — the sweep would report a tidy success — which is why they are
checked rather than trusted.

Dates are DRIVEN, never slept: every case passes `now`, so an age of two
hundred days costs nothing and the suite has no clock of its own.
"""
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import housekeeping as h                               # noqa: E402

fail = 0
NOW = datetime(2026, 10, 9, 12, 0, 0)
MB = 1024 * 1024


def check(label: str, got, want) -> None:
    global fail
    if got != want:
        print(f"  FAIL {label:<44} got {got!r}, want {want!r}")
        fail += 1


def make_archive(ws: Path, when: datetime, *, resolved=True, keeping=None,
                 out_bytes=0, in_bytes=0, turns=1) -> Path:
    """One recorded conversation, as the driver would have left it."""
    d = ws / "audio" / when.strftime(h.STAMP)
    d.mkdir(parents=True)
    rows = [{"stage": h.POLICY_STAGE, "resolved": resolved,
             "record_keeping": keeping}]
    for i in range(turns):
        rows.append({"at": when.isoformat(), "said": f"turn {i}"})
    with (d / "manifest.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    if out_bytes:
        (d / "out_0_deadbeef.wav").write_bytes(b"\0" * out_bytes)
    if in_bytes:
        (d / "in_att_cafe.mp3").write_bytes(b"\0" * in_bytes)
    return d


def workspace() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="escriba-hk-")) / "workspace"
    (ws / ".jaato").mkdir(parents=True)
    return ws


# --------------------------------------------------------------- the ceiling
budget = Path(tempfile.mkdtemp(prefix="escriba-budget-"))
(budget / "ok").write_text("# megabytes per person\n200\n")
(budget / "empty").write_text("# nothing here\n")
(budget / "two").write_text("200\n300\n")
(budget / "words").write_text("plenty\n")
(budget / "zero").write_text("0\n")

check("one integer is megabytes", h.ceiling(budget / "ok"), 200 * MB)
for name in ("empty", "two", "words", "zero", "absent"):
    try:
        h.ceiling(budget / name)
        check(f"{name} budget is refused", "accepted", "refused")
    except h.BudgetUnreadable:
        pass
shutil.rmtree(budget.parent if budget.name == "budget" else budget,
              ignore_errors=True)

# ------------------------------------------- an unreadable policy is not a licence
# The case that must never become a deletion: the snapshot could not be
# read, so the floor is unknown.  Being over the ceiling is not an excuse
# to guess.
ws = workspace()
make_archive(ws, NOW - timedelta(days=400), resolved=False, out_bytes=4 * MB)
r = h.prune(ws, limit=1 * MB, now=NOW)
check("unresolved policy: nothing removed", r.freed, 0)
check("unresolved policy: reported", len(r.undeclared), 1)
check("unresolved policy: conflict raised", r.conflict is not None, True)
check("unresolved policy: files still there",
      len(list((ws / "audio").glob("*/out_*"))), 1)
shutil.rmtree(ws.parent)

# A MISSING policy row is the same case: an archive written before any of
# this existed must not be swept away by the first run of it.
ws = workspace()
d = make_archive(ws, NOW - timedelta(days=400), out_bytes=4 * MB)
(d / "manifest.jsonl").write_text('{"at": "2026-01-01", "said": "x"}\n')
r = h.prune(ws, limit=1 * MB, now=NOW)
check("no policy row: nothing removed", r.freed, 0)
check("no policy row: reported", len(r.undeclared), 1)
shutil.rmtree(ws.parent)

# ---------------------------------------------------- the floor holds (Art. 19)
# Declared and still inside it: the ceiling may not reach in, and the
# shortfall is reported rather than resolved.
ws = workspace()
make_archive(ws, NOW - timedelta(days=5), out_bytes=8 * MB,
             keeping={"retention_days": 180,
                      "conversation_retention_days": 30})
r = h.prune(ws, limit=1 * MB, now=NOW)
check("inside the floor: nothing removed", r.freed, 0)
check("inside the floor: conflict reported", r.conflict is not None, True)
check("inside the floor: names the clock",
      "conversation_retention_days=30" in (r.conflict or ""), True)
shutil.rmtree(ws.parent)

# Past the conversation clock: the recordings go even though the person is
# WELL under the ceiling.  Retention is not a budget.
ws = workspace()
make_archive(ws, NOW - timedelta(days=45), out_bytes=1 * MB, in_bytes=1024,
             keeping={"retention_days": 180,
                      "conversation_retention_days": 30})
r = h.prune(ws, limit=500 * MB, now=NOW)
check("expired conversation: recordings go", len(r.removals), 2)
check("expired conversation: under the ceiling anyway",
      all(x.reason == "conversation_retention_days" for x in r.removals), True)
check("expired conversation: the manifest stays",
      len(list((ws / "audio").glob("*/manifest.jsonl"))), 1)
check("expired conversation: tombstoned",
      len((ws / "audio" / "pruned.jsonl").read_text().strip().splitlines()), 2)
# The tombstone lives beside the archives and not inside one, so the
# record of a pruning survives the archive it pruned.
check("the tombstone outlives what it documents",
      (ws / "audio" / "pruned.jsonl").is_file()
      and not list((ws / "audio").glob("*/pruned.jsonl")), True)
shutil.rmtree(ws.parent)

# The audit half outlives the conversation half, and only goes when ITS
# own clock has run out.
ws = workspace()
make_archive(ws, NOW - timedelta(days=200), out_bytes=1024,
             keeping={"retention_days": 180,
                      "conversation_retention_days": 30})
r = h.prune(ws, limit=500 * MB, now=NOW)
check("both clocks run out: the archive goes",
      list((ws / "audio").glob("2026*")), [])
check("both clocks run out: tombstone at the audio root",
      (ws / "audio" / "pruned.jsonl").is_file(), True)
shutil.rmtree(ws.parent)

# --------------------------------------------- undeclared is the ceiling's to take
# Read the snapshot, nothing declared: the framework's reading is that
# nothing promised to keep it, so the ceiling may act — but the expiry
# pass may not, because no clock ran out.
ws = workspace()
make_archive(ws, NOW - timedelta(days=400), keeping=None, out_bytes=4 * MB)
r = h.prune(ws, limit=500 * MB, now=NOW)
check("undeclared, room to spare: kept", r.freed, 0)
r = h.prune(ws, limit=1 * MB, now=NOW)
check("undeclared, over the ceiling: taken", r.freed, 4 * MB)
check("undeclared: removed for the ceiling",
      [x.reason for x in r.removals], ["ceiling"])
check("undeclared: the manifest is never taken for bytes",
      len(list((ws / "audio").glob("*/manifest.jsonl"))), 1)
shutil.rmtree(ws.parent)

# Model speech goes before the person's own voice: 93 % of the bytes for
# the half the transcript can still account for.
ws = workspace()
make_archive(ws, NOW - timedelta(days=400), keeping=None,
             out_bytes=3 * MB, in_bytes=1 * MB)
r = h.prune(ws, limit=2 * MB, now=NOW)
check("ceiling takes the model's speech first",
      [Path(x.path).name.split("_")[0] for x in r.removals], ["out"])
check("the person's own voice is still there",
      len(list((ws / "audio").glob("*/in_*"))), 1)
shutil.rmtree(ws.parent)

# Oldest first, across archives, and it STOPS once it is under.  The
# ceiling is on the whole of `audio/`, manifests included, so the limit
# here leaves room for them: at exactly 2 MB both archives would have to
# go, which would prove the order and hide the stopping.
ws = workspace()
make_archive(ws, NOW - timedelta(days=400), keeping=None, out_bytes=2 * MB)
make_archive(ws, NOW - timedelta(days=1), keeping=None, out_bytes=2 * MB)
r = h.prune(ws, limit=2 * MB + 65536, now=NOW)
check("oldest archive is pruned first",
      [Path(x.path).parts[1] for x in r.removals],
      [(NOW - timedelta(days=400)).strftime(h.STAMP)])
shutil.rmtree(ws.parent)

# ------------------------------------------------------------------ dry run
ws = workspace()
make_archive(ws, NOW - timedelta(days=400), keeping=None, out_bytes=4 * MB)
r = h.prune(ws, limit=1 * MB, now=NOW, dry_run=True)
check("dry run reports what would go", r.freed, 4 * MB)
check("dry run removes nothing",
      len(list((ws / "audio").glob("*/out_*"))), 1)
check("dry run writes no tombstone",
      list((ws / "audio").rglob("pruned.jsonl")), [])
shutil.rmtree(ws.parent)

# -------------------------------------------------------------------- sweep
# One root, several people, each judged on their own clocks — and a
# directory that is not a workspace is not swept.
root = Path(tempfile.mkdtemp(prefix="escriba-root-"))
for who, days in (("alice-1", 400), ("bob-2", 2)):
    ws = root / who / "workspace"
    (ws / ".jaato").mkdir(parents=True)
    make_archive(ws, NOW - timedelta(days=days), keeping=None, out_bytes=4 * MB)
(root / "not-a-workspace").mkdir()
reports = h.sweep(root, limit=1 * MB, now=NOW)
check("one report per workspace", len(reports), 2)
check("every workspace over the ceiling is pruned",
      sum(r.freed for r in reports), 8 * MB)
shutil.rmtree(root)

# ------------------------------------------------ the two halves, end to end
# The policy row is the ONLY thing joining the recorder to the sweep: if
# the driver stops writing it, every archive becomes unjudgeable and the
# ceiling can never be enforced again.  So it is asserted against a
# session record of the shape the daemon actually seals — `profile_snapshot`
# carrying `record_keeping` post-merge.
import archive as a                                    # noqa: E402

ws = workspace()
(ws / ".jaato" / "sessions").mkdir(parents=True, exist_ok=True)
(ws / ".jaato" / "sessions" / "sid-1.json").write_text(json.dumps({
    "session_id": "sid-1",
    "profile_snapshot": {"snapshot_version": 1, "name": "escriba",
                         "record_keeping": {"retention_days": 180,
                                            "conversation_retention_days": 30}},
}))
tape = a.Archive(ws, client_id="c1")
tape.identify("sid-1", "c1")
tape.turn(said="hola")
rows = [json.loads(l) for l in
        (tape.dir / "manifest.jsonl").read_text().splitlines() if l.strip()]
check("the policy row comes first", rows[0]["stage"], h.POLICY_STAGE)
check("it says the snapshot was read", rows[0]["resolved"], True)
check("it carries the resolved clocks",
      rows[0]["record_keeping"]["conversation_retention_days"], 30)
check("the sweep can read what the recorder wrote",
      h._clocks(tape.dir).conversation_retention_days, 30)

# No session record: the row says so, and says it in the one way the sweep
# will not mistake for "nothing was promised".
tape2 = a.Archive(ws, client_id="c1")
tape2.identify("sid-absent", "c1")
tape2.turn(said="hola")
rows2 = [json.loads(l) for l in
         (tape2.dir / "manifest.jsonl").read_text().splitlines() if l.strip()]
check("an unreadable snapshot is marked unresolved", rows2[0]["resolved"], False)
check("the sweep refuses to judge it", h._clocks(tape2.dir).resolved, False)

# The write-time ceiling stops the RECORDINGS and not the record.
tape3 = a.Archive(ws, client_id="c1", limit=1024)
tape3.identify("sid-1", "c1")
tape3.heard(b"\0" * 4096)
rows3 = [json.loads(l) for l in
         (tape3.dir / "manifest.jsonl").read_text().splitlines() if l.strip()]
check("over the ceiling: nothing recorded",
      list(tape3.dir.glob("in_*")), [])
check("over the ceiling: the manifest says why",
      [r.get("stage") for r in rows3], [h.POLICY_STAGE, "budget"])
shutil.rmtree(ws.parent)

print("tests_housekeeping:", "OK" if not fail else f"{fail} FAILED")
sys.exit(1 if fail else 0)

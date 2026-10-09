"""The data contract a remote view depends on, asserted headless.

The live view redraws the whole frame every time, so it never needed to
know WHICH row changed and these properties did not exist.  A browser
does: it applies a streamed token, a late audio id and a `repeats` bump
to rows it has already drawn, over a connection that may have missed
something.  If an id can come to mean a different entry, the wrong row
updates and nothing raises.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board as b                                      # noqa: E402

fail = 0


def check(label: str, got, want) -> None:
    global fail
    if got != want:
        print(f"  FAIL {label:<46} got {got!r}, want {want!r}")
        fail += 1


# --------------------------------------------------------- stable entry ids
v = b.StateBoard()
v.spoke("uno")
v.spoke("dos")
check("every entry gets an id", [e.id for e in v.state.entries], [1, 2])

# A folded repeat is the SAME row: one id, a higher count.  A remote view
# that saw two ids here would draw the line twice, which is the thing the
# folding exists to prevent.
v.note("buscando")
v.note("buscando")
check("a folded repeat keeps one id", [e.id for e in v.state.entries], [1, 2, 3])
check("and counts instead", v.state.entries[-1].repeats, 2)

# Ids are never reused, including after the deque has dropped the entry
# they named: an id a reader still holds must not come to mean another row.
v = b.StateBoard()
for i in range(b.TRANSCRIPT + 50):
    v.spoke(f"turn {i}")
first, last = v.state.entries[0], v.state.entries[-1]
check("the window holds its maximum", len(v.state.entries), b.TRANSCRIPT)
check("the oldest surviving id is past the evictions", first.id, 51)
check("ids keep counting past the window", last.id, b.TRANSCRIPT + 50)
check("no id is reused", len({e.id for e in v.state.entries}), b.TRANSCRIPT)

# ------------------------------------------------- what was there at the start
# `None` until something has been read.  Zero is a READING — "the
# catalogue is empty" — and a view that cannot tell it from "not yet
# known" renders a delta against a number nobody observed.
v = b.StateBoard()
check("catalogue start is unknown until read", v.state.catalogue_at_start, None)
check("documents start is unknown until read", v.state.documents_at_start, None)

v.catalogue(7)
v.catalogue(9)
check("the catalogue's start is its first reading", v.state.catalogue_at_start, 7)
check("and the current value moves", v.state.catalogue, 9)

# The documents count is the LISTING's length, not `state.documents`:
# that deque holds only what this process wrote, and the documenter is a
# subagent whose output this process never sees.
v.listing("documentos", [{"text": "a"}, {"text": "b"}])
v.listing("documentos", [{"text": "a"}, {"text": "b"}, {"text": "c"}])
check("documents start is the first listing", v.state.documents_at_start, 2)
check("and the panel holds the latest", len(v.state.items["documentos"]), 3)

# An empty first reading is still a reading.
v = b.StateBoard()
v.catalogue(0)
v.listing("documentos", [])
check("zero is observed, not unknown", v.state.catalogue_at_start, 0)
check("an empty listing is observed too", v.state.documents_at_start, 0)

print("board contract OK" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)

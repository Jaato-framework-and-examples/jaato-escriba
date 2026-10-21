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

# ------------------------------------------- a turn that spoke more than once
# THE DEFECT THIS SUITE COULD NOT SEE.  Every leg above passed on
# 2026-10-20 while a turn that spoke twice showed the first utterance's
# words beside the SECOND one's audio and left the first recording named
# by no row at all -- because nothing here, or anywhere, ever handed the
# turn two recordings.
def rec(sha: str, text: str = "", seconds: float = 1.0) -> dict:
    return {"sha": sha, "text": text, "seconds": seconds}


rows = b.utterances([], "lo he anotado")
check("a silent turn still gets one row", len(rows), 1)
check("carrying the turn's words", rows[0]["text"], "lo he anotado")
check("and no audio, which is what says so", rows[0]["audio"], None)

# A row even with nothing to put in it: `Driver._settle` marks the LAST
# `spoke` row settled, and a turn with no row has nothing to mark.
check("a turn with neither still gets a row", len(b.utterances([], "")), 1)

rows = b.utterances([rec("att_a", "lo que dijo")], "")
check("one utterance keeps its own transcript", rows[0]["text"], "lo que dijo")
check("and its own recording", rows[0]["audio"], "att_a")
check("and its own length", rows[0]["seconds"], 1.0)

# The provider sends no transcript when the model also wrote text, so the
# turn's words are the only ones there are.
rows = b.utterances([rec("att_a")], "lo escribió")
check("an utterance with no transcript takes the turn's", rows[0]["text"], "lo escribió")

# ---- the VPS case, 2026-10-20: two audios, one of them transcribed
two = [rec("att_first", "la primera"), rec("att_second")]
rows = b.utterances(two, "la primera")
check("two utterances make two rows", len(rows), 2)
check("each keeps its own words, and no other's",
      [r["text"] for r in rows], ["la primera", ""])
check("and no recording is left unnamed",
      [r["audio"] for r in rows], ["att_first", "att_second"])

# Neither spoke a transcript: the turn's words are a TURN fact, so they
# are spent once, on the row the note follows.
rows = b.utterances([rec("att_a"), rec("att_b")], "lo escribió")
check("the turn's words go on the last row only",
      [r["text"] for r in rows], ["", "lo escribió"])
check("and both recordings survive it",
      [r["audio"] for r in rows], ["att_a", "att_b"])

# ------------------------------------------------ and the row model keeps them
v = b.StateBoard()
v.spoke("", audio="att_one")
v.spoke("", audio="att_two")
rows = [e for e in v.state.entries if e.kind == "spoke"]
check("two wordless replies are two rows", len(rows), 2)
check("each with its own recording",
      [e.audio for e in rows], ["att_one", "att_two"])
check("and neither folded into the other", [e.repeats for e in rows], [1, 1])

# A genuine repeat still folds: same words, same recording, one row.
v = b.StateBoard()
v.spoke("otra vez", audio="att_one")
v.spoke("otra vez", audio="att_one")
check("the same reply twice is still one row",
      len([e for e in v.state.entries if e.kind == "spoke"]), 1)

# REVERSION GUARD.  One speaker's consecutive presses fold ON PURPOSE --
# each press is already its own manifest row -- so the recording joins
# the key for a reply and not for a `said`.
v = b.StateBoard()
v.heard(2.0, "att_one", by="marta.ruiz")
v.heard(3.0, "att_two", by="marta.ruiz")
check("one speaker twice over still folds",
      len([e for e in v.state.entries if e.kind == "said"]), 1)


print("board contract OK" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)

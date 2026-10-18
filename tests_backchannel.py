"""Where the escriba speaks while somebody is demonstrating.

WHAT THIS SUITE IS FOR.  Every defect here is silent and arrives as a
judgement about taste: an escriba that interrupts mid-sentence, or one
that goes quiet for a whole demonstration, and neither raises anything.
The legs below are the statements of record for decisions that were
settled by somebody LISTENING -- see `backchannel`'s docstring -- so
each one is a thing that was measured and must not drift back.

NO MODEL IS LOADED.  The transcriber is the expensive, non-deterministic
half, so it is passed in: these legs drive the pure functions with
transcripts written by hand, which is the only way a boundary rule can
be asserted rather than sampled.  `python -m cadence --calibration` and
the listening tests are where the real audio is answered for.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backchannel as bc                                   # noqa: E402

fail = 0


def check(label: str, got, want) -> None:
    global fail
    if got != want:
        print(f"  FAIL {label.ljust(56)} got {got!r}, want {want!r}")
        fail += 1
    else:
        print(f"  ok   {label}")


# ------------------------------------------------------------- the marks
#
# A FULL STOP IS AN IDEA ENDING and the commonest signal: everybody
# pauses at the end of a thought, whether or not they ask to be followed.
check("a full stop is an idea ending",
      [k for _t, k in bc.marks([(0.0, 10.0, "Aquí está el menú. Y ahora")])],
      ["idea"])
# INTERPOLATED INSIDE ITS OWN SEGMENT, which is all one decode can give:
# the full stop is character 18 of 26, so it lands at 6,9 s of a
# ten-second segment.  The error this carries is why `snap` exists.
check("and it lands where its character falls",
      round(bc.marks([(0.0, 10.0, "Aquí está el menú. Y ahora")])[0][0], 1),
      6.9)

# A QUESTION MARK IS A CUE ONLY IF THE SPEAKER ASKED, and the allow-list
# is what keeps an invented question out.  Measured 2026-10-18: primed
# transcription turned "a los cuales yo tengo acceso" into "¿Cuáles yo
# tengo acceso?" -- a question manufactured from a relative clause, which
# unfiltered would have placed an acknowledgement mid-sentence.
check("a handover question is a cue",
      [k for _t, k in bc.marks([(0.0, 6.0, "Cada uno tiene cuatro, ¿vale?")])],
      ["cue"])
check("and an invented one is not a boundary at all",
      bc.marks([(0.0, 6.0, "¿Cuáles yo tengo acceso?")]), [])
check("de acuerdo is a cue", [k for _t, k in bc.marks(
    [(0.0, 5.0, "los pipelines, ¿de acuerdo?")])], ["cue"])
check("me explico is a cue", [k for _t, k in bc.marks(
    [(0.0, 5.0, "para destilar, ¿me explico?")])], ["cue"])
# AND THE ACCENTS DO NOT DECIDE IT: the transcriber's output is not
# stable about them, and a cue list that only matched one spelling would
# work until it did not.
check("a cue matches without its accents", bc._is_cue("entonces, ¿esta claro"),
      True)
check("and with them", bc._is_cue("entonces, ¿está claro"), True)
check("a word that merely ends like a cue is not one",
      bc._is_cue("no me lo tomes a mal"), False)

# EMPTY TEXT IS NOT A CRASH.  A window of silence transcribes to nothing,
# and that is the ordinary case at the start of a demonstration.
check("an empty segment yields nothing", bc.marks([(0.0, 3.0, "")]), [])


# ------------------------------------------------------------ the snapping
#
# THE SILENCE PUTS THE MARK BACK ON THE CLOCK.  Interpolation is worth
# about a second; the VAD's gaps are exact, so the mark is moved to the
# nearest one.
SILENCES = [(6.0, 0.70), (12.0, 0.45), (30.0, 0.90)]
check("a mark snaps to the silence beside it",
      bc.snap([(6.4, "idea")], SILENCES), [bc.Boundary(6.0, "idea", 0.7)])
# DROPPED RATHER THAN GUESSED AT.  A mark with no silence near it is the
# last sentence of the recording, or a mark the transcriber put where
# nobody paused.  Neither is somewhere to speak.
check("a mark with no silence near it is dropped",
      bc.snap([(20.0, "idea")], SILENCES), [])
# AND A SILENCE TOO SHORT TO SPEAK INTO IS NOT ROOM.  This is not a
# threshold on what counts as a pause -- that idea is buried in
# `cadence.py` -- it is the room a word needs to land in.
check("a silence shorter than the room is not a boundary",
      bc.snap([(6.4, "idea")], [(6.0, 0.05)]), [])
# ONE SILENCE IS ONE MOMENT.  A cue at the end of a sentence is the same
# breath as its full stop, and answering both would be two
# acknowledgements in one gap.
check("two marks on one silence are one boundary",
      len(bc.snap([(6.2, "idea"), (6.3, "cue")], SILENCES)), 1)
check("and the cue wins, because the speaker asked",
      bc.snap([(6.2, "idea"), (6.3, "cue")], SILENCES)[0].kind, "cue")


# -------------------------------------------------------------- the pacing
#
# THE CHATTINESS DIAL.  One of the four speakers in `cadence.py` offers a
# boundary every eight seconds; answering all of them is a different
# product.
MANY = [bc.Boundary(t, "idea", 0.5) for t in (5.0, 9.0, 13.0, 22.0, 40.0)]
check("boundaries are thinned to one per spacing",
      [b.at for b in bc.due(MANY, spacing=15.0)], [5.0, 22.0, 40.0])
check("and a wider spacing is quieter",
      [b.at for b in bc.due(MANY, spacing=30.0)], [5.0, 40.0])
# THE FIRST OF A CLUSTER, NOT THE BEST OF IT, because there is no later:
# by the time a second boundary is known the first has already passed.
check("the first of a cluster is the one answered",
      bc.due([bc.Boundary(5.0, "idea", 0.3),
              bc.Boundary(6.0, "cue", 0.9)], spacing=15.0)[0].at, 5.0)
# AND A WINDOW THAT OVERLAPS THE LAST ONE DOES NOT ANSWER IT TWICE.
check("a moment already answered is not answered again",
      bc.due(MANY, after=5.0, spacing=15.0)[0].at, 22.0)


# ------------------------------------------------------------- the listener
class _Engine:
    """A transcriber that returns what the test wrote, and counts calls."""

    def __init__(self, segments) -> None:
        self.segments_out = segments
        self.calls: list = []

    def segments(self, samples, prime=None, language=None):
        self.calls.append(prime)
        return self.segments_out


engine = _Engine([(0.0, 10.0, "Aquí está el menú. Y ahora los bundles")])
ear = bc.Listener(engine)
got = ear.heard(b"", [(6.0, 0.70)])
check("the listener finds the boundary", [b.at for b in got], [6.0])
# PRIMED, ALWAYS.  Unprimed, one recording yielded a single full stop in
# 49 seconds and the escriba would have said nothing for a whole
# demonstration.  This is the leg that would catch its removal.
check("and it primes the transcriber", engine.calls, [bc.PRIME])
check("the prime is punctuated, which is the whole point of it",
      all(m in bc.PRIME for m in (".", "?", "¿")), True)

# THE OFFSET IS THE DEMONSTRATION'S CLOCK.  A window's own zero is on
# nobody's clock: the frames, the journal and the person all count from
# the start of the recording.
ear2 = bc.Listener(_Engine([(0.0, 10.0, "Aquí está el menú. Y ahora")]))
check("a window reports on the demonstration's clock",
      [b.at for b in ear2.heard(b"", [(66.0, 0.70)], offset=60.0)], [66.0])

# AND SPEAKING IS RECORDED, so the next window does not answer the same
# moment.  The listener re-reads an overlapping window by design.
ear3 = bc.Listener(_Engine([(0.0, 20.0, "Uno. Dos. Tres.")]))
first = ear3.heard(b"", [(5.0, 0.5), (12.0, 0.5), (19.0, 0.5)])
ear3.said(first[0])
again = ear3.heard(b"", [(5.0, 0.5), (12.0, 0.5), (19.0, 0.5)])
check("the moment just answered does not come back",
      first[0].at not in [b.at for b in again], True)


print("backchannel OK" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)

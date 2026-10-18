"""When to say "ya veo" while somebody is still demonstrating.

The escriba follows a demonstration as it is recorded (`demo_live`), and
this decides the one thing that made the difference between company and
interruption: WHERE it speaks.  Everything here was settled by listening
rather than by argument, and the losing answers are recorded because
each one is a thing a reader would otherwise try again.

WHAT WAS TRIED AND REJECTED, 2026-10-18.  The first design waited for a
pause of at least N seconds.  `cadence.py` holds the measurements: nine
recordings, four speakers, and no threshold that travels.  Then somebody
LISTENED to the gaps that a 0,40 s cut-off would answer and a 0,50 s one
would not -- blind, with a control band -- and called them interruptions
in the wrong place, because an interruption belongs at the end of an
IDEA.  Transcribed, the silence at a sentence end is the same silence as
mid-clause (median 0,54 s against 0,51 s), so no gap threshold can tell
them apart.  The boundary is in the WORDS, and `cadence.py` survives as
the evidence that it is not in the silence.

AND THEN THE SAME TEST SETTLED THE REST OF IT.  Three mixes of one
narration, the escriba's own voice over it, blind:

  linked to the end of an idea, 2,0 s behind it   -> "sounds natural"
  the same, 3,5 s behind                          -> liked less
  tied to nothing                                 -> "definitively bad"

So the LINKAGE is the load-bearing part, the delay is a quality dial and
not a constraint, and tying acknowledgements to nothing is the one thing
that must not ship.  A second round added the speaker's own invitations
-- "¿vale?", "¿de acuerdo?" -- and was preferred again.  The two compose:
everybody pauses at the end of an idea, not everybody uses cues, and a
speaker who does is asking out loud.

TWO SIGNALS, ONE DECODE.  A full stop is an idea ending; a question mark
behind a short tag phrase is the speaker asking to be followed.  Reading
both from ONE transcript matters more than it looks: asking whisper for
word timings to place the cues precisely costs two thirds of the full
stops (nine marks became three on the same audio), which would trade the
universal signal for the optional one.  Interpolating a mark's position
inside its own segment is worth about a second, and snapping it to a
real silence puts it back on the clock.

WHY THE WORDS ARE NOT THE MODEL'S.  They were, for one round.  The
escriba's own choices were better than a hand-written list -- "entiendo",
"ya veo", "ajá", "de acuerdo", varied and apt -- and the mechanism is not
usable: asked at nine boundaries it spoke at five, and told that the
moment was already decided and never to stay silent, it still spoke at
five, with FOUR of the nine moments flipping between the two runs.  An
escriba that answers half the boundaries, differently each time, cannot
be tuned or tested.  So the vocabulary is `i18n`'s, seeded from what the
escriba actually chose, and the model is kept for PROBES -- a question
that goes unasked costs nothing, which is the property acknowledgements
do not have.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

#: Punctuated Spanish, in the register a demonstration is narrated in,
#: handed to the transcriber as `initial_prompt`.  It is not a prompt
#: about the task: whisper copies the STYLE of what it is primed with,
#: and what is wanted from it is the punctuation.  See
#: `transcribe.Transcriber.segments` for the measurement.
PRIME = ("Aquí tenemos la aplicación. Este es el menú lateral. "
         "Vamos a ver los bundles, ¿de acuerdo? Cada uno tiene cuatro "
         "pestañas. ¿Me explico?")

#: What ends an idea.
STOPS = ".!…"

#: What the speaker says when they are asking to be followed.  Matched
#: against the words immediately before a question mark, so a question
#: that is not one of these is NOT a cue: primed transcription invents
#: the occasional question -- "a los cuales yo tengo acceso" came back
#: as "¿Cuáles yo tengo acceso?" -- and an allow-list is what keeps an
#: invention from becoming an acknowledgement in the wrong place.
CUES = ("me explico", "de acuerdo", "vale", "verdad", "entiendes",
        "me sigues", "se me entiende", "esta claro", "lo ves", "lo veis",
        "ok", "no se si me explico")

#: How many words before a question mark are read when looking for a cue.
TAG_WORDS = 4

#: How far a mark may sit from a real silence and still be taken as that
#: silence, in seconds.  A mark's time comes from where its character
#: falls inside its segment, and a segment runs five to ten seconds, so
#: the estimate is worth about a second.
SNAP_SECONDS = 1.5

#: The silence a boundary must have behind it.  Not a threshold on what
#: counts as a pause -- that is the idea `cadence.py` buried -- but the
#: room needed for a word to land without colliding with the next one.
ROOM_SECONDS = 0.20

#: The shortest gap between two acknowledgements, in seconds.  This is
#: the chattiness dial, and it is a POLICY rather than a measurement:
#: the composed trigger produced one boundary every 17 s on the
#: narration that was judged, and a listener called that rate acceptable,
#: while one of the four speakers measured in `cadence.py` offers a
#: boundary every 8 s.
SPACING_SECONDS = 15.0


class Boundary(NamedTuple):
    """One moment the escriba could speak into."""

    at: float
    #: `idea` or `cue`.  Both are boundaries; the kind is for the journal
    #: and for choosing what to say, never for deciding whether to speak.
    kind: str
    #: The silence found there, in seconds.
    room: float


def _fold(text: str) -> str:
    """Lower case, no accents, no punctuation: for matching a cue only."""
    bare = unicodedata.normalize("NFKD", text.lower())
    bare = "".join(c for c in bare if not unicodedata.combining(c))
    return re.sub(r"[^\w\s]", " ", bare)


def _is_cue(before: str) -> bool:
    """Do the words before a question mark name a handover?"""
    words = _fold(before).split()[-TAG_WORDS:]
    tail = " ".join(words)
    return any(tail == cue or tail.endswith(" " + cue) for cue in CUES)


def marks(segments: Sequence[Tuple[float, float, str]]) -> List[Tuple[float, str]]:
    """Every idea end and cue in a transcript, with the second it falls on.

    PLACED BY INTERPOLATION, and the imprecision is deliberate rather
    than overlooked: the alternative is word timings, which cost two
    thirds of the punctuation this reads.  `_snap` is what recovers the
    precision, from the silence rather than from the model.
    """
    out: List[Tuple[float, str]] = []
    for start, end, text in segments:
        if not text:
            continue
        for i, ch in enumerate(text):
            if ch in STOPS:
                kind = "idea"
            elif ch == "?":
                if not _is_cue(text[:i]):
                    continue
                kind = "cue"
            else:
                continue
            where = start + (end - start) * (i + 1) / len(text)
            out.append((where, kind))
    return sorted(out)


def snap(moments: Sequence[Tuple[float, str]],
         silences: Sequence[Tuple[float, float]]) -> List[Boundary]:
    """Put each mark on the silence it belongs to, or drop it.

    DROPPED AND NOT GUESSED AT.  A mark with no silence near it is
    either the last sentence of a recording -- nothing follows it, so
    there is nowhere to speak -- or a mark the transcriber put somewhere
    the speaker did not pause.  Neither is a moment to answer, and an
    earlier version of this that kept them placed acknowledgements
    mid-word.
    """
    out: List[Boundary] = []
    for when, kind in moments:
        near = [(abs(at - when), at, room) for at, room in silences
                if abs(at - when) <= SNAP_SECONDS and room >= ROOM_SECONDS]
        if not near:
            continue
        _d, at, room = min(near)
        out.append(Boundary(round(at, 2), kind, round(room, 2)))
    # One silence is one moment, however many marks landed on it: a cue
    # at the end of a sentence is the same breath as the full stop.
    seen: Dict[float, Boundary] = {}
    for b in out:
        # A cue wins the tie, because it is the speaker asking rather
        # than the escriba inferring.
        if b.at not in seen or (b.kind == "cue" and seen[b.at].kind != "cue"):
            seen[b.at] = b
    return sorted(seen.values())


def due(boundaries: Sequence[Boundary], after: Optional[float] = None,
        spacing: float = SPACING_SECONDS) -> List[Boundary]:
    """The boundaries worth answering, thinned to one per `spacing`.

    `after` is when the escriba last spoke, so a listener that re-reads
    an overlapping window does not answer the same moment twice.

    THE FIRST OF A CLUSTER AND NOT THE BEST, because there is no later:
    by the time a second boundary is known, the first has passed.  This
    is the one place the whole design is online rather than retrospective.
    """
    out: List[Boundary] = []
    last = after
    for b in sorted(boundaries):
        if last is not None and b.at - last < spacing:
            continue
        out.append(b)
        last = b.at
    return out


class Listener:
    """Reads a demonstration's narration and says where it would speak.

    ONE TRANSCRIBER, held: loading the model costs seconds and hundreds
    of megabytes, which a demonstration pays once at its start and never
    per window.  `transcribe.Transcriber` already owns that bargain, so
    this borrows it rather than loading a second copy.
    """

    def __init__(self, engine, spacing: float = SPACING_SECONDS) -> None:
        self.engine = engine
        self.spacing = spacing
        #: When the escriba last spoke, in demonstration seconds.
        self.spoke_at: Optional[float] = None

    def heard(self, samples, silences: Sequence[Tuple[float, float]],
              offset: float = 0.0) -> List[Boundary]:
        """Where to speak in this window, in DEMONSTRATION seconds.

        `offset` is where the window starts, because everything else --
        the journal, the frames, the person's own sense of the
        demonstration -- is on that clock and a window's own zero is on
        nobody's.
        """
        segments = self.engine.segments(samples, prime=PRIME)
        shifted = [(a + offset, b + offset, t) for a, b, t in segments]
        found = snap(marks(shifted), silences)
        return due(found, after=self.spoke_at, spacing=self.spacing)

    def said(self, boundary: Boundary) -> None:
        """Record that the escriba answered this one."""
        self.spoke_at = boundary.at

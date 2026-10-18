"""How often a person stops talking, measured rather than assumed.

    python -m cadence <audio> [<audio> ...]

The escriba can acknowledge a demonstration while it is being recorded —
"vale", "ya veo" — and the question that decides whether that is company
or interruption is how often the person actually pauses. This answers it
from recordings, and exists because the first attempt to answer it from
one recording was wrong twice over.

WHAT WENT WRONG THE FIRST TIME, so it is not repeated. A pause was
detected by loudness: quiet meant RMS under a threshold. Across five
recordings of one speaker through one microphone the beat interval came
out between 12 and 49 seconds, and the first write-up quoted the slowest
of the five as the rule. A threshold on amplitude measures a microphone's
gain in a room — `p25` across those same five ranged 7 to 517 — so it
cannot travel to another person, and a version that derived the threshold
from the audio only added a second arbitrary constant: changing its
divisor moved one recording from six pauses to two, and silenced another
entirely.

WHAT IS USED INSTEAD is the Silero voice-activity detector that
`faster-whisper` already ships: a model trained across many speakers and
languages, so the judgement of what counts as speech is not this
repository's to tune. Over the same five recordings its rate of
phrase-length gaps came out at 8.2, 9.0, 11.7, 11.9 and 12.1 seconds — a
1.5x spread against the 4x of the loudness rule. That looked like a
property of speech rather than of the recording, and across ONE speaker
it is; the paragraphs below are what happened when two more arrived.

ITS DEFAULTS ARE NOT THESE DEFAULTS, and the difference is easy to miss.
`VadOptions` merges anything separated by less than two seconds and pads
each segment by 400 ms, which is right for cutting audio into chunks to
transcribe and wrong for finding the gaps between phrases: asked with its
own defaults, every one of the five recordings reported ZERO pauses.

FOUR SPEAKERS, AND THE SPREAD WAS THE WRONG THING TO MEASURE. Five of
the nine recordings are one person narrating a screen into a laptop; the
others are three more people into phones, through WhatsApp's Opus, AAC
and 16 kbit/s Opus. At a 0,6 s cut-off they come out at 8,2 to 12,1 s,
then 19,9 s, then 29,1 s, then 4,7 s -- and two earlier readings of this
file died on the way. The first, from one speaker, was that the cut-off
"buys no robustness". The second, from three, was to pick the cut-off
where the SPREAD between speakers is narrowest. The fourth speaker shows
why that is wrong: they are the fastest pauser by a factor of two, so
they widened every spread without changing the only number a cut-off has
to answer for.

TWO DIFFERENT FAILURES, AND ONLY ONE OF THEM IS THE CUT-OFF'S. A cut-off
set too HIGH strands the slowest speaker -- 29,1 s between beats at
0,60 s means the escriba has nothing to speak into for half a minute. A
cut-off set too LOW answers a breath instead of a pause, and talks over
somebody. How OFTEN the escriba speaks to the fastest pauser is neither:
it is the floor's, and the floor is a policy that can be argued with.
Measuring the spread mixed the first failure with a question that is not
the cut-off's to answer.

SO THE CUT-OFF IS CHOSEN ON THE SLOWEST SPEAKER, and the slowest column
over the nine reads 7,5 s at 0,40, 11,9 s at 0,50, 19,9 s at 0,55 and
29,1 s at 0,60 -- unchanged by the fourth speaker, because they are
nowhere near it. The histogram says where 0,60 breaks: the third
speaker's commonest pause falls in [0,50 s, 0,60 s), four of their eleven
gaps, so 0,60 clips the very place they pause and leaves two events in
58 s, too few to call a rate. At 0,50 s nobody waits longer than 11,9 s,
which a floor of 30 to 45 s clears for all four.

AND A ONE-SECOND CUT-OFF IS OUT, which the first write-up proposed as
"the beat". Over the nine, two of the four speakers produce NO gap that
long at all, while the fourth produces one every ten seconds. A constant
that strands half the people in the room is not a constant.

THREE THINGS CHANGE AT ONCE between these people -- the person, the
device and the register -- so which of them moves a number cannot be read
off this sample, and an earlier version of this file blamed the register
alone. What the sample establishes is the RANGE, and the range is what
the floor has to clear.

AND THE AMPLITUDE RULE COULD NOT HAVE TRAVELLED, which is worth recording
because it was the reason for choosing a VAD over loudness. Taking
|x| < 0,002 as near-zero, the nine recordings run from 11,6% to 42,0%,
and they do NOT sort by codec or by bitrate: the 16 kbit/s Opus notes sit
at 25,8% and 30,3% while the 18 kbit/s one sits at 19,8% and the AAC one
at 11,6%. An earlier paragraph here claimed each codec left less silence
than the last, which was a story told from three files. What the nine
show is that the near-silent fraction is a property of the room, the
microphone AND the encoder together -- which is exactly what an amplitude
threshold would have been measuring. Silero was not.

AND ADAPTING TO THE SPEAKER DOES NOT PAY, which was the reason for
gathering the recordings in the first place: the escriba asks for a
spoken overview before the demonstration, so it could read that person's
cadence off it and set the cut-off per speaker. Tested the way it would
run -- calibrate on each recording's first 15 s, score on the remainder
it never saw -- the best calibrated rule leaves the slowest speaker
waiting 8,6 s with a 2,9x spread, while a flat 0,40 s leaves them 6,5 s
with 1,9x. Every calibrated variant loses to a constant.

WHY IT CANNOT WORK FROM FIFTEEN SECONDS, which is the part worth keeping
even if somebody tries again. Fifteen seconds of speech holds one to five
gaps, so a rate of LONG pauses has nothing to count -- three of the nine
recordings produce no qualifying gap at all in their first twenty
seconds, which is the same hole the pre-demo push-to-talk utterances fell
into. A central quantile of ALL the gaps does estimate from a sample that
small, and it even ranks the speakers correctly: from fifteen seconds the
fourth speaker reads 1,1 to 1,3 s against everybody else's 0,38 to 0,70,
with no overlap. It still does not help, because the cut-off is not a
pause length to copy -- somebody who pauses LONGER should have MORE of
their gaps answered, not fewer, and setting the bar at their own p70
strands them at 37 s.

WHAT PEOPLE ACTUALLY SHARE, and the reason a constant wins: how OFTEN
they pause at all is close -- 0,19 to 0,41 gaps a second across the nine,
a 2,2x range -- and nearly all the 6x range in LONG pauses comes from how
long each person's pauses are. A low cut-off reads the shared property
and ignores the one that differs. So the overview earns its place on
CONTENT, which is what it was asked for -- the PARA QUÉ the documenter
has no other source for -- and not on timing.

WHAT THIS STILL DOES NOT ANSWER. Four speakers is four, every one of them
speaking Spanish, and only one of the nine recordings is somebody
narrating a screen -- the thing the escriba will actually hear. The rate
may differ by language, by subject, by how nervous somebody is about
being recorded; nothing here can say. That is what this tool is for.

AND THEN SOMEBODY LISTENED, which ended the question this file was
built to answer. The gaps either candidate cut-off would have answered
were cut out of one narration with three seconds of speech on each side,
shuffled with a control band nobody disputes, and played blind. The
verdict was that they are interruptions in the wrong place -- and that an
interruption belongs at a SENTENCE EDGE.

THE SILENCE DOES NOT KNOW WHERE THE SENTENCES END. Transcribed, that
recording has ten sentence-final marks in 152 s -- one about every 20 s,
a perfectly good cadence. The silence sitting at each of them has a
median of 0,54 s and 3 of 10 reach 0,60 s. The silences that are NOT at a
sentence end have a median of 0,51 s and 10 of 30 reach 0,60 s. The two
distributions are the same one, so NO threshold on gap length can tell
the two apart: of the sixteen clips played, every single one -- the
control band included -- fell mid-sentence.

SO THIS TOOL ANSWERED ITS QUESTION BY RETIRING IT. Everything above is
sound and none of it is load-bearing any more: how often somebody pauses
is not how often somebody finishes a thought, and it was the second that
was wanted all along. The boundary is in the WORDS. What this file is
still good for is the evidence that it is not in the silence, and the
reminder of how many plausible readings of one speaker it took to find
that out.

AND THE PUNCTUATION IS NOT FREE EITHER, measured on the way past:
`faster-whisper` with the `base` model reports nine full stops and a
question mark for that recording, and THREE full stops and no question
mark for the same audio when `word_timestamps=True` -- the flag needed to
place them in time. Whatever reads the boundary has to be told that the
punctuation and its timing do not come out of one decode.

AND THERE IS NO PAUSE LONG ENOUGH ANYWAY, which closes the question
from the other end. Reading the words costs 1,4 to 3,2 s -- the model's
own round trip, measured across a real demonstration's turns -- and
1,8 s for a local `faster-whisper` base pass over a five-second window,
so a local reader is no faster. Against that, the nine recordings
contain NO silence of three seconds, exactly ONE of two, and a longest
pause of 2,69 s; most top out between 1,0 and 2,0 s. Whoever decides,
the decision arrives after the person has started talking again -- at
the 2,3 words a second this narration runs at, three to seven words into
the next sentence. Speaking INTO a gap during continuous narration is
not a thing this stack can do, and no provider installed here offers
duplex audio to change that: `audio_input` is a WAV attached to a turn.

WHAT IS LEFT IS NOT A THRESHOLD. Either the person stops and invites the
escriba -- in which case there is no gap to hit and no latency that
matters -- or the escriba speaks OVER the narration the way a listener
actually says "ya" while somebody is still talking, which also needs no
gap. Both are decisions about the product. Neither needs this file.

AND THE RATE IS NOT THE POLICY. How often somebody pauses is a fact about
them; how often the escriba should answer is a decision about how chatty
a second brain ought to be. Keeping the second out of the detector is the
point — it belongs where it can be argued with.
"""
from __future__ import annotations

import statistics
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

#: Gaps at or above this, in seconds, are what the escriba would answer.
#: Measured over nine recordings and four speakers, the SLOWEST pauser
#: gets a beat every 7,5 s at 0,40, every 11,9 s at 0,50 and every 29,1 s
#: at 0,60 -- so this constant decides whether the slowest person in the
#: room is served or stranded, and nothing else: how often the escriba
#: answers the FASTEST one is the floor's to decide. The docstring above
#: has the sweep and the reading that got this wrong twice.
BEAT_SECONDS = 0.6

#: What the VAD is asked, instead of its own defaults. Silence shorter
#: than this does not separate two segments, and no padding is added, so
#: a reported gap is a real one.
MIN_SILENCE_MS = 200

#: 16 kHz mono is what the model wants and what every path here already
#: produces.
RATE = 16000


def samples(path: Path) -> "object":
    """One recording as float32 mono, decoded by ffmpeg.

    ffmpeg AND NOT the library's own decoder, for `transcribe._decode`'s
    reason: `faster-whisper` decodes through PyAV and the pair that
    resolves today does not work together. ffmpeg is a hard requirement
    of this project already.
    """
    import numpy as np

    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"],
        capture_output=True)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError(
            f"ffmpeg could not decode {path}: "
            f"{out.stderr.decode()[:200]}")
    return np.frombuffer(out.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def gaps(audio: "object") -> Dict[str, object]:
    """Every silence between two stretches of speech, in seconds."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    spoken = get_speech_timestamps(
        audio, VadOptions(min_silence_duration_ms=MIN_SILENCE_MS,
                          speech_pad_ms=0))
    between = [(b["start"] - a["end"]) / RATE
               for a, b in zip(spoken, spoken[1:])]
    # WHEN each gap happened, in seconds, so a recording can be cut into
    # a sample and a remainder the sample has not seen.
    at = [a["end"] / RATE for a in spoken[:-1]]
    seconds = len(audio) / RATE
    talking = sum(s["end"] - s["start"] for s in spoken) / RATE
    beats = [g for g in between if g >= BEAT_SECONDS]
    return {
        "seconds": seconds,
        "talking": talking / seconds if seconds else 0.0,
        "segments": len(spoken),
        "gaps": between,
        "at": at,
        "beats": len(beats),
        # The headline: how long the escriba would wait, on average, for
        # a moment it could speak into.
        "every": seconds / len(beats) if beats else None,
    }


def report(rows: List[Dict[str, object]]) -> str:
    out = [f"{'recording':<40} {'length':>8} {'talks':>6} {'segs':>5} "
           f"{'beats':>6} {'one every':>10}", ""]
    for row in rows:
        every = row["every"]
        out.append(
            f"{str(row['name'])[-40:]:<40} {row['seconds']:>7.1f}s "
            f"{100 * row['talking']:>5.0f}% {row['segments']:>5} "
            f"{row['beats']:>6} "
            f"{(f'{every:.1f}s' if every else '—'):>10}")

    spans = [r["every"] for r in rows if r["every"]]
    if len(spans) > 1:
        out += ["",
                f"across {len(spans)} recordings: one beat every "
                f"{min(spans):.1f}–{max(spans):.1f} s "
                f"(median {statistics.median(spans):.1f} s)"]

    # THE SWEEP, and the column to read is the SLOWEST rate rather than
    # the spread between speakers.  Over nine recordings and four people
    # the spread is narrowest at 0,40 s, but only because the fastest
    # pauser got faster -- which is the floor's problem and not the
    # cut-off's.  What a cut-off has to answer for is the person who
    # pauses least often, because a cut-off above their commonest pause
    # leaves them with nothing the escriba can speak into.
    if rows:
        out += ["", "by cut-off (one beat every):", ""]
        heads = (0.4, 0.5, 0.6, 0.7, 0.8)
        out.append(f"  {'recording':<40}" + "".join(f"{t:>9}" for t in
                                                    (f">={h}s" for h in heads)))
        for row in rows:
            cells = ""
            for cut in heads:
                k = len([g for g in row["gaps"] if g >= cut])
                cells += f"{(row['seconds'] / k if k else 0):8.1f}s" if k \
                    else f"{'—':>9}"
            out.append(f"  {str(row['name'])[-40:]:<40}" + cells)
        fastest = {}
        for cut in heads:
            rates = [row["seconds"] / len([g for g in row["gaps"] if g >= cut])
                     for row in rows
                     if len([g for g in row["gaps"] if g >= cut])]
            if rates:
                fastest[cut] = min(rates)
        if fastest:
            out += ["", "  fastest at each cut-off: " + "  ".join(
                f">={c}s {v:.1f}s" for c, v in fastest.items())]
    return "\n".join(out)


#: How much of a recording stands in for the spoken overview the escriba
#: asks for before a demonstration. Fifteen seconds is about two
#: sentences, which is what the escriba asks for.
SAMPLE_SECONDS = 15.0


def calibration(rows: List[Dict[str, object]]) -> str:
    """Can a speaker's own first seconds beat a constant? Measured: no.

    Every rule is calibrated on `SAMPLE_SECONDS` of each recording and
    scored on the REMAINDER, which it has not seen -- the only comparison
    worth making, since a rule fitted to a whole recording describes that
    recording rather than predicting anybody.

    THE SCORE IS WHAT THE SPEAKERS WAIT: the worst of them, and the ratio
    between the worst and the best. A rule that answers one person every
    four seconds and another every forty is not serving a room.
    """
    split = []
    for row in rows:
        pairs = list(zip(row["at"], row["gaps"]))
        split.append((
            [g for t, g in pairs if t <= SAMPLE_SECONDS],
            [g for t, g in pairs if t > SAMPLE_SECONDS],
            row["seconds"] - SAMPLE_SECONDS))

    def score(cut_for):
        waits, stranded = [], 0
        for early, late, span in split:
            cut = cut_for(early)
            beats = len([g for g in late if g >= cut]) if cut else 0
            if beats:
                waits.append(span / beats)
            else:
                stranded += 1
        return max(waits), max(waits) / min(waits), stranded

    def quantile(q):
        def cut_for(early):
            ordered = sorted(early)
            return ordered[min(len(ordered) - 1, int(q * len(ordered)))] \
                if ordered else None
        return cut_for

    out = [f"calibrated on each recording's first {SAMPLE_SECONDS:.0f} s, "
           f"scored on the rest", "",
           f"  {'rule':<36}{'slowest':>9}{'spread':>8}{'stranded':>10}", "",
           "  one constant for everybody:"]
    def line(label, cut_for):
        worst, spread, stranded = score(cut_for)
        return (f"    {label:<32}{worst:>8.1f}s{spread:>7.1f}x"
                f"{stranded:>10}")

    for cut in (0.40, 0.45, 0.50, 0.55, 0.60):
        out.append(line(f"global {cut:.2f}", lambda _early, c=cut: c))
    out += ["", "  calibrated per speaker:"]
    for q in (0.3, 0.4, 0.5, 0.6, 0.7):
        out.append(line(f"p{q * 100:.0f} of that speaker's own gaps",
                        quantile(q)))
    return "\n".join(out)


def main(argv: List[str]) -> int:
    # `--calibration` SCORES THE RULES instead of describing the
    # recordings, and needs several of them: the question it answers is
    # whether a speaker's own opening seconds beat one constant for
    # everybody, and one recording cannot disagree with itself.
    calibrate = argv and argv[0] == "--calibration"
    argv = argv[1:] if calibrate else argv
    if not argv:
        print(__doc__.strip().splitlines()[0])
        print("\n  python -m cadence [--calibration] <audio> [<audio> ...]")
        return 2
    rows: List[Dict[str, object]] = []
    for name in argv:
        path = Path(name)
        if not path.is_file():
            print(f"no recording at {path}")
            return 1
        row = gaps(samples(path))
        # THE FOLDER AND THE FILE. Every demonstration's narration is
        # called `narration.webm`, so the file name alone labels five
        # rows identically and the table says nothing about which is
        # which.
        row["name"] = f"{path.parent.name}/{path.name}"
        rows.append(row)
    if calibrate:
        if len(rows) < 2:
            print("--calibration compares speakers; give it more than one "
                  "recording")
            return 2
        print(calibration(rows))
        return 0
    print(report(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

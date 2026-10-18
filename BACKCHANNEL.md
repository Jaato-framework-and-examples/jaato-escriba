# Where the escriba speaks, and the nine approaches that did not work

The escriba acknowledges a demonstration while it is still being
recorded — "vale", "ya veo" — and the question is **where**. This file
is the measurement record: every approach tried on 2026-10-18, the
evidence that killed it, and what it cost to find out. `backchannel.py`
holds what shipped; this holds what did not, because each dead end is a
thing the next reader would otherwise try again.

**The one-line summary.** The boundary is in the WORDS, not in the
silence. Nine recordings and four speakers established that no threshold
on pause length can find the end of a sentence, and a blind listening
test established that the end of a sentence is the only place an
acknowledgement belongs.

**The material.** Nine recordings: five of the project owner narrating a
screen into a laptop (webm/opus, 49–152 s), one of a second speaker
through a phone and WhatsApp's Opus (119 s), one of a third through a
phone and AAC (58 s), two of a fourth through 16 kbit/s Opus (59 s,
89 s). Four speakers, three codecs, all Spanish. Only the first five are
screen narration — the register the escriba actually hears — which is the
standing limit on everything below.

---

## 1. A loudness threshold — rejected, measures the room

Quiet meant RMS under a threshold. Across the five recordings of one
speaker the beat interval came out **12–49 s**, a 4× spread, and the
first write-up quoted the slowest as the rule.

An amplitude threshold measures a microphone's gain in a room: `p25`
across those same five ranged **7 to 517**. Deriving the threshold from
the audio only added a second arbitrary constant — changing its divisor
moved one recording from six pauses to two and silenced another
entirely.

Also measured, and the reason a VAD was never revisited: taking
|x| < 0,002 as near-zero, the nine recordings run from **11,6 % to
42,0 %** and do **not** sort by codec or bitrate. The 16 kbit/s Opus
notes sit at 25,8 % and 30,3 % while the 18 kbit/s one sits at 19,8 %
and the AAC one at 11,6 %. The near-silent fraction is a property of the
room, the microphone *and* the encoder together.

## 2. Silero VAD with a fixed cut-off — rejected, no constant travels

The VAD `faster-whisper` already ships, trained across many speakers.
Its own defaults merge anything under two seconds and pad by 400 ms,
which reports **zero** pauses on every recording; it must be asked with
`min_silence_duration_ms=200, speech_pad_ms=0`.

Over one speaker it looked like a property of speech — 8.2, 9.0, 11.7,
11.9, 12.1 s — a 1.5× spread. Then the other speakers arrived.

| cut-off | 0.40 | 0.45 | 0.50 | 0.55 | 0.60 | 0.65 | 0.70 |
|---|---|---|---|---|---|---|---|
| fastest pauser | 3.3 | 4.2 | 4.2 | 4.7 | 4.7 | 4.7 | 5.0 |
| **slowest pauser** | 7.5 | 9.9 | 11.9 | 19.9 | 29.1 | 29.1 | 29.1 |
| spread | 2.3× | 2.4× | 2.8× | 4.2× | 6.2× | 6.2× | 5.9× |

**Two readings of this table were wrong.** From one speaker: "the
cut-off buys no robustness". From three: "pick the cut-off where the
spread is narrowest" — which the fourth speaker falsified by being the
fastest pauser by 2×, widening every spread without touching the only
number a cut-off answers for. A cut-off set too high strands the slowest
speaker; how often the escriba answers the *fastest* one is the pacing
dial's business, not the cut-off's.

`python -m cadence <audio>…` reproduces the whole table.

## 3. A one-second cut-off — rejected, strands half the room

Proposed as "the beat" in the first write-up. Over the nine, **two of
the four speakers produce no gap that long at all**, while the fourth
produces one every ten seconds.

## 4. Calibrating per speaker from the pre-demo overview — rejected, 15 s holds nothing

The escriba asks for a spoken overview before a demonstration, so it
could read that person's cadence off it. Tested the way it would run —
fit each rule to a recording's first 15 s, score it on the remainder it
never saw:

| rule | slowest speaker waits | spread |
|---|---|---|
| **global 0.40 s** | **6.5 s** | **1.9×** |
| global 0.50 s | 10.4 s | 2.4× |
| calibrated p30 of that speaker's gaps | 8.6 s | 2.9× |
| calibrated p50 | 10.6 s | 2.7× |
| calibrated p70 | 37.1 s | 6.1× |

Every calibrated rule loses to a constant, for a structural reason:
**15 s of speech holds one to five pauses**, so the upper tail a cut-off
needs is not in there. Three of the nine recordings produce no
qualifying gap at all in their first *twenty* seconds — the same hole the
pre-demo push-to-talk utterances (5–13 s) fell into.

A *central* quantile does estimate from a sample that small, and even
ranks the speakers correctly: from 15 s the fourth speaker reads
1,12–1,28 s against everybody else's 0,38–0,70, with no overlap. It
still does not help, because a pause length is not a cut-off to copy —
somebody who pauses *longer* should have *more* of their gaps answered,
not fewer, and setting the bar at their own p70 strands them at 37 s.

What people share is how OFTEN they pause at all: **0,19 to 0,41 gaps a
second**, a 2,2× range, against 6× for long pauses. Nearly all the
variance is in pause LENGTH.

`python -m cadence --calibration <audio>…` reproduces it. **The overview
keeps its place on CONTENT** — the PARA QUÉ a demonstration's
screenshots never show — which is what `inventory.py` asks for and
`agent.demo_done` passes to the documenter.

## 5. Any threshold at all — rejected by ear, and then by measurement

The gaps a 0.40 s cut-off would answer and a 0.50 s one would not were
cut out of one narration with 3 s of context each side, shuffled blind
with a control band of 0.60–0.80 s gaps, and played. **Verdict: "too
many interruptions out of place, horrible. An interruption needs to
happen at a sentence edge."**

Transcribed, the recording has ten sentence-final marks in 152 s — one
about every 20 s, a perfectly good cadence. The silence at them:

| | median gap | ≥ 0.60 s |
|---|---|---|
| at a sentence end | 0.54 s | 3 of 10 |
| mid-clause | 0.51 s | 10 of 30 |

**The same distribution.** Every one of the sixteen clips played, the
control band included, fell mid-sentence. No threshold on gap length can
tell the two apart, at any value.

## 6. Reading sentence ends from punctuation as it comes — rejected, unstable

`faster-whisper` `base` on the same audio: **nine** full stops and a
question mark from a plain decode, **three** full stops and no question
mark with `word_timestamps=True` — the flag needed to place them in
time. And the density varies wildly between recordings of the same
speaker and microphone: **one** mark in 49 s against **ten** in 152 s.
Unprimed, the escriba would have gone silent through a whole
demonstration.

## 7. Locating cues by word timestamps — rejected, costs the universal signal

Word timings place a cue exactly, and a cue is a word, so the flag's
punctuation damage looked irrelevant. It is not: the same decode is where
the *idea ends* come from, and the flag costs two thirds of them. The
optional signal is not worth the universal one.

Two bugs found here, both the same shape — a signal found and then
dropped by the step that puts it back on the clock. The first version
interpolated a cue's position and discarded any cue whose guess landed
more than a second from a silence, which is how a recording with three
`de acuerdo` in it reported none. Priming resolves this differently:
a primed decode renders cues *with* their question marks, so one decode
carries both signals.

## 8. Priming the transcriber — shipped, with a known cost

Whisper copies the punctuation style of its `initial_prompt`.

| recording | bare | primed |
|---|---|---|
| 49 s narration | 1 mark | **8** |
| 59 s voice note | 2 | 3 |
| 119 s WhatsApp | 4 | **11** |

It also **invents**: `a los cuales yo tengo acceso` came back as
`¿Cuáles yo tengo acceso?`, a question manufactured from a relative
clause — roughly 2 of 8 marks fabricated in that recording. A question
mark therefore counts as a cue only when the words before it name one
(`backchannel.CUES`); an invented question is not a boundary at all.
Unfiltered it would place an acknowledgement mid-sentence, which is
precisely what §5 rejected.

## 9. Letting the model choose the moment and the words — rejected, not reproducible

The escriba itself was given 20 s windows and asked whether to speak. It
does judge: polled every 15 s it spoke 5 times and declined 5. Its word
choice was **better than a hand-written list** — `entiendo`, `ya veo`,
`ajá`, `de acuerdo`, varied and apt.

It is not usable as a mechanism:

| | spoke |
|---|---|
| asked to judge the moment, at nine boundaries | 5 of 9 |
| told the moment was decided and never to stay silent | 5 of 9 |

**Instructing it changed nothing, and four of the nine moments flipped
between the two runs.** An escriba that answers half the boundaries,
differently each time, cannot be tuned or tested. A claim that it
"declines for the right reasons" — it refused the two `de acuerdo` that
had no pause behind them — did not survive the second run, where it
spoke at both.

Three implementation facts, each one a bug first:

- Asking for audio output **silences the text channel**, so the decision
  has to be read from the audio.
- `ev.chunk`, the provider's transcript of its own speech, read
  `"Entiendo."` on every window it had **declined**. Trusting it reports
  the opposite of the truth.
- A decline is **not** digital zero but peak **0.001–0.003**, against
  0.13–0.79 for real speech. A `> 0` test counts four declines as speech.

So the model is kept for **probes**: a question that goes unasked costs
nothing, which is the property an acknowledgement does not have.

---

## The latency floor, which rules out speaking *into* a pause

| | cost |
|---|---|
| tap granularity, browser → server | ≤ 0.5 s (p90 510 ms, max 533 ms, over a real 119 s demo) |
| model round trip, same session | 1.37, 1.72, 1.83, 3.15 s |
| local `faster-whisper` base, 5 s window | 1.80 s |
| local `base`, 6 s window, word timings | 2.11 s |
| local `tiny`, 6 s window, word timings | 1.11 s — but misses 1 of 3 and 2 of 3 cues |

Against that, the nine recordings contain **no silence of three seconds,
exactly one of two**, and a longest pause of **2.69 s**; most top out
between 1.0 and 2.0 s. Whoever decides, the decision arrives after the
person has started talking again — at 2.3 words a second, three to seven
words into the next sentence.

**No provider installed here offers duplex audio.** `audio_input` is a
WAV attached to a turn; `streaming` is response SSE. Continuous
listening is not available on any of them, so this floor is not an
OpenRouter limitation to route around.

Which is why the acknowledgement **overlaps** rather than fitting into a
gap, and why the four words are recorded once rather than generated at
the moment: generating them late adds the model's 1.4–3.2 s *on top* of
the detector's 2.1 s, putting the word 3.5–5.3 s behind the idea — past
the version a listener liked least.

## The listening tests, in order

Blind every time, with the key in a separate file, because the diagnosis
had been argued at length before the test was built.

| | what it was | verdict |
|---|---|---|
| 16 clips | gaps 0.40–0.50 s vs 0.60–0.80 s, in context | "horrible", all 16 mid-sentence |
| A | idea-ends, 2.0 s behind | **"more natural"** — picked blind |
| B | idea-ends, 3.5 s behind | liked less |
| C | tied to nothing | **"definitively bad"** |
| D | ideas + cues, 1.2 s | good |
| E | ideas only, 1.2 s | good |
| F | ideas + cues, 2.0 s | **preferred overall** |
| G | whisper's moments, the escriba's own words | worse than F |
| H | the escriba's moments and words | worse than F |

A vs C is the load-bearing pair: the **linkage** is what matters. The
delay is a quality dial, not a constraint. G and H carried 4 and 5
interventions against F's 9 — one every 30 s and 38 s against 17 s — so
their loss is confounded with density, and the clean comparison could
not be built because the model would not speak at all nine moments.

## What shipped, and the rate it produces

One primed decode per window; full stops are idea ends, cue-phrase
questions are handovers, each mark snapped to a real silence or dropped;
one silence is one moment, cue beating idea on a tie; boundaries thinned
to one per `SPACING_SECONDS`.

Measured over all nine recordings, windowed as a live listener sees them:

| | boundaries | one every |
|---|---|---|
| owner ×5 (laptop, screen narration) | 2–7 | 19–25 s |
| B (WhatsApp Opus) | 7 | 17 s |
| C (AAC) | 3 | 19 s |
| D ×2 (16 kbit/s Opus) | 3–4 | 20–22 s |

**17–25 s across four speakers and three codecs**, against 8–59 s with
one silent recording before priming. Two caveats kept deliberately: the
uniformity is partly the pacing dial, and **cues are now found in only 2
of the 9** where the dedicated word-timestamp pass found 3 in the 152 s
narration alone — the single-decode simplification costs cue recall.
Under the composition that is the right trade, because the floor is what
must never fail, but it is a loss and not a free win.

## The deployed model is not the measured one

Everything above was measured with `faster-whisper` **`base`**, which is
what runs locally. The VPS unit has run **`small`** with
`HF_HUB_OFFLINE=1` since 2026-10-09, and only `small` is cached there —
so proposing a switch is proposing a failure, not a download.

Measured on 2026-10-18 over five of the nine, windowed as a live
listener sees them:

| recording | `base` | `small` |
|---|---|---|
| 49 s narration | 2 · every 25 s | 2 · every 25 s |
| 152 s narration | 7 · every 22 s | 8 · every 19 s |
| 119 s narration | 5 · every 24 s | 6 · every 20 s |
| speaker B, WhatsApp Opus | 7 · every 17 s | 6 · every 20 s |
| speaker C, AAC | 3 · every 19 s | 3 · every 19 s |

Equivalent, and slightly better where it differs: the range tightens to
19–25 s, and `small` finds a **cue in the 152 s narration that `base`
misses** — which is part of the recall the single-decode simplification
gave up. Nothing here needs the models kept in step, but a reader
comparing a live demonstration against the numbers above should know
they came from the smaller one.

## The composition, which is the design in one sentence

> Not everybody uses cues when talking, but everybody pauses at ideas
> awaiting only small signals of acknowledgement from the audience, and
> if the person talks with cues then it is even better if the audience
> reacts to them.

Idea-ends are the floor and must work for everyone; cues are
opportunistic and may be missed. A missed cue thins the texture and
never causes silence — the surrounding idea cadence keeps running — but
it is not *covered* either: the cues in the 152 s narration sit at 56.4 s
and 61.9 s, while the idea-ends around them are at 44.6 s and 68.9 s, so
dropping them leaves a genuine 24 s hole.

## Getting the escriba to say them itself, which is where this should go

Recorded clips are a stopgap and are named as one.  The expectation they
fall short of is that a model choosing in the moment would be **more
realistic and more random** than six files played in rotation, and over
a ten-minute demonstration with a boundary every twenty seconds that
rotation will be heard for what it is.

**The two things that stopped it, both measured (§9 and the latency
table), are different problems and only one of them is hard.**

*Irreproducibility is not actually in the way,* because the question was
wrong.  The model was asked "is this the moment, and what do you say?" —
and it answered the first half by staying silent, 5 of 9 either way,
flipping on 4 of 9 between runs.  But the moment is not its job: the
detector picks the moment deterministically, for everybody, every 17 to
25 s.  Asked only "if you were to acknowledge what you just heard, what
would you say?", there is no decision for it to make unreliably.

*Latency is the real obstacle, and it dissolves by asking EARLY instead
of asking faster.* The reason a live word costs 3,5 to 5,3 s is that it
is requested when the boundary is found.  Nothing requires that.  Keep
one word ready: ask on a rolling basis while the person talks, hold the
latest answer, and play it the instant a boundary arrives.  The round
trip then happens entirely inside the twenty seconds between boundaries,
and the delay at the boundary is the detector's 2,1 s and nothing more —
the figure a listener called natural.

```
narration ──► detector ──────────────────► boundary ──► play cached word
          └─► model, every ~15 s ──► cache ─┘            (no round trip here)
```

**What that costs and what it gives up.** One call per cache refresh —
about 30 in a ten-minute demonstration at a 20 s cadence, the same order
as the acknowledgements themselves, so it needs the per-demo ceiling
that is owed anyway.  And the word is chosen from audio up to a refresh
interval old, so it answers the idea that was being finished rather than
the instant it ended.  For an acknowledgement that is probably right —
it is *about* the idea that just closed — but it is unmeasured, and the
listening test that would settle it is the same shape as the ones in
this file.

**A cheaper step that buys most of the randomness today:** render
several takes of each phrase instead of one, and pick among them.  Six
words become eighteen clips, 130 KB instead of 43, the rotation stops
being audible, and nothing about the live path changes.  It does not
make the escriba responsive, only less mechanical — which is the smaller
half of the complaint but the half that is free.

**What would make the whole question go away** is duplex audio: a
session the narration streams into continuously, that can emit when it
decides to.  No provider installed here offers it — `audio_input` is a
WAV attached to a turn — so this is not a routing problem to solve but a
capability to wait for.  `jaato-scaffold explain providers` is where to
check whether that has changed.

## What this still does not answer

Four speakers is four, all Spanish, and only the owner's five recordings
are somebody narrating a screen — the register this feature exists for.
The other three are voice notes, where there is no listener and
therefore no reason for a cue to exist, which is why cues are confirmed
only in the owner's recordings. One listener judged every test, once per
file. The rate may differ by language, by subject, by how nervous
somebody is about being recorded; nothing here can say.

`python -m cadence` and `python -m cadence --calibration` re-answer
§1–§4 from any recordings. §5–§9 need somebody to listen.

"""Segment a push-to-talk microphone into utterances.

Reads a PipeWire/PulseAudio source continuously and cuts it into
utterances using an OUT-OF-BAND press signal, never by inferring
boundaries from the audio itself.  Knows nothing about jaato: it yields
``Utterance`` objects of raw PCM, and what happens to them is the
caller's business.

WHY THE SIGNAL IS OUT OF BAND.  Silence DURING a press is identical in
the samples to silence BETWEEN presses, so no amount of level detection
can tell "still listening" from "done".  The producer therefore
publishes a PipeWire metadata key and this module follows it.  The
outbound half of this system learned the same lesson the hard way: every
attempt to infer where a stream ended was wrong in some case, and the
one that shipped closed a player mid-utterance and started a second over
the top of it.

Contract, per the producer's spec:

* the source is ALWAYS readable -- between presses it yields digital
  silence, never EOF and never a stall.  Silence is not end-of-stream.
* it is opened ONCE for the session, not per utterance.
* ``wraith.ptt`` carries ``held`` / ``released`` / ``dropped``.
  ``released`` completes an utterance; ``dropped`` means the link died
  mid-press and the utterance is INCOMPLETE.
* the FIRST metadata line after connecting is STATE, not an edge --
  ``pw-metadata -m`` replays the current value on connect (confirmed by
  the producer against a fresh monitor with no writes).  Treating that
  replay as a transition fabricates a boundary at startup.
* the signal arrives BEFORE the audio it describes -- by 250-840 ms
  over the real link, bracketed by two real presses -- so a cut at
  the transition clips word endings.  :data:`TAIL_MS` carries past it.
"""
from __future__ import annotations

import audioop
import re
import shutil
import queue
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional, Tuple

#: The producer's source and signal.  Names, not paths: both are looked
#: up in the running audio graph.
SOURCE = "wraith_mic"
PTT_KEY = "wraith.ptt"

#: Native format of the source.  Requested explicitly so nothing
#: resamples: the source is s16le/16000/mono and so is this.
RATE, CHANNELS, WIDTH = 16000, 1, 2
BYTES_PER_SECOND = RATE * CHANNELS * WIDTH

# parec's DEFAULT buffer is two seconds: measured against wraith_mic it
# delivers nothing for 1.98s and then 40 blocks at once, so every
# boundary lands on a 2s grid and a sub-second tail cannot be expressed.
# Asking for a latency makes the cadence ~43ms. No samples are lost
# either way (4.05s of audio arrived in 4.0s wall with and without it);
# this buys granularity, not data.
LATENCY_MS = 50

#: How far past `released` to keep capturing.  This is not a cosmetic
#: fade -- it is the PIPELINE LATENCY L.  The phone encodes, the link
#: carries and ffmpeg decodes, so the audio for a moment arrives in the
#: stream well after the key transition that announced it.  Cut at the
#: release edge and audio is lost.  Measured on the first real press
#: through this path, a 250 ms tail ended the window 0.010 s after the
#: last voiced frame: the operator finished a sentence, paused, and had
#: begun a further phrase, which the cut severed 130 ms in.  Energy at
#: the final frame was 33 % of peak and RISING, so the window did not
#: close on silence.  Transcription confirms the shape -- the captured
#: part reads as a complete sentence, because what was lost was the
#: phrase AFTER it rather than the middle of a word.  A coherent
#: transcript is therefore no evidence that nothing was cut.
#:
#: L is BOUNDED, not known.  Two independent real presses bracket it.
#: Voiced audio was still at full energy 0.25 s past a release, and a
#: human never keeps speaking after tapping off, so L > 0.25 s.  Another
#: press put 0.837 s between the press signal and the first audio, and a
#: human never starts speaking before tapping on, so L < 0.837 s.  An
#: earlier reading of 1.32 s was the leading silence of a captured
#: window, which is L PLUS the operator's reaction time -- an upper
#: bound mistaken for a measurement, and excluded by the interval above.
#:
#: 1200 ms: past the 840 ms ceiling with room for jitter, and no more.
#:
#: It was 2500 ms, chosen when the only cost of over-shooting looked
#: like trailing silence a transcriber barely charges for.  That was
#: wrong about the cost.  This tail is DEAD AIR IN A CONVERSATION: it
#: runs after the caller stops speaking and before the request is even
#: sent, so it is added in full to the pause they sit through.  Measured
#: end to end, the gap before the agent starts talking was ~6.05 s, of
#: which 2.5 s was this constant -- 41 % of the silence, contributed by
#: a number picked for safety against a bound of 840 ms.
#:
#: The remaining margin is 360 ms over the measured ceiling.  Verified
#: complete captures had 2.24 s, 3.31 s and 3.41 s of trailing slack at
#: 2500 ms, so cutting 1300 ms still leaves every one of them whole.
TAIL_MS = 1200

#: Refuse to grow the ring without bound.  A press that outruns this is
#: a producer or operator fault, not something to absorb silently.
MAX_UTTERANCE_SECONDS = 120
#: Presses that may sit waiting for a slow consumer.  Bounded on
#: purpose: a consumer slower than the press rate would otherwise
#: grow this without limit and fall further behind forever.
MAX_PENDING_CUTS = 32

_LINE = re.compile(r"key:'([^']+)'\s+value:'([^']*)'")


#: Below this RMS a 20 ms frame counts as silence when trimming.  Not a
#: boundary detector -- the press already decided where the utterance
#: starts and ends, and this only removes quiet from INSIDE those
#: bounds, so it can neither merge two utterances nor split one.
SILENCE_RMS = 120

#: Silence kept either side of the speech.  Enough that a soft first
#: consonant is not clipped, far less than the seconds the press
#: boundaries leave in.
SILENCE_MARGIN_MS = 200


@dataclass
class Utterance:
    """One press, as PCM plus what is known about how it ended.

    ``complete`` is False when the press ended in ``dropped`` -- the link
    died mid-press, so the speaker did not finish.  Transcribing that as
    a finished sentence is how a half-question becomes a confident wrong
    answer, which is why it is carried rather than inferred later.
    """

    pcm: bytes
    complete: bool
    seconds: float
    started_at: float

    def trimmed(self) -> bytes:
        """The PCM with silence removed from both ends.

        The press decides WHERE an utterance begins and ends; this only
        removes quiet from inside those bounds, so it cannot merge two
        utterances or split one -- the failure mode that rules level
        detection out of boundary decisions does not apply here.

        Worth doing because the bounds are generous by construction.  A
        press captures the operator's reaction time at the front and
        :data:`TAIL_MS` at the back, and measured utterances ran 7.6 s to
        27.8 s for a sentence or two of speech.  Every one of those
        seconds is uploaded and billed as audio input, and paid for again
        in time-to-first-reply.

        Returns the original PCM when nothing crosses the threshold: an
        utterance of pure silence is a fact about the call, and sending
        nothing at all would look like a fault instead.
        """
        step = int(RATE * 0.020) * WIDTH
        frames = [self.pcm[i:i + step] for i in range(0, len(self.pcm), step)]
        loud = [i for i, f in enumerate(frames)
                if audioop.rms(f, WIDTH) > SILENCE_RMS]
        if not loud:
            return self.pcm
        margin = int(SILENCE_MARGIN_MS / 20)
        lo = max(0, loud[0] - margin)
        hi = min(len(frames), loud[-1] + 1 + margin)
        return b"".join(frames[lo:hi])

    def wav(self) -> bytes:
        """The trimmed PCM wrapped in a WAV header, for anything wanting a file."""
        import io, wave
        pcm = self.trimmed()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(CHANNELS)
            w.setsampwidth(WIDTH)
            w.setframerate(RATE)
            w.writeframes(pcm)
        return buf.getvalue()


class SourceMuted(RuntimeError):
    """The source is muted, so capture would record digital silence.

    Raised at startup rather than tolerated.  A muted source produces a
    pipeline that looks entirely healthy and records nothing, which is
    the worst failure this consumer can have -- and it was the shipped
    state of the producer until recently, so it is worth checking every
    time rather than assuming.
    """


class SignalLost(RuntimeError):
    """The press-signal watcher died, so no boundary can ever arrive.

    Fatal by design.  Without it the reader keeps consuming audio and
    simply never emits another utterance: alive, healthy-looking, and
    deaf.  A subscriber that connected successfully and receives nothing
    is indistinguishable from "nobody spoke", which is why this is loud.
    """


def source_is_muted(source: str = SOURCE) -> Optional[bool]:
    """Whether ``source`` is muted; None when it cannot be determined."""
    if not shutil.which("pactl"):
        return None
    out = subprocess.run(["pactl", "list", "sources"], capture_output=True,
                         text=True, env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"})
    block, seen = [], False
    for line in out.stdout.splitlines():
        if line.strip().startswith("Name:"):
            if seen:
                break
            seen = line.strip().endswith(source)
        if seen:
            block.append(line.strip())
    for line in block:
        if line.startswith("Mute:"):
            return line.split(":", 1)[1].strip() == "yes"
    return None


class PushToTalkMic:
    """Reads a PTT source and yields one :class:`Utterance` per press.

    Three threads.  Two because the halves are genuinely independent:
    audio arrives continuously whether or not anyone is speaking, and the
    boundaries arrive out of band.  Neither can be derived from the
    other, which is the whole reason this class exists.  The third exists
    because the thread watching the press key must never do work -- see
    :meth:`_drain`.

    The ring holds ``(absolute_offset, block)`` rather than raw bytes so
    a boundary can be expressed as a byte offset into a stream that never
    ends.  Wall-clock alone would not do: the two channels share no
    clock, so a transition is recorded as "the offset the reader had
    reached when the line arrived", which is the only quantity both
    halves can agree on.
    """

    def __init__(self, on_utterance: Callable[[Utterance], None],
                 source: str = SOURCE, tail_ms: int = TAIL_MS) -> None:
        self._on_utterance = on_utterance
        self._source = source
        #: The tail is carried as BYTES, not seconds.  The source runs at
        #: a fixed rate and never pauses, so "TAIL_MS after the release"
        #: is an arithmetic offset rather than a moment to wait for --
        #: which is what lets a late worker deliver an utterance whose
        #: bytes are still exactly right.
        self._tail_bytes = int(BYTES_PER_SECOND * tail_ms / 1000.0)
        self._cuts: "queue.Queue[Tuple[int, int, bool, float]]" = (
            queue.Queue(maxsize=MAX_PENDING_CUTS))
        #: Presses refused because the consumer fell too far behind.
        self.dropped_presses = 0
        self._blocks: Deque[Tuple[int, bytes]] = deque()
        self._read = 0                      # absolute bytes read, ever
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._audio: Optional[subprocess.Popen] = None
        self._signal: Optional[subprocess.Popen] = None
        self._fault: Optional[BaseException] = None
        #: Last value seen for the press key, or None before the first.
        #: An update repeating it is not an edge -- see _read_signal.
        self._state: Optional[str] = None
        #: None until a press is open; otherwise the offset it began at.
        self._press_from: Optional[int] = None
        self._press_started: float = 0.0

    # -- lifecycle ---------------------------------------------------
    def start(self) -> None:
        """Open both halves, refusing rather than recording silence."""
        muted = source_is_muted(self._source)
        if muted:
            raise SourceMuted(
                f"{self._source} is muted; capture would record digital "
                f"silence while looking perfectly healthy. Unmute it "
                f"(pactl set-source-mute {self._source} 0) and retry.")
        self._audio = subprocess.Popen(
            ["parec", "-d", self._source, "--format=s16le",
             f"--rate={RATE}", f"--channels={CHANNELS}",
             f"--latency-msec={LATENCY_MS}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._signal = subprocess.Popen(
            ["pw-metadata", "-m"], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        threading.Thread(target=self._read_audio, daemon=True).start()
        threading.Thread(target=self._read_signal, daemon=True).start()
        threading.Thread(target=self._drain, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        for proc in (self._audio, self._signal):
            if proc and proc.poll() is None:
                proc.terminate()

    def raise_if_faulted(self) -> None:
        """Surface a thread's fatal error on the caller's thread."""
        if self._fault is not None:
            raise self._fault

    # -- audio -------------------------------------------------------
    def _read_audio(self) -> None:
        """Consume the source forever; silence is data, not an ending."""
        block_size = BYTES_PER_SECOND // 20        # 50 ms
        limit = MAX_UTTERANCE_SECONDS * BYTES_PER_SECOND
        while not self._stop.is_set():
            block = self._audio.stdout.read(block_size)
            if not block:
                # stop() terminates parec, so the read we were parked in
                # returns empty. That is our own shutdown, not a death --
                # check before blaming the producer.
                if self._stop.is_set():
                    return
                # The contract says this never happens. If it does, the
                # producer died -- which is fatal, not an utterance end.
                self._fault = SignalLost(
                    "the audio source returned EOF; the spec says it is "
                    "always readable, so the producer has gone away")
                return
            with self._lock:
                self._blocks.append((self._read, block))
                self._read += len(block)
                while self._blocks and self._read - self._blocks[0][0] > limit:
                    self._blocks.popleft()

    def _slice(self, start: int, end: int) -> bytes:
        """Bytes in ``[start, end)`` from the ring, as far as it still holds."""
        out: List[bytes] = []
        with self._lock:
            for offset, block in self._blocks:
                lo, hi = offset, offset + len(block)
                if hi <= start or lo >= end:
                    continue
                out.append(block[max(0, start - lo):max(0, end - lo)])
        return b"".join(out)

    # -- the press signal --------------------------------------------
    def _read_signal(self) -> None:
        """Follow ``wraith.ptt``, acting only on genuine CHANGES.

        `pw-metadata -m` replays the whole metadata set on connect, and
        SOMETIMES replays it twice -- four keys, then the same four
        again, both dumps landing at t=0.00.  Measured on this startup
        path it happens in about 1 run in 10.  What triggers it is NOT
        known: a capture stream attaching afterwards never caused it in
        testing, and neither did one already settled, so it is a race at
        connect rather than a consequence of anything a consumer does.

        Which is exactly why the rule is worth more than the diagnosis.
        Deduplicating by value makes the trigger irrelevant: a repeated
        `held` cannot fabricate a press no matter what re-announced it,
        and the key is a LEVEL, so this subsumes the duplicate-`held`
        rule too.  The value already standing when we connect is STATE,
        not an edge: if it says `held`, a press is in flight whose audio
        we never saw, so that utterance is lost rather than merely late.
        """
        for line in self._signal.stdout:
            if self._stop.is_set():
                return
            match = _LINE.search(line)
            if not match or match.group(1) != PTT_KEY:
                continue
            value = match.group(2)
            if value == self._state:
                continue                     # a repeat, not a transition
            known = self._state is not None
            self._state = value
            if known:
                self._on_edge(value)
        if not self._stop.is_set():
            self._fault = SignalLost(
                "pw-metadata -m exited; no press boundary can arrive now, "
                "so this consumer would read audio forever and stay silent")

    def _on_edge(self, value: str) -> None:
        """Apply one transition, ignoring any that cannot follow this state.

        Must stay O(1).  This runs on the thread reading the press key,
        and that thread records WHERE the reader had got to, so anything
        slow here does not delay an utterance -- it moves the next
        press's boundary.  Measured with a 1.2s consumer inline (what a
        transcription call costs), three identical 0.4s presses came back
        as 0.60s, 0.25s and 0.25s: a press's ``held`` and ``released``
        were handled in the same instant at the same offset, collapsing
        the window to nothing but the tail.  Right count, no fault,
        wrong bytes.
        """
        holding = self._press_from is not None
        if value == "held":
            if holding:
                return                       # duplicate; keep the earlier start
            with self._lock:
                self._press_from = self._read
            self._press_started = time.time()
        elif value in ("released", "dropped"):
            if not holding:
                return                       # end without a start; nothing to cut
            start = self._press_from
            self._press_from = None
            with self._lock:
                end = self._read + self._tail_bytes
            try:
                self._cuts.put_nowait(
                    (start, end, value == "released", self._press_started))
            except queue.Full:
                # Refusing is honest; queueing would sink us deeper.
                self.dropped_presses += 1

    def _drain(self) -> None:
        """Cut and deliver each press, off the thread that watches the key.

        Both boundaries are byte offsets fixed at the instant their edge
        arrived, so this thread being late delays an utterance without
        altering it -- the whole reason the tail is bytes rather than a
        sleep.  Utterances are delivered in press order, one at a time,
        so a consumer is never re-entered.
        """
        while not self._stop.is_set():
            try:
                start, end, complete, started_at = self._cuts.get(timeout=0.1)
            except queue.Empty:
                continue
            if not self._wait_for(end):
                return                       # stopping; drop the partial cut
            pcm = self._slice(start, end)
            self._on_utterance(Utterance(
                pcm=pcm, complete=complete,
                seconds=len(pcm) / BYTES_PER_SECOND,
                started_at=started_at))

    def _wait_for(self, offset: int) -> bool:
        """Block until the reader has passed ``offset``; False if stopping."""
        while not self._stop.is_set():
            with self._lock:
                if self._read >= offset:
                    return True
            time.sleep(0.01)
        return False

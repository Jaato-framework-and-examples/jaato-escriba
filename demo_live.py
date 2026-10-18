"""The narration of a demonstration WHILE IT IS STILL BEING RECORDED.

`demo` stores what a demonstration LEAVES BEHIND: numbered frames, the
narration as the browser encoded it, and a manifest written at the end.
Everything there is an artifact the person keeps.  This holds the other
thing, which is not an artifact at all -- the person's voice arriving in
half-second pieces while they are still talking, so the escriba can
follow a demonstration instead of being handed one.

WHY NOT TIMESLICE THE RECORDER INSTEAD.  `MediaRecorder` was the obvious
place to cut, and it does not cut: only the first chunk of a webm carries
the header, so a later slice is not a file anything can decode, and
splicing the header back onto each one is a trick that depends on the
browser and the codec.  Restarting the recorder per slice gives whole
files and drops a few milliseconds at every boundary, which lands
mid-word.  The page already had the answer for the guest fan-out -- a
second consumer of the same `MediaStream`, raw samples, no container --
so the live path is that tap pointed somewhere else, and the recorder
keeps doing exactly what it did.  Raw PCM has no header to lose and no
splice to hear.

WHY A WINDOW AND NOT THE WHOLE DEMONSTRATION.  A demo is bounded at
`demo.MAX_FRAMES` -- 900 frames at a frame every two seconds, so half an
hour -- and half an hour of 48 kHz mono s16 is about 173 MB.  Per person,
in the server's memory, to be thrown away at close: the complete
narration is already on its way as one blob, and this exists only to be
heard SOON.  So what is kept is the recent past, and what falls off the
back was either already heard or was never going to be.

THE WINDOW IS NOT A POLICY ABOUT WHEN THE ESCRIBA LISTENS.  It is a
ceiling on memory, sized so that any listener reacting within three
minutes finds everything since it last looked.  Deciding when to look is
a separate question and does not live here.
"""
from __future__ import annotations

import io
import wave

#: How much of the recent narration is kept, in seconds.
#:
#: Three minutes, and the number comes from the slowest consumer rather
#: than from the memory: a window turn covers about 32 frames (64 s at
#: the page's cadence) and the turn that reads it takes seconds more, so
#: a minute would be inside the margin of one slow turn and this is not.
#: Costs 17 MB per person at 48 kHz mono, against the 173 MB of keeping
#: all of it.
WINDOW_SECONDS = 180

#: `s16le`, which is what the page's worklet converts its float samples
#: to before sending them.  Not configurable: the one producer sends this
#: and a second width would be a second thing to keep true.
WIDTH = 2


class ShapeChanged(ValueError):
    """The rate or channel count moved mid-demonstration.

    One page holds one `AudioContext` for the length of a recording, so
    its `sampleRate` cannot change under us.  If this is raised, the
    frames are not coming from where this thinks they are -- so it says
    so instead of resampling, which would hide a wiring mistake behind
    audio that merely sounds odd.
    """


class Narration:
    """The last `WINDOW_SECONDS` of one demonstration's live audio."""

    def __init__(self, window: float = WINDOW_SECONDS) -> None:
        self.window = window
        self.rate = 0
        self.channels = 0
        self._pcm = bytearray()
        #: Samples dropped off the back, so `heard` can report the true
        #: length of the demonstration rather than the length of what is
        #: still in hand.
        self._dropped = 0

    @property
    def _frame(self) -> int:
        return self.channels * WIDTH

    def add(self, pcm: bytes, rate: int, channels: int) -> None:
        """Append one piece of the narration, trimming the oldest away."""
        if not self.rate:
            self.rate, self.channels = rate, channels
        elif (rate, channels) != (self.rate, self.channels):
            raise ShapeChanged(
                f"narration began at {self.rate} Hz/{self.channels}ch "
                f"and this piece is {rate} Hz/{channels}ch")
        self._pcm += pcm
        keep = int(self.window * self.rate) * self._frame
        if len(self._pcm) > keep:
            self._dropped += len(self._pcm) - keep
            del self._pcm[:len(self._pcm) - keep]

    @property
    def held(self) -> float:
        """Seconds of narration in hand."""
        if not self.rate:
            return 0.0
        return len(self._pcm) / (self.rate * self._frame)

    @property
    def heard(self) -> float:
        """Seconds of narration that have arrived, including what fell off."""
        if not self.rate:
            return 0.0
        return (len(self._pcm) + self._dropped) / (self.rate * self._frame)

    def tail(self, seconds: float) -> bytes:
        """The most recent `seconds`, or everything if less has arrived."""
        if not self.rate or seconds <= 0:
            return b""
        want = int(seconds * self.rate) * self._frame
        return bytes(self._pcm[-want:] if want < len(self._pcm) else self._pcm)

    def wav(self, seconds: float) -> bytes:
        """The most recent `seconds` as a WAV file.

        WAV AND NOT MP3, because this module does not shell out: the
        encoder lives in `voice._to_mp3`, which takes any container
        ffmpeg reads, and one caller choosing to compress is not a
        reason for storage to depend on a binary being installed.
        """
        pcm = self.tail(seconds)
        if not pcm:
            return b""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(self.channels)
            w.setsampwidth(WIDTH)
            w.setframerate(self.rate)
            w.writeframes(pcm)
        return buf.getvalue()

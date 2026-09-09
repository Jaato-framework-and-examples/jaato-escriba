"""The scribe's ears and mouth — everything that is NOT the SDK.

Two thin adapters over two modules that already work and are copied
unchanged from `jaato-cascade-audio-interchange`:

    ptt_capture.PushToTalkMic   one key press -> one `Utterance`
    pulse_playback.PulsePlayer  mime + bytes  -> the speaker

Neither knows anything about jaato, and that is exactly why this file
exists: `run_escriba.py` should read as what it means to demonstrate —
two sessions and three `ask` calls — and not as audio plumbing with an
SDK call buried inside it.

All that is added here is the bridge between two worlds: the microphone
delivers on a THREAD, and the driver waits with `await`.
"""
from __future__ import annotations

import asyncio
import base64
import shutil
import subprocess
from typing import Optional

import ptt_capture
import pulse_playback

#: What the model receives.  MP3, not the WAV `Utterance.wav()` produces.
#:
#: WHY COMPRESS.  It began as a workaround for jaato#920: the runner RPC
#: serialised bytes with `json.dumps(default=str)`, a Python repr that
#: inflated a maximum-length press (`MAX_UTTERANCE_SECONDS` = 120, 3.84 MB
#: of PCM) 4.2x to 16.7 MB against a 10.49 MB frame cap, closing the
#: transport mid-turn and ending the session.
#:
#: That is FIXED (#921): bytes now cross as base64 and an oversized frame
#: is dropped as a typed error instead of desynchronising the channel.
#: Verified here — the exact recording that killed a real session now
#: completes uncompressed.
#:
#: This stays for headroom, not necessity.  The cap still exists, and the
#: utterance still sits in history until the turn that consumed it is
#: evicted: 5.12 MB per press as WAV against 0.64 MB as MP3.  8x is worth
#: keeping for a bound nobody wants to meet again.
UTTERANCE_MIME = "audio/mpeg"
#: Mono, 16 kHz, 32 kbps: 8x smaller than the PCM.  Speech at this
#: bitrate is what telephony has always been; if it ever costs the model
#: intelligibility, this is the one number to raise (64 kbps is still 4x).
MP3_BITRATE = "32k"


def _encoder() -> str:
    """The ffmpeg binary, or a loud failure.

    Deliberately NOT a fallback to sending WAV.  Falling back would put
    the 4.2x path back in silently, and the failure it causes — the
    transport closing mid-turn — reads as anything but "the encoder is
    missing".  Better to refuse to start.
    """
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise RuntimeError(
            "escriba needs ffmpeg to compress what you say: raw PCM of a "
            "long utterance overflows the runner RPC frame cap and kills "
            "the session (jaato#920).  Install ffmpeg.")
    return exe


def _to_mp3(wav: bytes) -> bytes:
    """WAV in, MP3 out. Raises on a failed encode rather than sending PCM."""
    proc = subprocess.run(
        [_encoder(), "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-codec:a", "libmp3lame", "-b:a", MP3_BITRATE, "-ac", "1",
         "-ar", "16000", "-f", "mp3", "pipe:1"],
        input=wav, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(
            f"ffmpeg could not encode the utterance "
            f"(rc={proc.returncode}): {proc.stderr.decode()[:200]}")
    return proc.stdout


class Ears:
    """The push-to-talk microphone, in `await` form.

    `PushToTalkMic` delivers each press by calling a callback from its own
    thread.  An asyncio driver cannot collect it there, so the queue is
    the meeting point: the mic thread deposits with `call_soon_threadsafe`
    and the driver's loop waits with `get`.
    """

    def __init__(self, source: str = ptt_capture.SOURCE) -> None:
        _encoder()                     # fail now, not on the first utterance
        self._queue: asyncio.Queue = asyncio.Queue()
        self._loop = asyncio.get_running_loop()
        self._mic = ptt_capture.PushToTalkMic(self._deliver, source=source)

    def _deliver(self, u: "ptt_capture.Utterance") -> None:
        """Runs on the MIC's thread; only crosses the boundary."""
        self._loop.call_soon_threadsafe(self._queue.put_nowait, u)

    def __enter__(self) -> "Ears":
        self._mic.start()          # raises SourceMuted if the source is muted
        return self

    def __exit__(self, *_) -> None:
        self._mic.stop()

    def _in_flight(self) -> bool:
        """Is an utterance under way right now?

        Two states, and both are needed:

        - `_press_from` stops being None while the key is held down.
        - `_cuts` holds closed cuts not yet delivered: between releasing
          the key and the utterance reaching the queue there is the audio
          queue's own delay (TAIL_MS plus however long the reader takes to
          reach the offset), and in that window the key is already up
          while the phrase is still on its way.

        Looking only at the key would let that window count as silence.
        """
        return (self._mic._press_from is not None
                or not self._mic._cuts.empty())

    async def listen(self, timeout: float) -> Optional[dict]:
        """The next utterance as an attachment, or None if nobody is there.

        `timeout` measures ABANDONMENT, not duration: it restarts while
        the person is still speaking.

        Counting it the other way was a real bug.  An utterance is
        delivered when the key is RELEASED, not when speech starts
        (ptt_capture.py:426-432), so a single `wait_for` on the queue
        measures "how long you take to finish talking".  Someone who
        paused ten seconds to think and then explained for forty
        delivered at fifty, and with the deadline at forty-five the
        conversation was declared over WHILE they were still speaking.  A
        long explanation was indistinguishable from an empty room.

        `MAX_UTTERANCE_SECONDS` bounds the wait: a press cannot last
        forever, so this cannot hang.
        """
        while True:
            self._mic.raise_if_faulted()   # a dead thread is not silence
            try:
                u = await asyncio.wait_for(self._queue.get(), timeout)
            except asyncio.TimeoutError:
                if self._in_flight():
                    continue              # still talking; deadline restarts
                return None
            # Encoding is CPU work on a 2-minute buffer; off the loop so
            # the SDK's drain task keeps running while it happens.
            data = await asyncio.to_thread(_to_mp3, u.wav())
            return {"mime_type": UTTERANCE_MIME, "data": data,
                    "display_name": "utterance.mp3"}


class Tongue:
    """Plays what the model says, as it arrives.

    Passed straight to `Session.ask` as `on_media=`: the SDK hands the
    model's speech over there while the text comes back through the
    return value.

    `finish` BLOCKS until that chunk stops sounding, and here that is
    correct rather than an oversight: while the scribe is speaking there
    is nothing to listen to, and reopening the microphone before it goes
    quiet would be recording itself.
    """

    def __init__(self) -> None:
        self._player = pulse_playback.PulsePlayer()
        #: What the provider says it said.  The FINAL chunk carries the
        #: transcript of its own audio (jaato#869); a spoken turn returns
        #: no text through `ask`, so without this nothing on screen would
        #: show what was heard.
        self.spoken: list[str] = []

    def speak(self, ev) -> None:
        self._player.feed(ev.stream_id, ev.mime_type,
                          base64.b64decode(ev.data_b64))
        if ev.final:
            if getattr(ev, "chunk", ""):
                self.spoken.append(ev.chunk)
            self._player.finish(ev.stream_id)

    def last(self) -> str:
        """What was spoken since this was last asked — and clears it."""
        text = " ".join(t.strip() for t in self.spoken if t.strip())
        self.spoken.clear()
        return text

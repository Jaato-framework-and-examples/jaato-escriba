"""PulseAudio playback for headerless PCM, fed chunk by chunk.

Split out of ``run_observer.py`` so that file reads as what it is meant
to demonstrate — attaching to a cascade and consuming its events through
the SDK — rather than as audio plumbing with a subscription buried in
it.  Nothing here knows about jaato: it takes a mime type and bytes.

The observer stays the SDK example; this is the speaker driver.
"""
import shutil
import time
import subprocess


def _pcm_params(mime_type: str) -> dict[str, str] | None:
    """Read rate/channels/encoding out of a mime type, or None.

    The framework tags streamed speech as e.g.
    ``audio/pcm;rate=24000;channels=1;encoding=s16le``.  Those parameters
    are spelled out precisely because the payload is HEADERLESS — nothing
    in the bytes says how to play them — so they are parsed here rather
    than assumed.  A mime type this player cannot satisfy returns None
    and is reported instead of being guessed at.
    """
    base, _, params = mime_type.partition(";")
    if base.strip().lower() != "audio/pcm":
        return None
    parsed = {}
    for item in params.split(";"):
        key, _, value = item.partition("=")
        if key.strip():
            parsed[key.strip().lower()] = value.strip()
    if not {"rate", "channels", "encoding"} <= parsed.keys():
        return None
    return parsed


class PulsePlayer:
    """Plays model speech through PulseAudio as chunks arrive.

    One `paplay` per stream_id, fed on stdin, so audio starts before the
    turn ends — the point of observing a run live rather than opening a
    file afterwards.

    Playback failure never stops the trace: this is a read-only observer,
    and losing sound is not a reason to stop reporting events.  It says
    so once, loudly, rather than failing silently.
    """

    def __init__(self, enabled: bool = True, on_problem=None) -> None:
        #: Where trouble goes when the caller owns the screen.  This class
        #: PRINTS by default, which is right for a plain terminal and
        #: invisible under a redrawing one — and a silent playback failure
        #: is indistinguishable from working audio, because everything
        #: downstream records what was RECEIVED, not what was heard.
        self._on_problem = on_problem
        self._procs: dict[str, subprocess.Popen] = {}
        #: Bytes handed to each player, and the frame size to turn them
        #: back into seconds.  A drain deadline has to scale with the
        #: audio: a fixed one silently orphans anything longer than it.
        self._written: dict[str, int] = {}
        self._bytes_per_second: dict[str, int] = {}
        self._enabled = enabled and shutil.which("paplay") is not None
        self._complained = False
        #: What the LAST finished stream actually did.  Read by
        #: the caller into the manifest, because 'we handed it to
        #: PulseAudio' is not the same claim as 'they heard it'.
        self.outcome: dict = {}
        if enabled and not self._enabled:
            print("  (paplay not on PATH — tracing without sound)")

    def feed(self, stream_id: str, mime_type: str, payload: bytes) -> None:
        """Push one chunk into this stream's player, starting it if needed."""
        if not self._enabled:
            return
        proc = self._procs.get(stream_id)
        if proc is None:
            params = _pcm_params(mime_type)
            if params is None:
                self._complain(f"cannot play {mime_type} — not headerless PCM")
                return
            try:
                proc = subprocess.Popen(
                    ["paplay", "--raw",
                     f"--format={params['encoding']}",
                     f"--rate={params['rate']}",
                     f"--channels={params['channels']}"],
                    stdin=subprocess.PIPE)
            except OSError as exc:
                self._complain(f"could not start paplay: {exc}")
                return
            self._procs[stream_id] = proc
            self._written[stream_id] = 0
            width = 2 if params["encoding"].endswith("16le") else 1
            self._bytes_per_second[stream_id] = (
                int(params["rate"]) * int(params["channels"]) * width)
        try:
            proc.stdin.write(payload)
            proc.stdin.flush()
            self._written[stream_id] = self._written.get(stream_id, 0) + len(payload)
        except (BrokenPipeError, ValueError) as exc:
            self._complain(f"playback stream died: {exc}")

    def finish(self, stream_id: str) -> None:
        """Close one stream and wait for it to finish PLAYING.

        The deadline is derived from the audio handed over, not fixed.
        It used to be ``timeout=30``, which orphaned every answer longer
        than thirty seconds: a 31s narration timed out, this process gave
        up and exited, and ``paplay`` kept playing -- into the NEXT run,
        heard as two answers at once.  A constant cannot bound a wait
        whose length is the caller's data.

        The margin covers PulseAudio's own buffering and scheduling, and
        the floor covers a stream so short the ratio is meaningless.
        """
        proc = self._procs.pop(stream_id, None)
        if proc is None:
            # Nothing was ever fed for this stream: paplay failed to start,
            # or the mime was unplayable.  Recorded rather than silently
            # skipped — this is the case that looks identical to success
            # everywhere downstream.
            self.outcome = {"stream_id": stream_id, "played": False,
                            "reason": "no player", "seconds": 0.0}
            return
        try:
            proc.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
        rate = self._bytes_per_second.pop(stream_id, 0)
        written = self._written.pop(stream_id, 0)
        seconds = (written / rate) if rate else 0.0
        started = time.monotonic()
        try:
            proc.wait(timeout=max(15.0, seconds + 15.0))
            waited = time.monotonic() - started
            # Audio that "finished" in a fraction of its own duration was
            # not played: paplay exited early, or never had the data.
            self.outcome = {"stream_id": stream_id,
                            "played": waited >= seconds * 0.5,
                            "waited": round(waited, 2),
                            "seconds": round(seconds, 2),
                            "exit": proc.returncode}
        except subprocess.TimeoutExpired:
            self.outcome = {"stream_id": stream_id, "played": False,
                            "reason": "timeout", "seconds": round(seconds, 2)}
            # Past the audio's own duration plus a margin it is wedged,
            # not playing.  Killing it is better than orphaning it: an
            # orphan outlives this process and bleeds into the next run.
            self._complain(f"playback did not finish in {seconds + 15.0:.0f}s; killing")
            proc.kill()
            proc.wait(timeout=5)

    def finish_all(self) -> None:
        for stream_id in list(self._procs):
            self.finish(stream_id)

    def _complain(self, message: str) -> None:
        if self._complained:
            return
        self._complained = True
        if self._on_problem is not None:
            self._on_problem(message)
        else:
            print(f"  AUDIO: {message}")

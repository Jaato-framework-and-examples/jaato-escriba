"""The terminal side of the conversation. Nothing here talks to the SDK.

A spoken turn is ten to twenty-five seconds during which the person has
released the key and nothing is on screen: no way to tell thinking from
hung. This draws a line that says which.

It also owns `log`, because a spinner and a bare `print` cannot share a
terminal — the print lands on top of the spinner's line and both are
mangled. Anything that writes while a turn is running goes through here.
"""
from __future__ import annotations

import asyncio
import itertools
import shutil
import sys
import textwrap
import threading
from datetime import datetime

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
INTERVAL = 0.09
#: A pipe or a file gets no spinner: the escape codes would be noise in a
#: log and garbage in a test's captured output.
LIVE = sys.stdout.isatty()

_lock = threading.Lock()
_active: "Spinner | None" = None


#: Width of the stamp, so continuation lines can be indented under it.
STAMP = len("[00:00:00] ")

#: Narrowest terminal worth wrapping to.  Below this the indent would eat
#: most of the line and the text would be less readable wrapped than
#: overflowing, so it is left alone.
MIN_WIDTH = STAMP + 24


def _wrap(line: str, width: int) -> list[str]:
    """One source line, hard-wrapped to sit right of the stamp.

    The terminal wraps a long line at column 0, so the tail of a reply
    landed under the timestamp instead of under the text it continues —
    which reads as a new entry rather than the same one. Wrapping here
    instead means the indent is ours and the continuation lines up with
    the first word.

    Long words are NOT broken: a reference URL that survives intact can
    be copied out of the transcript, and one that has been split at
    column 79 cannot. A URL wider than the terminal therefore still
    overflows and gets soft-wrapped — kept deliberately, because a
    readable line matters less than a usable link.
    """
    return textwrap.wrap(
        line, width=width - STAMP,
        break_long_words=False, break_on_hyphens=False) or [line]


def log(message: str) -> None:
    """Write a timestamped line without mangling a running spinner.

    The spinner redraws itself on its next tick, so clearing the line and
    printing above it is enough.

    Every line is stamped because a voice conversation has long silences
    in it — the model thinking, a search running behind — and "how long
    did that take" is the first question anyone asks of the transcript.
    A blank line stays blank: stamping it would be noise.

    Multi-line messages get the stamp once and the rest indented under it,
    so a wrapped reply stays readable as one entry.  That applies to lines
    the terminal would have wrapped as well as to ones that arrived with a
    newline in them: only the first line carries a timestamp, and
    everything else lines up with the text, not with the stamp.

    Wrapping is for the terminal only.  A pipe or a file has no width
    worth honouring, and inserting hard breaks there would change what a
    test greps for and what a log holds.
    """
    now = datetime.now().strftime("%H:%M:%S")
    width = shutil.get_terminal_size().columns if LIVE else 0
    out, stamped = [], False
    for line in message.split("\n"):
        if not line.strip():
            out.append("")                     # a blank line stays blank
            continue
        pieces = _wrap(line, width) if width >= MIN_WIDTH else [line]
        for piece in pieces:
            if not stamped:
                out.append(f"[{now}] {piece}")  # the FIRST non-blank carries it
                stamped = True
            else:
                out.append(" " * STAMP + piece)
    with _lock:
        if LIVE and _active is not None:
            sys.stdout.write("\r\033[K")
        print("\n".join(out), flush=True)


class Spinner:
    """Runs while a turn is in flight; stops the moment it starts speaking.

    `stop()` is deliberately callable from the media sink rather than only
    on exit: the interesting moment is the FIRST audio chunk, when the
    person starts hearing the answer, not when the turn finally settles
    seconds later.
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def _spin(self) -> None:
        for frame in itertools.cycle(FRAMES):
            if self._stop.is_set():
                return
            with _lock:
                now = datetime.now().strftime("%H:%M:%S")
                sys.stdout.write(f"\r\033[K[{now}] {frame} {self._text}")
                sys.stdout.flush()
            try:
                await asyncio.wait_for(self._stop.wait(), INTERVAL)
                return
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """Idempotent: the media sink calls it on every chunk."""
        self._stop.set()

    async def __aenter__(self) -> "Spinner":
        global _active
        if LIVE:
            _active = self
            self._task = asyncio.create_task(self._spin())
        return self

    async def __aexit__(self, *_) -> None:
        global _active
        self.stop()
        if self._task is not None:
            await self._task
        with _lock:
            if LIVE:
                sys.stdout.write("\r\033[K")
                sys.stdout.flush()
            _active = None

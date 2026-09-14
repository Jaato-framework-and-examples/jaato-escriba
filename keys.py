"""One reader for the terminal's keys, because there can only be one.

WHY THIS IS NOT IN `richboard`.  Two things in this repo want stdin: the
live view, for navigation, and `KeyboardPushToTalkMic`, for the Space that
starts and stops recording.  A file descriptor cannot be read twice —
whichever loop calls `read` first takes the byte and the other never sees
it — so the choice is not "who reads" but "who reads AND passes on".

This is that reader.  The view registers what it wants; anything it does
not claim goes to the fallback, which is how the microphone still gets its
Space in WSL while the view gets `j`, `k` and the rest.  On the wraith
backend the microphone reads no keys at all (presses arrive through
`pw-metadata`), so the fallback is simply never used.

CBREAK, NOT RAW — the same lesson `ptt_capture` learned.  `raw` clears
OPOST, and OPOST is what turns `\\n` into `\\r\\n` on the way out; under it
every line the program prints loses its carriage return.  cbreak gives the
unbuffered single keys this needs and leaves output alone, and it keeps
ISIG so Ctrl-C is still a signal rather than a byte to handle.
"""
from __future__ import annotations

import contextlib
import os
import select
import sys
import termios
import threading
import tty
from typing import Callable, Dict, Optional


class Keys:
    """Reads single keys on a thread and dispatches them by name.

    Started only when the caller owns the screen.  Without a TTY it
    refuses rather than pretending: a reader on a pipe would consume
    nothing and report no error, which looks exactly like a keyboard
    nobody is touching.
    """

    def __init__(self, bindings: Dict[str, Callable[[], None]],
                 fallback: Optional[Callable[[str], None]] = None) -> None:
        self._bindings = bindings
        self._fallback = fallback
        self._stop = threading.Event()
        self._old: Optional[list] = None
        self._wake_r: Optional[int] = None
        self._wake_w: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def __enter__(self) -> "Keys":
        if sys.stdin.isatty():
            self._wake_r, self._wake_w = os.pipe()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._stop.set()
        if self._wake_w is not None:
            try:
                os.write(self._wake_w, b"\x00")
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._restore()
        self._close_pipe()
        return False

    @contextlib.contextmanager
    def paused(self):
        """Give the terminal to a child program, then take it back.

        The counterpart of `prompt_toolkit`'s `input.detach()` plus
        `input.cooked_mode()`, which is how `jaato-tui` runs an editor
        over its own display (`pt_display.py:_open_workspace_file` ->
        `run_in_terminal`).

        CALLED FROM A KEY HANDLER, AND ONLY FROM THERE.  Handlers run on
        the reader thread — `_loop` calls `_fire` — so while one is
        running this class is, by construction, not in `select` and not
        reading stdin.  There is therefore no second reader to stand
        down, and `detach` collapses to nothing: all that is left is
        putting the line discipline back, which is what the child needs.

        That matters for more than tidiness.  A full-screen program asks
        the terminal where the cursor is (`ESC[6n`) and reads the answer
        BACK from stdin; with this loop still selecting, the answer goes
        to the wrong reader and the child fails — `leaf` reports "The
        cursor position could not be read within a normal duration".
        `prompt_toolkit` guards the same edge, one step earlier, by
        draining its own pending CPRs before detaching.
        """
        cooked = self._old        # what the terminal was before cbreak
        self._restore()
        try:
            yield
        finally:
            # Back to the ORIGINAL settings, not to whatever the child
            # left behind: re-reading them here would bake a crashed
            # program's terminal state into our own restore-on-exit.
            if cooked is not None and not self._stop.is_set():
                with self._lock:
                    self._old = cooked
                tty.setcbreak(sys.stdin.fileno())

    def _restore(self) -> None:
        """Put the terminal back.  Safe from either thread, once."""
        with self._lock:
            if self._old is None:
                return
            old, self._old = self._old, None
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old)

    def _close_pipe(self) -> None:
        for attr in ("_wake_r", "_wake_w"):
            fd = getattr(self, attr)
            if fd is None:
                continue
            setattr(self, attr, None)
            try:
                os.close(fd)
            except OSError:
                pass

    def _loop(self) -> None:
        fd = sys.stdin.fileno()
        self._old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        try:
            while not self._stop.is_set():
                ready, _, _ = select.select([sys.stdin, self._wake_r], [], [], 0.4)
                if self._wake_r in ready or self._stop.is_set():
                    return
                if not ready:
                    continue
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    # An escape is either the key itself or the start of a
                    # sequence (the arrows are `ESC [ A`).  Peek without
                    # blocking: nothing behind it means the key.
                    more, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if not more:
                        self._fire("esc")
                        continue
                    seq = sys.stdin.read(2)
                    self._fire({"[A": "up", "[B": "down",
                                "[C": "right", "[D": "left"}.get(seq, ""))
                    continue
                # Enter arrives as CR or LF depending on ICRNL, which
                # cbreak leaves alone — so a caller binding one of them
                # misses the key half the time, on a terminal setting it
                # has no reason to know about.  Named once, here.
                self._fire({"\r": "enter", "\n": "enter"}.get(ch, ch))
        finally:
            self._restore()
            self._close_pipe()

    def _fire(self, name: str) -> None:
        if not name:
            return
        handler = self._bindings.get(name)
        if handler is not None:
            try:
                handler()
            except Exception:
                # A handler that raises must not take the reader with it:
                # the terminal is in cbreak and only this loop restores it.
                pass
        elif self._fallback is not None:
            self._fallback(name)

"""Hand the terminal to a child program, and take it back intact.

The riskiest code here, and the least visible: every failure mode shows up
as a corrupted screen rather than an exception.  Both halves are checked
against what they are FOR, not against how they are written —

    the key reader   must leave a COOKED terminal behind, because a
                     full-screen child asks where the cursor is (`ESC[6n`)
                     and reads the reply from stdin; with this loop still
                     selecting, the reply goes to the wrong reader and the
                     child fails outright.

    the board        must stop DRAWING, because the disk tick and the
                     enrichment observer keep calling in from their own
                     tasks the whole time the child is up, and `Live.stop`
                     has already popped the render hook — so every one of
                     those refreshes would land on the child's screen.

Runs headless on a pty.  `leaf` itself is not launched: a bare pty never
answers the cursor query, so it would fail here for a reason that has
nothing to do with this code.  A stub stands in and reports the terminal
it was handed, which is the whole of what we owe it.
"""
import os, pty, subprocess, sys, termios, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

fail = 0


def check(label: str, got, want) -> None:
    global fail
    if got != want:
        print(f"  FAIL {label:<34} got {got!r}, want {want!r}")
        fail += 1


# ----------------------------------------------------------------- input
master, slave = pty.openpty()
# fd 0 must BE the pty: a child inherits descriptors, not Python objects,
# and `Keys` works on `sys.stdin.fileno()`.
os.dup2(slave, 0)
sys.stdin = os.fdopen(0, "r", closefd=False)
# Drain the master, or the refresh thread blocks on a full buffer and
# `Live.stop` hangs forever joining it.
threading.Thread(target=lambda: [os.read(master, 65536) for _ in iter(int, 1)],
                 daemon=True).start()

import keys as _keys          # noqa: E402  (must follow the stdin swap)
import richboard              # noqa: E402
from rich.console import Console  # noqa: E402


def canonical() -> bool:
    return bool(termios.tcgetattr(sys.stdin.fileno())[3] & termios.ICANON)


seen = {}


def on_key() -> None:
    seen["in_handler"] = canonical()
    with typing.paused():
        seen["while_paused"] = canonical()
        seen["child_saw_tty"] = subprocess.run(
            [sys.executable, "-c",
             "import sys,termios;a=termios.tcgetattr(0);"
             "print(sys.stdin.isatty() and bool(a[3]&termios.ICANON))"],
            capture_output=True, text=True).stdout.strip()
    seen["after_paused"] = canonical()


typing = _keys.Keys(bindings={"enter": on_key})
typing.__enter__()
time.sleep(0.3)
check("cbreak while reading", canonical(), False)
os.write(master, b"\r")
time.sleep(1.5)
typing.__exit__(None, None, None)
time.sleep(0.2)

check("handler runs in cbreak", seen.get("in_handler"), False)
check("COOKED for the child", seen.get("while_paused"), True)
check("child got a real terminal", seen.get("child_saw_tty"), "True")
check("cbreak taken back", seen.get("after_paused"), False)
check("terminal returned on exit", canonical(), True)

# ----------------------------------------------------------- one reader
# Two loops on one descriptor do not merely compete for Space: the
# microphone's reader discards whatever is not Space, so it ate half of
# `j`, `k`, `enter` and `esc` as well.  Measured before the fix: 15 of 30
# `j` presses gone, and Space a coin flip.  Invisible on a wraith machine,
# where a press is a `pw-metadata` event and nothing reads the terminal.
import ptt_capture                                        # noqa: E402
import voice                                              # noqa: E402

check("Ears.toggle is a real method",
      callable(getattr(voice.Ears, "toggle", None)), True)
check("the backend says if Space is a key",
      ptt_capture.KeyboardPushToTalkMic.reads_keyboard, True)
check("wraith needs no keyboard",
      ptt_capture.PushToTalkMic.reads_keyboard, False)

quiet_mic = ptt_capture.KeyboardPushToTalkMic(
    lambda u: None, source="dummy", on_state=lambda on: None, read_keys=False)
check("told not to read, it does not", quiet_mic._read_keys, False)

fired = []
quiet_mic._on_edge = lambda state: fired.append(state)
moved = {"n": 0}
nav = _keys.Keys({"j": lambda: moved.__setitem__("n", moved["n"] + 1),
                  " ": quiet_mic.toggle})
nav.__enter__()
time.sleep(0.4)
SENT = 30
for _ in range(SENT):
    os.write(master, b"j")
    time.sleep(0.05)
for _ in range(10):
    os.write(master, b" ")
    time.sleep(0.05)
time.sleep(0.8)
nav.__exit__(None, None, None)

check("no navigation key is lost", moved["n"], SENT)
check("every Space toggles", len(fired), 10)

# ---------------------------------------------------------------- output
ROW = [{"text": "docs/a.md", "path": "/tmp/a.md", "at": "2026-09-11 10:00"}]


class Recorder:
    """A file that remembers what was written to it, and how much."""

    def __init__(self):
        self.buf, self.lock = "", threading.Lock()

    def write(self, s):
        with self.lock:
            self.buf += s
        return len(s)

    @property
    def n(self):
        return len(self.buf)

    def flush(self): ...
    def isatty(self): return True


rec = Recorder()
view = richboard.RichBoard()
view._console = Console(file=rec, force_terminal=True, width=100, height=30)
view.__enter__()
view.listing("documentos", ROW)
time.sleep(0.4)
first_live = view._live

with view.suspended():
    # The frame must come OFF the screen, not merely stop being redrawn:
    # `stop` leaves the cursor one row below a frame it still remembers,
    # and a display resumed in that state steps back one row short and
    # draws a second copy of the panel's top border over the first.
    check("frame erased on suspend", first_live.transient, True)
    before = rec.n
    for i in range(5):                     # the disk tick and the observer
        view.memories(i, i)
        view.listing("documentos", ROW)
        view.note(f"tick {i}")
    time.sleep(0.8)                        # long enough to catch a stray tick
    drawn_while_suspended = rec.n - before
    leaving = rec.n

drawn_by_resume = rec.n - leaving       # the display coming back
resumed_live = view._live               # BEFORE __exit__, which drops it
mark = rec.n
view.note("after")                      # and still answering events
time.sleep(0.3)
drawn_after = rec.n - mark
view.__exit__(None, None, None)

# ------------------------------------------------------- wasted redraws
# The disk tick reports the same counts and the same rows every couple of
# seconds.  Each of those calls used to repaint the pane, and a repaint
# erases every row before rewriting it -- which is what the flicker was.
rec2 = Recorder()
idle = richboard.RichBoard()
idle._console = Console(file=rec2, force_terminal=True, width=100, height=30)
idle.__enter__()
idle.memories(26, 4); idle.catalogue(43)
idle.listing("documentos", ROW)
idle.spoke("una respuesta", "anotado")
time.sleep(0.3)
quiet = rec2.n
for _ in range(5):                      # exactly what `_reconcile` does
    idle.memories(26, 4)
    idle.catalogue(43)
    idle.listing("documentos", ROW)
time.sleep(0.5)
redundant = rec2.n - quiet
idle.spoke("otra respuesta", "anotado")     # a real change
time.sleep(0.3)
real = rec2.n - quiet - redundant
idle.__exit__(None, None, None)

check("no redraw when unchanged", redundant, 0)
check("a real change still draws", real > 0, True)

check("silent while suspended", drawn_while_suspended, 0)
check("resumes on a fresh display", resumed_live is not first_live, True)
check("redrawn on resume", drawn_by_resume > 0, True)
check("events draw again", drawn_after > 0, True)
check("state kept accumulating", view.state.curated, 4)

print("terminal handover OK" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)

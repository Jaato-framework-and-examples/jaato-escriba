"""What the conversation looks like from outside, as state rather than text.

Nothing here imports `rich` and nothing here writes to a terminal, so the
whole model can be exercised headless — the same split the cascade
observers in the sibling repos use, and for the same reason: a bug in what
the display CLAIMS is then a bug in a class with tests, not in a renderer
nobody can run without a tty.

Four boards, one interface:

    NullBoard     no-op. What tests pass when they do not care.
    LineBoard     prints, exactly as this driver always has. The default:
                  without `--tui` nothing about the output changes, which
                  is what keeps every harness and every grepped log valid.
    StateBoard    records into `Conversation` and prints nothing.
    RichBoard     `StateBoard` plus drawing. Lives in `richboard.py`,
                  the only module that imports `rich`.

WHY EVERY LINE GOES THROUGH HERE.  With a live display running, a stray
`print` lands inside the region `rich.Live` redraws and corrupts it — the
same reason `console.log` had to exist in the first place, one layer up.
So the driver and the observer stop formatting their own lines and report
WHAT HAPPENED instead; the board decides whether that becomes a printed
line, a panel row, or nothing.

That is also why the methods are named for events rather than for text.
`note("· searching: x")` would have moved the formatting, not removed it.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Dict, List, Optional

import console

#: The sidebar panels, in the order the cursor walks them.  Named here
#: rather than in the renderer: which panels EXIST is a fact about the
#: conversation, and only how they look belongs to `richboard`.
PANELS = ("memoria", "referencias", "documentos")

#: Turns kept in the live view.  A window on the present, not a log: the
#: manifest under `audio/` is the durable record and it is complete.
TRANSCRIPT = 400


@dataclass
class Entry:
    """One line of the conversation, before anyone decides how it looks."""
    at: datetime
    kind: str                     # said | spoke | note | anotado
    text: str
    seconds: Optional[float] = None
    audio: Optional[str] = None   # the att_ id, when there is a recording
    repeats: int = 1


@dataclass
class Conversation:
    """Everything the panels draw, and the only thing that decides truth."""
    entries: Deque[Entry] = field(default_factory=lambda: deque(maxlen=TRANSCRIPT))
    curated: int = 0
    raw: int = 0
    #: Deltas since the session opened, so the sidebar can show movement.
    #: A count alone does not distinguish "8 waiting" from "8 more since
    #: you started", and on 2026-09-11 the difference was 83 duplicates.
    curated_at_start: Optional[int] = None
    raw_at_start: Optional[int] = None
    #: How many references the catalogue HOLDS, read from disk. Distinct
    #: from `found`, which is what this session added: a panel showing only
    #: the session's finds reads as a total and is wrong by the size of
    #: everything learnt before today.
    catalogue: int = 0
    found: Deque[str] = field(default_factory=lambda: deque(maxlen=60))
    selected: Deque[str] = field(default_factory=lambda: deque(maxlen=60))
    documents: Deque[str] = field(default_factory=lambda: deque(maxlen=40))
    #: What each panel HOLDS, newest first, for the panel preview and for
    #: the popup behind it.  Filled by the disk tick, not by events: the
    #: curator writes from its own session and the documenter from a
    #: subagent, so an event-fed list is missing exactly what this is for.
    items: Dict[str, List[dict]] = field(default_factory=dict)
    #: Where the cursor is, at each depth.  Three levels: the sidebar, the
    #: list of one panel, and one item of that list.  `j`/`k`/`enter`/`esc`
    #: mean something different at each, which is why the depth is STATE
    #: rather than three sets of keys the person has to keep apart.
    focus: int = 0
    open_panel: Optional[str] = None
    item_focus: int = 0
    open_item: bool = False
    searching: Optional[str] = None
    thinking: bool = False
    speaking: bool = False
    listening: bool = False
    archive_dir: Optional[str] = None

    def add(self, kind: str, text: str, **kw) -> None:
        """Append, or fold a consecutive repeat into a count.

        Straight from the sibling boards: a loop that emits one line over
        and over pushes everything else off the panel, and `×N` says what
        N identical rows say, in one row and better.  It cost 83 memories
        to learn that the repetition IS the signal.
        """
        prev = self.entries[-1] if self.entries else None
        if prev is not None and prev.kind == kind and prev.text == text:
            prev.repeats += 1
            return
        self.entries.append(Entry(datetime.now(), kind, text, **kw))


class NullBoard:
    """The interface.  Does nothing, so a test can ignore the display."""

    def __init__(self) -> None:
        self.state = Conversation()

    # conversation
    def note(self, line: str) -> None: ...
    def thinking(self, on: bool) -> None: ...
    def speaking(self) -> None: ...
    def listening(self, on: bool) -> None: ...
    def heard(self, seconds: float, audio: Optional[str] = None) -> None: ...
    def spoke(self, words: str, anotado: str = "",
              audio: Optional[str] = None) -> None: ...
    # facts
    def memories(self, curated: int, raw: int) -> None: ...
    def catalogue(self, total: int) -> None: ...
    def listing(self, panel: str, items: List[dict]) -> None: ...
    def move(self, delta: int) -> None: ...
    def open(self) -> None: ...
    def close(self) -> None: ...
    def searching(self, query: str) -> None: ...
    def found(self, names: List[str]) -> None: ...
    def selected(self, ids: List[str]) -> None: ...
    def document(self, path: str) -> None: ...
    def recording_to(self, path: str) -> None: ...
    # lifecycle
    def __enter__(self): return self
    def __exit__(self, *exc): return False


class LineBoard(NullBoard):
    """Today's output, unchanged.  The default when `--tui` is absent.

    Every string this prints is the string the driver printed before the
    board existed, character for character: the logs are an artifact people
    grep — this session alone greps them in four harnesses — and a display
    refactor must not rewrite them.
    """

    def note(self, line: str) -> None:
        console.log(line)

    def thinking(self, on: bool) -> None:
        # The spinner is owned by the caller in line mode: it needs the
        # async context manager, which a synchronous board cannot hold.
        ...

    def speaking(self) -> None:
        console.log("· speaking…")

    def heard(self, seconds: float, audio: Optional[str] = None) -> None:
        console.log(f"· heard {seconds:.1f}s")

    def spoke(self, words: str, anotado: str = "",
              audio: Optional[str] = None) -> None:
        console.log(f"escriba: {words}")
        if anotado:
            console.log(f"   ↳ {anotado}")

    def memories(self, curated: int, raw: int) -> None:
        console.log(f"· waking with {curated} validated memories ({raw} raw)")

    def searching(self, query: str) -> None:
        console.log(f"· searching: {query}")

    def found(self, names: List[str]) -> None:
        console.log(f"· found outside: {', '.join(names)}")

    def document(self, path: str) -> None:
        console.log(f"· wrote {path}")

    def recording_to(self, path: str) -> None:
        console.log(f"· recording to {path}")


class StateBoard(NullBoard):
    """Records what happened.  Draws nothing; `RichBoard` adds that."""

    def note(self, line: str) -> None:
        # Any event the driver reports means the turn is no longer waiting
        # on the model.  `thinking` is TRANSIENT — set when a turn starts
        # and true only until something happens — so every terminal event
        # clears it.  Cleared only on the arrival of audio, it stuck on
        # after a turn that ended any other way, and the status line read
        # "pensando…" through the closing drain and out the far side.
        self.state.thinking = False
        self.state.add("note", line)
        self._refreshed()

    def thinking(self, on: bool) -> None:
        self.state.thinking = on
        if on:
            self.state.speaking = False
        self._refreshed()

    def speaking(self) -> None:
        self.state.thinking = False
        self.state.speaking = True
        self._refreshed()

    def listening(self, on: bool) -> None:
        self.state.listening = on
        self._refreshed()

    def heard(self, seconds: float, audio: Optional[str] = None) -> None:
        self.state.thinking = False
        self.state.add("said", "", seconds=seconds, audio=audio)
        self._refreshed()

    def spoke(self, words: str, anotado: str = "",
              audio: Optional[str] = None) -> None:
        self.state.thinking = self.state.speaking = False
        self.state.add("spoke", words, audio=audio)
        if anotado:
            self.state.add("anotado", anotado)
        self._refreshed()

    def memories(self, curated: int, raw: int) -> None:
        s = self.state
        if s.curated_at_start is None:
            s.curated_at_start, s.raw_at_start = curated, raw
        s.curated, s.raw = curated, raw
        self._refreshed()

    def catalogue(self, total: int) -> None:
        self.state.catalogue = total
        self._refreshed()

    def listing(self, panel: str, items: List[dict]) -> None:
        self.state.items[panel] = items
        self._refreshed()

    def move(self, delta: int) -> None:
        """Move the cursor at whatever depth it is.

        Clamped rather than modular at every level: a cursor that leaps
        from the last entry back to the first looks like a misread
        keypress, and in a list of 43 it loses your place entirely.
        """
        s = self.state
        if s.open_item:
            return                      # the detail is one thing; nothing to move
        if s.open_panel:
            n = len(s.items.get(s.open_panel) or [])
            s.item_focus = max(0, min(max(0, n - 1), s.item_focus + delta))
        else:
            s.focus = max(0, min(len(PANELS) - 1, s.focus + delta))
        self._refreshed()

    def open(self) -> None:
        """Go one level deeper: sidebar -> list -> item."""
        s = self.state
        if s.open_item:
            return
        if s.open_panel:
            if s.items.get(s.open_panel):
                s.open_item = True
        else:
            s.open_panel = PANELS[s.focus]
            s.item_focus = 0            # a list always opens at its newest
        self._refreshed()

    def close(self) -> None:
        """Come back one level, not all the way out.

        `esc` closing everything from the detail would throw away the place
        in a 43-item list to get rid of one panel, which is never what was
        meant by it.
        """
        s = self.state
        if s.open_item:
            s.open_item = False
        elif s.open_panel:
            s.open_panel = None
        self._refreshed()

    def selected_item(self) -> Optional[dict]:
        """The item the cursor is on, or None."""
        s = self.state
        items = s.items.get(s.open_panel or "") or []
        return items[s.item_focus] if 0 <= s.item_focus < len(items) else None

    def searching(self, query: str) -> None:
        self.state.searching = query
        self._refreshed()

    def found(self, names: List[str]) -> None:
        self.state.searching = None
        self.state.found.extend(names)
        self._refreshed()

    def selected(self, ids: List[str]) -> None:
        self.state.selected.extend(ids)
        self._refreshed()

    def document(self, path: str) -> None:
        self.state.documents.append(path)
        self._refreshed()

    def recording_to(self, path: str) -> None:
        self.state.archive_dir = path
        self._refreshed()

    def _refreshed(self) -> None:
        """Called after every change.  A no-op until something draws."""

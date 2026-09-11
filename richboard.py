"""The live view: the conversation on the left, what it has learnt on the right.

The renderer, and the ONLY module that imports `rich`.  `board.Conversation`
decides what is true; this decides what it looks like, so the model can be
tested without a terminal and this file stays replaceable.

Chosen only by `--tui`.  Without it the driver keeps its plain lines,
because that output is also the artifact people grep — four harnesses in
this repo read it — and a live view would either fight a pipe or emit
nothing useful into one.

WHY THE TRANSCRIPT TAKES THE SLACK, where the cascade boards give it to the
trace.  There the diagram is the point and the trace is context; here the
conversation is what a person reads and the counters are context.  So the
sidebar gets fixed, small heights and the transcript gets everything left.

THE SIDEBAR COLLAPSES rather than shrinking.  Below `MIN_SIDEBAR_COLS` a
22-column column of five panels is unreadable, so it becomes one counter
strip under the transcript: the same facts, one row, no borders.  Panels
that cannot be read are worse than counts that can.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from board import Conversation, Entry, StateBoard

SIDEBAR_COLS = 26
#: Under this the sidebar is dropped for a counter strip.  22 columns of
#: content plus borders needs roughly a third of an 80-column terminal;
#: taking that from the transcript costs more than the panels give back.
MIN_SIDEBAR_COLS = 100

#: Two borders and a title per panel, spent before anything gets a budget.
_CHROME = 4

_KIND = {
    "said":    ("tú",      "cyan"),
    "spoke":   ("escriba", "white"),
    "anotado": ("",        "grey58"),
    "note":    ("",        "grey42"),
}


class RichBoard(StateBoard):
    """Live two-region display, driven by the same calls every board takes.

    Inherits state from `StateBoard` and adds only drawing, so what the
    panels CLAIM is decided by a class with no terminal in it.
    """

    def __init__(self, refresh_per_second: float = 8) -> None:
        super().__init__()
        self._refresh = refresh_per_second
        self._live: Optional[Live] = None
        self._console = Console()

    # ------------------------------------------------------------ lifecycle
    def __enter__(self):
        self._live = Live(self._render(), console=self._console,
                          refresh_per_second=self._refresh,
                          screen=False, transient=False)
        self._live.__enter__()
        return self

    def __exit__(self, *exc):
        if self._live is not None:
            self._live.update(self._render())
            self._live.__exit__(*exc)
            self._live = None
        return False

    def _refreshed(self) -> None:
        if self._live is not None:
            self._live.update(self._render())

    # -------------------------------------------------------------- drawing
    def _size(self) -> Tuple[int, int]:
        s = self._console.size
        return s.width, s.height

    def _render(self):
        width, height = self._size()
        wide = width >= MIN_SIDEBAR_COLS
        # MUST fit: a renderable taller than the console cannot be redrawn
        # in place with screen=False — rich rewrites the whole region every
        # refresh and it reads as flicker.
        body = max(4, height - _CHROME - (0 if wide else 2))
        transcript = Panel(self._transcript(body,
                                            width - (SIDEBAR_COLS if wide else 0) - 4),
                           title="escriba", title_align="left",
                           subtitle=self._status(), subtitle_align="right")
        if not wide:
            return Group(transcript, self._strip())
        layout = Layout()
        layout.split_row(Layout(transcript, name="main"),
                         Layout(self._sidebar(), name="side", size=SIDEBAR_COLS))
        return layout

    def _status(self) -> str:
        s = self.state
        if s.listening:  return "[cyan]⬤ grabando[/]"
        if s.thinking:   return "[yellow]⠙ pensando…[/]"
        if s.speaking:   return "[green]▶ hablando[/]"
        if s.searching:  return f"[grey58]buscando: {s.searching[:28]}[/]"
        return "[grey42]escuchando[/]"

    def _transcript(self, rows: int, width: int) -> Group:
        """The tail of the conversation, one entry per line where it fits.

        Tail rather than head: the present is what a person is reading, and
        the sidebar carries the totals a scrolled-off line would have said.
        """
        out: List[Text] = []
        for e in list(self.state.entries)[-rows:]:
            out.append(self._line(e, width))
        return Group(*out)

    #: `HH:MM:SS ` plus the eight-column speaker field.
    _PREFIX = 9 + 8

    def _line(self, e: Entry, width: int) -> Text:
        """One entry, truncated to fit EXACTLY one row.

        The suffixes are measured before the body is cut, not appended
        after: an id added to an already-full line pushes it past the panel
        and rich wraps the remainder to column 0, which reads as a second
        entry with no timestamp.  The budget is the row, and everything on
        it competes for the same width.
        """
        who, colour = _KIND.get(e.kind, ("", "white"))
        suffix = Text()
        if e.repeats > 1:
            suffix.append(f"  ×{e.repeats}", style="yellow")
        if e.audio:
            suffix.append(f"  {e.audio}", style="grey35")

        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(e.at.strftime("%H:%M:%S "), style="grey35")
        t.append(f"{who:<8}" if who else " " * 8, style=colour)

        room = max(8, width - self._PREFIX - suffix.cell_len)
        if e.kind == "said":
            # The person's words exist nowhere as text — the model is never
            # asked to transcribe its input — so the recording IS the line.
            t.append(f"▸ {e.seconds:.1f}s" if e.seconds else "▸ —", style="cyan")
        else:
            body = e.text if len(e.text) <= room else e.text[:room - 1] + "…"
            t.append(body, style=colour)
        t.append_text(suffix)
        return t

    def _sidebar(self) -> Group:
        s = self.state
        return Group(
            self._panel("memoria", [
                self._count("curadas", s.curated, s.curated_at_start),
                self._count("crudas", s.raw, s.raw_at_start)]),
            self._panel("referencias", [
                # What the catalogue HOLDS versus what this conversation
                # put to use. Two different questions, and showing the
                # session's finds alone answered neither.
                self._count("catálogo", s.catalogue, None),
                self._count("nuevas", len(s.found), 0),
                self._count("en uso", len(s.selected), 0)]),
            self._panel("documentos",
                        [Text(p[-22:], style="white") for p in list(s.documents)[-4:]]
                        or [Text("—", style="grey42")]),
        )

    def _panel(self, title: str, rows: List[Text]) -> Panel:
        return Panel(Group(*rows), title=title, title_align="left",
                     border_style="grey35")

    def _count(self, label: str, value: int, start: Optional[int]) -> Text:
        """A count, and the movement since the session opened.

        The delta is the half that carries information: `8` does not say
        whether the queue is draining or filling, and a run that stored 83
        duplicates looked exactly like a healthy one until someone counted.
        """
        t = Text(f"{label:<9}", style="grey58")
        t.append(f"{value:>4}", style="white")
        if start is not None and value != start:
            d = value - start
            t.append(f" {'▲' if d > 0 else '▼'}{abs(d)}",
                     style="yellow" if d > 0 else "green")
        return t

    def _strip(self) -> Panel:
        """The sidebar's narrow form: the same facts, one row, no borders."""
        s = self.state
        t = Table.grid(padding=(0, 2))
        t.add_row(
            Text(f"curadas {s.curated}", style="white"),
            Text(f"crudas {s.raw}", style="white"),
            Text(f"refs {s.catalogue}+{len(s.found)}/{len(s.selected)}", style="white"),
            Text(f"docs {len(s.documents)}", style="white"),
        )
        return Panel(t, border_style="grey35", padding=(0, 1))

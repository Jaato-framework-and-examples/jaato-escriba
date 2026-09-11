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

import textwrap
from typing import List, Optional, Tuple

from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from board import PANELS, Conversation, Entry, StateBoard

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
        if self.state.open_panel:
            # A modal, not an overlay: `rich` composites nothing, so the
            # popup REPLACES the view rather than floating over it. That is
            # also the honest behaviour — a list you opened deliberately is
            # what you are reading, and a half-covered transcript behind it
            # would only compete for the eye.
            return self._popup(width, height)
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

    #: `HH:MM:SS ` plus the eight-column speaker field.
    _PREFIX = 9 + 8

    def _transcript(self, rows: int, width: int) -> Group:
        """The tail of the conversation, wrapped, newest last.

        Entries are rendered from the END backwards until the budget is
        spent, because an entry can be several rows now and the tail has
        to be counted in ROWS, not in entries: taking the last N entries
        and hoping they fit is how a long reply pushes the panel past the
        console and rich starts redrawing the whole region.
        """
        out: List[Text] = []
        used = 0
        for e in reversed(self.state.entries):
            block = self._lines(e, width)
            room = rows - used
            if len(block) > room:
                # It does not fit whole.  Spend what is left on its END
                # rather than dropping it: a reply longer than the panel
                # used to disappear entirely, leaving its own annotation
                # underneath and five blank rows where the answer had
                # been.  The tail is the right half to keep — the rows
                # above it are the ones the panel is scrolling away.
                if room >= 2:
                    mark = Text(" " * self._PREFIX + "…", style="grey42")
                    out = [mark] + block[-(room - 1):] + out
                break
            out = block + out
            used += len(block)
        return Group(*out)

    def _lines(self, e: Entry, width: int) -> List[Text]:
        """One entry as one or more rows, continuations under the TEXT.

        A reply is a paragraph and truncating it to a single row threw away
        most of what was said — the panel showed an opening clause and an
        ellipsis. It wraps like `console.log` does: the stamp and the
        speaker appear once, and every continuation lines up with the first
        word rather than with the timestamp, so a wrapped answer still
        reads as one entry.

        The suffixes ride on the LAST row and are budgeted there. Measured
        into the body of a single-row entry they would be counted twice;
        appended after wrapping they would overflow the final row, which is
        the bug this replaced.
        """
        who, colour = _KIND.get(e.kind, ("", "white"))
        suffix = Text()
        if e.repeats > 1:
            suffix.append(f"  ×{e.repeats}", style="yellow")
        if e.audio:
            suffix.append(f"  {e.audio}", style="grey35")

        room = max(12, width - self._PREFIX)
        if e.kind == "said":
            # The person's words exist nowhere as text — the model is never
            # asked to transcribe its input — so the recording IS the line.
            body = [f"▸ {e.seconds:.1f}s" if e.seconds else "▸ —"]
        else:
            body = textwrap.wrap(e.text, width=room) or [""]
            # The tail must leave room for what rides on it.
            if suffix.cell_len and len(body[-1]) + suffix.cell_len > room:
                body += [""]

        out: List[Text] = []
        for i, piece in enumerate(body):
            row = Text(no_wrap=True, overflow="ellipsis")
            if i == 0:
                row.append(e.at.strftime("%H:%M:%S "), style="grey35")
                row.append(f"{who:<8}" if who else " " * 8, style=colour)
            else:
                row.append(" " * self._PREFIX)
            row.append(piece, style="cyan" if e.kind == "said" else colour)
            if i == len(body) - 1:
                row.append_text(suffix)
            out.append(row)
        return out

    #: Items previewed inside a panel before the popup is needed.
    _PREVIEW = 2

    #: The keys, said where the thing they act on is.  A binding nobody is
    #: told about is a binding nobody uses: the popup announced `esc` from
    #: the moment it opened, and the navigation that REACHES the popup
    #: announced nothing at all.
    _HINT = "[grey42]j/k mover · enter abrir[/]"

    def _sidebar(self) -> Group:
        s = self.state
        return Group(
            self._panel("memoria", [
                self._count("curadas", s.curated, s.curated_at_start),
                self._count("crudas", s.raw, s.raw_at_start)]
                + self._preview("memoria")),
            self._panel("referencias", [
                # What the catalogue HOLDS versus what this conversation
                # put to use. Two different questions, and showing the
                # session's finds alone answered neither.
                self._count("catálogo", s.catalogue, None),
                self._count("nuevas", len(s.found), 0),
                self._count("en uso", len(s.selected), 0)]
                + self._preview("referencias")),
            # Counted from the LISTING, not from the events: documents are
            # written by a subagent and by earlier runs, and the event
            # stream only ever saw this session's.
            self._panel("documentos",
                        [self._count("escritos",
                                     len(s.items.get("documentos") or s.documents),
                                     None)]
                        + self._preview("documentos")),
            Align.center(Text.from_markup(self._HINT)),
        )

    def _preview(self, panel: str) -> List[Text]:
        """The newest items, short enough to sit in the sidebar.

        Two, because the panel answers "what just happened" and the popup
        answers "what is in there".  A sidebar that tries to be the list
        is neither: 22 columns cannot hold a memory description, and the
        width it takes comes out of the transcript.
        """
        items = self.state.items.get(panel) or []
        if not items:
            return [Text("  —", style="grey42")]
        # The panel is SIDEBAR_COLS wide minus its borders and padding, and
        # the bullet costs four more.  Truncated to that and pinned
        # no-wrap: left to wrap, a description spills onto a second line
        # with no bullet and reads as a separate item — measured, "Función
        # y patrones c" followed by a lone "c".
        room = SIDEBAR_COLS - 4 - 4
        out = []
        for it in items[:self._PREVIEW]:
            txt = " ".join((it.get("text") or "").split())
            if len(txt) > room:
                txt = txt[:room - 1] + "…"
            line = Text(no_wrap=True, overflow="ellipsis")
            line.append("  · ", style="grey42")
            line.append(txt, style="grey58")
            out.append(line)
        return out

    def _panel(self, title: str, rows: List[Text]) -> Panel:
        focused = PANELS[self.state.focus] == title
        return Panel(Group(*rows),
                     title=f"[bold]{title}[/]" if focused else title,
                     title_align="left",
                     border_style="cyan" if focused else "grey35")

    def _popup(self, width: int, height: int):
        """The focused panel's list, or one item of it, centred and modal.

        `rich` composites nothing, so going deeper REPLACES rather than
        floats — and `esc` comes back one level, which is what makes a
        stack of two feel like a stack rather than a dead end.
        """
        panel = self.state.open_panel or PANELS[self.state.focus]
        items = self.state.items.get(panel) or []
        w = max(44, min(width - 8, 96))
        if self.state.open_item:
            return Align.center(self._detail(panel, w, height), vertical="middle")

        rows = max(3, height - 8)
        cur = self.state.item_focus
        # Scroll so the cursor stays inside the window, keeping it away from
        # the very edge where you cannot see what is coming next.
        top = max(0, min(cur - rows // 2, max(0, len(items) - rows)))
        body: List[Text] = []
        for i, it in enumerate(items[top:top + rows], start=top):
            here = i == cur
            line = Text(no_wrap=True, overflow="ellipsis")
            line.append("▸ " if here else "  ", style="cyan")
            line.append(f"{(it.get('at') or ''):<17}", style="grey35")
            line.append((it.get("text") or "")[:w - 26],
                        style="bold white" if here else "white")
            body.append(line)
        if not body:
            body = [Text("(vacío)", style="grey42")]
        title = f"[bold cyan]{panel}[/]  ({cur + 1}/{len(items)})" if items \
            else f"[bold cyan]{panel}[/]"
        return Align.center(
            Panel(Group(*body), title=title, title_align="left",
                  subtitle="[grey42]j/k mover · enter ver · esc volver[/]",
                  subtitle_align="right", border_style="cyan", width=w),
            vertical="middle")

    def _detail(self, panel: str, w: int, height: int) -> Panel:
        """One item, in full — the reason for drilling in.

        Every field the listing carried, and nothing invented: a detail
        view that pads with empty labels teaches you to stop reading it.
        """
        it = self.selected_item() or {}
        body: List[Text] = []

        def field(label: str, value: str) -> None:
            if not value:
                return                  # absent is absent, not "—"
            for i, piece in enumerate(textwrap.wrap(str(value), width=w - 14)
                                      or [""]):
                row = Text()
                row.append(f"{label if i == 0 else '':<11}", style="grey42")
                row.append(piece, style="white")
                body.append(row)

        field("id", it.get("id", ""))
        field("fecha", it.get("at", ""))
        field("etiquetas", ", ".join(it.get("tags") or []))
        field("url", it.get("url", ""))
        if it.get("confidence") is not None:
            field("confianza", f"{it['confidence']} · {it.get('uses', 0)} usos")
        if it.get("content"):
            body.append(Text(""))
            for piece in textwrap.wrap(it["content"], width=w - 4)[:height - 14]:
                body.append(Text(piece, style="grey70"))
        if not body:
            body = [Text("(sin datos)", style="grey42")]
        return Panel(Group(*body),
                     title=f"[bold cyan]{(it.get('text') or panel)[:w - 20]}[/]",
                     title_align="left",
                     subtitle="[grey42]esc volver a la lista[/]",
                     subtitle_align="right", border_style="cyan", width=w)

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
            Text(f"docs {len(s.items.get('documentos') or s.documents)}", style="white"),
            # The panels are gone at this width, but the keys still work and
            # still need saying.
            Text.from_markup(self._HINT),
        )
        return Panel(t, border_style="grey35", padding=(0, 1))

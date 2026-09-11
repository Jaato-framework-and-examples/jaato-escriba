"""Render EVERY state the board can be in, at several sizes.

Written after a refactor deleted `_count` and `_strip` and every test that
followed happened to open a popup — so `_sidebar` was never rendered again
and the break reached the user's terminal instead of this file. The point
is coverage of the PATHS, not of the prettiness: each of these calls a
different branch of `_render`.
"""
import io, sys
sys.path.insert(0, "/home/apanoia/Sources/Jaato-framework-and-examples/jaato-escriba")
from rich.console import Console
import richboard

ITEMS = [{"text": f"item {i}", "id": f"id{i}", "at": "2026-09-11 13:30",
          "tags": ["a", "b"], "content": "cuerpo " * 30, "url": "https://x/y",
          "confidence": 0.9, "uses": 2} for i in range(30)]

def states():
    """(label, setup) for every branch the renderer has."""
    def base(b):
        b.memories(26, 4); b.catalogue(43)
        for panel in ("memoria", "referencias", "documentos"):
            b.listing(panel, ITEMS)
        b.heard(3.0, "att_x"); b.spoke("una respuesta larga " * 12, "anotado", audio="att_y")
        b.note("· nota")
    yield "sidebar", base
    yield "sidebar+thinking", lambda b: (base(b), b.thinking(True))
    yield "sidebar+listening", lambda b: (base(b), b.listening(True))
    yield "sidebar+searching", lambda b: (base(b), b.searching("consulta"))
    yield "list", lambda b: (base(b), b.open())
    yield "list+moved", lambda b: (base(b), b.open(), [b.move(1) for _ in range(9)])
    yield "detail", lambda b: (base(b), b.open(), b.move(3), b.open())
    yield "empty board", lambda b: None
    yield "empty list", lambda b: (b.listing("memoria", []), b.open())

fail = 0
for w, h in ((118, 24), (100, 18), (84, 14), (70, 10)):
    for label, setup in states():
        b = richboard.RichBoard()
        b._console = Console(file=io.StringIO(), width=w, height=h, force_terminal=True)
        try:
            setup(b)
            out = Console(file=io.StringIO(), width=w, height=h)
            out.print(b._render())
            rows = out.file.getvalue().rstrip("\n").split("\n")
            wide = [r for r in rows if len(r) > w]
            if len(rows) > h or wide:
                print(f"  FAIL {w}x{h:<3} {label:<18} rows={len(rows)}/{h} over-wide={len(wide)}")
                fail += 1
        except Exception as exc:
            print(f"  RAISED {w}x{h:<3} {label:<18} {type(exc).__name__}: {exc}")
            fail += 1
print("every render path OK" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)

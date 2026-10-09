"""The fan-out, and the two guards on paths that come back from a browser.

This is the half of the web backend whose failures are silent and whose
consequences are not cosmetic: one person's transcript reaching another
person's browser looks, from both ends, exactly like a working page.  The
split into `hub.py` exists so this can be asserted without starting a web
server, a daemon or a session.

Nothing here opens a session.  The turn itself is `run_escriba`'s and is
covered where it lives; what is new in the web backend is the fan-out,
the board-to-event mapping and the two path guards.
"""
import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from escriba_web.hub import Hub, frame                   # noqa: E402
from escriba_web.driver import WebBoard, entry_json      # noqa: E402

fail = 0


def check(label, got, want):
    global fail
    if got != want:
        print(f"  FAIL {label:<48} got {got!r}, want {want!r}")
        fail += 1


def parse(payload: bytes):
    kind = re.search(rb"event: (.+)\n", payload).group(1).decode()
    data = json.loads(re.search(rb"data: (.+)\n", payload).group(1))
    return kind, data


def drain(q):
    out = []
    while not q.empty():
        out.append(parse(q.get_nowait()))
    return out


async def main():
    # ------------------------------------------------ one hub per person
    alice, bob = Hub(), Hub()
    qa, qb = alice.subscribe(), bob.subscribe()
    alice.publish("entry", {"id": 1, "text": "lo del balcón"})
    check("the subscriber hears their own hub", len(drain(qa)), 1)
    check("and nobody else's", drain(qb), [])

    # Two tabs of the SAME person both hear it: the person may have the
    # page open twice, and a reply that reached only one is a transcript
    # that disagrees with itself.
    q1, q2 = alice.subscribe(), alice.subscribe()
    drain(q1); drain(q2)
    alice.publish("status", {"status": "thinking"})
    check("every tab of one person is told", (len(drain(q1)), len(drain(q2))), (1, 1))

    # ---------------------------------------------- the snapshot on join
    # A delta against nothing is not interpretable, so a browser that
    # arrives mid-conversation is handed the state first.
    alice.publish("state", {"entries": [1, 2, 3]})
    late = alice.subscribe()
    kinds = [k for k, _ in drain(late)]
    check("a late joiner gets the state first", kinds, ["state"])

    # --------------------------------------------- a reader that stalls
    # A tab that stops reading must not grow a queue for the life of the
    # session; it is dropped, and `EventSource` reconnects into a fresh
    # snapshot.
    # The discriminating version: one reader keeps up and one does not.
    # Asserting only that the count fell would pass on a hub that dropped
    # everybody, which is the same outage by another route.
    fresh = Hub()
    fast, slow = fresh.subscribe(), fresh.subscribe()
    for i in range(1000):
        fresh.publish("entry", {"id": i})
        drain(fast)
    check("the reader that keeps up survives", fast in fresh._subs, True)
    check("the one that stalls is dropped", slow in fresh._subs, False)

    # ------------------------------------------- the board becomes events
    hub = Hub()
    board = WebBoard(hub)
    q = hub.subscribe()
    board.spoke("buenos días")
    seen = drain(q)
    check("a new entry is published", [k for k, _ in seen][0], "entry")
    check("with the id the page patches by", seen[0][1]["id"], 1)

    # The status is the SERVER's, derived from the board's own flags. The
    # page is forbidden from inferring it from the entries, because an
    # entry having audio says the file exists, not that it is sounding.
    drain(q)
    board.thinking(True)
    check("thinking is published", [d for k, d in drain(q) if k == "status"],
          [{"status": "thinking", "query": None}])
    board.searching("riego por goteo")
    check("searching carries the query",
          [d for k, d in drain(q) if k == "status"],
          [{"status": "searching", "query": "riego por goteo"}])

    # A folded repeat is the same row: one `entry`, with a higher count,
    # so the page updates in place instead of drawing the line twice.
    drain(q)
    board.note("juez: descartado")
    board.note("juez: descartado")
    rows = [d for k, d in drain(q) if k == "entry"]
    check("a repeat republishes one row", len({r["id"] for r in rows}), 1)
    check("with the count", rows[-1]["repeats"], 2)

    # An unchanged tick publishes nothing: the disk reconciliation runs
    # every two seconds forever, and a hub that re-sent the world each
    # time would be the terminal's 103 KB/s idle redraw all over again.
    board.listing("memoria", [{"text": "a"}])
    board.memories(7, 2)
    drain(q)                       # the first reading of anything IS news
    board.listing("memoria", [{"text": "a"}])
    board.memories(7, 2)
    check("an unchanged tick is silent", drain(q), [])
    # ...and a changed one is not, so the silence above is the comparison
    # working rather than the publisher being dead.
    board.memories(8, 2)
    check("a changed count is published", [k for k, _ in drain(q)], ["counts"])

    # ------------------------------------------------------ path guards
    from escriba_web.app import ATT
    check("a real attachment id is accepted", bool(ATT.match("att_0123456789abcdef")), True)
    for hostile in ("att_../../etc/passwd", "../../etc/passwd", "att_ZZZZ", "att_0123", ""):
        check(f"refused: {hostile!r}", bool(ATT.match(hostile)), False)

    # `/doc` resolves and then checks containment. The path comes from a
    # panel this server filled, but it arrives back over the wire.
    ws = Path(tempfile.mkdtemp(prefix="escriba-doc-"))
    (ws / "docs").mkdir()
    (ws / "docs" / "ok.md").write_text("# ok")
    (ws / "secret.md").write_text("not yours")
    docs = (ws / "docs").resolve()
    for path, allowed in (("docs/ok.md", True),
                          ("docs/../secret.md", False),
                          ("../../../etc/passwd", False),
                          ("/etc/passwd", False)):
        target = (ws / path).resolve()
        inside = docs in target.parents and target.is_file()
        check(f"doc {path!r}", inside, allowed)

    print("web backend OK" if not fail else f"{fail} failure(s)")
    return 1 if fail else 0


sys.exit(asyncio.run(main()))

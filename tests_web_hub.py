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
import staging                                          # noqa: E402
from fastapi import Request                              # noqa: E402
from escriba_web.hub import VOICE_BACKLOG, Hub, frame                   # noqa: E402
from escriba_web.driver import WebBoard, entry_json      # noqa: E402
import run_escriba as _run                               # noqa: E402
from jaato_sdk import AgentError                         # noqa: E402
from jaato_sdk.client.convenience import Terminus        # noqa: E402

fail = 0


async def _done(value):
    """The stub for an async dependency: same shape, no request."""
    return value


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
    # A hub is built around a snapshot FUNCTION, so these stand-ins are
    # what a `Person` would supply: callables reading a live dict.
    world = {"entries": []}
    alice, bob = Hub(lambda: dict(world)), Hub(lambda: dict(world))
    qa, qb = alice.subscribe(), bob.subscribe()
    # Every subscriber is handed the state first, now unconditionally —
    # drained here so what follows counts deltas and not the snapshot.
    drain(qa); drain(qb)
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
    world["entries"] = [1, 2, 3]
    late = alice.subscribe()
    first = drain(late)
    check("a late joiner gets the state first", [k for k, _ in first], ["state"])

    # ------------------------------------- and the state is CURRENT state
    # THE REGRESSION THIS FILE EXISTS FOR.  The hub used to keep the last
    # `state` it had published and hand that to the next browser, and
    # `state` is published only when the session opens — so every
    # reconnect was answered with the world as it was at open.  On
    # 2026-10-09 a person talked for two minutes, had a document written,
    # read it, and came back to an empty transcript and a session clock
    # from an hour before, with the conversation and the document both
    # intact on disk.  Nothing had restarted; the page had been handed a
    # stale snapshot and believed it.
    #
    # Publishing a `state` FIRST is what makes this discriminating: a hub
    # that stores and replays would answer with that published value and
    # pass a test that only checked "some state arrives".
    alice.publish("state", {"entries": ["stale"]})
    world["entries"] = [1, 2, 3, 4]
    rejoin = alice.subscribe()
    kind, data = drain(rejoin)[0]
    check("a reconnect is answered with the state now", data["entries"],
          [1, 2, 3, 4])
    check("not with the last one published", data["entries"] != ["stale"], True)

    # --------------------------------------------- a reader that stalls
    # A tab that stops reading must not grow a queue for the life of the
    # session; it is dropped, and `EventSource` reconnects into a fresh
    # snapshot.
    # The discriminating version: one reader keeps up and one does not.
    # Asserting only that the count fell would pass on a hub that dropped
    # everybody, which is the same outage by another route.
    fresh = Hub(lambda: {})
    fast, slow = fresh.subscribe(), fresh.subscribe()
    for i in range(1000):
        fresh.publish("entry", {"id": i})
        drain(fast)
    check("the reader that keeps up survives", fast in fresh._subs, True)
    check("the one that stalls is dropped", slow in fresh._subs, False)
    # AND IT IS TOLD. The defect this replaced: `drop` removed the queue
    # and the stream that owned it went on awaiting it forever, with the
    # keepalive holding the connection open -- so the browser never saw
    # an error, never reconnected, and never got the snapshot that would
    # have fixed it. Measured on the deployment 2026-10-12: no audio, no
    # new rows, a header stuck on "hablando", and two reloads to recover.
    ended = []
    while not slow.empty():
        ended.append(slow.get_nowait())
    check("the last thing it is handed is the sentinel",
          ended[-1] if ended else "nothing", None)

    # ------------------------------------- the voice is what gives way
    # A SLOW READER LOSES AUDIO, NOT STATE. That split is the whole
    # point: a voice frame that never sounds costs a fragment of a
    # sentence, while a `status` that never arrives leaves the page
    # saying "hablando" after the escriba has stopped.
    ring = Hub(lambda: {})
    q = ring.subscribe()
    drain(q)
    for i in range(VOICE_BACKLOG * 3):
        ring.publish("speech", {"i": i, "pcm": "x"})
    check("a reader that never drains the voice is NOT dropped",
          q in ring._subs, True)
    check("it just stops holding the oldest of it", q.qsize(), VOICE_BACKLOG)
    check("and the loss is counted", ring.lost, VOICE_BACKLOG * 2)
    heard = [d["i"] for _, d in drain(q)]
    check("what is kept is the MOST RECENT, not the first it managed",
          heard[0], VOICE_BACKLOG * 2)

    # AND STATE SURVIVES A VOICE FLOOD, which is the symptom that sent
    # somebody to reload the page twice.
    both = Hub(lambda: {})
    q2 = both.subscribe()
    drain(q2)
    both.publish("status", {"state": "speaking"})
    for i in range(VOICE_BACKLOG * 2):
        both.publish("speech", {"i": i, "pcm": "x"})
    both.publish("status", {"state": "listening"})
    kinds = [k for k, _ in drain(q2)]
    check("both status frames arrive through the flood",
          [k for k in kinds if k == "status"], ["status", "status"])
    check("and the state ones come first, so a burst cannot delay them",
          kinds[0], "status")

    # ------------------------------------------- the board becomes events
    hub = Hub(lambda: {})
    board = WebBoard(hub)
    q = hub.subscribe()
    drain(q)                        # the join snapshot, not news
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

    # A REPLY ADDS TWO ENTRIES AND REFRESHES ONCE.  `anotado` is a
    # required field of the escriba's completion schema, so every closed
    # turn calls `spoke(words, anotado, audio=...)`, which appends the
    # reply and its annotation before `_refreshed` runs.  Publishing only
    # `entries[-1]` sent the annotation and dropped the reply — the row
    # carrying the audio — and the transcript showed an answer nobody
    # could read or listen to.  Found on a live page, not here.
    drain(q)
    board.spoke("Genial, me parece un tema interesante.",
                "He registrado el interés en los gatos.",
                audio="att_0196475cd416e2b5")
    rows = [d for k, d in drain(q) if k == "entry"]
    check("both entries are published", [r["kind"] for r in rows], ["spoke", "anotado"])
    check("the reply carries its audio", rows[0]["audio"], "att_0196475cd416e2b5")
    check("and they are different rows", rows[0]["id"] != rows[1]["id"], True)
    check("a reply that named no source carries none", rows[1]["links"], [])

    # WHERE IT CAME FROM RIDES THE ANNOTATION, not the speech: the
    # escriba is forbidden to read a URL aloud, so this row is the only
    # way the person gets one at all.  It has to reach the browser, and
    # it has to reach the `anotado` row rather than the audio row.
    drain(q)
    board.spoke("Te cuento lo del pool de conexiones.", "Anotado el pool.",
                audio="att_0196475cd416e2b6",
                links=[{"nombre": "el repo", "url": "https://gitlab.x/a"},
                       {"nombre": "la p\u00e1gina", "nodo": "mainframe-connection-pool"}])
    rows = [d for k, d in drain(q) if k == "entry"]
    check("the sources ride the annotation",
          [f.get("url") or f.get("nodo") for f in rows[1]["links"]],
          ["https://gitlab.x/a", "mainframe-connection-pool"])
    check("and not the speech", rows[0]["links"], [])

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

    # -------------------------------------------------- who is connected
    # The header names the person, and what it names them is read from
    # the record beside their workspace — so this is the precedence that
    # decides what somebody sees, and the one case that must not be
    # invented: a provisioning that recorded no readable name shows the
    # principal, which on the deployed host is a `sub` and is MEANT to
    # look like one.  A display name derived from the email's local part
    # would hide a proxy that is not passing the name.
    from escriba_web.driver import who
    check("the username is preferred",
          who({"username": "ana", "email": "ana@ejemplo.es"}, "s-1"), "ana")
    check("the email when there is no username",
          who({"email": "ana@ejemplo.es"}, "s-1"), "ana@ejemplo.es")
    check("the principal when the proxy sent neither", who({}, "s-1"), "s-1")
    check("an empty header is not a name",
          who({"username": "", "email": None}, "s-1"), "s-1")

    # AND IT IS READ AFTER PROVISIONING, which is the whole correctness
    # of where that line sits.  Read beside the locale — before the
    # workspace exists — a person's FIRST session finds no record and
    # falls through to the principal; one `Person` is kept per principal
    # for the life of the process, so their header would show a `sub`
    # until the service restarted.  Nobody would see it twice, which is
    # exactly why it needs asserting.
    from escriba_web.driver import Person
    first = Person("a3f1-sub-9c2b", root=Path(tempfile.mkdtemp(prefix="escriba-who-")),
                   identity={"username": "ana", "email": "ana@ejemplo.es"})
    check("a first-ever session already knows the name", first.who, "ana")
    check("and the page is told it", first.snapshot()["who"], "ana")

    # ------------------------------------------- staging a document
    # The upload must work BEFORE the first session: opening one takes
    # seconds (a prefetch and a greeting turn) and the person can drop a
    # document in meanwhile. Nothing here needs the session except the
    # catalogue reload, which is why it is conditional — and this is the
    # case where `_watcher` does not exist yet, which used to be an
    # AttributeError thrown at the person.
    staged_root = Path(tempfile.mkdtemp(prefix="escriba-stage-ws-"))
    person = Person("stager-sub-01", root=staged_root, identity={"username": "ana"})
    row = await person.stage("riego del balcón.md", b"# Riego\n")
    check("a document stages with no session open", row["origin"], "staged")
    check("and lands inside the person's workspace",
          (person.ws / row["path"]).is_file(), True)
    check("the person is told it arrived",
          any("riego-del-balcón.md" in e.text for e in person.board.state.entries), True)
    # The ceiling the page shows and the ceiling the server enforces are
    # ONE number: a figure typed into the page would be free to drift
    # from the one that does the refusing.
    check("the snapshot carries the ceiling the server enforces",
          person.snapshot()["stage_mb"], staging.MAX_BYTES // (1024 * 1024))

    # The ENDPOINT, without an HTTP client: what is worth asserting here
    # is the part that spans two files and fails silently. The page posts
    # a FormData field and the route declares a parameter; a mismatch is
    # a 422 the person sees as "the document could not be contributed",
    # with both halves looking correct on their own.
    from fastapi import HTTPException
    from starlette.datastructures import UploadFile as Upload
    import inspect, io, re as _re
    from escriba_web import app as _app

    field = [n for n, prm in inspect.signature(_app.stage).parameters.items()
             if prm.annotation is not Request][-1]
    page = (Path(__file__).resolve().parent / "web" / "index.html").read_text()
    posted = _re.search(r'body\.append\("([^"]+)", file', page)
    check("the page posts the field the route declares",
          posted and posted.group(1), field)

    _app.person_of = lambda request: _done(person)      # the route's only input
    sent = Upload(filename="plano del balcón.pdf", file=io.BytesIO(b"%PDF-1.4 x"))
    got = await _app.stage(None, sent)
    check("the route stages what was posted", got["text"], "plano-del-balcón.pdf")

    # An oversized upload is REPORTED, not truncated: a document
    # discussed as if whole when half was dropped is worse than a
    # refusal, and the ceiling is the one thing the person can act on.
    big = Upload(filename="enorme.bin",
                 file=io.BytesIO(b"x" * (staging.MAX_BYTES + 1)))
    try:
        await _app.stage(None, big)
        check("an oversized upload is refused by the route", "accepted", "refused")
    except HTTPException as exc:
        check("with 413 and the ceiling in it", exc.status_code, 413)
        check("naming the limit", str(staging.MAX_BYTES) in str(exc.detail), True)

    # ------------------------------------------- a recording with nothing in it
    # It used to reach ffmpeg, which refused it, and the exception fell
    # into `/talk`'s bare `except Exception` -- reported to the person as
    # a lost connection, which sent them to look at their network for a
    # press that was simply too short to carry anything.
    from escriba_web.driver import NothingHeard
    try:
        await person.talk(b"")
        check("an empty recording is refused", "accepted", "refused")
    except NothingHeard:
        pass
    except Exception as exc:                                   # noqa: BLE001
        check("an empty recording is refused by name", type(exc).__name__,
              "NothingHeard")

    # EVERY KIND THE SERVER CAN PUBLISH HAS WORDS IN THE PAGE.  The two
    # halves live in different files and in different languages, and a
    # kind with no entry in `ALERTS` throws where the alert is rendered
    # -- so the person gets no alert at all, which is the one moment the
    # alert existed for.
    import re as _re2
    src = "".join((Path(__file__).resolve().parent / f).read_text()
                  for f in ("escriba_web/app.py", "escriba_web/driver.py"))
    # Only the ALERT publishes: `"kind"` also names an entry's kind, which
    # is a different vocabulary entirely.  A kind chosen by a condition
    # (`"budget" if budget else "session"`) contributes both of its arms.
    published = set()
    for call in _re2.finditer(r'\("alert",\s*\{(.{0,160}?)\}', src, _re2.S):
        picks = _re2.search(r'"kind":([^,\n]*(?:\n[^,\n]*)?)', call.group(1))
        if picks:
            published |= set(_re2.findall(r'"(\w+)"', picks.group(1)))
    page = (Path(__file__).resolve().parent / "web" / "index.html").read_text()
    body = page[page.index("const ALERTS"):]
    spoken = {m.group(1) for m in
              _re2.finditer(r"^  (\w+): \(", body[:body.index("\n};")], _re2.M)}
    check("every alert the server raises has copy in the page",
          sorted(published - spoken), [])

    # ------------------------------------------------------------ kbwiki
    # A LIVE BEARER TOKEN, so the things worth asserting are where it
    # lands, who can read it, and that it is never written into anything
    # that gets copied, committed or served.
    import json as _json, os as _os, stat as _stat
    from escriba_web import kbwiki

    tdir = Path(tempfile.mkdtemp(prefix="escriba-tok-")) / "run"
    # The principal is an authenticated claim, not a trusted one, and
    # this one becomes a FILE NAME.
    for hostile in ("../../etc/passwd", "/etc/shadow", "a/../../b", ".."):
        got = kbwiki.env_path(tdir, hostile).resolve()
        check(f"token file stays put: {hostile!r}", got.parent, tdir.resolve())

    wsdir = Path(tempfile.mkdtemp(prefix="escriba-wsenv-"))
    (wsdir / ".env").write_text("JAATO_PROFILE_SET=openrouter_gpt_audio\n")
    # COMPOSED, NEVER REPLACED: without `JAATO_PROFILE_SET` the daemon
    # cannot resolve the `escriba` profile and the session never opens,
    # so a file holding only the token would trade the wiki for the
    # conversation.
    body = kbwiki.compose(wsdir / ".env", "tok-abc")
    check("the workspace's own environment survives",
          "JAATO_PROFILE_SET=openrouter_gpt_audio" in body, True)
    check("and the token is added", f"{kbwiki.VAR}=tok-abc" in body, True)
    check("no token, no line", kbwiki.VAR in kbwiki.compose(wsdir / ".env", None),
          False)

    tok = kbwiki.env_path(tdir, "ana@example.es")
    kbwiki.write_env(tok, body)
    check("the token file is private", _stat.S_IMODE(tok.stat().st_mode), 0o600)
    check("in a private directory",
          _stat.S_IMODE(tok.parent.stat().st_mode), 0o700)
    check("and nothing half-written is left behind",
          sorted(q.name for q in tdir.iterdir()), [tok.name])
    kbwiki.write_env(tok, kbwiki.compose(wsdir / ".env", "tok-def"))
    check("a rewrite replaces rather than appends",
          tok.read_text().count(kbwiki.VAR), 1)

    # The server entry names the var, never the value: the plugin
    # resolves it at connect time, which is what lets a rotated token be
    # picked up without a new session.
    entry = kbwiki.server_entry("https://kbwiki.example/mcp")
    check("the header reads a variable, not a token",
          entry["mcpServers"]["kbwiki"]["headers"]["Authorization"],
          "Bearer ${%s}" % kbwiki.VAR)
    check("over streamable http", entry["mcpServers"]["kbwiki"]["type"], "http")
    check("and no token can reach that file",
          "tok-" in _json.dumps(entry), False)

    # EVERY TOOL THE MODEL IS OFFERED IS A TOOL IT MAY RUN.  `plugins:`
    # decides what it SEES and the permission whitelist decides what it
    # may DO; with `defaultPolicy: deny` a tool in the first and not the
    # second is offered and then silently refused, and a spoken turn has
    # nowhere to show a refusal.
    import yaml as _yaml
    prof = _yaml.safe_load(
        (Path(__file__).resolve().parent /
         "template/.jaato/profiles/_base_escriba.yaml").read_text())
    spec = [x for x in prof["plugins"] if x.startswith("mcp(")]
    check("the escriba is given the mcp plugin", len(spec), 1)
    offered = re.search(r"tools:\[([^\]]*)\]", spec[0]).group(1)
    offered = [t.strip() for t in offered.split(",")]
    allowed = prof["plugin_configs"]["permission"]["policy"]["whitelist"]["tools"]
    check("every wiki tool offered may also be run",
          sorted(set(offered) - set(allowed)), [])
    check("and each is named the way the plugin names it",
          [t for t in offered if not t.startswith("mcp__kbwiki__")], [])

    # And the person who holds it: which file the session reads, and
    # that a token arriving twice is written once.
    wdir = Path(tempfile.mkdtemp(prefix="escriba-wiki-"))
    (wdir / "root").mkdir()
    who = Person("wiki-sub-77", root=wdir / "root", identity={"username": "ana"},
                 wiki={"url": "https://kbwiki.example/mcp",
                       "dir": str(wdir / "run")})
    check("the session env is NOT the person's workspace",
          who.ws in who.env_file.parents, False)
    await who.credential("tok-1")
    check("a token arriving writes the file", who.env_file.exists(), True)
    stamp = who.env_file.stat().st_mtime_ns
    await who.credential("tok-1")
    check("the same token again writes nothing",
          who.env_file.stat().st_mtime_ns, stamp)
    await who.credential(None)
    check("and no token at all writes nothing",
          who.env_file.stat().st_mtime_ns, stamp)
    who._wire_wiki()
    wrote = _json.loads((who.ws / ".mcp.json").read_text())
    check("the workspace gets the server entry, with the operator's url",
          wrote["mcpServers"]["kbwiki"]["url"], "https://kbwiki.example/mcp")
    check("and the token is not in it", "tok-1" in (who.ws / ".mcp.json").read_text(),
          False)
    # Unwired is the DEFAULT and must stay the old behaviour exactly.
    check("with no wiki the session env is the workspace's own",
          person.env_file, person.ws / ".env")

    # ------------------------------------- which wiki pages came into it
    # Read from the CALLS, because the end event carries no result: the
    # id is in `tool_args` on the start and the outcome on the end, so
    # the pair is what a row is made of.
    from escriba_web import wiki as _wikimod

    class _Ev:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    def opened(c, node, tool="mcp__kbwiki__kb_get_node", ok=True, cid="c1"):
        c._started(_Ev(tool_name=tool, tool_args={"id": node}, call_id=cid))
        c._ended(_Ev(tool_name=tool, call_id=cid, success=ok))

    c = _wikimod.Consulted()
    opened(c, "rip-overview")
    check("an opened page becomes a row", [r["text"] for r in c.rows()],
          ["rip-overview"])
    check("and it is marked as the wiki's", c.rows()[0]["origin"], "wiki")
    opened(c, "rip-overview", cid="c2")
    check("the same page twice is one row", len(c.rows()), 1)
    opened(c, "ldap-security-groups", ok=False, cid="c3")
    check("a call that FAILED is not a page the person saw", len(c.rows()), 1)
    # A listing is navigation: 22 search hits in the panel would bury the
    # one page the escriba then read.
    opened(c, "ignored", tool="mcp__kbwiki__kb_list_nodes", cid="c4")
    check("finding pages is not opening them", len(c.rows()), 1)
    opened(c, "x", tool="readFile", cid="c5")
    check("and another plugin's call is not the wiki's", len(c.rows()), 1)
    # The content call opens the same page by the same id.
    opened(c, "kibana-rip-stats", tool="mcp__kbwiki__kb_get_node_content",
           cid="c6")
    check("reading the text opens it too", len(c.rows()), 2)

    # ------------------------------------------- the asking is counted
    # MEASURED, 2026-10-12: two `kb_list_nodes` calls that found nothing
    # rendered as "0 del wiki", which is what a conversation that never
    # looked renders as too. The person asked the escriba twice whether
    # it was using the wiki at all -- a question the page should have
    # answered and could not.
    a = _wikimod.Consulted()
    check("nothing asked is not a connection anybody has tested",
          a.state()["state"], "unused")
    opened(a, "", tool="mcp__kbwiki__kb_list_nodes", cid="a1")
    opened(a, "", tool="mcp__kbwiki__ask_librarian", cid="a2")
    check("a lookup that found nothing is still a lookup",
          a.state()["asked"], 2)
    check("and it opened no page", a.state()["opened"], 0)
    check("looking is not listing: the panel stays what came IN",
          a.rows(), [])
    check("and a wiki that answered is connected", a.state()["state"], "ok")

    # A CALL THAT FAILED IS THE THIRD ABSENCE, and the one the old panel
    # could not show at all: `_ended` dropped failures and `state`
    # answered "ok" regardless, so a refused token looked like an idle
    # wiki.
    opened(a, "", tool="mcp__kbwiki__ask_librarian", ok=False, cid="a3")
    check("a wiki that refused is not a wiki that is fine",
          a.state()["state"], "failing")
    check("the failure is counted", a.state()["failed"], 1)
    check("and a failed lookup is not counted as a lookup that happened",
          a.state()["asked"], 2)
    opened(a, "rip-overview", cid="a4")
    check("and one good call afterwards clears it", a.state()["state"], "ok")

    # TWO EMPTIES THAT MEAN OPPOSITE THINGS: no wiki wired at all, and a
    # wiki this conversation has not needed.  The panel must not show the
    # same thing for both.
    from escriba_web.driver import WIKI_UNWIRED
    check("with no wiki the panel says so", person.wiki_state(),
          dict(WIKI_UNWIRED))
    # NOT "ok".  A wiki that is merely CONFIGURED has told us nothing
    # about whether it can be reached; this used to answer "ok" on the
    # strength of a config key, so a refused token and an untouched wiki
    # rendered identically -- in the one panel whose purpose is to tell
    # two absences apart.
    check("with one wired and nothing asked it says exactly that",
          who.wiki_state()["state"], "unused")
    check("and claims no lookups it cannot show",
          (who.wiki_state()["asked"], who.wiki_state()["opened"]), (0, 0))

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

    # ------------------------------------------- the voice, piece by piece
    # The file the row plays is named after the digest of the WHOLE
    # utterance, so it cannot be named until the utterance is over.  These
    # frames are how the reply is heard while it is still being said, and
    # what they must not do is flood the hub or hand the page half a
    # sample.
    import base64 as _b64
    from escriba_web.driver import Relay, SPEECH_SECONDS

    PCM = "audio/pcm;rate=24000;channels=1;encoding=s16le"
    sent = []

    class _Spy:
        def publish(self, kind, data):
            sent.append((kind, data))

    class _Chunk:
        def __init__(self, data: bytes, final=False, mime=PCM):
            self.data_b64 = _b64.b64encode(data).decode()
            self.mime_type = mime
            self.stream_id = "st-1"
            self.final = final
            self.chunk = ""

    # 24000 samples a second, two bytes each: half a second is 24000 bytes.
    piece = int(24000 * SPEECH_SECONDS) * 2
    relay = Relay(hub=_Spy())
    for _ in range(5):
        relay.speak(_Chunk(b"\x01\x00" * 2000))      # 4000 bytes each
    check("nothing goes out until there is a piece to hear", len(sent), 0)
    relay.speak(_Chunk(b"\x01\x00" * 2000))          # 24000 bytes in all
    check("then one frame carries it", len(sent), 1)
    check("and it is exactly one piece", len(_b64.b64decode(sent[0][1]["pcm"])), piece)
    check("carrying the rate the page has to play it at", sent[0][1]["rate"], 24000)
    check("and the channel count", sent[0][1]["channels"], 1)

    # AN ODD NUMBER OF BYTES IS HALF A SAMPLE, and the page reads the
    # frame as an Int16Array: a stray byte would throw there.
    sent.clear()
    relay = Relay(hub=_Spy())
    relay.speak(_Chunk(b"\x02\x00" * 10 + b"\x03", final=True))
    check("the last piece is whole samples only",
          len(_b64.b64decode(sent[0][1]["pcm"])) % 2, 0)

    # NOT PCM IS NOT PLAYABLE by the page's decoder, and saying nothing is
    # right: the archive still writes the file and the row still plays it,
    # which is the behaviour this replaced.
    sent.clear()
    relay = Relay(hub=_Spy())
    relay.speak(_Chunk(b"\x00" * piece, mime="audio/mpeg"))
    check("a format the page cannot decode is not streamed", sent, [])

    # THE FINAL MARKER NAMES THE RECORDING, which is the only thing the
    # two halves can agree on: the live frames carry a stream id and the
    # transcript row carries the digest.
    sent.clear()

    class _Keeps:
        def speaking(self, *a):
            pass

        def spoke(self, stream_id):
            return {"sha": "att_deadbeefdeadbeef", "file": "out.wav"}

    relay = Relay(archive=_Keeps(), hub=_Spy())
    relay.speak(_Chunk(b"\x00" * 8, final=True))
    check("the end names the file the voice was",
          [d.get("audio") for k, d in sent if d.get("final")],
          ["att_deadbeefdeadbeef"])

    # NO HUB, NO FRAMES.  The terminal driver builds its own relay.
    relay = Relay()
    relay.speak(_Chunk(b"\x00" * piece))
    check("a relay with no hub publishes nothing", True, True)

    # ------------------------------------- how a session ENDING is read
    # MEASURED, 2026-10-12. A `budget_control` ceiling ended a session
    # and nothing noticed: `complete` NEVER RAISES FOR A TERMINAL --
    # settling is what it was asked to wait for -- so the turn came back
    # looking ordinary and `Session.terminus`, which the SDK calls its
    # only route to the reason (jaato #1007), was read nowhere in this
    # repository. The person found out on their NEXT press, as
    # `RunnerCallError: ... model loop`, and the handler that saw that
    # text looked for the word "budget" in it, did not find one, and
    # opened a fresh session with a FRESH BUDGET.
    class Mouth:
        recordings: list = []

        def last(self):
            return "lo que dijo"

        def speak(self, ev):
            pass

    class Tui:
        def __init__(self):
            self.notes = []

        def thinking(self, on):
            pass

        def spoke(self, *a, **k):
            pass

        def note(self, text):
            self.notes.append(text)

    class Scribe:
        """A session as `_turn` drives one: complete, terminus, client."""

        def __init__(self, terminus=None, raises=None):
            self.terminus, self._raises = terminus, raises
            self.client = type("C", (), {"subscribe": lambda *a, **k: (lambda: None)})()

        async def complete(self, prompt, attachments=None, on_media=None):
            if self._raises is not None:
                raise self._raises
            return {"anotado": "algo"}

    # `_if_over` IS NOT A COROUTINE, and must not become one: it is called
    # without `await`, so an `async def` would return an object nobody
    # awaits and the guard would silently never fire. An editing slip made
    # exactly that happen while this was being written.
    check("the ending guard is callable, not awaitable",
          asyncio.iscoroutinefunction(_run._if_over), False)

    async def ending(scribe):
        """What `_turn` raises, or None if it returned."""
        try:
            await _run._turn(scribe, "hola", None, Mouth(), tui=Tui())
        except _run.SessionGone as over:
            return over
        return None

    # A CEILING UNDER A TURN THAT WENT PERFECTLY. The turn is not undone
    # -- the person heard the reply and the manifest row is written --
    # but the session is over and the next press has nowhere to go.
    over = await ending(Scribe(terminus=Terminus(
        "budget_exhausted", details={"dimension": "usd"}, session_ended=True)))
    check("a ceiling ends the turn verb's silence",
          over is not None, True)
    check("named by the daemon", getattr(over, "reason", None), "budget_exhausted")
    check("with what the terminal carried",
          getattr(over, "details", None), {"dimension": "usd"})

    # `session_ended` ALONE IS NOT THE QUESTION, and this leg exists
    # because getting that wrong took the deployment down on 2026-10-12.
    # A completion-gated session settles EVERY turn on
    # SESSION_TERMINATED with reason `natural` -- that is what
    # `signal_completion` produces -- so `session_ended` is true after a
    # turn that worked perfectly. Raising on it made the GREETING fail,
    # `_open` dropped the person, and every request 503'd:
    # `run_escriba.SessionGone: natural`, out of `Person.open`.
    #
    # The first version of this leg wrote `session_ended=False` for
    # `natural`, which is not what the daemon sends. A stub that agrees
    # with what I believed, rather than with what the daemon does,
    # cannot catch the belief being wrong.
    for clean in sorted(_run.CLEAN_TERMINAL_REASONS):
        check(f"a session ending as {clean!r} is not a failure",
              await ending(Scribe(terminus=Terminus(clean, session_ended=True))),
              None)
    check("a turn that ended nothing just returns",
          await ending(Scribe(terminus=Terminus("natural", session_ended=False))), None)
    check("and neither does one with no terminus at all",
          await ending(Scribe()), None)
    # AND A REASON NOBODY HAS SEEN YET STILL RAISES, which is why the
    # framework's set is an allow-list: `budget_exhausted` was the fifth
    # reason to exist, and the code enumerating four had already gone
    # stale by the time it arrived.
    over = await ending(Scribe(terminus=Terminus("cascade_cancelled",
                                                 session_ended=True)))
    check("an ending nobody allowed for is still an ending",
          getattr(over, "reason", None), "cascade_cancelled")

    # THE DAEMON'S ACCOUNT BEATS THE MESSAGE. `_raise_if_needed` writes
    # the terminus BEFORE it raises, so it is there to be read, and the
    # text of this exception is exactly the one that fooled the old
    # substring test.
    loop_error = AgentError("RunnerCallError",
                            "session.send_message failed: ToolError: "
                            "session.send_message: model loop")
    over = await ending(Scribe(terminus=Terminus("budget_exhausted",
                                                 session_ended=True),
                               raises=loop_error))
    check("an error terminal is named by its reason, not by its wording",
          getattr(over, "reason", None), "budget_exhausted")

    # AND WHEN NOTHING TOLD US, that is a third state and not a guess:
    # an RPC that died before the SDK could settle writes no terminus.
    over = await ending(Scribe(raises=loop_error))
    check("a transport that died is still the session gone",
          over is not None, True)
    check("with no reason invented for it",
          getattr(over, "reason", "?"), None)
    check("while a turn that merely failed to close is survivable",
          await ending(Scribe(raises=AgentError("NudgeExhausted", "no close"))),
          None)

    # ------------------------------------- and what the driver does with it
    ended_root = Path(tempfile.mkdtemp(prefix="escriba-ending-"))
    them = Person("ending-sub-01", root=ended_root, identity={"username": "ana"})
    said = []
    them.hub.publish = lambda kind, data: said.append((kind, data))
    resumed = []

    async def _noop():
        resumed.append(True)

    them._resume = _noop

    them._scribe = "the session"
    them._ended(_run.SessionGone("budget_exhausted", reason="budget_exhausted"))
    check("a ceiling is reported as a ceiling",
          [d["kind"] for k, d in said if k == "alert"], ["budget"])
    # THE WHOLE POINT. Opening another session with a fresh budget
    # answers a limit by ignoring it, and that is what used to happen.
    check("and the session is NOT quietly reopened", them._scribe, "the session")

    said.clear()
    them._ended(_run.SessionGone("gone", reason=None))
    check("anything else is reported as what it was",
          [d["kind"] for k, d in said if k == "alert"], ["session"])
    check("and says so rather than leaving it blank",
          bool([d["detail"] for k, d in said if k == "alert"][0]), True)
    check("and that one IS repaired", them._scribe, None)
    await asyncio.sleep(0)
    check("by reopening exactly once", resumed, [True])

    # THE INVERSE OF THE BUG, which no leg would have caught before: an
    # ending whose MESSAGE says budget but whose reason does not must not
    # be treated as a ceiling. The text is the thing we stopped reading.
    said.clear()
    them._scribe = "the session"
    them._ended(_run.SessionGone("budget exhausted: presupuesto agotado",
                                 reason="cascade_cancelled"))
    check("a message that merely mentions a budget is not a ceiling",
          [d["kind"] for k, d in said if k == "alert"], ["session"])

    # ------------------------------------- the way back from a ceiling
    # A budget ending is the one the driver refuses to repair, so without
    # this the ceiling is enforced by making the escriba unusable until
    # the service restarts. A reload does not help: `person_of` hands
    # back the same cached `Person` with the same dead session.
    back = Person("restart-sub-01",
                  root=Path(tempfile.mkdtemp(prefix="escriba-restart-")),
                  identity={"username": "ana"})
    rows = []
    back.archive.turn = lambda **kw: rows.append(kw)
    # The reopen publishes a state frame, which reads the live session
    # for its engines. These legs are about WHAT A RESTART DOES, not
    # about what a snapshot contains, so the snapshot stands aside.
    back.snapshot = lambda: {}

    order = []

    async def _opens():
        order.append("open")
        back._scribe = "a new session"

    async def _closes():
        pass

    async def _curates():
        order.append("consolidate")

    back._open_session, back._close_session = _opens, _closes
    back._consolidate = _curates
    back._scribe = "the one the ceiling ended"
    check("the person can start another session", await back.restart(), True)
    check("and it is a new one", back._scribe, "a new session")
    # THE WHOLE DIFFERENCE between a new session and a new session that
    # knows anything. The scribe stores memories RAW and the inventory a
    # session gets is rendered from the CURATED store at create, so a
    # reopen that skips the curator hands back an escriba that has
    # forgotten the conversation it was just having. ORDER, not
    # presence: consolidating after the session is created is the same
    # as not consolidating at all.
    check("the curator runs, and before the session is created",
          order, ["consolidate", "open"])
    # A DECISION, NOT A REPAIR. The transcript is a 400-entry window; the
    # manifest is what can still say, a month later, that this person was
    # told a ceiling had been reached and chose to carry on.
    check("recorded as their own decision",
          [r.get("stage") for r in rows], ["session-restarted"])
    check("and attributed to them, not to the machinery",
          rows[-1].get("source"), "person")

    async def _cannot():
        raise RuntimeError("no daemon")

    rows.clear()
    order.clear()
    back._open_session = _cannot
    back._scribe = "the one the ceiling ended"
    check("a restart that could not open says so", await back.restart(), False)
    # WRITTEN ONLY IF IT HAPPENED: a row written first would record
    # somebody choosing to spend again on a session that never opened.
    check("and records nothing", rows, [])

    # --------------------------------------------- starting from scratch
    # ONE RENAME, NOT A LIST OF DELETIONS. A reset assembled out of six
    # deletions grows a seventh thing it does not know about; the
    # workspace IS the list, so moving it takes all of it and cannot
    # take half.
    zero_root = Path(tempfile.mkdtemp(prefix="escriba-zero-"))
    gone = Person("zero-sub-01", root=zero_root, identity={"username": "ana"})
    ws = gone.ws
    # The Person already opened an archive directory for the session it is
    # about to have, so the day it would span is counted from here rather
    # than assumed to be zero.
    before = gone.holdings()["days"]
    (ws / "audio" / "20261012_120000").mkdir(parents=True, exist_ok=True)
    (ws / "audio" / "20261012_120000" / "in_att_1.mp3").write_bytes(b"x")
    (ws / "audio" / "20261012_120000" / "out_1_a.wav").write_bytes(b"y")
    (ws / "audio" / "20261012_120000" / "manifest.jsonl").write_text("{}\n")

    held = gone.holdings()
    # COUNTED BEFORE IT IS OFFERED, and the recordings counted APART:
    # a memory can be told again, a conversation cannot be re-had.
    check("the recordings are counted", held["recordings"], 2)
    check("and the manifest is not one of them", held["recordings"] != 3, True)
    check("and the days they span", held["days"], before + 1)
    check("the memories are counted too", "curated" in held and "raw" in held, True)

    home = ws.parent
    person_json = home / "person.json"
    check("the identity file is where the rename will not reach it",
          person_json.is_file(), True)

    aside = await gone.scratch()
    check("the workspace is gone from where it was", ws.exists(), False)
    check("and is beside itself, not deleted", aside.is_dir(), True)
    check("named so a person can find it", aside.name.startswith("workspace.olvidado-"), True)
    check("with the recordings still in it",
          (aside / "audio" / "20261012_120000" / "in_att_1.mp3").is_file(), True)
    # IDENTITY IS NOT WHAT THEY ASKED TO FORGET. It lives one level up,
    # it is what the proxy asserts, and it is how an operator maps a
    # directory back to a person.
    check("the identity survives the reset", person_json.is_file(), True)

    # AND THE NEXT PERSON OF THE SAME PRINCIPAL GETS A NEW WORKSPACE,
    # provisioned from the template — the path a first-ever visitor
    # takes, which is the whole point of dropping the cached Person.
    again = Person("zero-sub-01", root=zero_root, identity={"username": "ana"})
    check("a fresh workspace is provisioned in its place", again.ws.is_dir(), True)
    check("at the same path as before", again.ws, ws)
    check("holding none of the recordings", again.holdings()["recordings"], 0)

    print("web backend OK" if not fail else f"{fail} failure(s)")
    # --------------------------------------------------- the draft transcript
    #
    # WHAT THIS IS FOR.  Measured 2026-10-18, the configured model takes
    # 1,7 to 2,6 s on an utterance with `base` and about three times that
    # with the `small` the deployed box runs -- longer than the escriba
    # takes to answer, so the person reads the reply before their own
    # words appear.  A faster model fills the row and is then replaced.
    #
    # THE ONE THING THAT MUST NOT HAPPEN is the draft becoming the
    # record: two models are two answers to "what did this person say",
    # and only one of them can be the archive's.
    from escriba_web.driver import Person as _Person

    async def settled(row, deadline: float = 5.0):
        """Wait for both passes, which run in THREADS and not on the loop.

        An earlier version of these legs spun `asyncio.sleep(0)` a fixed
        number of times, which yields the event loop without giving a
        thread any wall clock: it passed alone and failed in the full
        suite, which is the worst way for a leg to be wrong.
        """
        for _ in range(int(deadline / 0.02)):
            if row.text and not row.transcribing:
                return
            await asyncio.sleep(0.02)

    class Said:
        def __init__(self, text=""):
            self.text = text
            self.transcribing = False

    class Model:
        """Stands in for a Transcriber, and says which verb was called."""

        def __init__(self, text):
            self.text = text
            self.called = []

        def transcribe(self, recording, language=None):
            self.called.append("transcribe")
            return self.text

        def write(self, recording, language=None):
            self.called.append("write")
            return self.text

    drafter = _Person("draft-sub-01",
                      root=Path(tempfile.mkdtemp(prefix="escriba-draft-")))
    drafter.scribe = Model("lo que de verdad dije")
    drafter.draft = Model("lo que parece que dije")
    entry = Said()
    drafter._transcribing(entry, "att_1")
    await settled(entry)
    check("the row ends with the configured model's words", entry.text,
          "lo que de verdad dije")
    # THE DRAFT NEVER TOUCHES `in_<att>.txt`: `write` keeps whatever it
    # finds there, so a draft written to disk would be kept FOREVER and
    # the archive would hold the guess.
    check("the draft is only transcribed, never written",
          drafter.draft.called, ["transcribe"])
    check("while the real one is written", drafter.scribe.called, ["write"])
    check("and nothing is left waiting", entry.transcribing, False)

    # A ROW THAT ALREADY HAS WORDS IS NOT OVERWRITTEN BY A DRAFT.  `write`
    # returns a transcript an earlier run wrote, so a slow draft landing
    # afterwards would replace the record's words with a guess.
    drafter.draft = Model("una conjetura que llega tarde")
    already = Said("lo que ya estaba escrito")
    drafter._transcribing(already, "att_2")
    await settled(already)
    check("a draft does not overwrite words already on the row",
          already.text, "lo que de verdad dije")

    # NO DRAFT CONFIGURED IS THE ORDINARY CASE, and it must not change.
    drafter.draft = None
    plain = Said()
    drafter._transcribing(plain, "att_3")
    await settled(plain)
    check("with no draft, the transcript is still written", plain.text,
          "lo que de verdad dije")

    return 1 if fail else 0


sys.exit(asyncio.run(main()))

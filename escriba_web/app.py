"""The only thing the browser talks to, and the only SDK client.

THE PROXY AUTHENTICATES, THIS DOES NOT.  Caddy sends the request through
oauth2-proxy to Keycloak and hands on `X-Forwarded-User`; nothing here
checks a password, issues a cookie or stores a credential.  The header is
therefore load-bearing: without it there is no principal, and without a
principal there is no workspace — so a request that arrives without one
is REFUSED rather than served a default.  A default would quietly put
everybody in one workspace, which is the one failure this whole design
exists to prevent, and it would look like it was working.

For a run with no proxy in front, `--dev-principal` names the person
explicitly on the command line.  That is a flag somebody has to type, not
a fallback the code reaches for on its own.

ONE PERSON, ONE EVERYTHING.  `Person` holds the workspace, the session,
the archive and the hub; this module is routing and nothing else.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse  # noqa: E402

import i18n                                                      # noqa: E402
import archive as _archive                                         # noqa: E402
import housekeeping as _housekeeping                               # noqa: E402
import transcribe as _transcribe                                   # noqa: E402
import demo as _demo                                              # noqa: E402
import demo_live as _live                                         # noqa: E402
import sequence as _sequence                                      # noqa: E402
import staging as _staging                                        # noqa: E402
import workspace as _workspace                                     # noqa: E402

from .driver import FloorTaken, NothingHeard, NotReady, Person     # noqa: E402
from .guests import ROOM, Invitation, label, token  # noqa: E402
from .hub import frame                                             # noqa: E402
from . import realm as _realm                                       # noqa: E402

#: WHY THIS EXISTS.  Both `except` branches below answer the browser with
#: an alert and used to swallow the exception whole, so a failure in this
#: process left no trace anywhere: uvicorn logged a 200, the daemon logged
#: nothing because nothing reached it, and the page showed copy that named
#: the wrong cause.  Diagnosing one cost a round trip to the VPS journal
#: that could not answer either.  The person still gets the alert; the
#: operator now gets the traceback.
log = logging.getLogger("escriba.web")

HERE = Path(__file__).resolve().parent
PAGE = HERE.parent / "web" / "index.html"

#: An attachment id as the framework mints it: `att_` and sixteen hex.
#: Matched rather than sanitised — a filename is built from this, and the
#: only safe treatment of a path fragment from a browser is to refuse
#: anything that is not exactly the shape it should be.
ATT = re.compile(r"^att_[0-9a-f]{16}$")


async def _pcm(request: Request) -> Tuple[bytes, int, int]:
    """One piece of live microphone from the page: the body and its shape.

    SHARED BY THE TWO TAPS, which send the same thing to different ends
    -- the room hears one and the escriba hears the other -- and the
    shape a browser may send is a property of the browser, not of who
    is listening.  One check, so the two cannot drift into disagreeing
    about what a sample is.
    """
    try:
        rate = int(request.query_params.get("rate", ""))
        channels = int(request.query_params.get("channels", ""))
    except ValueError as exc:
        raise HTTPException(400, "rate and channels are integers") from exc
    if not (8000 <= rate <= 192000) or channels not in (1, 2):
        raise HTTPException(400, "not an audio shape this reads")
    pcm = await request.body()
    # WHOLE SAMPLES ONLY.  The page reads the payload back as one
    # `Int16Array` when it plays it, so a trailing half sample would
    # shift every value after it by a byte; and framed into a WAV for
    # the model it would do the same to the encoder.
    if len(pcm) % (channels * _live.WIDTH):
        raise HTTPException(400, "a partial sample")
    return pcm, rate, channels

# No Swagger, no ReDoc, no schema.  This serves one page to one person;
# an API explorer is surface with no reader, and `/docs` sitting beside a
# person's own `docs/` is a confusion waiting to happen.
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
PEOPLE: Dict[str, Person] = {}
CONFIG: Dict[str, object] = {}

#: When a person's session last failed to open, and how long their
#: reconnects are answered with a refusal instead of another attempt.
#: Long enough that a browser reconnecting every few seconds cannot
#: start a greeting turn per reconnect; short enough that a daemon
#: coming back is noticed without anybody reloading.
FAILED: Dict[str, float] = {}
RETRY_AFTER = 30.0

#: Set the moment a signal arrives, and watched by every stream.
#:
#: WHY NOT THE LIFESPAN SHUTDOWN HOOK, which is the obvious place: it
#: runs AFTER uvicorn has drained the connections, and an SSE response is
#: a generator that never ends on its own, so the hook is waiting for the
#: very streams that are waiting for it.  Measured on the deployed host:
#: "Waiting for connections to close" at 10:58:03, exit at 10:58:38, and
#: a sibling service on the same box reached systemd's 90 s timeout and
#: was SIGKILLed — which skips the shutdown hook entirely, so the
#: consolidation that makes the escriba remember never runs.
#:
#: The signal is caught where it ARRIVES instead.  Ordinary requests
#: still finish normally; only the endless ones are asked to end.
CLOSING = asyncio.Event()


def principal_of(request: Request) -> str:
    who = request.headers.get("x-forwarded-user") or CONFIG.get("dev_principal")
    if not who:
        raise HTTPException(401, "no authenticated principal: the proxy sends "
                                 "X-Forwarded-User, or run with --dev-principal")
    return str(who)


async def person_of(request: Request) -> Person:
    """This person, with their session opening in the background.

    NOT awaited.  Opening means a session, a prefetched inventory and a
    greeting turn — seconds of model time — and the first thing the
    browser does is ask for `/events`.  Waiting for the greeting before
    the stream exists would leave the page empty for exactly as long as
    the greeting takes, and the greeting itself is delivered through that
    stream.  So the person exists first and the conversation starts
    behind them; the snapshot is built by `Hub.subscribe` when `/events`
    arrives, which is also what makes a later RECONNECT correct rather
    than a replay of whatever was last published.

    `Person(...)` does not await, so the check and the insert cannot
    interleave with another request on this loop: two tabs opening at
    once get one session, not two.
    """
    who = principal_of(request)
    person = PEOPLE.get(who)
    if person is None:
        since = FAILED.get(who)
        if since is not None and time.monotonic() - since < RETRY_AFTER:
            raise HTTPException(503, "the escriba could not open a session; "
                                     "retrying shortly")
        FAILED.pop(who, None)
        # The readable name the proxy also sends, recorded once beside the
        # workspace so an operator can map a directory to a person.  It is
        # never the directory's name: `sub` is what survives a rename, and
        # an email in a path is a disclosure to anyone who can list the
        # root.
        identity = {"username": request.headers.get("x-forwarded-preferred-username"),
                    "email": request.headers.get("x-forwarded-email")}
        person = Person(who, root=Path(str(CONFIG["root"])), limit=CONFIG.get("limit"),
                        identity=identity, scribe=CONFIG.get("scribe"),
                        wiki=CONFIG.get("wiki"))
        PEOPLE[who] = person
        person.seen_as(request.headers.get("x-forwarded-email"))
        # BEFORE the session is opened, so the first one already has a
        # token rather than reaching the wiki with none and learning it
        # on a 401.
        await person.credential(request.headers.get("x-forwarded-access-token"))
        asyncio.create_task(_open(person))
        return person
    # THE TOKEN IS TAKEN FROM EVERY REQUEST, because a request is the only
    # thing that produces a fresh one: oauth2-proxy renews the forwarded
    # token when it handles one, and the held-open `/events` stream is a
    # single request it handles once.  `credential` returns immediately
    # unless the value actually changed.
    await person.credential(request.headers.get("x-forwarded-access-token"))
    person.seen_as(request.headers.get("x-forwarded-email"))
    return person


async def _open(person: Person) -> None:
    """Open a session, and SAY SO if it cannot be opened.

    A failure here is the one the page can least afford to guess at: no
    greeting arrives, no status changes, and the transcript stays empty,
    which looks exactly like an escriba waiting for you to speak.
    """
    try:
        await person.open()
    except Exception as exc:                                  # noqa: BLE001
        # DROPPED, BUT NOT RETRIED ON SIGHT.  Forgetting the person means
        # the next request builds another one — and `EventSource`
        # reconnects every few seconds, so a daemon that is down turns
        # into a new session, a new greeting turn and a new archive
        # directory several times a minute, each one paid for.  The
        # failure is remembered for a moment so the reconnects land on
        # it instead of on the provider.
        log.exception("opening a session for %s failed", person.principal)
        FAILED[person.principal] = time.monotonic()
        PEOPLE.pop(person.principal, None)
        # AND THE ROOM WITH IT.  An invitation into a session that does
        # not exist is an invitation into nothing, and a guest holding
        # one would be told the host is simply quiet.
        ROOM.forget(person.principal)
        person.hub.publish("alert", {"kind": "connection",
                                     "detail": f"{type(exc).__name__}: {exc}"[:200]})


@app.get("/")
async def page() -> FileResponse:
    return FileResponse(PAGE, media_type="text/html")


def _sse(request: Request, hub, queue: asyncio.Queue,
         closed: Optional[dict] = None) -> StreamingResponse:
    """The event stream, for the owner of a hub and for a guest alike.

    LIFTED OUT RATHER THAN COPIED.  Everything in here is load-bearing
    and was learnt the hard way — the single carried getter, the
    keepalive through a buffering proxy, the `closing` frame that keeps a
    restart from being reported as a network fault. A second route with
    its own paraphrase of it would drift from this one, and the drift
    would show up as a page that hangs.

    `closed` is the frame to send when the queue yields the sentinel:
    somebody else ended this subscription, and only the far end knows
    why. The owner's own stream passes none, because nothing ends it
    but the person closing the page or the service going down.
    """

    async def stream():
        waiting = asyncio.ensure_future(CLOSING.wait())
        # ONE getter, carried across iterations and never cancelled while
        # it might be holding something.  Creating a fresh `queue.get()`
        #每 loop and cancelling it on the keepalive timeout has a window:
        # an item delivered between the timeout firing and the cancel is
        # already out of the queue, and cancelling a finished future
        # drops it.  What would go missing is one event, and the one that
        # matters most is `status` — lose the `listening` that ends a
        # turn and the page says "pensando…" forever while the server
        # believes it answered.
        nxt = asyncio.ensure_future(queue.get())
        try:
            while not CLOSING.is_set():
                done, _ = await asyncio.wait({nxt, waiting},
                                             timeout=20,
                                             return_when=asyncio.FIRST_COMPLETED)
                if nxt in done:
                    item = nxt.result()
                    if item is None:
                        # THE SENTINEL, not an event. A host withdrawing
                        # an invitation has to end a request that is
                        # sitting on `queue.get()`, and the only thing
                        # that both wakes it and lets it say why is a
                        # value it can recognise.
                        #
                        # TWO THINGS END A STREAM AND ONLY ONE IS NEWS.
                        # `overflowed` means this reader fell behind and
                        # the hub ended it so the browser reconnects into
                        # a fresh snapshot — nothing has changed about
                        # whether the guest is welcome, and telling them
                        # the host withdrew would be a lie that costs
                        # them the room.
                        if closed is not None and not queue.overflowed:
                            yield frame("closed", closed)
                        break
                    yield item
                    nxt = asyncio.ensure_future(queue.get())
                else:
                    if CLOSING.is_set():
                        break
                    # A comment keeps the connection warm through a proxy
                    # that closes idle streams; `EventSource` ignores it.
                    yield b": keepalive\n\n"
                if await request.is_disconnected():
                    break
        finally:
            waiting.cancel()
            nxt.cancel()
            hub.drop(queue)
            # Told, not dropped.  `EventSource` reconnects by itself and
            # is answered with a fresh snapshot, so the page recovers
            # without a reload — but a stream that simply stops looks
            # identical to a network fault, and the page would spend five
            # seconds saying so.
            if CLOSING.is_set():
                yield frame("closing", {"reason": "el escriba se está reiniciando"})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/events")
async def events(request: Request) -> StreamingResponse:
    person = await person_of(request)
    return _sse(request, person.hub, person.hub.subscribe())


@app.post("/talk")
async def talk(request: Request, audio: UploadFile) -> StreamingResponse:
    person = await person_of(request)
    blob = await audio.read()

    async def stream():
        # The turn's own events reach the browser through the HUB, because
        # the person may have two tabs open and both must see the reply.
        # This response carries only the outcome, so the upload knows
        # whether it was accepted.
        #
        # A TURN IN FLIGHT IS LET FINISH.  It is seconds of model time
        # that has already been paid for, it ends with a memory stored
        # and a recording archived, and dropping it mid-audio would lose
        # all of that to save a second of restart.  What the shutdown
        # changes is that nothing NEW is accepted.
        # EVERY PATH ENDS WITH A STATUS, including the ones that fail.
        # The page sets "pensando…" when it starts the upload, and only a
        # status from the server takes it off again.  A turn that ended
        # with an alert and no status left the pill thinking forever — a
        # person looking at an escriba that had already given up, with no
        # way to tell that from one still working.  The alert says what
        # went wrong; the status says it is over.
        try:
            if CLOSING.is_set():
                yield frame("alert", {"kind": "session",
                                      "detail": "el escriba se está reiniciando; "
                                                "inténtalo de nuevo en un momento"})
                return
            await person.talk(blob)
        except NotReady as exc:
            yield frame("alert", {"kind": "waking", "detail": str(exc)})
        except FloorTaken as exc:
            # ITS OWN KIND. Somebody else is talking, which is not a
            # failure of anything and must not read as one: the page
            # disables the button while the floor is held, and this is
            # for the press that raced it anyway.
            yield frame("alert", {"kind": "floor", "detail": str(exc)})
        except NothingHeard:
            # NOT a connection failure, and it used to be reported as one.
            # The page owns the words; what crosses is which situation it
            # is.
            yield frame("alert", {"kind": "silence"})
        except Exception as exc:                      # noqa: BLE001
            log.exception("a turn for %s failed", principal_of(request))
            yield frame("alert", {"kind": "connection", "detail": str(exc)[:200]})
        finally:
            yield frame("status", person.board.status())

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/stage")
async def stage(request: Request, document: UploadFile):
    """Take a document the person wants to discuss, and declare it.

    A PLAIN JSON REPLY, not a stream: unlike `/talk` there is no turn to
    narrate — the file is written, the catalogue entry beside it, and the
    panel picks the row up on its next tick.  What the browser needs back
    is whether the upload was accepted.

    THE CEILING IS REPORTED, NOT ENFORCED SILENTLY.  A refused upload is
    the one failure a person can do something about (export it smaller,
    split it), so it answers 413 with the ceiling in it rather than
    truncating the file to fit — a document discussed as if whole, when
    half of it was dropped, is worse than a refusal.
    """
    person = await person_of(request)
    blob = await document.read()
    try:
        return await person.stage(document.filename or "", blob)
    except _staging.TooLarge as exc:
        raise HTTPException(413, f"{exc}") from exc


@app.post("/demo/abrir")
async def demo_open(request: Request) -> dict:
    """Start recording a demonstration, and answer with its id.

    REFUSED WHILE THE SERVER IS CLOSING, where a frame is not.  The rule
    `/talk` states for a turn applies with the sides swapped: what is
    already recording has minutes of somebody's screen behind it and is
    let finish, and what must not happen is something NEW beginning
    seconds before the process goes away.
    """
    if CLOSING.is_set():
        raise HTTPException(503, "el escriba se está reiniciando; "
                                 "inténtalo de nuevo en un momento")
    person = await person_of(request)
    try:
        ident = _demo.open_demo(person.ws)
        person.demo_opened(ident)
        return {"id": ident}
    except _demo.AlreadyOpen as exc:
        # 409 AND NOT 400: nothing was malformed. Somebody is already
        # recording, which is a state the page can show rather than an
        # error it has to explain.
        raise HTTPException(409, f"{exc}") from exc


@app.post("/demo/{ident}/fotograma")
async def demo_frame(request: Request, ident: str, frame: UploadFile,
                     at_ms: int = Form(0)) -> dict:
    """One frame of a demonstration in progress.

    NO FILENAME IS READ from the upload.  `/stage` has to guard the name
    the browser chose because it becomes a path; frames arrive in order
    and `demo` numbers them, so there is nothing here to guard.

    THE REFUSAL FOR A FRAME THE MODEL WOULD NOT TAKE IS 422, carrying
    jaato's own words.  It is the one failure the page can act on while
    the demo is still running -- a page scaling to the wrong size learns
    it on frame one instead of at document time, when the demo is over.
    """
    person = await person_of(request)
    try:
        return _demo.frame(person.ws, ident, await frame.read(), at_ms)
    except _demo.NotAFrame as exc:
        raise HTTPException(422, f"{exc}") from exc
    except _demo.TooLarge as exc:
        raise HTTPException(413, f"{exc}") from exc
    except _demo.NoSuchDemo as exc:
        raise HTTPException(404, "no hay ninguna demostración abierta con ese id") from exc


@app.post("/demo/{ident}/voz")
async def demo_voice(request: Request, ident: str) -> Response:
    """The narration of a demonstration, live, while it is still going on.

    ITS OWN ROUTE AND NOT `/voz`, which is the decision this carries.
    `/voz` ends at `Person.voice`, which fans the frames out to whoever
    else is in the room -- right for a press, and for a demonstration it
    would broadcast the host's narration to a guest as a side effect of
    the escriba listening.  Who hears a demonstration is a question for
    the guest boundary, so it is asked by having a separate end rather
    than answered by reusing one.

    SEPARATE FROM `/cerrar` TOO, for the reason `_voice` gives against
    `/talk`: the blob that becomes the archive's narration must not grow
    a new failure mode, so this is a second consumer of the same
    microphone and a piece of it going missing costs the escriba a
    fragment of what it heard rather than the demonstration its record.
    """
    person = await person_of(request)
    pcm, rate, channels = await _pcm(request)
    if pcm:
        try:
            person.demo_voice(ident, pcm, rate, channels)
        except _demo.NoSuchDemo as exc:
            raise HTTPException(
                404, "no hay ninguna demostración abierta con ese id") from exc
        except _live.ShapeChanged as exc:
            raise HTTPException(400, f"{exc}") from exc
    return Response(status_code=204)


@app.post("/demo/{ident}/cerrar")
async def demo_close(request: Request, ident: str,
                     audio: UploadFile | None = None,
                     suffix: str = Form("webm")) -> dict:
    """Stop recording: store the narration and seal the manifest.

    The narration is OPTIONAL. A demo whose microphone was refused is
    still a sequence of screens somebody can be asked about, and losing
    the frames because the audio never arrived would be the expensive
    way to report a missing permission.
    """
    person = await person_of(request)
    try:
        manifest = _demo.close_demo(person.ws, ident,
                                    await audio.read() if audio else b"", suffix)
    except _demo.TooLarge as exc:
        raise HTTPException(413, f"{exc}") from exc
    except _demo.NoSuchDemo as exc:
        raise HTTPException(404, "no hay ninguna demostración abierta con ese id") from exc
    # FIRED, NOT AWAITED, as `/invitar` fires `ask_why`: commissioning the
    # document is a whole turn, and the page is holding a button down
    # waiting for this answer. What the browser needs back is that the
    # demonstration was stored.
    asyncio.create_task(person.demo_closed(manifest))
    return manifest


# NO GUEST ROUTE, deliberately. A demonstration is the host's own
# screen, captured on the host's own machine; nothing in the feature
# needs a second browser, and `escriba_web/CLAUDE.md`'s rule is that a
# guest receives what is BUILT for them rather than what is left
# unguarded.


def _recording(person: Person, att_id: str) -> FileResponse:
    """One archived recording of one conversation.

    The id is matched against its shape rather than sanitised: a
    filename is built from it, and the only safe treatment of a path
    fragment that came from a browser is to refuse anything that is not
    exactly what it should be.
    """
    if not ATT.match(att_id):
        raise HTTPException(400, "not an attachment id")
    root = person.ws / _archive.ROOT
    for pattern, media in ((f"*/in_{att_id}.mp3", "audio/mpeg"),
                           (f"*/out_{att_id}.wav", "audio/wav"),
                           (f"*/out_{att_id}.bin", "application/octet-stream")):
        for found in root.glob(pattern):
            return FileResponse(found, media_type=media)
    raise HTTPException(404, "no recording with that id")


@app.get("/audio/{att_id}")
async def audio_file(request: Request, att_id: str) -> FileResponse:
    return _recording(await person_of(request), att_id)


@app.get("/doc")
async def doc(request: Request, path: str) -> PlainTextResponse:
    person = await person_of(request)
    # Resolved and then checked to be INSIDE `docs/`: the path comes from
    # a panel this server filled, but it arrives back over the wire and
    # `../` is the oldest trick there is.
    docs = (person.ws / "docs").resolve()
    target = (person.ws / path).resolve()
    if docs not in target.parents or not target.is_file():
        raise HTTPException(404, "no such document")
    return PlainTextResponse(target.read_text(encoding="utf-8"))


@app.get("/secuencia")
async def secuencia(request: Request) -> dict:
    """This conversation's sequence, as the page draws it.

    BUILT HERE AND NOT IN THE BROWSER.  `sequence.outline` decides what
    nests under what -- a call under a turn of its own agent, a subagent
    under the turn that created it -- and a second implementation of
    that in JavaScript would be a second set of answers, disagreeing
    exactly when a run was interesting enough to be looked at.  So the
    page is handed the spans and draws them.

    NO GUEST ROUTE, for the reason the demonstration has none and more
    so: this is the whole shape of the host's conversation — every tool
    it called, what it spent — and a colleague invited for ten minutes
    of an interview is owed none of it.  `guest_snapshot` is BUILT key
    by key for exactly this reason, and a route the guest page never
    calls is the same discipline one layer down.

    AN EMPTY ANSWER IS AN ANSWER.  A journal with nothing in it yet is a
    conversation that has not had a turn, which the page says in a line
    rather than treating as a failure.
    """
    person = await person_of(request)
    return _sequence.outline(_sequence.read(person.journal.path)
                             if person.journal.path.exists() else [])


@app.post("/sesion/nueva")
async def nueva(request: Request) -> dict:
    """Start another session, because the person asked for one.

    THE ONE WAY BACK FROM A CEILING. A budget ending is deliberately not
    repaired — see `Person._ended` — so this is what the alert offers
    instead, and pressing it is a decision the manifest records.

    NOT A GUEST ROUTE, and not reachable as one: a guest's page talks to
    `/invitado/*`, and this resolves the CALLER's own person, so the
    worst a guest could do by posting here is restart their own session.
    The page does not offer it to them either — they see the host's
    alerts through the host's hub, and a button there would be a button
    about somebody else's ceiling.
    """
    person = await person_of(request)
    if not await person.restart():
        raise HTTPException(503, "a new session could not be opened")
    return {"ok": True}


@app.get("/de-cero")
async def de_cero_counts(request: Request) -> dict:
    """What starting from scratch would take, counted before it is offered.

    A SEPARATE READ, so the confirmation shows the person their own
    numbers rather than a sentence about "everything". The recordings
    are in it because they are the half nothing can reconstruct.
    """
    person = await person_of(request)
    return person.holdings()


@app.post("/de-cero")
async def de_cero(request: Request) -> dict:
    """Move this person's whole workspace aside and begin again.

    ONE RENAME, NOT A LIST OF DELETIONS. `Person.scratch` says why; what
    this route adds is the forgetting of the PROCESS's own state. The
    `Person` is dropped and the room with it, exactly as `_open` does
    when a session cannot be opened, so the next request builds a new
    one and provisions a workspace from the template — the same path a
    person takes on their first ever visit, which is the point.

    NOT A GUEST ROUTE, and not reachable as one: a guest's page talks to
    `/invitado/*`, and this resolves the CALLER's own person, so the
    worst a guest could do by posting here is reset their own escriba.

    THE ESCRIBA CANNOT REACH IT EITHER. There is no tool for this and
    there will not be one: a second brain that can decide to forget
    itself is not a second brain.
    """
    person = await person_of(request)
    aside = await person.scratch()
    PEOPLE.pop(person.principal, None)
    # AND THE ROOM WITH IT. An invitation into a session that no longer
    # exists is an invitation into nothing.
    ROOM.forget(person.principal)
    log.warning("%s started from scratch; the old workspace is at %s",
                person.principal, aside)
    return {"moved": aside.name}


@app.get("/days")
async def days(request: Request):
    person = await person_of(request)
    return person.days()


@app.get("/strings/{locale}")
async def strings(locale: str) -> dict:
    """One locale's catalogue.

    The snapshot already carries the active one, so a live page never
    needs this — it exists for `?mock=1`, which has no session to be
    handed a snapshot by and would otherwise need a second copy of the
    catalogue inlined in the page.  `i18n` resolves an unknown locale to
    the default, so the path cannot name a file.
    """
    return i18n.catalogue(locale)


@app.post("/locale")
async def locale(request: Request):
    """Change the language this person is answered in.

    A POST rather than a query on `/events`, because it CHANGES
    something: the choice is written beside the workspace and survives
    the next sign-in, on this device or another.  The new state is
    published to every tab the person has open, so a language picked in
    one is not a second opinion in the next.

    The body names a locale; an unknown one resolves to the default
    rather than being refused, since `i18n` already decides what exists
    and there is nothing useful for a page to do with the rejection.
    """
    person = await person_of(request)
    body = await request.json()
    return {"locale": person.speak(str((body or {}).get("locale") or ""))}


@app.get("/day/{date}")
async def day(request: Request, date: str):
    person = await person_of(request)
    try:
        return person.day(date)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/heard")
async def heard(request: Request) -> Response:
    """What a BROWSER says it played, recorded as a claim.

    Written under its own stage and never interleaved with the server's
    own timestamps.  The framework's audit record is careful about the
    same distinction — an external approver's name is "asserted, recorded
    as claimed" — and this is the same kind of fact: nobody here saw the
    sound come out, a client said it did.
    """
    person = await person_of(request)
    body = await request.json()
    if not ATT.match(str(body.get("audio", ""))):
        raise HTTPException(400, "not an attachment id")
    person.archive.turn(stage="playback-claimed", audio=body["audio"],
                        event=str(body.get("event", ""))[:16],
                        claimed_at=str(body.get("at", ""))[:40],
                        source="browser")
    return Response(status_code=204)


# ---------------------------------------------------------------- guests
#
# WHAT THIS IS FOR, because it reads like multi-user and is not. Somebody
# is working with their own escriba and needs a detail only a colleague
# has: they invite that colleague in for a few minutes, the colleague
# listens, says the thing, maybe drops a document in, and leaves. The
# host is present throughout and the whole visit is on the host's bill,
# in the host's workspace, under the host's wiki grants. Nothing here
# makes a session shared, and nothing here gives a guest a second brain
# that is not their own.


def _name_of(request: Request) -> str:
    """How a guest is shown, from what the proxy asserted about them."""
    return label(request.headers.get("x-forwarded-preferred-username")
                 or request.headers.get("x-forwarded-email") or "")


def _invited(request: Request) -> Tuple[Person, Invitation, str]:
    """The HOST's person, for a guest who was named and is who they say.

    NEVER THROUGH `person_of`, and that is not tidiness. `person_of`
    builds a Person for whoever is calling AND takes the forwarded
    access token from every request — deliberately, because the proxy
    renews it on each one. A guest routed through it would have their own
    kbwiki token written over the host's on their first frame, and the
    host's escriba would spend the rest of the conversation reading the
    wiki as somebody else. So this resolver finds the host and never
    touches a credential.

    EVERY REQUEST RE-CHECKS, rather than the entry being checked once:
    the host withdrawing an invitation has to take effect on the next
    thing the guest does, not whenever something expires.
    """
    principal = principal_of(request)                 # the proxy authenticated
    email = request.headers.get("x-forwarded-email")
    if not email:
        # SAID, NOT WORKED AROUND. Matching on the principal instead
        # would be inventing an identity for somebody, and a guess about
        # who is at the door is the one thing this must not make.
        #
        # THE CAUSE IS NOT NAMED, because this code cannot tell which of
        # the two it is, and naming the wrong one sends an operator to
        # check something that is already correct. This used to blame the
        # proxy. Measured on the deployment on 2026-10-12, the proxy
        # forwards the header (`escriba-oauth2-proxy` runs with
        # `--pass-user-headers=true`), so the only remaining cause was an
        # account with no email address, and since 2026-10-12 the realm
        # requires the attribute of an admin creating an account as well
        # as of somebody editing their own profile, so that cause is
        # closed too. Both of this feature's deployment facts therefore
        # hold, and arriving here means one of them has regressed:
        # `CLAUDE.md` beside this file names them and says what each one
        # looks like when it does.
        raise HTTPException(403, "this request carries no email address, so "
                                 "there is nothing to match an invitation "
                                 "against")
    # BOTH HALVES OF THE LOOKUP: the room from the opaque token, and then
    # the invitation issued to the address the proxy asserted -- because
    # one room now holds several, one per person the host named.
    inv = ROOM.find(request.query_params.get("room", ""), email)
    # ONE REFUSAL FOR EVERY CAUSE. No room, no invitation to that address,
    # somebody else already holding it: an uninvited caller learns nothing
    # about which, and therefore nothing about whose session exists.
    if inv is None or ROOM.claim(inv, principal, _name_of(request)) is None:
        raise HTTPException(403, "no open invitation for you")
    host = PEOPLE.get(inv.host)
    if host is None:
        raise HTTPException(409, "the session you were invited to is not open")
    return host, inv, inv.name


@app.post("/invitar")
async def invitar(request: Request) -> dict:
    """Name one colleague, and ask the escriba what to tell them.

    NOTHING IS SENT HERE. This makes the invitation and asks the escriba
    why that person is needed; `/invitar/enviar` is what lets anybody
    see it. The host knows WHO can settle the question and knows their
    address; the escriba has the overview of what the question is. So
    each says the part it is the only one that can.

    THE HOST SEES IT STANDING OPEN. The address is shown back as it was
    typed, next to a cancel: a wrong one then sits there visibly wrong
    rather than failing silently, which is the one failure mode of
    naming somebody in advance instead of admitting them on arrival.

    AND THAT WAS NOT ENOUGH, measured on 2026-10-12: an address that is
    nobody's looks exactly like one whose owner has not arrived yet, and
    the host waited on an invitation that could never be claimed. So the
    address is put to the realm BEFORE an invitation is made of it, when
    the host is still looking at what they typed. `realm.Directory` is
    the whole of that; absent, nothing is checked and nothing is
    guessed, which is the behaviour this route had before it.

    THE TURN IS NOT AWAITED. A model turn takes seconds and the band is
    fed by the hub, so holding the response open for it would buy
    nothing and time out on the slow case. The justification arrives on
    the `room` frame, by the same route as everything else that changes
    while the page is open.
    """
    person = await person_of(request)
    body = await request.json()
    email = str((body or {}).get("email") or "").strip()
    if "@" not in email:
        raise HTTPException(400, "an email address")
    # TWO REFUSALS, NEVER ONE. "nobody has that address" sends the host
    # to fix a typo; "I could not ask" sends them to press the button
    # again in a minute, and reporting the second as the first would
    # send them to correct an address that may be perfectly right.
    # Neither is silent, which is the entire point: an invitation this
    # route accepted is one the guest can actually be offered.
    directory = CONFIG.get("realm")
    if directory is not None:
        try:
            known = await directory.holds(email)
        except _realm.Unreachable as exc:
            raise HTTPException(503, "the realm could not be asked whether "
                                     "that address belongs to anybody") from exc
        if not known:
            raise HTTPException(404, "no account here has that address")
    # ONLY THE INVITATION TO THIS ADDRESS IS REPLACED, and if somebody
    # was holding it they are shown out through the same path a
    # withdrawal takes -- stream closed, transcript, manifest, and the
    # escriba told on its next turn. Everybody else in the room stays:
    # a host fixing a typo must not evict the colleague who is in the
    # middle of answering a question.
    going = next((i for i in ROOM.of_host(person.principal)
                  if i.email == email.strip().lower() and i.guest), None)
    if going is not None:
        person.guest_gone(going.name or going.email, stage="guest-replaced")
    inv = ROOM.invite(person.principal, email)
    # THE EARLIER ONE IS GONE, so an offer this person may already be
    # looking at goes with it: `ROOM.invite` closed it, and the new one
    # is not open yet. The same frame that delivers an offer withdraws
    # one, because it carries the whole list rather than an event.
    _offer(inv.email)
    person.drafting(inv.email)
    asyncio.create_task(person.ask_why(inv.email))
    return {"room": token(person.principal), "guests": _room_of(person)}


@app.post("/invitar/enviar")
async def enviar(request: Request) -> dict:
    """The host read what the escriba wrote, and is sending it.

    THE ONLY PLACE AN INVITATION BECOMES VISIBLE TO ANYBODY ELSE. Until
    this runs, `Room.waiting_for` and `Room.find` both ignore it, so the
    guest is neither offered the room nor able to force their way into
    it by guessing the token.

    IT IS A SEPARATE REQUEST, AND THAT IS THE POINT. The justification is
    written by a model out of the host's own conversation and read by
    somebody outside it — the only text in this feature that travels
    that way. The person whose session it describes reads it first and
    decides. A 409 rather than a quiet no-op when there is nothing to
    send: the button should not exist in that state, and if it did, the
    host would have to be told why pressing it did nothing.
    """
    person = await person_of(request)
    body = await request.json()
    email = str((body or {}).get("email") or "").strip().lower()
    inv = ROOM.open_up(person.principal, email)
    if inv is None:
        raise HTTPException(409, "no invitation to that address is waiting to "
                                 "be sent, or it has no justification yet")
    person.invited(inv.email)
    # AND THE PERSON IT IS FOR, if they are already looking at a page.
    # Without this the offer waits for their next load, which is how an
    # invitation sent at 18:14 reached a page that had asked at 18:06.
    _offer(inv.email)
    return {"room": token(person.principal), "guests": _room_of(person)}


@app.delete("/invitar")
async def desinvitar(request: Request) -> dict:
    """Show one guest out, or everybody.

    NAMED RATHER THAN "THE GUEST", because an interview has two sides
    and one of them finishes first: the colleague who has said their
    piece leaves, and the other one is still answering.
    """
    person = await person_of(request)
    email = str(request.query_params.get("email") or "").strip().lower()
    gone = ([ROOM.revoke(person.principal, email)] if email
            else ROOM.cancel(person.principal))
    for inv in [i for i in gone if i is not None]:
        person.guest_gone(inv.name or inv.email, stage="guest-revoked")
        # An invitation withdrawn before it was claimed leaves no stream
        # to close, so this is the only thing that takes the offer off
        # the invited person's screen. `Room._close` covers the other
        # case, where somebody was already inside.
        _offer(inv.email)
    return {"room": token(person.principal), "guests": _room_of(person)}


def _offers(principal: str, email: str) -> list:
    """Every room waiting for this person, as the page draws one.

    ONE BUILDER, TWO CALLERS: `/invitacion` answers with it on load, and
    `_offer` pushes it when something changes while the page is already
    open. Two copies would drift, and the half that drifted would be the
    one nobody notices -- the push, which nothing asks for.
    """
    return [{"room": token(i.host), "host": PEOPLE[i.host].who,
             "since": i.since.isoformat(timespec="seconds"),
             # WHY THEY ARE WANTED, BEFORE THEY DECIDE. The whole reason
             # the host's escriba writes this: somebody asked to step
             # into a conversation they cannot see should know what it is
             # about and what is wanted of them while they still have the
             # choice, not after they are in the room.
             "motivo": i.motivo, "tema": i.tema}
            for i in ROOM.waiting_for(email or "")
            # An invitation whose host has no live session is not an
            # invitation anywhere, and offering it would send the guest
            # to a 409 they can do nothing about. One already claimed by
            # somebody else is not theirs to see at all.
            if i.host in PEOPLE and i.guest in (None, principal)]


def _offer(email: str) -> None:
    """Tell the invited person NOW, if their page is already open.

    MEASURED, 2026-10-12: `/invitacion` is asked once, on load. A guest
    whose page was already open when the host pressed send learned
    nothing -- their page booted at 18:06 and the invitation went out at
    18:14, so the one question that would have told them had been asked
    and answered eight minutes earlier. They sat in their own escriba
    while the host waited, which is the same silence twice over.

    They already have a stream: whoever has their own session open is
    subscribed to their OWN hub. So the offer goes down it, and this is
    also what WITHDRAWS one -- the frame carries the whole list, so a
    revoked invitation leaves by the same path it arrived, instead of an
    offer that stays on screen and 403s when pressed.

    IT TELLS THE HOST NOTHING. The caller's answer does not change by one
    byte whether or not the invited person turned out to be connected:
    inviting somebody must not become a way to find out who is online.

    Somebody who is currently a guest in another room is subscribed to
    THAT host's hub, not their own, so this cannot reach them -- and it
    does not need to: they are already in a conversation.
    """
    folded = (email or "").strip().lower()
    guest = next((p for p in PEOPLE.values() if p.email == folded), None)
    if guest is None:
        return
    guest.hub.publish("invitacion",
                      {"rooms": _offers(guest.principal, guest.email)})


def _room_of(person: Person) -> list:
    """Everybody this host has named, and whether they are here yet."""
    return [i.json() for i in ROOM.of_host(person.principal)]


@app.get("/invitacion")
async def invitacion(request: Request):
    """Who, if anyone, is expecting this person.

    RESOLVES NO PERSON OF THEIR OWN, which is the whole reason the page
    can ask this on load: `person_of` would open a session, a prefetched
    inventory and a greeting turn — seconds of model time — for somebody
    who is about to go and sit in someone else's conversation instead.
    """
    principal = principal_of(request)                 # authenticated first
    email = request.headers.get("x-forwarded-email")
    rooms = _offers(principal, email or "")
    # THE ONE LINE THAT WOULD HAVE NAMED IT. On 2026-10-12 a guest was
    # offered nothing and nobody could say why without opening this
    # route by hand in a browser: the host saw an invitation standing
    # open, the guest saw their own escriba, and the two facts that
    # disagreed -- the address the host typed and the one the proxy
    # asserts for this person -- were never in the same place.
    #
    # The address IS logged, deliberately. It is the whole content of
    # the mismatch, it is already held beside the workspace in
    # `person.json`, and a line saying only "0 waiting" answers nothing.
    log.info("invitation check for %s under %s: %d waiting",
             principal, email or "(the proxy asserted no address)", len(rooms))
    return {"rooms": rooms, "email": email}


@app.get("/invitado/events")
async def guest_events(request: Request) -> StreamingResponse:
    host, inv, name = _invited(request)
    queue = host.hub.subscribe(
        lambda: host.guest_snapshot(principal_of(request), name))
    # HELD SO THE HOST CAN END IT. Every other guest route re-checks the
    # invitation when it is called; this one is a single request that
    # lasts the whole visit, so withdrawing has to reach in from the
    # other side.
    inv.queues.append(queue)
    host.guest_here(name, who_id=token(principal_of(request)))
    return _sse(request, host.hub, queue,
                closed={"reason": "la sesión se ha cerrado"})


@app.post("/invitado/talk")
async def guest_talk(request: Request, audio: UploadFile) -> StreamingResponse:
    """A guest's utterance, into the host's conversation.

    THE SAME TURN THE HOST WOULD HAVE TAKEN, on the host's session and
    the host's budget, with the speaker named so the escriba is not left
    to infer that the person it knows has changed their voice.
    """
    host, _inv, name = _invited(request)
    blob = await audio.read()

    async def stream():
        try:
            if CLOSING.is_set():
                yield frame("alert", {"kind": "session",
                                      "detail": "el escriba se está reiniciando; "
                                                "inténtalo de nuevo en un momento"})
                return
            await host.talk(blob, by=name)
        except NotReady as exc:
            yield frame("alert", {"kind": "waking", "detail": str(exc)})
        except FloorTaken as exc:
            yield frame("alert", {"kind": "floor", "detail": str(exc)})
        except NothingHeard:
            yield frame("alert", {"kind": "silence"})
        except Exception as exc:                      # noqa: BLE001
            log.exception("a guest turn in %s failed", host.principal)
            yield frame("alert", {"kind": "connection", "detail": str(exc)[:200]})
        finally:
            yield frame("status", host.board.status())

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/invitado/stage")
async def guest_stage(request: Request, document: UploadFile) -> dict:
    """A document the guest brought, staged in the host's workspace.

    Which is the point of inviting them: the detail the host is missing
    is often a file. `Person.stage` is called unchanged — the filename
    guard that keeps a browser from naming a path belongs to it and now
    covers a second browser for free — and the note says who brought it,
    because a document that appears in your own workspace with no
    provenance is a document you cannot account for later.
    """
    host, _inv, name = _invited(request)
    blob = await document.read()
    try:
        row = await host.stage(document.filename or "", blob, by=name)
        # THE PROVENANCE OF A FILE IN SOMEBODY ELSE'S WORKSPACE. The
        # staging catalogue records what the document is; this records
        # that it was not the owner who brought it, which is the question
        # anybody auditing their own workspace will have.
        host.archive.turn(stage="guest-staged", guest=name,
                          document=row.get("text"), source="invitation")
        return row
    except _staging.TooLarge as exc:
        raise HTTPException(413, f"{exc}") from exc


@app.get("/invitado/audio/{att_id}")
async def guest_audio(request: Request, att_id: str) -> FileResponse:
    """One recording of this conversation, for somebody who is in it.

    A guest sees no row from before they arrived, so the only ids they
    can hold are ones they watched go past — but the id is checked
    against the shape and the file is looked up under the HOST's archive
    either way, because what a browser sends back is never the thing
    that decides which file is read.
    """
    host, _inv, _name = _invited(request)
    return _recording(host, att_id)


@app.post("/invitado/voz")
async def guest_voice(request: Request) -> Response:
    host, _inv, name = _invited(request)
    return await _voice(request, host, name, principal_of(request))


@app.post("/voz")
async def voz(request: Request) -> Response:
    """The host's own microphone, live, while the press is still going on.

    SEPARATE FROM `/talk` ON PURPOSE. `/talk` carries the one blob the
    model is fed and the archive keeps, and it has to stay exactly that:
    this is a second tap on the same microphone, and a piece of it going
    missing costs the guest a fragment of audio rather than corrupting a
    turn.
    """
    person = await person_of(request)
    return await _voice(request, person, person.who, person.principal)


async def _voice(request: Request, person: Person, name: str,
                 principal: str) -> Response:
    """One piece of somebody's live microphone, fanned out to the room.

    RAW `s16le` IN THE BODY, with the shape in the query: the frame the
    page already decodes for the escriba's voice is PCM, so sending PCM
    means one scheduler in the browser instead of two and no container
    to reassemble. A press that produces nothing worth sending sends
    nothing — there is no empty frame to publish.
    """
    stream = request.query_params.get("stream", "")
    if not re.fullmatch(r"[a-z0-9]{4,32}", stream):
        raise HTTPException(400, "not a stream id")
    if request.query_params.get("final") == "1":
        person.voice_end(stream, token(principal))
        return Response(status_code=204)
    pcm, rate, channels = await _pcm(request)
    if pcm:
        person.voice(stream, pcm, rate, channels, name, token(principal))
    return Response(status_code=204)


@app.on_event("shutdown")
async def shutdown() -> None:
    for person in list(PEOPLE.values()):
        ROOM.forget(person.principal)
        await person.close()


def main(argv=None) -> int:
    import uvicorn

    p = argparse.ArgumentParser(prog="python -m escriba_web",
                                description="escriba on the web: the BFF.")
    p.add_argument("--root", default=str(_workspace.DEFAULT_ROOT),
                   help="the workspace root (default: %(default)s)")
    p.add_argument("--budget-file", metavar="PATH",
                   help="file holding ONE integer: megabytes of recordings a "
                        "person may hold. Omitted, this run has no ceiling")
    p.add_argument("--dev-principal", metavar="NAME",
                   help="run with no proxy in front and treat every request as "
                        "this person. For a developer's own machine")
    p.add_argument("--transcribe", metavar="MODEL",
                   help="transcribe each utterance locally with this whisper "
                        "model: tiny (~75 MB), base (~145 MB), small (~480 MB), "
                        "downloaded on first use. Omitted, nothing is "
                        "transcribed and no model is loaded")
    # ABSENT IS OFF, and nothing is defaulted.  A URL this project
    # invented would point each person's own access token at a host
    # nobody chose; a token directory it invented would put a live
    # credential somewhere the operator did not agree to.  Both or
    # neither.
    p.add_argument("--kbwiki-url", metavar="URL",
                   help="the kbwiki MCP endpoint each person's escriba may "
                        "call AS THEM, with the access token the proxy "
                        "forwards. Needs --token-dir. Omitted, no .mcp.json "
                        "is written and the wiki panel stays unwired")
    p.add_argument("--token-dir", metavar="DIR",
                   help="where each person's session environment file is "
                        "written, 0600 in a 0700 directory. NOT the "
                        "workspace: it holds a live bearer token, so put it "
                        "on tmpfs (/run/escriba). Needs --kbwiki-url")
    # A PATTERN, NOT A HOST, because the route is kbwiki's business and
    # not ours.  `https://kbwiki.darumalabs.ai/#/knowledge/{id}` is what
    # that deployment serves (RIP-KB-EXTRACTOR#2, 14f7dd8); another one
    # may serve something else, and an operator stating it is the
    # difference between a contract and this repo guessing at a peer's
    # URLs.  Without it a wiki page is shown as its id, which is the
    # honest rendering of a thing that has no address.
    p.add_argument("--kbwiki-page", metavar="PATTERN",
                   help="how to link one wiki page, with {id} where the "
                        "node id goes. Needs --kbwiki-url. Omitted, a wiki "
                        "page is shown as its id instead of as a link")
    # THE THREE TOGETHER OR NONE OF THEM, the way the wiki pair above
    # works and for the same reason: a URL this project invented would
    # ask a realm nobody chose whether somebody's colleague exists, and
    # a realm named with no credential cannot be asked at all. Omitted,
    # the address is taken as the host typed it -- which is what this
    # did before, stated rather than defaulted.
    p.add_argument("--keycloak-url", metavar="URL",
                   help="the Keycloak base URL whose realm holds the accounts "
                        "a host may invite. With --keycloak-realm and "
                        "--keycloak-client-file, an address that belongs to "
                        "nobody is refused while the host is still looking at "
                        "it. Omitted, nothing is checked")
    p.add_argument("--keycloak-realm", metavar="NAME",
                   help="the realm to ask. Needs --keycloak-url")
    p.add_argument("--keycloak-client-file", metavar="PATH",
                   help="file holding ONE line, <client-id>:<client-secret>, "
                        "for a service account whose only grant is view-users "
                        "on that realm. A FILE because an argument would put "
                        "the secret in /proc/<pid>/cmdline. Needs "
                        "--keycloak-url")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args(argv)

    CONFIG["root"] = args.root
    CONFIG["dev_principal"] = args.dev_principal
    CONFIG["limit"] = None
    CONFIG["scribe"] = (_transcribe.Transcriber(args.transcribe)
                        if args.transcribe else None)
    if bool(args.kbwiki_url) != bool(args.token_dir):
        print("escriba_web: --kbwiki-url and --token-dir are given together "
              "or not at all: a wiki with nowhere to put the token cannot be "
              "called, and a token directory with no wiki holds a credential "
              "for nothing", file=sys.stderr)
        return 2
    if args.kbwiki_page and not args.kbwiki_url:
        print("escriba_web: --kbwiki-page needs --kbwiki-url: there is no "
              "wiki to link pages of", file=sys.stderr)
        return 2
    if args.kbwiki_page and "{id}" not in args.kbwiki_page:
        print("escriba_web: --kbwiki-page has no {id} in it, so every page "
              "would get the same link", file=sys.stderr)
        return 2
    CONFIG["wiki"] = ({"url": args.kbwiki_url, "dir": args.token_dir,
                       "page": args.kbwiki_page}
                      if args.kbwiki_url else None)
    keycloak = (args.keycloak_url, args.keycloak_realm, args.keycloak_client_file)
    if any(keycloak) and not all(keycloak):
        print("escriba_web: --keycloak-url, --keycloak-realm and "
              "--keycloak-client-file are given together or not at all: a "
              "realm with no credential cannot be asked, and a credential "
              "with no realm has nothing to ask", file=sys.stderr)
        return 2
    CONFIG["realm"] = None
    if all(keycloak):
        try:
            client, secret = _realm.read_client(Path(args.keycloak_client_file))
        except (OSError, ValueError) as exc:
            print(f"escriba_web: {exc}", file=sys.stderr)
            return 2
        CONFIG["realm"] = _realm.Directory(args.keycloak_url,
                                           args.keycloak_realm, client, secret)
    if args.budget_file:
        try:
            CONFIG["limit"] = _housekeeping.ceiling(Path(args.budget_file))
        except _housekeeping.BudgetUnreadable as exc:
            print(f"escriba_web: {exc}", file=sys.stderr)
            return 2
    # The signal is caught HERE, by wrapping uvicorn's own handler: it
    # fires when the signal arrives, which is what the streams need, and
    # the lifespan hook does not.
    class Server(uvicorn.Server):
        def handle_exit(self, sig, frame) -> None:      # noqa: D102
            CLOSING.set()
            super().handle_exit(sig, frame)

    # A SAFETY NET, not the mechanism.  Anything the event above fails to
    # reach can delay a restart by five seconds and no longer; the
    # lifespan shutdown still runs, which is what consolidates the
    # conversation.  Lowering systemd's TimeoutStopSec would not do this
    # — SIGKILL skips the shutdown entirely.
    # OUR OWN RECORDS, NOT UVICORN'S.  Its logging config names only the
    # `uvicorn.*` loggers and leaves the root alone, so without this the
    # app's tracebacks reach stderr through `logging.lastResort` — level
    # WARNING, no format, no timestamp.  They are the only account of a
    # failure that never reaches the daemon, so they are worth a line.
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = uvicorn.Config(app, host=args.host, port=args.port,
                            log_level="info", timeout_graceful_shutdown=5)
    Server(config).run()
    return 0

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
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, Request, UploadFile    # noqa: E402
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse  # noqa: E402

import i18n                                                      # noqa: E402
import archive as _archive                                         # noqa: E402
import housekeeping as _housekeeping                               # noqa: E402
import transcribe as _transcribe                                   # noqa: E402
import workspace as _workspace                                     # noqa: E402

from .driver import NotReady, Person                               # noqa: E402
from .hub import frame                                             # noqa: E402

HERE = Path(__file__).resolve().parent
PAGE = HERE.parent / "web" / "index.html"

#: An attachment id as the framework mints it: `att_` and sixteen hex.
#: Matched rather than sanitised — a filename is built from this, and the
#: only safe treatment of a path fragment from a browser is to refuse
#: anything that is not exactly the shape it should be.
ATT = re.compile(r"^att_[0-9a-f]{16}$")

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
                        identity=identity, scribe=CONFIG.get("scribe"))
        PEOPLE[who] = person
        asyncio.create_task(_open(person))
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
        FAILED[person.principal] = time.monotonic()
        PEOPLE.pop(person.principal, None)
        person.hub.publish("alert", {"kind": "connection",
                                     "detail": f"{type(exc).__name__}: {exc}"[:200]})


@app.get("/")
async def page() -> FileResponse:
    return FileResponse(PAGE, media_type="text/html")


@app.get("/events")
async def events(request: Request) -> StreamingResponse:
    person = await person_of(request)
    queue = person.hub.subscribe()

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
                    yield nxt.result()
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
            person.hub.drop(queue)
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
        except Exception as exc:                      # noqa: BLE001
            yield frame("alert", {"kind": "connection", "detail": str(exc)[:200]})
        finally:
            yield frame("status", person.board.status())

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/audio/{att_id}")
async def audio_file(request: Request, att_id: str) -> FileResponse:
    person = await person_of(request)
    if not ATT.match(att_id):
        raise HTTPException(400, "not an attachment id")
    root = person.ws / _archive.ROOT
    for pattern, media in ((f"*/in_{att_id}.mp3", "audio/mpeg"),
                           (f"*/out_{att_id}.wav", "audio/wav"),
                           (f"*/out_{att_id}.bin", "application/octet-stream")):
        for found in root.glob(pattern):
            return FileResponse(found, media_type=media)
    raise HTTPException(404, "no recording with that id")


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


@app.on_event("shutdown")
async def shutdown() -> None:
    for person in list(PEOPLE.values()):
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
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args(argv)

    CONFIG["root"] = args.root
    CONFIG["dev_principal"] = args.dev_principal
    CONFIG["limit"] = None
    CONFIG["scribe"] = (_transcribe.Transcriber(args.transcribe)
                        if args.transcribe else None)
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
    config = uvicorn.Config(app, host=args.host, port=args.port,
                            log_level="info", timeout_graceful_shutdown=5)
    Server(config).run()
    return 0

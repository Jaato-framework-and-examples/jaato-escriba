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
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, Request, UploadFile    # noqa: E402
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse  # noqa: E402

import archive as _archive                                         # noqa: E402
import housekeeping as _housekeeping                               # noqa: E402
import workspace as _workspace                                     # noqa: E402

from .driver import Person                                         # noqa: E402
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
    stream.  So the snapshot goes out first and the conversation starts
    behind it.

    `Person(...)` does not await, so the check and the insert cannot
    interleave with another request on this loop: two tabs opening at
    once get one session, not two.
    """
    who = principal_of(request)
    person = PEOPLE.get(who)
    if person is None:
        # The readable name the proxy also sends, recorded once beside the
        # workspace so an operator can map a directory to a person.  It is
        # never the directory's name: `sub` is what survives a rename, and
        # an email in a path is a disclosure to anyone who can list the
        # root.
        identity = {"username": request.headers.get("x-forwarded-preferred-username"),
                    "email": request.headers.get("x-forwarded-email")}
        person = Person(who, root=Path(str(CONFIG["root"])), limit=CONFIG.get("limit"),
                        identity=identity)
        PEOPLE[who] = person
        person.hub.publish("state", person.snapshot())
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
        try:
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    # A comment keeps the connection warm through a proxy
                    # that closes idle streams; `EventSource` ignores it.
                    yield b": keepalive\n\n"
                if await request.is_disconnected():
                    break
        finally:
            person.hub.drop(queue)

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
        try:
            await person.talk(blob)
            yield frame("status", person.board.status())
        except Exception as exc:                      # noqa: BLE001
            yield frame("alert", {"kind": "connection", "detail": str(exc)[:200]})

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
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args(argv)

    CONFIG["root"] = args.root
    CONFIG["dev_principal"] = args.dev_principal
    CONFIG["limit"] = None
    if args.budget_file:
        try:
            CONFIG["limit"] = _housekeeping.ceiling(Path(args.budget_file))
        except _housekeeping.BudgetUnreadable as exc:
            print(f"escriba_web: {exc}", file=sys.stderr)
            return 2
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0

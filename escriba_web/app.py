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

from fastapi import FastAPI, HTTPException, Request, UploadFile    # noqa: E402
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse  # noqa: E402

import i18n                                                      # noqa: E402
import archive as _archive                                         # noqa: E402
import housekeeping as _housekeeping                               # noqa: E402
import transcribe as _transcribe                                   # noqa: E402
import staging as _staging                                        # noqa: E402
import workspace as _workspace                                     # noqa: E402

from .driver import FloorTaken, NothingHeard, NotReady, Person     # noqa: E402
from .guests import ROOM, Invitation, label, token  # noqa: E402
from .hub import frame                                             # noqa: E402

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
                        if closed is not None:
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
    """Open the door to one named colleague, and say so on the transcript.

    THE HOST SEES IT STANDING OPEN. The address is shown back as it was
    typed, next to a cancel: a wrong one then sits there visibly wrong
    rather than failing silently, which is the one failure mode of
    naming somebody in advance instead of admitting them on arrival.
    """
    person = await person_of(request)
    body = await request.json()
    email = str((body or {}).get("email") or "").strip()
    if "@" not in email:
        raise HTTPException(400, "an email address")
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
    person.invited(inv.email)
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
    return {"room": token(person.principal), "guests": _room_of(person)}


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
    rooms = [{"room": token(i.host), "host": PEOPLE[i.host].who,
              "since": i.since.isoformat(timespec="seconds")}
             for i in ROOM.waiting_for(email or "")
             # An invitation whose host has no live session is not an
             # invitation anywhere, and offering it would send the guest
             # to a 409 they can do nothing about. One already claimed by
             # somebody else is not theirs to see at all.
             if i.host in PEOPLE and i.guest in (None, principal)]
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
    try:
        rate = int(request.query_params.get("rate", ""))
        channels = int(request.query_params.get("channels", ""))
    except ValueError as exc:
        raise HTTPException(400, "rate and channels are integers") from exc
    if not (8000 <= rate <= 192000) or channels not in (1, 2):
        raise HTTPException(400, "not an audio shape this plays")
    pcm = await request.body()
    frame_bytes = channels * 2
    # WHOLE SAMPLES ONLY. The page reads the payload as one `Int16Array`,
    # so a trailing half sample would shift every value after it by a
    # byte and play as noise.
    if len(pcm) % frame_bytes:
        raise HTTPException(400, "a partial sample")
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

"""One person's conversation, driven by HTTP instead of a microphone.

WHAT CHANGES FROM THE TERMINAL DRIVER, and what deliberately does not.

Only the two ends move.  The microphone becomes an upload — the browser
records and POSTs, so nothing here opens `parec` — and the speaker
becomes the archive plus a reference the browser fetches, so nothing
here opens `paplay`.  Everything between them is `run_escriba`'s and is
IMPORTED rather than reimplemented: the turn with its completion gate and
its tolerance of a turn that never closes, the two-second disk
reconciliation, the enrichment observer.  A second copy of `_turn` would
be a second place for the nudge behaviour to drift.

`Relay` IS `voice.Tongue` WITH THE PLAYER REMOVED.  Same methods, same
order of work, same `recordings` contract — what is gone is the three
lines that feed and wait on PulseAudio, because on this path the person's
own browser is the speaker and this process has no audio device.

ONE OF THESE PER AUTHENTICATED PERSON.  The workspace, the session, the
archive and the hub all hang off this object, so two people share
nothing: not a transcript, not a memory store, not an audio file.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import re
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jaato_sdk import (ClientType, EventType, IPCClient,          # noqa: E402
                       IPCRecoveryClient)
from jaato_sdk.media_identity import ATTACHMENT_ID_KEY           # noqa: E402

import archive as _archive                                       # noqa: E402
import board as _board                                           # noqa: E402
import enrichment
import i18n                                                # noqa: E402
import memory                                                    # noqa: E402
from pulse_playback import _pcm_params                           # noqa: E402
import staging                                                   # noqa: E402
import voice                                                     # noqa: E402
import yaml                                                      # noqa: E402
import transcribe as _transcribe                                 # noqa: E402
import workspace as _workspace                                   # noqa: E402
from run_escriba import (BUDGET_ENDED, DRAIN, SessionGone,      # noqa: E402
                         _reconcile, _turn)

from . import kbwiki                                             # noqa: E402
from . import wiki as _wiki                                      # noqa: E402
from .hub import Hub                                             # noqa: E402
from .guests import ROOM, token as _token


#: The one tool the escriba has that reaches back into this process.
#:
#: SPANISH, AND NOT IN `i18n/`, for the persona's reason rather than the
#: greeting's: this is instruction to the model, never read by a person,
#: and a copy per locale would be drift with nothing to gain.  What the
#: escriba WRITES into it follows the person's language, because the
#: `speech` agent param already pins that for everything it says.
#:
#: IT TAKES NO DECISION AWAY FROM ANYBODY.  It cannot name a guest -- the
#: address is required and is matched against one the host typed, so the
#: model has nothing to invent -- and it cannot let anybody in.  All it
#: does is fill in the two fields the host is about to read and send.
JUSTIFY_TOOL = "justificar_invitado"
JUSTIFY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["email", "motivo", "tema"],
    "properties": {
        "email": {
            "type": "string",
            "description": "La direccion que la persona acaba de escribir, "
                           "copiada tal cual. No te la inventes ni la "
                           "corrijas: si no sabes cual es, no llames a esta "
                           "herramienta.",
        },
        "motivo": {
            "type": "string",
            "minLength": 1,
            "description": "En una linea, por que hace falta precisamente "
                           "esta persona: que puede aclarar que no este ya "
                           "claro. Lo va a leer ella antes de decidir si "
                           "entra, asi que escribelo para ella y no para la "
                           "persona que te lo pide. DI LO QUE HACE FALTA, "
                           "no lo que supones que esa persona sabe o ha "
                           "hecho: de ella no sabes nada, y afirmarselo a "
                           "la cara es inventarselo. «Hay una discrepancia "
                           "en las provisiones del cierre», no «tu "
                           "trabajaste en el cierre».",
        },
        "tema": {
            "type": "string",
            "minLength": 1,
            "description": "De que se la llama a hablar, en pocas palabras: "
                           "el asunto concreto, no «una duda».",
        },
    },
}


def _iso(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


def entry_json(e) -> Dict[str, Any]:
    return {"id": e.id, "at": _iso(e.at), "kind": e.kind, "text": e.text,
            "seconds": e.seconds, "audio": e.audio, "repeats": e.repeats,
            "silent": e.silent, "transcribing": e.transcribing,
            "links": list(getattr(e, "links", None) or []),
            "by": getattr(e, "by", "") or ""}


#: Which agents the page names.  The curator and the juez run too, but
#: neither answers the person: one consolidates afterwards and the other
#: judges search results.  Naming every profile would be a list of
#: machinery rather than a cue about who is talking.
SHOWN = ("escriba", "documentalista")


def engine_rows(summary) -> List[dict]:
    """What one profile is bound to, flattened for display.

    A profile binds exactly one provider and model — EXCEPT one with
    `model_tiers`, and the escriba is exactly that: `voz` is the tier the
    person hears and `escribano` is where it annotates and commissions
    documents, silently.  So "the model in place" is not one value for
    it, and showing a single name would be picking one of two and hiding
    the switch that explains why it sometimes goes quiet.

    `model_tiers` carries `initial` and `fallback` alongside the tiers
    themselves, so the tier entries are the mapping-valued ones.
    """
    get = (lambda k: summary.get(k) if isinstance(summary, dict)
           else getattr(summary, k, None))
    tiers = get("model_tiers") or {}
    rows = []
    for name, spec in tiers.items():
        if not isinstance(spec, dict):
            continue          # `initial` / `fallback` name a tier, are not one
        rows.append({"tier": name, "model": spec.get("model"),
                     "provider": spec.get("provider"),
                     "audible": bool((spec.get("modalities") or {}).get("audio")),
                     "initial": name == tiers.get("initial")})
    if not rows:
        rows.append({"tier": None, "model": get("model"),
                     "provider": get("provider"), "audible": False,
                     "initial": True})
    return rows


class WebBoard(_board.StateBoard):
    """The same conversation state, published instead of drawn.

    `StateBoard` already holds everything a view needs and calls
    `_refreshed()` after every change; the live display redraws its frame
    there, and this works out what actually moved and sends only that.

    THE STATUS IS DECIDED HERE, not in the browser.  The page is forbidden
    from inferring it from the entries, because an entry having audio says
    the file exists and not that anything is sounding — the confusion the
    terminal view had.  This maps the board's own flags, which are set by
    the driver as the turn happens.
    """

    def __init__(self, hub: Hub) -> None:
        super().__init__()
        self.hub = hub
        self._high = 0                 # the highest entry id published
        self._sig: Optional[tuple] = None
        self._counts: Optional[dict] = None
        self._status: Optional[dict] = None
        self._panels: Dict[str, int] = {}

    # -- what the page is told ------------------------------------------
    def status(self) -> dict:
        s = self.state
        if s.searching:
            return {"status": "searching", "query": s.searching}
        if s.speaking:
            return {"status": "speaking", "query": None}
        if s.thinking:
            return {"status": "thinking", "query": None}
        return {"status": "listening", "query": None}

    def counts(self) -> dict:
        s = self.state
        return {"curated": s.curated, "curated_at_start": s.curated_at_start,
                "raw": s.raw, "raw_at_start": s.raw_at_start,
                "catalogue": s.catalogue, "catalogue_at_start": s.catalogue_at_start,
                "documents_at_start": s.documents_at_start,
                "found": len(s.found), "selected": len(s.selected)}

    def snapshot(self, **extra) -> dict:
        s = self.state
        return {"entries": [entry_json(e) for e in s.entries], "counts": self.counts(),
                "items": dict(s.items), "status": self.status(),
                "archive_dir": s.archive_dir, **extra}

    @staticmethod
    def _draws(e) -> tuple:
        """What a row shows.  Anything else changing is not news."""
        return (e.id, e.text, e.audio, e.repeats, e.seconds, e.silent,
                e.transcribing, getattr(e, "by", ""))

    def _refreshed(self) -> None:
        s = self.state
        # EVERY NEW ENTRY, not just the last one.  `spoke()` adds the reply
        # AND its annotation before calling this once, and `anotado` is a
        # REQUIRED field of the escriba's completion schema — so publishing
        # only `entries[-1]` dropped the reply itself on every closed turn.
        # The browser got the annotation and nothing to listen to, which is
        # exactly how it was found.
        for e in s.entries:
            if e.id > self._high:
                self._high = e.id
                self._sig = self._draws(e)
                self.hub.publish("entry", entry_json(e))
        last = s.entries[-1] if s.entries else None
        if last is not None:
            # A row already sent can still change in place: a folded repeat
            # bumps its count, and audio arrives after the text.
            sig = self._draws(last)
            if sig != self._sig:
                self._sig = sig
                self.hub.publish("entry", entry_json(last))
        counts = self.counts()
        if counts != self._counts:
            self._counts = counts
            self.hub.publish("counts", counts)
        status = self.status()
        if status != self._status:
            self._status = status
            self.hub.publish("status", status)
        for panel, rows in s.items.items():
            mark = len(rows), repr(rows[:1])
            if self._panels.get(panel) != mark:
                self._panels[panel] = mark
                self.hub.publish("items", {"panel": panel, "rows": rows})


#: The wiki group's state when no MCP source is wired.  A DECLARED
#: absence: the panel says "sin conectar" rather than showing an empty
#: list, because an empty list and an unreachable server look the same to
#: a reader and mean opposite things.
#: A CODE, like every other state this panel reports, so the sentence a
#: person reads comes out of `i18n/` rather than out of this file.
WIKI_UNWIRED = {"state": "unwired", "rows": [], "detail": "",
                "asked": 0, "opened": 0, "failed": 0}


class NotReady(RuntimeError):
    """A turn was asked for before the session existed."""


class FloorTaken(RuntimeError):
    """Somebody else is already talking.

    REFUSED RATHER THAN QUEUED, once a conversation can hold three
    people. The turn lock has always serialised -- a second press while
    a turn is in flight would interleave two conversations and the model
    would answer the pair -- and waiting was the right answer while the
    only person who could collide with you was yourself, two tabs open.
    With two guests in an interview, waiting means a press that appears
    to do nothing and an answer arriving a minute later to a question
    somebody else has since asked. So the lock's holder is published,
    every page disables its own button while somebody else has it, and a
    press that races anyway is told so.
    """


class NothingHeard(RuntimeError):
    """The recording that arrived holds no audio.

    NAMED, because it used to be anonymous.  An empty blob reached
    `voice._to_mp3`, ffmpeg refused it, and the exception fell into
    `/talk`'s bare `except Exception` — which reports every unknown
    failure as a lost connection.  So a recording with nothing in it
    sent the person to look at their network, and the one occurrence we
    had to diagnose cost a trip to the server's journal.

    It carries no text: the page owns the words, in the person's own
    language, the same way it owns every other alert's.
    """


#: How long one turn may take before it is declared lost.
#:
#: NOT A PERFORMANCE LIMIT.  The slowest healthy turn on this path is
#: about eight seconds; this is three minutes.  It exists because a turn
#: can HANG rather than fail: on 2026-10-09 the daemon was restarted
#: under a running backend, and the client sat waiting on a socket
#: nobody was answering — no exception, no event, `/talk` never
#: returning, and a page reading "pensando…" until somebody restarted
#: the service.  A turn that cannot end on its own must end anyway.
TURN_TIMEOUT = 180.0


#: How much speech is gathered before a piece of it goes out, in seconds.
#: The provider streams in pieces far smaller than this, and one SSE
#: frame each would put hundreds of frames through a hub that drops a
#: reader 256 behind (`hub.BACKLOG`) — while half a second is under the
#: delay a person hears as a delay.
SPEECH_SECONDS = 0.5


class Relay:
    """`voice.Tongue` with PulseAudio replaced by the browser.

    THE FILE IS THE RECORD AND THE FRAMES ARE THE VOICE, and the
    difference is why both exist.  The archive still accumulates every
    chunk and still writes one file per utterance — model media is
    client-audience and this process is the client — but that file is
    named after the DIGEST OF THE WHOLE utterance, so its name cannot
    exist until the utterance is over.  A page that waits for it waits
    for the reply to finish before the reply starts, which is seconds of
    silence under a "hablando" pill and the one thing a voice assistant
    must not do.

    So the same headerless PCM the terminal feeds to PulseAudio is
    published to the person's own hub as it arrives, in `SPEECH_SECONDS`
    pieces, and the file stays exactly what it was: what the row replays,
    and what the manifest can verify.
    """

    def __init__(self, archive: Optional[_archive.Archive] = None,
                 hub: Optional[Hub] = None) -> None:
        self._archive = archive
        self._hub = hub
        self.recordings: List[dict] = []
        self.spoken: List[str] = []
        #: Per stream, the bytes gathered but not yet published.
        self._pending: Dict[str, bytes] = {}

    def speak(self, ev) -> None:
        payload = base64.b64decode(ev.data_b64)
        if self._archive is not None:
            self._archive.speaking(ev.stream_id, ev.mime_type, payload)
        # BEFORE the final marker below, which announces an utterance
        # that has already gone out in full.
        self._stream(ev, payload)
        if ev.final:
            if getattr(ev, "chunk", ""):
                self.spoken.append(ev.chunk)
            if self._archive is not None:
                kept = self._archive.spoke(ev.stream_id)
                if kept is not None:
                    self.recordings.append(kept)
                    if self._hub is not None:
                        # NAMES THE RECORDING THE LIVE VOICE WAS.  The
                        # page suppresses the autoplay of a file it has
                        # just heard, and the digest is the only name
                        # both halves can agree on: the live frames carry
                        # a stream id, the transcript row carries the sha.
                        self._hub.publish("speech", {
                            "stream": ev.stream_id, "final": True,
                            "audio": kept["sha"]})

    def _stream(self, ev, payload: bytes) -> None:
        """Publish this utterance's voice in `SPEECH_SECONDS` pieces.

        ONLY 16-BIT PCM, and not as a precaution: the page decodes
        `Int16Array` straight out of the frame, so a stream the mime
        describes as anything else is one it cannot play.  Saying nothing
        then is right — the archive's file is still written, the row
        still plays it, and that is precisely the behaviour this
        replaced.
        """
        if self._hub is None:
            return
        params = _pcm_params(ev.mime_type)
        if params is None or params["encoding"] != "s16le":
            return
        rate, channels = int(params["rate"]), int(params["channels"])
        frame = channels * 2
        #: A multiple of `frame` by construction, so every piece sent
        #: holds whole samples and the page can read it as one array.
        target = int(rate * SPEECH_SECONDS) * frame
        buffered = self._pending.pop(ev.stream_id, b"") + payload
        while len(buffered) >= target:
            self._send(ev.stream_id, buffered[:target], rate, channels)
            buffered = buffered[target:]
        if not ev.final:
            self._pending[ev.stream_id] = buffered
            return
        whole = len(buffered) - (len(buffered) % frame)
        if whole:
            self._send(ev.stream_id, buffered[:whole], rate, channels)

    def _send(self, stream_id: str, pcm: bytes, rate: int, channels: int) -> None:
        self._hub.publish("speech", {
            "stream": stream_id, "rate": rate, "channels": channels,
            "pcm": base64.b64encode(pcm).decode("ascii")})

    def last(self) -> str:
        text = " ".join(t.strip() for t in self.spoken if t.strip())
        self.spoken.clear()
        return text


def who(record: Dict[str, Any], principal: str) -> str:
    """The person's own name for themselves, most readable first.

    `username` and `email` are what the proxy asserted when the
    directory was provisioned, and EITHER CAN BE ABSENT: oauth2-proxy
    sends each only when it is configured to, and `person.json` records
    whatever arrived that day.  So the order is by how much a reader
    gets out of it, ending at the principal — which is the one thing
    always there, because no request is served without it.

    The principal is not a consolation prize, it is the honest answer.
    On a developer's machine it is whatever `--dev-principal` named and
    reads perfectly well; on the deployed host it is Keycloak's `sub`
    and reads like a UUID, which is exactly what a proxy that is not
    passing the name looks like.  Inventing a display name out of the
    email's local part would hide that, and the header would be lying
    about something a person can check.
    """
    return str(record.get("username") or record.get("email") or principal)


#: The raw account of a failure belongs to whoever runs this, not to
#: whoever is talking to it: `RunnerCallError: session.send_message
#: failed: ToolError: ...` reached a person's transcript once, truncated
#: at 90 characters and in English, and told them nothing.
log = logging.getLogger("escriba.driver")


class Person:
    """One authenticated person: a workspace, a session, a hub."""

    def __init__(self, principal: str, root: Path, limit: Optional[int] = None,
                 identity: Optional[dict] = None,
                 scribe: Optional["_transcribe.Transcriber"] = None,
                 wiki: Optional[dict] = None) -> None:
        #: Shared across everyone on this process: one loaded model, not
        #: one per person.  It is hundreds of megabytes.
        self.scribe = scribe
        self.principal = principal
        # The hub is handed `self.snapshot`, not a snapshot: it calls it
        # whenever a browser connects, so a reconnecting page is answered
        # with what is true then rather than what was true at open.
        # Passing the bound method here is safe before the attributes it
        # reads exist — nothing can subscribe until `Person(...)` returns.
        self.hub = Hub(self.snapshot)
        self.board = WebBoard(self.hub)
        #: Read BEFORE provisioning, because `provision` can already emit
        #: a note — the template refresh — and the first note of a
        #: session is the worst one to get in the wrong language.
        #: `caller_dir` rather than a second copy of the naming rule:
        #: the directory is not simply the principal.
        self.home = (root / _workspace.caller_dir(principal)).resolve()
        self.locale = _workspace.prefs(self.home).get("locale", i18n.DEFAULT)
        # The board exists first so the refresh can be SEEN.  A person's
        # profiles being replaced under them is a thing that happened,
        # and the transcript is where this driver says what happened.
        self.ws = _workspace.provision(
            principal, root=root, identity=identity,
            on_refresh=lambda what: self.board.note(
                self.say("note.assets_refreshed", what=", ".join(what))))
        #: Who the header says is connected.  Read from the record beside
        #: the workspace rather than from the request: the page is told
        #: the identity the directory was PROVISIONED for, which is the
        #: one an operator can map back to a directory, and which the
        #: session living inside cannot rewrite.
        #:
        #: AFTER `provision`, which is what writes that record.  Read
        #: before it — beside the locale, where this started — a person's
        #: FIRST session finds no file and falls through to the
        #: principal, and since one `Person` is kept per principal for
        #: the life of the process, their header would show a `sub`
        #: until the service restarted.  The locale is read early on
        #: purpose, because provisioning can already emit a note; this
        #: has no such reason and the order is the whole correctness.
        self.who = who(_workspace.identity(self.home), principal)
        #: The address the proxy ASSERTS for this person, as of their
        #: last request.  Not `who`, which is read from the record beside
        #: the workspace on purpose: that one is for showing, and the
        #: session living inside the workspace must not be able to
        #: rewrite it.  This one is for MATCHING, and it has to be the
        #: very value `Room.waiting_for` is given -- an invitation that
        #: `/invitacion` would offer on load and a push would not is the
        #: shape of bug this whole area keeps producing.
        self.email = ""
        self.archive = _archive.Archive(
            self.ws, limit=limit,
            on_full=lambda m: self.board.note(f"· {m}"))
        self.board.recording_to(str(self.archive.dir))
        self.opened = datetime.now()
        self._scribe = None
        self._stack: Optional[Any] = None
        self._ticker: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._turn_lock = asyncio.Lock()
        self._opening = asyncio.Lock()
        #: How full the window is, from ContextUpdatedEvent, and what the
        #: session has spent, from the daemon's own consumption report.
        #: Two questions with two sources, never merged into one number:
        #: `context` is measured against the shared history and belongs to
        #: no particular model, while spend is attributed per binding —
        #: and the escriba is a TIERED profile, so it has one history and
        #: several bills.
        #: The enrichment observer, which exists only while a session
        #: does.  Named here so an upload arriving BEFORE the first
        #: session can ask whether to reload the catalogue instead of
        #: raising AttributeError at the person.
        self._watcher = None
        #: WHAT THE ESCRIBA HAS NOT BEEN TOLD YET.
        #:
        #: A guest arriving or leaving is something the model has to know
        #: — it is about to hear a second voice, and later to stop
        #: hearing it — and `board.note` does not tell it: that publishes
        #: to the TRANSCRIPT, which is what the two people read.
        #:
        #: NOT ANNOUNCED AS A TURN OF ITS OWN, for the reason `stage`
        #: gives for not announcing a document: a turn spent saying
        #: "somebody arrived" answers a question nobody asked and puts
        #: the escriba's voice between the host and their own next
        #: sentence. So it waits here and rides the next real turn,
        #: whichever side takes it, which is exactly when it starts
        #: mattering.
        self._notices: List[str] = []
        #: WHO ELSE IS IN THE ROOM, while anybody is.
        #:
        #: Read for one purpose: every turn taken while this is set gets
        #: a row of its own in the manifest. The escriba reads the wiki
        #: with THIS person's grants whichever voice asked the question —
        #: the session holds one token and swapping it per turn would
        #: force an MCP reconnect mid-conversation — so that consequence
        #: cannot be enforced away. What can be done is make it
        #: ANSWERABLE afterwards: which reads happened while somebody
        #: else was in the room, and which of the two was speaking.
        self._guests: List[str] = []
        #: WHO HOLDS THE TURN, so every page can say so and disable its
        #: own button rather than letting a press do nothing. `None` is
        #: nobody, `""` is the owner, a name is that guest.
        self._floor: Optional[str] = None
        #: `{"url": ..., "dir": Path}` when the operator wired a kbwiki,
        #: `None` when they did not.  ABSENT IS OFF, with nothing
        #: defaulted: a URL this project invented would point the
        #: person's own credential at a host nobody chose.
        self.wiki = wiki
        #: The token last written into this person's environment file.
        #: Compared rather than re-written blindly, because every request
        #: carries one and a `reload_session_env` per request would
        #: reconnect the MCP server for no reason.
        self._token: Optional[str] = None
        #: The wiki pages this conversation opened.  Built whether or not
        #: a wiki is wired -- with none there are no calls to observe, so
        #: it stays empty and `wiki_state` reports the unwired state
        #: instead of an empty connected one, which mean opposite things.
        self._consulted = _wiki.Consulted(self.hub)
        self._window: Optional[dict] = None
        self._spend: Optional[dict] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -- lifecycle -------------------------------------------------------
    @property
    def env_file(self) -> Path:
        """The file the daemon reads this session's environment from.

        The workspace's own, until a kbwiki is wired -- then a private
        one COMPOSED from it, because the person's access token has to
        reach the MCP header and `session.reload_session_env` re-reads
        this file and nothing else.  `kbwiki` says why it is not the
        workspace's.
        """
        if self.wiki is None:
            return self.ws / ".env"
        return kbwiki.env_path(Path(self.wiki["dir"]), self.principal)

    @property
    def conn(self) -> dict:
        return dict(workspace_path=str(self.ws), env_file=str(self.env_file),
                    config_root=str(self.ws / ".jaato"), client_type=ClientType.API)

    async def credential(self, token: Optional[str]) -> None:
        """Take the access token this request arrived with, if it is new.

        EVERY REQUEST, because a request is the ONLY thing that produces
        a fresh one: oauth2-proxy renews the forwarded token when it
        handles a request, and a held-open `/events` stream is one
        request that it handles once.  So the token that matters is the
        one on the turn's OWN request -- `/talk` carries it moments
        before the escriba might call the wiki with it.

        A RELOAD ONLY WHEN IT CHANGED.  `reload_session_env` reopens the
        remote MCP servers whose headers read a var the reload touched,
        so doing it per request would drop and remake the kbwiki
        connection several times a turn.
        """
        if self.wiki is None or not token or token == self._token:
            return
        self._token = token
        await asyncio.to_thread(
            kbwiki.write_env, self.env_file,
            kbwiki.compose(self.ws / ".env", token))
        if self._scribe is None:
            return                      # it will be read when the session opens
        await self._scribe.client.reload_session_env()

    async def _consolidate(self) -> None:
        """Turn what was said into what will be remembered.

        WITHOUT THIS THE CONVERSATION IS NOT KEPT.  The scribe stores a
        memory raw; the curator is what judges it and writes
        `curated.jsonl`, and the next session's inventory is rendered
        from the store when the session is CREATED.  The terminal
        driver's last act is this, and the web driver's was nothing —
        measured on a real workspace after a real conversation: 2 raw, 0
        curated, so the escriba woke up knowing none of it.

        Run at BOTH ends, which is the terminal driver's arrangement and
        is not redundant: at close it costs nobody anything, and at open
        it catches the shutdown that never happened — a killed process, a
        crash, a machine that went to sleep.  Conditional on there being
        something, because unconditional it is seconds of silence to do
        nothing.
        """
        pending = memory.uncurated_count(self.ws)
        if not pending:
            return
        self.board.note(self.say("note.consolidating", n=pending))
        async with IPCClient.session(profile="curator", agent="curator",
                                     **self.conn) as curator:
            await curator.ask(DRAIN)
        held = memory.counts(self.ws)
        self.board.memories(held["curated"], held["raw"])
        self.board.note(self.say("note.consolidated"))

    def _wire_wiki(self) -> None:
        """This person's environment file and their workspace's `.mcp.json`.

        The server entry is written by US rather than copied from the
        template, because the URL is the operator's (`--kbwiki-url`) and
        a production host hard-coded into this repository would be a
        deployment nobody chose.  Rewritten every open so a changed flag
        reaches an existing workspace, which a template copy would not.
        """
        kbwiki.write_env(self.env_file,
                         kbwiki.compose(self.ws / ".env", self._token))
        (self.ws / ".mcp.json").write_text(
            json.dumps(kbwiki.server_entry(self.wiki["url"]), indent=2) + "\n",
            encoding="utf-8")

    def wiki_state(self) -> Dict[str, Any]:
        """What the `del wiki` tab is told, and WHICH ABSENCE it is.

        Two empties that mean opposite things: no wiki wired at all, and
        a wiki the escriba has not needed to open yet.  The first is a
        deployment that cannot reach the kbwiki; the second is a
        conversation that has not asked it anything.  The panel says
        which.
        """
        if self.wiki is None:
            return dict(WIKI_UNWIRED)
        return self._consulted.state()

    async def open(self) -> None:
        """Open the session and start the clock on this conversation.

        The drain runs BEFORE the scribe opens, exactly as the terminal
        driver does it and for its reason: the inventory is rendered when
        the session is created, so this is the only moment that can get
        last time's memories into today's greeting.
        """
        # BEFORE THE SESSION, both of them: the daemon reads `env_file`
        # when it bootstraps and the MCP plugin reads `.mcp.json` when it
        # loads, so a file written afterwards is a file this session
        # never sees.
        if self.wiki is not None:
            await asyncio.to_thread(self._wire_wiki)
        await self._consolidate()
        held = memory.counts(self.ws)
        self.board.memories(held["curated"], held["raw"])
        async with self._opening:
            await self._open_session()
        self._ticker = asyncio.create_task(_reconcile(self.board, self.ws, self._stop))
        self.hub.publish("state", self.snapshot())
        await _turn(self._scribe, self.say("agent.greeting"), None, self._relay(),
                    log=self.archive.turn, tui=self.board)
        self._settle()
        asyncio.create_task(self._measure())

    async def _open_session(self) -> None:
        """One session on a connection that can come back.

        THE RECOVERING CLIENT, because the daemon is restarted on every
        framework upgrade and this process is meant to outlive that.
        Plain `IPCClient` has no reconnect: on 2026-10-09 it held a dead
        socket and a dead session id across a restart, and two turns were
        recorded, archived, and delivered to nobody.

        REATTACHMENT IS NOT CONFIGURED, because it cannot be: the
        facade's `session()` does not accept a `RecoveryConfig`
        (`open_session() got an unexpected keyword argument 'config'`),
        so `reattach_session` keeps its default.  The recovery is
        therefore not trusted to produce a usable session by itself —
        across a daemon restart the old id may be gone — and two things
        cover it: a fresh session is opened when the connection returns,
        and a turn that hangs anyway is bounded by `TURN_TIMEOUT` and
        reopens the session on its way out.

        `auto_start=False`: on the deployed host the daemon is a
        root-managed service, and a tenant's web backend must never
        spawn a second one under its own account.
        """
        self._loop = asyncio.get_running_loop()
        self._stack = IPCRecoveryClient.session(
            profile="escriba", agent="escriba",
            # The speech rule travels as an agent param, so one persona
            # serves every language.  The persona itself stays in
            # Spanish deliberately: it is instruction to the model, never
            # read by the person, and a translated copy per locale would
            # be the drift this whole change exists to avoid.  Params
            # cross the wire as strings, which this is.
            agent_params={"speech": self.say("agent.speech")},
            auto_start=False,
            # The generator's own numbers (`jaato-scaffold new client
            # --recoverable`): the SDK's default connect timeout is 5 s
            # against a cold autostart of 30-60 s, and the session
            # confirmation needs room on a shared daemon.
            connect_timeout=120.0, session_timeout=60.0,
            # Before this stopped rebuilding on CONNECTED there could be
            # several clients alive at once, each with this same callback
            # attached and none of them removed.  There is now exactly
            # one at a time.
            # THE ONE WAY BACK IN. Passed to the facade rather than
            # registered by hand, for two reasons that are both in the
            # SDK and neither in `explain` (filed as jaato#1639).
            # `convenience.py:897` registers after connect and BEFORE
            # `create_session`, which is the only ordering the
            # runner-tier model sees; and `recovery.py:1798` re-registers
            # them on the fresh inner client after a reconnect, so the
            # tool survives a daemon restart. By hand, it would work
            # until the first restart and then be gone with no error on
            # either side — just an escriba that stops calling a tool it
            # can no longer see.
            client_tools=[{"name": JUSTIFY_TOOL,
                           "description":
                               "Di por que hace falta la persona que te "
                               "acaban de nombrar y de que se la llama a "
                               "hablar. Lo lee ella antes de decidir si "
                               "entra, y lo lee primero quien te lo pide.",
                           "parameters": JUSTIFY_SCHEMA,
                           "handler": self._justify}],
            on_status_change=self._connection, **self.conn)
        self._scribe = await self._stack.__aenter__()
        self.archive.identify(self._scribe.session_id,
                              getattr(self._scribe.client, "client_id", None))
        self._watcher = enrichment.Observer(self.conn, self.ws, board=self.board)
        self._watcher.attach(self._scribe.client)
        self._consulted.attach(self._scribe.client)
        self._scribe.client.subscribe(EventType.CONTEXT_UPDATED, self._context)

    def _connection(self, status) -> None:
        """The transport's state changed, reported from the SDK's thread.

        Hopped onto this loop before anything is touched: everything it
        affects — the board, the hub, the session — belongs to the loop.
        """
        state = getattr(getattr(status, "state", None), "name", None) or str(status)
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._connection_changed, state)

    def _connection_changed(self, state: str) -> None:
        """Say what happened.  Do not act on it.

        THIS USED TO REBUILD THE SESSION and that was the defect.  On a
        daemon restart (2026-10-09 17:10) the old backend opened FOUR IPC
        connections and created three sessions where one was wanted, then
        the daemon logged 184 broken-pipe sends to the two it had been
        streaming to, and held SessionManager._lock for 1374 ms fighting
        a socket nobody was reading.

        Two mistakes, one cause.  `IPCRecoveryClient` OWNS the connection
        and restores it — that is the whole reason it is used here — and
        this handler built a second client, and therefore a second
        connection, on top of the one the SDK had just brought back.  And
        every such client installed this same callback while the previous
        one's was never removed, so a stale CONNECTED from an abandoned
        client started the next rebuild: two connections became four.

        So the transport's state is now REPORTED and nothing else.  The
        session is replaced only when a turn proves it dead — `SessionGone`
        and `TURN_TIMEOUT` both already do that, and were written for
        exactly this — which costs the first turn after a restart and
        cannot cost a second connection, because nothing here opens one.

        `self._scribe` is deliberately NOT cleared.  Clearing it made
        `talk()` answer "still waking" to every press, and with the
        rebuild gone nothing would ever set it again: the person would be
        told to wait forever.  The session is left in place precisely so
        the next turn can fail against it and trigger the repair.
        """
        if state in ("RECONNECTING", "DISCONNECTED", "CLOSED"):
            self.board.note(self.say("note.connection_lost"))

    def _ended(self, over: SessionGone) -> None:
        """The session is over.  Say which ending it was, and repair it.

        THE DAEMON'S OWN WORD, COMPARED — never searched for.  This used
        to decide by looking for "budget", "exhaust" or "presupuesto" in
        the exception's text, and on 2026-10-12 the text was
        `RunnerCallError: session.send_message failed: ToolError:
        session.send_message: model loop`, which contains none of them.
        So a ceiling was reported as a transport fault AND the branch
        below ran, opening a fresh session with a fresh budget: the one
        thing its own comment forbids. `Session.terminus.reason` is the
        daemon's `SessionTerminatedEvent.reason`, carried here by
        `SessionGone`, and `budget_exhausted` is a value to equal.

        THE RAW TEXT GOES TO THE LOG, not to the person.  They cannot act
        on `ToolError: session.send_message: model loop` and it is not in
        their language; whoever runs this can act on it and needs all of
        it, not ninety characters of it.
        """
        budget = over.reason == BUDGET_ENDED
        log.warning("the session ended (reason=%s, details=%r): %s",
                    over.reason or "none reported", over.details, over)
        self.hub.publish("alert", {
            "kind": "budget" if budget else "session",
            "at": datetime.now().strftime("%H:%M"),
            # The reason as the daemon named it, or the honest absence of
            # one. The budget alert does not read this; the other does.
            "detail": over.reason or self.say("alert.no_reason")})
        if budget:
            # NOT REPAIRED.  That session ended because a declared ceiling
            # was reached, and quietly opening another one with a fresh
            # budget answers a limit by ignoring it.
            return
        # AND REPAIR IT, which this did not used to do.  The transport
        # handler no longer rebuilds the session when the connection
        # returns — that was opening a second connection every time — so
        # a turn failing is now the ONLY thing that replaces a dead
        # session.  `TURN_TIMEOUT` already did it for a turn that hangs;
        # a `SessionGone` fails fast instead, and without this the next
        # press would fail the same way forever.
        self._scribe = None
        asyncio.create_task(self._resume())

    async def restart(self) -> bool:
        """A new session, because the person ASKED for one.

        THE ONLY WAY BACK FROM A CEILING, and the reason this exists at
        all. `_ended` refuses to repair a budget ending on purpose —
        reopening one by itself answers a limit by ignoring it — and
        nothing else will: a page reload hands back the same cached
        `Person`, and `PEOPLE.pop` runs only for one that failed to
        open. Without this the ceiling would be enforced by making the
        escriba unusable until the service restarted, which is not a
        limit, it is a wall.

        RECORDED, AND ONLY IF IT HAPPENED. A row written before the
        session exists would say somebody chose to spend again on a
        session that never opened. The manifest is what can answer, a
        month later, that this person was told a ceiling had been
        reached and decided to carry on — the transcript is a
        400-entry window and cannot.

        AND IT CONSOLIDATES FIRST, which is the whole difference between
        a new session and a new session that knows anything. The scribe
        stores memories RAW; the curator is what judges them, and the
        inventory a session is given is rendered from the store at
        CREATE. Reopening without this hands the person an escriba that
        has forgotten the conversation they were just having — the exact
        state `_consolidate` records having been measured once already,
        "2 raw, 0 curated, so the escriba woke up knowing none of it".
        `open` does it in this same order and for this same reason.

        It is NOT the verbatim transcript, and should not be: carrying
        that into the new session means replaying history, which is what
        made the session expensive enough to reach a ceiling. The
        archive keeps the words for the person; the model gets what the
        curator judged worth keeping.

        Only here and not in `_resume`: that one fires from a background
        task after a dropped connection, often mid-sentence, and its
        value is being fast and quiet. This one happens because somebody
        read an alert and pressed a button.
        """
        await self._consolidate()
        self._scribe = None
        if not await self._resume("note.session_restarted"):
            return False
        self.archive.turn(stage="session-restarted", source="person")
        return True

    async def _resume(self, note: str = "note.session_new") -> bool:
        """Put a session back, and say whether there is one now.

        The note is the caller's, because the two callers are not the
        same event: one is a repair nobody asked for and the other is a
        person pressing a button.
        """
        async with self._opening:
            if self._scribe is not None:
                return True
            try:
                await self._close_session()
                await self._open_session()
            except Exception as exc:                          # noqa: BLE001
                self.board.note(self.say("note.reopen_failed",
                                         what=f"{type(exc).__name__}: {str(exc)[:90]}"))
                return False
        self.board.note(self.say(note))
        self.hub.publish("state", self.snapshot())
        return True

    async def _close_session(self) -> None:
        """Let go of a session, whether or not the far end still exists."""
        stack, self._stack, self._scribe = self._stack, None, None
        if stack is None:
            return
        try:
            await stack.__aexit__(None, None, None)
        except Exception as exc:                          # noqa: BLE001
            # SAID, not swallowed.  A stack that fails to close is a
            # connection the daemon keeps streaming to: on 2026-10-09 two
            # abandoned ones drew 184 broken-pipe sends and a 1374 ms
            # hold on the daemon's session lock.  Still not raised — we
            # are usually here because the far end is already gone, and a
            # close failure must not stop the caller — but no longer
            # invisible.
            self.board.note(self.say("note.close_failed",
                                     what=f"{type(exc).__name__}: {str(exc)[:90]}"))

    def declared(self, agent: str) -> dict:
        """What a profile file binds, for an agent with no session yet.

        The `_base_*` tier is PROVIDER-AGNOSTIC by this repo's own design
        — "Model and provider live in profiles/<set>/<agent>.yaml" — so
        the set file answers it outright and no merge is involved.
        """
        chosen = _workspace.profile_set(self.ws)
        path = self.ws / ".jaato" / "profiles" / chosen / f"{agent}.yaml"
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def engines(self) -> List[dict]:
        """Who is answering, and with what.

        TWO SOURCES, FOR TWO DIFFERENT QUESTIONS, and not a fallback
        chain for one.  The escriba HAS a session, so the honest answer
        is what that session froze: `profile_snapshot`, sealed by the
        daemon, post-merge.  The documentalista has none — it is spawned
        as a subagent when a document is wanted — so the only thing that
        can be said is what its profile declares.

        NOT `session.profiles`, which looked like the right answer and is
        not: measured against this daemon it returns 25 profiles, the
        workspace's four `_base_*` plus 21 from the user tier, and NOT
        the set profiles the deployment actually runs.  The overlay is
        applied when a session is created and not when that list is
        built, so a picker made from it cannot offer `escriba` at all.
        """
        rows = []
        if self._scribe is not None:
            frozen = _archive.frozen_profile(self.ws, self._scribe.session_id)
            if frozen:
                rows.append({"agent": "escriba", "rows": engine_rows(frozen)})
        for agent in SHOWN:
            if any(r["agent"] == agent for r in rows):
                continue
            rows.append({"agent": agent, "rows": engine_rows(self.declared(agent))})
        # The transcriber is part of what answers too, even though it is
        # not an agent and names no profile: it is what turns the
        # person's own voice into the words on their row, and a reader
        # comparing a transcript against what they remember saying needs
        # to know which model read it — `tiny` and `small` are not the
        # same witness.  Its absence is reported rather than omitted: a
        # row that simply is not there says nothing about whether
        # transcription is off or broken.
        scribe = self.scribe
        rows.append({"agent": "transcripción", "rows": [
            {"tier": None,
             "model": scribe.model_name if scribe else "desactivada",
             "provider": "faster-whisper · local" if scribe else None,
             "audible": False, "initial": True}]})
        return rows

    def _context(self, ev) -> None:
        """How full the window is, as the daemon measures it.

        Delivered on this loop — the SDK calls subscribers there, which
        is why `enrichment.Observer` can `create_task` from one — so the
        hub is published to directly.

        `tokens_remaining` AND `percent_used` ARE ALREADY NET of what the
        request reserves for its own output (jaato#1444): the window a
        provider will accept input into is `context_limit` minus that
        reservation, not `context_limit`.  So the reservation is carried
        through and shown rather than quietly subtracted, because "43% of
        128 000" beside "58 240 used" is arithmetic a reader cannot make
        work and will assume is a bug.
        """
        reserved = getattr(ev, "reserved_output_tokens", 0) or 0
        limit = getattr(ev, "context_limit", None)
        self._window = {
            "percent_used": getattr(ev, "percent_used", None),
            "tokens_remaining": getattr(ev, "tokens_remaining", None),
            "context_limit": limit,
            # The limit the percentage is actually against.  Derived here
            # rather than in the page: one definition, jaato#1444's.
            "effective_limit": (limit - reserved) if limit else None,
            "reserved_output": reserved or None,
            "turns": getattr(ev, "turns", None),
        }
        self.hub.publish("consumo", self.consumption())

    async def _measure(self) -> None:
        """What the session has spent, from the daemon's accounting.

        NOT RE-DERIVED FROM TURN EVENTS, which is the trap this avoids.
        `UsageBreakdown.spend_*` and `cost_usd` on `ContextUpdatedEvent`
        and `TurnCompletedEvent` are PER-TURN — billed across that turn's
        responses — not session-cumulative, despite the name.  Summing
        them client-side drifts the moment one event is missed, and
        rendering one as a session total is wrong by however many turns
        the person has had.  `get_diagnostics()` answers the daemon's own
        `get_consumption()`, which is where spend already accumulates.

        KEPT VERBATIM.  The report is one dict with its own rules — three
        disjoint input buckets, `cost_source` beside any cost, dimensions
        OMITTED rather than zeroed when nothing measured them — and a
        client that reshapes it is a second opinion about what the
        numbers mean.  The page reads the keys that are there.

        A failure leaves the previous reading in place: an empty panel
        reads as "nothing spent", which is a lie a stale figure does not
        tell.
        """
        scribe = self._scribe
        if scribe is None:
            return
        try:
            answer = await scribe.client.get_diagnostics()
        except Exception:                                     # noqa: BLE001
            return            # a reading we could not take is not a zero
        consumption = getattr(answer, "consumption", None)
        if not consumption:
            return            # absent is not zero, here as everywhere
        self._spend = consumption
        self.hub.publish("consumo", self.consumption())

    def consumption(self) -> dict:
        """The two readouts, kept apart because they answer differently."""
        return {"window": self._window, "spend": self._spend}

    def _settle(self) -> None:
        """Close the books on a turn that produced no speech.

        A `spoke` entry with no audio means one of two opposite things,
        and only the driver knows which: DURING a turn the provider is
        still generating and the audio is on its way; AFTER it, nothing
        is coming.  The escriba has a silent tier by design — `escribano`
        is where it annotates and commissions documents, and its own
        description says so — so a turn answered from there sounds
        nothing, ever.  Observed live: a reply whose row sat on "audio
        llegando…" forever while the manifest recorded `spoke: null`.
        """
        for e in reversed(self.board.state.entries):
            if e.kind != "spoke":
                continue
            if e.audio is None and not e.silent:
                e.silent = True
                self.board._refreshed()
            return

    def _relay(self) -> Relay:
        return Relay(archive=self.archive, hub=self.hub)

    def holdings(self) -> dict:
        """What a reset would move aside, counted before it is offered.

        COUNTS FIRST, which is `_forget`'s rule and the reason it has
        one: "erase everything" and "erase the nineteen things you told
        me over three weeks" are the same command and very different
        decisions. The recordings are counted separately because they
        are the half nothing can reconstruct -- a memory can be told
        again, a conversation cannot be re-had -- so the number has to
        be in front of the person before they type the word.
        """
        held = memory.counts(self.ws)
        catalogue = self.ws / enrichment.CATALOGUE
        audio = self.ws / _archive.ROOT
        return {
            "curated": held["curated"], "raw": held["raw"],
            "references": (len(list(catalogue.glob("auto-*.json")))
                           if catalogue.is_dir() else 0),
            "recordings": (len([f for f in audio.rglob("*")
                                if f.is_file() and f.suffix != ".jsonl"])
                           if audio.is_dir() else 0),
            "days": (len([d for d in audio.iterdir() if d.is_dir()])
                     if audio.is_dir() else 0),
        }

    async def scratch(self) -> Path:
        """Everything this escriba has, moved aside in ONE rename.

        "FROM SCRATCH, ABSOLUTELY" means the workspace, not a list of
        things to remember to clear. Memories, references, documents,
        recordings, the manifest, staged files, preferences and the
        authored assets all live under it, so a rename takes all of them
        at once and cannot take half. A reset assembled out of six
        deletions is a reset that grows a seventh thing it does not know
        about.

        MOVED, NEVER UNLINKED, which is `_forget`'s rule and is worth
        more here than there: this takes the recordings too, and those
        are the only copy of what was actually said. The tree is
        renamed beside itself and the path is returned, so the person
        can be told where their old escriba went.

        `person.json` SURVIVES, because it lives one level up: identity
        is what the proxy asserts and what an operator maps a directory
        back to a person with, not something this person is choosing to
        forget.

        NOT CONSOLIDATED ON THE WAY OUT. `close` normally runs the
        curator so the session is remembered; here it is about to be
        moved aside, and spending a model turn to curate memories into
        a tree nobody will read again is work for nothing.
        """
        await self.close(consolidate=False)
        return await asyncio.to_thread(self._aside)

    def _aside(self) -> Path:
        """The rename itself, off the loop, and refusing to collide."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        aside = self.ws.with_name(f"{self.ws.name}.olvidado-{stamp}")
        # A SECOND RESET IN THE SAME SECOND would otherwise rename one
        # tree ON TOP of another on some platforms. Rare, and the cost
        # of being wrong is somebody's only copy of a conversation.
        n = 0
        while aside.exists():
            n += 1
            aside = self.ws.with_name(f"{self.ws.name}.olvidado-{stamp}-{n}")
        self.ws.rename(aside)
        return aside

    async def close(self, consolidate: bool = True) -> None:
        self._stop.set()
        if self._ticker is not None:
            await self._ticker
        if getattr(self, "_watcher", None) is not None:
            await self._watcher.drain()
        await self._close_session()
        if not consolidate:
            return
        # With the conversation closed and nobody waiting, the curator.
        await self._consolidate()

    def _room(self, stage: str, who: str, note: str, tell: str = "",
              **fields) -> None:
        """One change in who is in the room: recorded, shown, and told.

        THREE AUDIENCES, ONE CALL, because they must never be able to
        disagree about who was here. The MANIFEST gets a durable row —
        the transcript is a 400-entry window that a restart does not
        keep, so it cannot answer "who was in this conversation?" a month
        later and the manifest can. The TRANSCRIPT gets a line, because
        the host has to see it while it is happening. And the ESCRIBA
        gets told on its next turn, because it is about to hear a second
        voice.

        `source` names what asserted this, the way the playback claim
        does: an invitation is the host naming somebody and the proxy
        confirming who turned up, not something this process observed.
        """
        self.archive.turn(stage=stage, guest=who, source="invitation", **fields)
        self.board.note(self.say(note, who=who))
        if tell:
            self._notices.append(self.say(tell, who=who))

    def drafting(self, email: str) -> None:
        """The host typed an address.  Nothing has been sent to anybody.

        THE ESCRIBA IS NOT TOLD HERE, unlike every other transition. It
        is about to be ASKED, directly, in a turn of its own — a notice
        riding the next turn saying "the host named somebody" would reach
        it after the question it is meant to prepare, or alongside it,
        and say nothing it is not being handed anyway.
        """
        self.archive.turn(stage="guest-drafted", guest=email,
                          source="invitation")
        self.board.note(self.say("note.drafting", who=email))

    def justified(self, email: str, motivo: str, tema: str) -> None:
        """The escriba said why this person is wanted, and what for.

        RECORDED BEFORE THE HOST HAS SENT IT, because what was written
        and what was sent are different facts and the gap between them
        is the interesting one: a justification the host read and chose
        not to send is exactly the case this flow exists to allow.
        """
        self.archive.turn(stage="guest-justified", guest=email,
                          motivo=motivo, tema=tema, source="invitation")
        self.board.note(self.say("note.justified", who=email, tema=tema))
        self._told_who()

    def invited(self, email: str) -> None:
        """The host read the justification and sent it.  Nobody is here yet."""
        self._room("guest-invited", email, "note.inviting")

    def guest_here(self, who: str, who_id: str = "") -> None:
        """Somebody else is in the room, until they are not.

        ONE CALL PER TRANSITION, and that is the whole point of the
        method existing: being in the room and being recorded as being
        in the room were two calls, and a caller that made one without
        the other would leave the manifest saying the opposite of the
        truth. A test found exactly that before a person could.
        """
        if who not in self._guests:
            self._guests.append(who)
        self._room("guest-joined", who, "note.guest_here",
                   "agent.guest_joined", who_id=who_id)
        self._told_who()

    def guest_gone(self, who: str, stage: str = "guest-left") -> None:
        """One of them is not here any more, whichever way it ended.

        NAMED, never "the guest": with two people in an interview, the
        one who leaves when the other stays is the whole reason this
        takes an argument.
        """
        if who in self._guests:
            self._guests.remove(who)
        self._room(stage, who, "note.guest_left", "agent.guest_left")
        self._told_who()

    def _told_who(self) -> None:
        """Publish the room, so every page shows the same one.

        THE INVITATIONS TRAVEL TOO, and they did not before. The band
        used to be filled in from the reply to the host's own `POST
        /invitar` and from nowhere else, which was survivable while
        typing an address was the only thing that ever changed one: a
        second tab showed an empty band, and so did the first one after
        a reconnect. It stops being survivable the moment a field is
        filled in by a MODEL, seconds later and on nobody's request —
        there is no reply for that to arrive in.
        """
        self.hub.publish("room", {"present": list(self._guests),
                                  "floor": self._floor,
                                  "guests": self.invitations()})

    def invitations(self) -> List[dict]:
        """Everybody this host has named, in whatever state."""
        return [i.json() for i in ROOM.of_host(self.principal)]

    def _justify(self, args: dict) -> dict:
        """The escriba answering the question the host's click asked.

        RUNS ON THIS LOOP. The SDK delivers subscribers here — which is
        what `_context` already relies on and says so — and a host tool
        is dispatched from one, so the room, the board and the hub are
        touched directly rather than hopped onto. `on_status_change` is
        the callback that arrives on another thread, not this.

        A REFUSAL IS RAISED rather than returned in a field the model may
        not read: the framework turns the exception into the tool call's
        error result, which is the channel a refused call has. Each cause
        is told apart, unlike the guest routes' single 403 — there the
        caller is somebody uninvited who must learn nothing, here it is
        the escriba, which has to know what to do differently.
        """
        email = str(args.get("email") or "").strip().lower()
        held = ROOM.of_address(self.principal, email)
        if held is None:
            raise ValueError(
                f"no hay ninguna invitacion a «{email}». La direccion es la "
                "que ha escrito la persona; copiala tal cual de lo que te he "
                "dicho y no la corrijas.")
        if held.open:
            raise ValueError(
                f"la invitacion a «{email}» ya se envio, y lo que lea quien "
                "la reciba no cambia despues. No hay nada que justificar.")
        inv = ROOM.justify(self.principal, email,
                           str(args.get("motivo") or ""),
                           str(args.get("tema") or ""))
        if inv is None:
            raise ValueError(
                "hacen falta los dos: «motivo» y «tema». Quien lo lea tiene "
                "que poder decidir si entra, y «te necesitamos» sin asunto "
                "no le dice nada.")
        self.justified(inv.email, inv.motivo, inv.tema)
        # NOTHING IS ECHOED BACK. This used to hand `motivo` and `tema`
        # to the model so it could read them out -- which it did not
        # need, having just composed them, and which cost two things.
        #
        # Measured on the deployment 2026-10-12 (session 20261012_091640):
        # the references enrichment appends its hint block to the
        # WORDIEST STRING FIELD of a tool result (jaato#922's text-view
        # rule), so the stored result's `tema` came back carrying
        # "⟦JAATO⟧ **Reference sources available** — use selectReferences
        # … @auto-…". The persona asks the escriba to say out loud what
        # it put, so a result it reads back from is a result whose
        # framework hints it can read aloud to the person.
        #
        # The guest was never at risk -- the offer renders
        # `Invitation.motivo`, set from the CALL ARGS above and never
        # from the result -- but a short answer removes the anchor
        # instead of relying on that.
        return {"listo": True, "email": inv.email}

    async def ask_why(self, email: str) -> None:
        """Ask the escriba why this person is needed, as a turn of its own.

        A TURN, AND DELIBERATELY SO. `stage` argues against spending a
        turn announcing a document, because that answers a question
        nobody asked. This is the opposite case: the host pressed a
        button whose whole meaning is "say why", so a turn is the answer
        to a question they did ask, and the escriba's reply belongs in
        the transcript beside it.

        UNDER THE SAME LOCK AND THE SAME FLOOR as a spoken turn. It is
        the host's own action, so it holds the floor as the host — and
        without the lock it would race an utterance into one history.
        """
        if self._scribe is None:
            # NOT a silent pass: the host is looking at an address with
            # nothing under it, and the reason has to be on the
            # transcript where they are already looking.
            self.board.note(self.say("note.no_justification", who=email))
            return
        async with self._turn_lock:
            # `""`, THE HOST'S OWN MARKER, and not their display name.
            # `talk` holds the floor as `by or ""`, and the page compares
            # `S.floor !== (S.guest ? S.who : "")` — so holding it as
            # "dani" would disable dani's own button and tell them that
            # dani was talking, while a guest saw the right thing. The
            # host asked for this turn; it is theirs.
            with self._holding(""):
                lines = self._notices
                self._notices = []
                try:
                    await asyncio.wait_for(
                        _turn(self._scribe,
                              "\n".join(lines + [self.say("agent.justify_guest",
                                                          who=email)]),
                              None, self._relay(),
                              log=self.archive.turn, tui=self.board),
                        timeout=TURN_TIMEOUT)
                except SessionGone as over:
                    # NOBODY IS AWAITING THIS. `/invitar` fires it and
                    # returns, so an exception here has no caller to
                    # reach and would surface as an unretrieved task.
                    # The alert and the repair ARE the answer; the host
                    # is looking at an address with nothing under it and
                    # the band already says the escriba is writing.
                    self._ended(over)

    def seen_as(self, email: Optional[str]) -> None:
        """Record the address the proxy asserted on this request.

        Taken from EVERY request, for the reason `credential` is: the
        proxy is the only thing that produces it, and a person whose
        address changed upstream must not keep being matched on the old
        one for as long as this process lives.
        """
        self.email = (email or "").strip().lower()

    def floor(self, who: Optional[str]) -> None:
        """Who is talking, or nobody.  Published, because three can race."""
        self._floor = who
        self._told_who()

    @contextlib.contextmanager
    def _holding(self, who: str):
        """The floor, for exactly as long as this block runs."""
        self.floor(who)
        try:
            yield
        finally:
            self.floor(None)

    # -- a turn ----------------------------------------------------------
    async def talk(self, blob: bytes, by: str = "") -> None:
        """One spoken turn, from the browser's recording.

        `by` NAMES SOMEBODY WHO IS NOT THE OWNER of this escriba — a
        guest the host invited in to supply a detail. It reaches the
        model as the turn's only text, beside the audio, because an
        escriba told nothing would answer a second voice as if the
        person it knows had changed their mind mid-interview. It reaches
        the transcript for the same reason in reverse: both windows, and
        the manifest afterwards, have to be able to say which voice was
        which.

        TRANSCODED HERE, to the format the model path already takes: the
        browser hands us webm/opus from `MediaRecorder` and the attachment
        the daemon has been fed since this project started is MP3 at
        32 kbit/s mono.  `voice._to_mp3` is that encoder, reused rather
        than copied, so both paths produce the same bytes and the archive's
        recompute-the-digest recipe keeps holding.

        SERIALISED PER PERSON. A second press while a turn is in flight
        would interleave two conversations in one session, and the model
        would answer the pair.
        """
        # REFUSED BEFORE THE LOCK: nothing about an empty recording needs
        # a turn, a session or a place in the queue behind one.
        if not blob:
            raise NothingHeard()
        # THE FLOOR IS TAKEN, OR IT IS NOT, decided before the lock is
        # awaited rather than by waiting on it.
        #
        # The lock has always serialised, and waiting was right while
        # the only person who could collide with you was yourself with
        # two tabs open. Once two guests are in an interview, waiting
        # means a press that looks like it did nothing and an answer
        # arriving to a question somebody else has since replaced. Each
        # page disables its own button while somebody else holds the
        # floor; this is for the press that races anyway.
        #
        # YOUR OWN SECOND PRESS STILL QUEUES, which is the behaviour the
        # lock was given for and the page already prevents: nothing is
        # changed for a person talking to their own escriba.
        speaker = by or ""
        if self._turn_lock.locked() and self._floor != speaker:
            raise FloorTaken(self.say("note.floor_taken",
                                      who=self._floor or self.who))
        async with self._turn_lock:
            if self._scribe is None:
                # NOT A FAULT, AND IT SHOULD NOT READ LIKE ONE.  Opening
                # takes a consolidation pass, a session and a greeting —
                # tens of seconds on a cold daemon — and the page is up
                # and usable throughout, so a press during that window is
                # the most ordinary thing a person can do.  It was
                # reported as a connection failure, which sends somebody
                # to look at the network.
                raise NotReady("el escriba todavía está despertando; "
                               "inténtalo de nuevo en unos segundos")
            # HELD BY A CONTEXT MANAGER, not by a pair of calls, and
            # taken after the one refusal that needs no turn.
            #
            # `_to_mp3` and `archive.heard` both sit inside here and
            # both can raise, so releasing it in the turn's own
            # `finally` left the floor held by somebody whose
            # recording never became a turn: a room in which nobody
            # can speak again, with a reload the only remedy. One
            # entry, one exit, every path.
            with self._holding(speaker):
                data = await asyncio.to_thread(voice._to_mp3, blob)
                seconds = len(data) / 4000.0      # 32 kbit/s mono, by construction
                said = {"mime_type": voice.UTTERANCE_MIME, "data": data,
                        "display_name": "utterance.mp3", "seconds": seconds,
                        ATTACHMENT_ID_KEY: self.archive.heard(data)}
                # Popped the way the terminal driver pops it: `seconds` is for
                # the view and the manifest, and an unknown key on an
                # attachment is not something to send to the daemon.
                self.board.heard(said.pop("seconds"), said.get(ATTACHMENT_ID_KEY),
                                 by=by)
                said["seconds"] = seconds
                # Started BEFORE the turn and never waited for: the model is
                # about to take several seconds and the transcriber takes a
                # few of its own, so they overlap and the person waits for
                # neither.  The row updates when it lands, by id.
                self._transcribing(self.board.state.entries[-1],
                                   said[ATTACHMENT_ID_KEY])
                try:
                    # THE ONLY TEXT A SPOKEN TURN CARRIES, and until there
                    # could be two voices in one conversation there was none:
                    # who is speaking, and whatever has happened in the room
                    # that the escriba has not been told about yet. Drained
                    # here rather than accumulated, so a notice is delivered
                    # once and the next turn is clean again.
                    # THE TRACE FOR THE ONE THING NOT ENFORCED. A guest's
                    # question reaches kbwiki with the HOST's grants, and
                    # the host's question does too while a guest is
                    # listening to the answer. Neither can be prevented
                    # without reconnecting MCP between turns, so both are
                    # recorded: the manifest is append-only and in order, so
                    # a row here sits immediately before the turn's own and
                    # says who was present and who spoke.
                    if self._guests:
                        self.archive.turn(stage="guest-present",
                                          guests=list(self._guests),
                                          speaker=by or "host",
                                          grants="host", source="invitation")
                    lines = self._notices
                    self._notices = []
                    if by:
                        lines = lines + [self.say("agent.guest_speaks", who=by)]
                    await asyncio.wait_for(
                        _turn(self._scribe, "\n".join(lines),
                              said, self._relay(),
                              log=self.archive.turn, tui=self.board),
                        timeout=TURN_TIMEOUT)
                    self._settle()
                    asyncio.create_task(self._measure())
                except asyncio.TimeoutError:
                    # Nothing came back and nothing failed.  The recording is
                    # archived either way; what must not happen is the page
                    # waiting on it forever, so the session is dropped and
                    # reopened and the person is told to try again.
                    self.board.note(self.say("note.turn_timeout", secs=int(TURN_TIMEOUT)))
                    self._scribe = None
                    asyncio.create_task(self._resume())
                    raise NotReady(self.say("note.no_answer"))
                except SessionGone as over:
                    self._ended(over)
                    raise

    async def stage(self, filename: str, data: bytes,
                    by: str = "") -> Dict[str, Any]:
        """Put a document the person uploaded in front of the escriba.

        THE FILE IS STAGED WHETHER OR NOT A SESSION IS UP.  Writing it
        and declaring it are disk work in the person's own workspace; a
        session that has not opened yet reads the catalogue when it does,
        so an upload during the opening seconds is not a lost upload.
        What needs a live session is only the RELOAD, and that is why it
        is conditional rather than awaited blindly.

        NOT ANNOUNCED TO THE ESCRIBA AS A TURN.  Staging is not a
        question: the person uploads a document and then says what they
        want done with it, in their own words, when they are ready.
        Spending a turn on "a document has arrived" would answer a
        question nobody asked and put the escriba's voice between the
        person and their own next sentence.
        """
        row = await asyncio.to_thread(staging.stage, self.ws, filename, data)
        if self._watcher is not None:
            # Same reason as the judge's finds: the plugin reads the
            # catalogue when the session opens, so an entry written
            # mid-conversation does not exist for it until this runs.
            await self._watcher.reload()
        # The panel itself needs nothing: `_reconcile` reads the
        # catalogue every couple of seconds and the row arrives with the
        # next tick, by the same route as everything else in it.
        self.board.note(self.say("note.staged_by", name=row["text"], who=by)
                        if by else self.say("note.staged", name=row["text"]))
        return row

    def _transcribing(self, entry, att: str) -> None:
        """Put the person's own words on their row, when they are ready.

        A FAILURE HERE IS NOT A FAILED TURN.  The conversation does not
        depend on the transcript: the recording is archived, the model
        heard it, and the answer is already on its way.  So this reports
        and gives up rather than propagating — the row simply keeps the
        play control it already had.
        """
        if self.scribe is None:
            return
        recording = self.archive.dir / f"in_{att}.mp3"
        # Said BEFORE the work starts, so the row shows it is waiting for
        # words rather than looking like an utterance that has none.
        entry.transcribing = True
        self.board._refreshed()

        async def run() -> None:
            try:
                # This person's language, not the host's: the model is
                # shared, the language is not.
                text = await asyncio.to_thread(self.scribe.write, recording,
                                               self.locale)
            except Exception as exc:                          # noqa: BLE001
                self.board.note(f"· no pude transcribir {att}: "
                                f"{type(exc).__name__}: {str(exc)[:90]}")
                text = None
            # Cleared on EVERY path.  A row left waiting after the
            # transcriber has given up is the same defect as "audio
            # llegando…" on a turn that produced none.
            entry.transcribing = False
            if text:
                entry.text = text
            self.board._refreshed()

        asyncio.create_task(run())

    # -- what a new browser is handed ------------------------------------
    def say(self, key: str, **params) -> str:
        """One string in this person's language."""
        return i18n.t(self.locale, key, **params)

    def speak(self, locale: str) -> str:
        """Change the language, and redraw everything in it.

        Mid-conversation is allowed, and nothing is warned about: the
        recalled memories and the archive stay in whatever language they
        were written in, which is accepted rather than prevented.

        Only the CHROME re-renders.  Notes already in the transcript keep
        the words they were written with, because the transcript is a
        record of what was said at the time, not a view that re-renders
        into the language of the moment.
        """
        self.locale = locale if locale in i18n.available() else i18n.DEFAULT
        _workspace.set_pref(self.home, "locale", self.locale)
        self.hub.publish("state", self.snapshot())
        return self.locale

    def snapshot(self) -> dict:
        return self.board.snapshot(session_at=_iso(self.opened),
                                   who=self.who,
                                   locale=self.locale,
                                   locales=[{"code": c,
                                             "name": i18n.t(c, "locale.name"),
                                             "flag": i18n.t(c, "locale.flag")}
                                            for c in i18n.available()],
                                   # The whole catalogue travels WITH the
                                   # snapshot rather than being fetched:
                                   # a language change then arrives by
                                   # the same route as everything else
                                   # the page redraws from, and there is
                                   # no window where the strings and the
                                   # data disagree.
                                   strings=i18n.catalogue(self.locale),
                                   playback="navegador", days=self.days(),
                                   engines=self.engines(),
                                   wiki=self.wiki_state(),
                                   # How to link ONE wiki page, with
                                   # `{id}` where the node id goes.  The
                                   # page builds the href from it rather
                                   # than knowing anything about kbwiki's
                                   # routes; `None` and a node is shown
                                   # as its id, which is the honest
                                   # rendering of a thing with no address.
                                   wiki_page=(self.wiki or {}).get("page"),
                                   # The upload ceiling travels to the
                                   # page so the refusal it shows and
                                   # the refusal the server makes are
                                   # one number, declared once.
                                   stage_mb=staging.MAX_BYTES // (1024 * 1024),
                                   # Who the host has named, and how far
                                   # each one has got. Here as well as in
                                   # the `room` frame so a reconnect
                                   # redraws the band instead of blanking
                                   # it -- which is what it did, and
                                   # nobody noticed while the band was
                                   # only ever filled in by the reply to
                                   # the host's own POST.
                                   #
                                   # NOT in `guest_snapshot`. These are
                                   # other people's addresses and the
                                   # host's reasons for wanting them, and
                                   # that snapshot is built key by key
                                   # precisely so a key nobody added
                                   # cannot leak.
                                   guests=self.invitations(),
                                   # WHICH VOICE IS ITS OWN.  Live
                                   # microphone frames carry the speaker,
                                   # so a browser can decline to play its
                                   # owner back at them; comparing names
                                   # would mute one person to another
                                   # whenever two are called the same
                                   # thing, and sending principals would
                                   # hand each party the other's
                                   # identifier for nothing.
                                   self_id=_token(self.principal),
                                   # Who is in the room and who is
                                   # talking, so a reconnecting page
                                   # comes back to the same room rather
                                   # than an empty one.
                                   present=list(self._guests),
                                   floor=self._floor,
                                   consumo=self.consumption())

    def guest_snapshot(self, principal: str, name: str) -> dict:
        """What a guest's browser is told, which is deliberately little.

        BUILT NARROW RATHER THAN RENDERED NARROW.  The host's own
        snapshot carries the whole conversation and the contents of the
        memory, references and documents panels — their second brain,
        which is the most private thing this application holds. A guest
        page that received all that and merely declined to draw it would
        have it anyway, sitting in a browser on somebody else's desk.
        So the keys are listed here, by hand, and the ones that are not
        listed never leave this process.

        NOTHING FROM BEFORE THEY ARRIVED.  `entries` is empty, not
        trimmed: a host arms the door to ask one question, and that is a
        decision about the next five minutes rather than about
        everything already said. What the guest needs to know, the host
        can say out loud — which is what anybody does when they bring
        somebody into a meeting.
        """
        return {"entries": [], "counts": {}, "items": {}, "days": [],
                "engines": [],
                # The pills still have to work: a guest watching an
                # escriba think needs to see that it is thinking.
                "status": self.board.status(),
                "guest": True, "host": self.who, "who": name,
                "self_id": _token(principal),
                # WHO ELSE IS HERE, which a guest in an interview is
                # meant to know: the host called two people in so that
                # one could correct the other, and somebody answering
                # into a room without being told who is listening has
                # not been given the chance to decide what to say.
                "present": list(self._guests),
                "floor": self._floor,
                # The host's language, not the guest's: the conversation
                # is in it, the escriba speaks it, and the guest has no
                # session here whose preference could be read.
                "locale": self.locale,
                "strings": i18n.catalogue(self.locale),
                # No picker. A guest changes nothing about the host's
                # session, and the language is the host's.
                "locales": [],
                "playback": "navegador",
                "stage_mb": staging.MAX_BYTES // (1024 * 1024)}

    def voice(self, stream: str, pcm: bytes, rate: int, channels: int,
              by: str, by_id: str) -> None:
        """One piece of a live microphone, on its way to everybody else.

        The same frame the escriba's own voice travels in — `Relay._send`
        publishes exactly this shape — so the page decodes both with one
        scheduler and a second playback path never has to exist. What is
        added is who is speaking: `by` to show, `by_id` so a browser can
        recognise its own voice and decline to play it.
        """
        self.hub.publish("speech", {
            "stream": stream, "rate": rate, "channels": channels,
            "pcm": base64.b64encode(pcm).decode("ascii"),
            "by": by, "by_id": by_id})

    def voice_end(self, stream: str, by_id: str) -> None:
        """That microphone stopped.  Ends the stream the pieces built."""
        self.hub.publish("speech", {"stream": stream, "final": True,
                                    "by_id": by_id})

    def day(self, date: str) -> List[dict]:
        """A past day's conversation, read back out of the manifest.

        THE MANIFEST IS THE RECORD, and this is what it was for: the
        transcript keeps 400 entries in memory and a restart keeps none,
        while `audio/<session>/manifest.jsonl` has one appended row per
        turn with the ids of both halves.  Reading it back is the only
        way a person can see a conversation they had last week.

        WHAT IT CANNOT GIVE BACK, stated rather than faked: the `note`
        lines.  Searching, judging, a document being written — those are
        this driver's own commentary, never written to the manifest,
        because the manifest records what the CONVERSATION did.  A
        reconstructed day is the talking, and nothing claims otherwise.

        Ids are NEGATIVE.  Live entries count up from 1 and a reloaded
        day must never collide with one — the page patches rows by id,
        and a collision would have a search note overwritten by a reply
        from last Tuesday.
        """
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise ValueError("a date, as YYYY-MM-DD")
        stamp = date.replace("-", "")
        rows: List[dict] = []
        root = self.ws / _archive.ROOT
        for d in sorted(root.glob(f"{stamp}_*")) if root.is_dir() else []:
            manifest = d / "manifest.jsonl"
            if not manifest.is_file():
                continue
            with manifest.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        rows.extend(self._turn_entries(json.loads(line), d))
        rows.sort(key=lambda r: r["at"])
        for n, row in enumerate(rows, start=1):
            row["id"] = -n
        return rows

    @staticmethod
    def _turn_entries(row: dict, archive_dir: Path) -> List[dict]:
        """One manifest row, as the entries it describes."""
        if row.get("stage"):
            return []            # policy, budget, a playback claim: not talk
        at = row.get("at") or ""
        out = []
        if row.get("heard"):
            out.append({"at": at, "kind": "said",
                        "text": _transcribe.spoken(
                            archive_dir / f"in_{row['heard']}.mp3") or "",
                        "seconds": row.get("heard_seconds"),
                        "audio": row["heard"], "repeats": 1, "silent": False})
        spoke = row.get("spoke") or {}
        if row.get("transcript") or spoke:
            out.append({"at": at, "kind": "spoke", "text": row.get("transcript") or "",
                        "seconds": spoke.get("seconds"), "audio": spoke.get("sha"),
                        "repeats": 1, "silent": not spoke})
        if row.get("anotado"):
            out.append({"at": at, "kind": "anotado", "text": row["anotado"],
                        "seconds": None, "audio": None, "repeats": 1, "silent": False})
        return out

    def days(self) -> List[dict]:
        """Every day that has an archive, and whether it is in the view.

        Read from `audio/`, which is the durable record: the transcript
        holds 400 entries and the person may well have talked on days that
        fell out of it.  A day the view still holds opens where it is; a
        day only the archive has is labelled `archivo` and READ BACK on
        click, through `/day/{date}` and `day()` below.  `in_view` is
        therefore about which of the two routes the page takes, not about
        whether a day can be reached at all — including TODAY, whose
        earlier conversation is an archive like any other once the
        session that held it has gone.
        """
        in_view: Dict[str, int] = {}
        for e in self.board.state.entries:
            key = e.at.strftime("%Y-%m-%d")
            in_view[key] = in_view.get(key, 0) + 1
        out = []
        root = self.ws / _archive.ROOT
        for d in sorted(root.iterdir()) if root.is_dir() else []:
            if not d.is_dir() or not (d / "manifest.jsonl").is_file():
                continue
            key = d.name[:8]
            key = f"{key[:4]}-{key[4:6]}-{key[6:8]}"
            in_view.setdefault(key, 0)
        for key, count in sorted(in_view.items(), reverse=True):
            out.append({"date": key, "count": count, "in_view": count > 0})
        return out

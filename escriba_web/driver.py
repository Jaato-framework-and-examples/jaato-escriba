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
import json
import re
import sys
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
from run_escriba import DRAIN, SessionGone, _reconcile, _turn   # noqa: E402

from . import kbwiki                                             # noqa: E402
from . import wiki as _wiki                                      # noqa: E402
from .hub import Hub                                             # noqa: E402
from .guests import token as _token


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
WIKI_UNWIRED = {"state": "sin conectar", "rows": [],
                "detail": "no hay ninguna fuente MCP configurada para el wiki"}


class NotReady(RuntimeError):
    """A turn was asked for before the session existed."""


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
        #: WHO ELSE IS IN THE ROOM, while somebody is.
        #:
        #: Read for one purpose: every turn taken while this is set gets
        #: a row of its own in the manifest. The escriba reads the wiki
        #: with THIS person's grants whichever voice asked the question —
        #: the session holds one token and swapping it per turn would
        #: force an MCP reconnect mid-conversation — so that consequence
        #: cannot be enforced away. What can be done is make it
        #: ANSWERABLE afterwards: which reads happened while somebody
        #: else was in the room, and which of the two was speaking.
        self._guest: Optional[str] = None
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

    async def _resume(self) -> None:
        async with self._opening:
            if self._scribe is not None:
                return
            try:
                await self._close_session()
                await self._open_session()
            except Exception as exc:                          # noqa: BLE001
                self.board.note(self.say("note.reopen_failed",
                                         what=f"{type(exc).__name__}: {str(exc)[:90]}"))
                return
        self.board.note(self.say("note.session_new"))
        self.hub.publish("state", self.snapshot())

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

    async def close(self) -> None:
        self._stop.set()
        if self._ticker is not None:
            await self._ticker
        if getattr(self, "_watcher", None) is not None:
            await self._watcher.drain()
        await self._close_session()
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

    def invited(self, email: str) -> None:
        """The host named somebody.  Nobody is here yet."""
        self._room("guest-invited", email, "note.inviting")

    def guest_here(self, who: str, who_id: str = "") -> None:
        """Somebody else is in the room, until they are not.

        ONE CALL PER TRANSITION, and that is the whole point of the
        method existing: being in the room and being recorded as being
        in the room were two calls, and a caller that made one without
        the other would leave the manifest saying the opposite of the
        truth. A test found exactly that before a person could.
        """
        self._guest = who
        self._room("guest-joined", who, "note.guest_here",
                   "agent.guest_joined", who_id=who_id)

    def guest_gone(self, who: str = "", stage: str = "guest-left") -> None:
        """They are not here any more, whichever way it ended."""
        who = who or self._guest or ""
        self._guest = None
        if who:
            self._room(stage, who, "note.guest_left", "agent.guest_left")

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
                if self._guest is not None:
                    self.archive.turn(stage="guest-present",
                                      guest=self._guest,
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
            except SessionGone as exc:
                # NAME THE REASON WE HAVE, not the one that sounds likely.
                # `SessionGone` covers an RPC closing, a session
                # terminating and a session not being found — see
                # `run_escriba._GONE` — and this used to report every one
                # of them as "se alcanzó el límite de presupuesto".  A
                # budget abort says so in its own text; anything else is
                # reported as what it was, because sending somebody to
                # raise a ceiling that was never reached wastes the one
                # piece of evidence they had.
                text = str(exc)
                budget = any(w in text.lower() for w in ("budget", "exhaust", "presupuesto"))
                self.hub.publish("alert", {
                    "kind": "budget" if budget else "session",
                    "at": datetime.now().strftime("%H:%M"), "detail": text[:200]})
                if not budget:
                    # AND REPAIR IT, which this did not used to do.  The
                    # transport handler no longer rebuilds the session
                    # when the connection returns — that was opening a
                    # second connection every time — so a turn failing
                    # is now the ONLY thing that replaces a dead
                    # session.  `TURN_TIMEOUT` already did it for a turn
                    # that hangs; a `SessionGone` fails fast instead,
                    # and without this the next press would fail the
                    # same way forever.
                    #
                    # Not after a BUDGET ending: that session ended
                    # because a declared ceiling was reached, and
                    # quietly opening another one with a fresh budget
                    # answers a limit by ignoring it.  The alert stands
                    # and the person decides.
                    self._scribe = None
                    asyncio.create_task(self._resume())
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

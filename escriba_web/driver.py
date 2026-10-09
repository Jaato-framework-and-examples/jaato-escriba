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
import voice                                                     # noqa: E402
import yaml                                                      # noqa: E402
import transcribe as _transcribe                                 # noqa: E402
import workspace as _workspace                                   # noqa: E402
from run_escriba import DRAIN, SessionGone, _reconcile, _turn   # noqa: E402

from .hub import Hub                                             # noqa: E402


def _iso(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


def entry_json(e) -> Dict[str, Any]:
    return {"id": e.id, "at": _iso(e.at), "kind": e.kind, "text": e.text,
            "seconds": e.seconds, "audio": e.audio, "repeats": e.repeats,
            "silent": e.silent, "transcribing": e.transcribing}


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
                e.transcribing)

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


def wiki_references() -> Dict[str, Any]:
    """References the person may see in the kbwiki, listed LIVE.

    NOTHING FROM THE WIKI IS WRITTEN INTO THE WORKSPACE, and that is the
    design rather than an omission.  The wiki serves each identity only
    what it has been granted, and a copy on disk outlives the grant: a
    node revoked tomorrow would still sit in `.jaato/references/` and the
    references plugin would go on offering its content to the model.
    Listing live means a revocation fails closed, with no expiry pass to
    write and nothing to get wrong.

    THIS IS THE SEAM.  The MCP tools are not connected yet; until they
    are, the state is reported and no rows are invented.  When they land,
    this returns `{"state": "ok", "rows": [...]}` with each row carrying
    `origin: "wiki"` — and the local path above stays untouched, because
    the two never meet on disk.
    """
    return dict(WIKI_UNWIRED)


class NotReady(RuntimeError):
    """A turn was asked for before the session existed."""


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


class Relay:
    """`voice.Tongue` with the speaker taken out.

    The player is the only thing removed: the archive still accumulates
    every chunk and still writes one file per utterance, because model
    media is client-audience and this process is the client.  What the
    browser plays is that file, fetched afterwards by its id.
    """

    def __init__(self, archive: Optional[_archive.Archive] = None) -> None:
        self._archive = archive
        self.recordings: List[dict] = []
        self.spoken: List[str] = []

    def speak(self, ev) -> None:
        payload = base64.b64decode(ev.data_b64)
        if self._archive is not None:
            self._archive.speaking(ev.stream_id, ev.mime_type, payload)
        if ev.final:
            if getattr(ev, "chunk", ""):
                self.spoken.append(ev.chunk)
            if self._archive is not None:
                kept = self._archive.spoke(ev.stream_id)
                if kept is not None:
                    self.recordings.append(kept)

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
                 scribe: Optional["_transcribe.Transcriber"] = None) -> None:
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
        self._window: Optional[dict] = None
        self._spend: Optional[dict] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -- lifecycle -------------------------------------------------------
    @property
    def conn(self) -> dict:
        return dict(workspace_path=str(self.ws), env_file=str(self.ws / ".env"),
                    config_root=str(self.ws / ".jaato"), client_type=ClientType.API)

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

    async def open(self) -> None:
        """Open the session and start the clock on this conversation.

        The drain runs BEFORE the scribe opens, exactly as the terminal
        driver does it and for its reason: the inventory is rendered when
        the session is created, so this is the only moment that can get
        last time's memories into today's greeting.
        """
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
        return Relay(archive=self.archive)

    async def close(self) -> None:
        self._stop.set()
        if self._ticker is not None:
            await self._ticker
        if getattr(self, "_watcher", None) is not None:
            await self._watcher.drain()
        await self._close_session()
        # With the conversation closed and nobody waiting, the curator.
        await self._consolidate()

    # -- a turn ----------------------------------------------------------
    async def talk(self, blob: bytes) -> None:
        """One spoken turn, from the browser's recording.

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
            self.board.heard(said.pop("seconds"), said.get(ATTACHMENT_ID_KEY))
            said["seconds"] = seconds
            # Started BEFORE the turn and never waited for: the model is
            # about to take several seconds and the transcriber takes a
            # few of its own, so they overlap and the person waits for
            # neither.  The row updates when it lands, by id.
            self._transcribing(self.board.state.entries[-1],
                               said[ATTACHMENT_ID_KEY])
            try:
                await asyncio.wait_for(
                    _turn(self._scribe, "", said, self._relay(),
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
                                   wiki=wiki_references(),
                                   consumo=self.consumption())

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

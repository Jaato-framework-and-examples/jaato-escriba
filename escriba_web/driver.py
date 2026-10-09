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

from jaato_sdk import ClientType, IPCClient                      # noqa: E402
from jaato_sdk.media_identity import ATTACHMENT_ID_KEY           # noqa: E402

import archive as _archive                                       # noqa: E402
import board as _board                                           # noqa: E402
import enrichment                                                # noqa: E402
import memory                                                    # noqa: E402
import voice                                                     # noqa: E402
import yaml                                                      # noqa: E402
import workspace as _workspace                                   # noqa: E402
from run_escriba import DRAIN, GREETING, SessionGone, _reconcile, _turn  # noqa: E402

from .hub import Hub                                             # noqa: E402


def _iso(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


def entry_json(e) -> Dict[str, Any]:
    return {"id": e.id, "at": _iso(e.at), "kind": e.kind, "text": e.text,
            "seconds": e.seconds, "audio": e.audio, "repeats": e.repeats,
            "silent": e.silent}


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
        return (e.id, e.text, e.audio, e.repeats, e.seconds, e.silent)

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


class Person:
    """One authenticated person: a workspace, a session, a hub."""

    def __init__(self, principal: str, root: Path, limit: Optional[int] = None) -> None:
        self.principal = principal
        self.ws = _workspace.provision(principal, root=root)
        self.hub = Hub()
        self.board = WebBoard(self.hub)
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
        self.board.note(f"· {pending} memorias en crudo — consolidando")
        async with IPCClient.session(profile="curator", agent="curator",
                                     **self.conn) as curator:
            await curator.ask(DRAIN)
        held = memory.counts(self.ws)
        self.board.memories(held["curated"], held["raw"])
        self.board.note("· consolidado")

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
        self._stack = IPCClient.session(profile="escriba", agent="escriba", **self.conn)
        self._scribe = await self._stack.__aenter__()
        self.archive.identify(self._scribe.session_id,
                              getattr(self._scribe.client, "client_id", None))
        self._watcher = enrichment.Observer(self.conn, self.ws, board=self.board)
        self._watcher.attach(self._scribe.client)
        self._ticker = asyncio.create_task(_reconcile(self.board, self.ws, self._stop))
        self.hub.publish("state", self.snapshot())
        await _turn(self._scribe, GREETING, None, self._relay(), log=self.archive.turn,
                    tui=self.board)
        self._settle()

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
        return rows

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
        if self._stack is not None:
            await self._stack.__aexit__(None, None, None)
            self._stack = self._scribe = None
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
                raise RuntimeError("the session is not open")
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
            try:
                await _turn(self._scribe, "", said, self._relay(),
                            log=self.archive.turn, tui=self.board)
                self._settle()
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
                raise

    # -- what a new browser is handed ------------------------------------
    def snapshot(self) -> dict:
        return self.board.snapshot(session_at=_iso(self.opened),
                                   playback="navegador", days=self.days(),
                                   engines=self.engines(),
                                   wiki=wiki_references())

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
                        rows.extend(self._turn_entries(json.loads(line)))
        rows.sort(key=lambda r: r["at"])
        for n, row in enumerate(rows, start=1):
            row["id"] = -n
        return rows

    @staticmethod
    def _turn_entries(row: dict) -> List[dict]:
        """One manifest row, as the entries it describes."""
        if row.get("stage"):
            return []            # policy, budget, a playback claim: not talk
        at = row.get("at") or ""
        out = []
        if row.get("heard"):
            out.append({"at": at, "kind": "said", "text": "",
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
        fell out of it.  A day the view still holds is clickable; the rest
        say `archivo`, because loading them back is not built yet and a
        button that does nothing is worse than a label that explains.
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

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
import workspace as _workspace                                   # noqa: E402
from run_escriba import GREETING, SessionGone, _reconcile, _turn  # noqa: E402

from .hub import Hub                                             # noqa: E402


def _iso(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


def entry_json(e) -> Dict[str, Any]:
    return {"id": e.id, "at": _iso(e.at), "kind": e.kind, "text": e.text,
            "seconds": e.seconds, "audio": e.audio, "repeats": e.repeats}


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
        return (e.id, e.text, e.audio, e.repeats, e.seconds)

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

    async def open(self) -> None:
        """Open the session and start the clock on this conversation.

        The uncurated drain that the terminal driver runs before greeting
        is deliberately NOT here: it blocks for seconds and the browser is
        already on screen waiting. It belongs to `close`, where the same
        work costs nobody anything.
        """
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
            except SessionGone as exc:
                self.hub.publish("alert", {"kind": "budget", "at": datetime.now().strftime("%H:%M"),
                                           "detail": str(exc)[:200]})
                raise

    # -- what a new browser is handed ------------------------------------
    def snapshot(self) -> dict:
        return self.board.snapshot(session_at=_iso(self.opened),
                                   playback="navegador", days=self.days())

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
            if key not in in_view:
                in_view.setdefault(key, 0)
        for key, count in sorted(in_view.items(), reverse=True):
            out.append({"date": key, "count": count, "in_view": count > 0})
        return out

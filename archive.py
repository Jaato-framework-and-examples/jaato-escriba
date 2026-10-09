"""The audio archive: what was heard, what was said, and a record tying
both to the transcript.  Nothing here talks to the SDK.

WHY THIS EXISTS AT ALL.  Audio is CLIENT-audience by construction — the
daemon says so (`jaato_session.py:8719`): *"the model produced it, so
replaying it back into the model's own history would be both redundant
and, for audio, meaningless"*.  It reaches us, it plays, and unless we
write it down it is gone.  The same holds inbound: the framework mints an
id for an utterance *"so the caller knows the id it sent, and can name the
file it archived"* (`media_identity.py`).  Both halves are the client's to
keep, and the client is this repo.

WHAT AN AUDITOR ASKS, and what each piece answers:

    "produce the recording this turn consumed"   -> in_att_<id>.mp3
    "prove it is the one the transcript names"   -> recompute the digest
    "produce what the caller actually heard"     -> out_<n>_<sha>.wav
    "prove THAT is the one you claim"            -> recompute the digest
    "show me the turn"                           -> manifest.jsonl

THE TWO HALVES ARE IDENTIFIED DIFFERENTLY, and the difference is not
cosmetic.  An inbound id is a DIGEST of the bytes, so a `.wav` and a
transcript naming `att_9f2c…` can be matched by anyone holding both, with
no mapping to trust.  Outbound media carries `model:<agent>:<n>` — a
POSITION, not content, and the counter restarts each session.  Nothing
about that name can be recomputed from a recording, so this module hashes
the reassembled audio itself and records the digest, giving the outbound
half the same recompute-from-the-recording property the inbound half has
by construction.

IT IS NOT SWEPT BY `--forget`.  That erases memories and references, which
is the point of it; an audit archive a `--forget` erases is not an audit
archive.  The recordings live outside `.jaato/` entirely, so the forget
path cannot reach them even by accident.
"""
from __future__ import annotations

import hashlib
import json
import wave
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

#: The mime grammar for headerless PCM lives in the player, which is the
#: module that had to learn it first.  Imported rather than re-implemented:
#: two parsers for one wire format drift, and the vendored file is stable.
from pulse_playback import _pcm_params

#: Where recordings go.  At the WORKSPACE ROOT, not under `.jaato/`.
#:
#: `.jaato/` is the framework's namespace — profiles, agents, scripts,
#: completion schemas, the memory store, the session journals.  These
#: recordings are not framework assets: they are CLIENT-owned, kept
#: because model media is client-audience and nobody else will keep it.
#: They sit beside `docs/`, which is the same kind of thing from the same
#: reasoning — output this repo produces and owns.
#:
#: It also puts the audit archive outside every path `--forget` touches,
#: which is a property rather than a coincidence: that flag erases
#: `.jaato/memory` and `.jaato/references`, and an archive it could reach
#: would not be an archive.
ROOT = Path("audio")


#: How a conversation's directory is named, and the name `housekeeping`
#: parses an age out of.  Defined here because this module makes the name.
STAMP = "%Y%m%d_%H%M%S"


def _unused(path: Path) -> Path:
    """`path`, or the first free `path_1`, `path_2`, … beside it."""
    if not path.exists():
        return path
    for n in range(1, 1000):
        candidate = path.with_name(f"{path.name}_{n}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"{path}: a thousand archives in one second")


def _bytes_under(path: Path) -> int:
    """What a person's recordings occupy today."""
    if not path.is_dir():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def frozen_profile(workspace: Path, session_id: str) -> Optional[dict]:
    """The resolved profile a session froze at creation, or None.

    `profile_snapshot` is *"the RESOLVED profile the session actually ran
    under, frozen at creation"* (`session_manager.py:14232`), sealed by
    the daemon and carrying every field post-merge.  It is the only
    client-reachable answer to "what is this session actually running",
    and it answers several questions — the retention clocks below, and
    which model is behind the voice.
    """
    record = workspace / ".jaato" / "sessions" / f"{session_id}.json"
    try:
        snapshot = json.loads(record.read_text(encoding="utf-8")).get(
            "profile_snapshot")
    except (OSError, json.JSONDecodeError):
        return None
    return snapshot if isinstance(snapshot, dict) else None


def clocks(workspace: Path, session_id: str) -> Optional[dict]:
    """The RESOLVED retention this session runs under, or None.

    Read out of the session record the daemon sealed, whose
    `profile_snapshot` is *"the RESOLVED profile the session actually ran
    under, frozen at creation"* (`session_manager.py:14232`) and carries
    `record_keeping` post-merge — after `inherits:` and
    most-restrictive-wins.  That matters because the clocks are what the
    housekeeping sweep is allowed to act on, and the merge rule is the
    framework's: restating it here would be a second statement of it.

    Two outcomes that must not be confused, because they mean opposite
    things for a sweep: `resolved: false` is "the snapshot could not be
    read", and nothing may be deleted on a guess; `resolved: true` with a
    null block is "the profile declares no retention", which the framework
    reads as nothing having promised to keep it.  Collapsing the two into
    one absence is how a housekeeping pass deletes an archive it was
    simply unable to ask about.
    """
    snapshot = frozen_profile(workspace, session_id)
    if snapshot is None:
        return {"resolved": False, "record_keeping": None}
    keeping = snapshot.get("record_keeping")
    return {"resolved": True,
            "record_keeping": keeping if isinstance(keeping, dict) else None}


def digest(data: bytes) -> str:
    """The id scheme the framework mints inbound, computed for any bytes.

    Deliberately the same shape and the same arithmetic as
    `jaato_sdk.media_identity.mint_attachment_id` — sha256, first 16 hex
    characters, `att_` prefix — so an auditor uses ONE recipe for both
    directions:

        python -c "import hashlib,sys; print('att_'+hashlib.sha256(
            open(sys.argv[1],'rb').read()).hexdigest()[:16])" file
    """
    return "att_" + hashlib.sha256(data).hexdigest()[:16]


class Archive:
    """One conversation's recordings and its manifest.

    Opened before the first turn and closed with the session.  Every write
    is append-only: a manifest that can be rewritten is not evidence.
    """

    def __init__(self, workspace: Path, client_id: Optional[str] = None,
                 limit: Optional[int] = None,
                 on_full: Optional[Callable[[str], None]] = None) -> None:
        self._started = datetime.now()
        self._ws = workspace
        # ONE DIRECTORY PER CONVERSATION, and the stamp alone does not
        # guarantee it: two archives opened inside the same second would
        # share a directory and a manifest, which gives the sweep two
        # policy rows for one archive and no way to tell whose clocks
        # govern which recording.  The suffix is the daemon's own
        # spelling for the same collision — its session records run
        # `20261002_210637`, `20261002_210637_1`, `_2`.
        self.dir = _unused(workspace / ROOT / self._started.strftime(STAMP))
        self.dir.mkdir(parents=True)
        self._manifest = self.dir / "manifest.jsonl"
        self._client_id = client_id
        self._session_id: Optional[str] = None
        #: The ceiling, in bytes, or None for no ceiling at all.  The
        #: sweep is the enforcer; this exists because one long
        #: conversation can cross the ceiling between two sweeps, and the
        #: honest place to stop is before the write.
        self._limit = limit
        self._on_full = on_full
        #: Counted once, here, from what the person already holds — not
        #: re-walked per write.  `audio/` is ours alone, so nothing else
        #: grows it behind us.
        self._used = _bytes_under(workspace / ROOT)
        self._suspended = False
        self._policy_written = False
        #: Chunks in flight, keyed by `stream_id`.  The provider restarts
        #: `sequence` at 0 per utterance and the session turns that into a
        #: new stream_id, so a key here is exactly one spoken utterance.
        self._speaking: Dict[str, list] = {}
        self._mime: Dict[str, str] = {}

    def identify(self, session_id: str, client_id: Optional[str]) -> None:
        """Record who we are, once the daemon has told us."""
        self._session_id = session_id
        self._client_id = client_id or self._client_id

    # ------------------------------------------------------------ limits
    def _room_for(self, size: int) -> bool:
        """May these bytes be kept?

        The RECORD never stops — the manifest goes on being appended, and
        it is the audit half, governed by `retention_days`.  Only the
        recordings stop, which are the conversation half.  That split is
        the framework's (`explain audit`: the session record *"is the
        CONVERSATION, not the log about it"*), and stopping both would
        throw away the cheap evidence to save the expensive kind.
        """
        if self._limit is None:
            return True
        if self._used + size <= self._limit:
            return True
        if not self._suspended:
            self._suspended = True
            self.turn(stage="budget", limit=self._limit, held=self._used,
                      note="recordings stopped; the manifest continues")
            if self._on_full is not None:
                self._on_full(f"tope de audio alcanzado ({self._limit} bytes): "
                              f"dejo de grabar, el registro sigue")
        return False

    def _wrote(self, size: int) -> None:
        self._used += size

    # ---------------------------------------------------------- inbound
    def heard(self, data: bytes) -> str:
        """Archive one utterance exactly as sent, and return its id.

        The id covers THESE bytes — the MP3 that goes on the wire, not the
        WAV it was encoded from.  The daemon hashes what it receives
        (`jaato_session.py:4746`), so archiving anything else would give a
        file whose recomputed digest does not match the transcript's ref,
        which is the one property the scheme rests on.
        """
        att = digest(data)
        # The id is returned whether or not the bytes are kept: it is the
        # daemon's own identifier for this utterance, so the manifest can
        # still name the turn when the ceiling stopped the recording.
        if self._room_for(len(data)):
            (self.dir / f"in_{att}.mp3").write_bytes(data)
            self._wrote(len(data))
        return att

    # --------------------------------------------------------- outbound
    def speaking(self, stream_id: str, mime_type: str, data: bytes) -> None:
        """Accumulate one chunk of the scribe's own speech."""
        self._speaking.setdefault(stream_id, []).append(data)
        self._mime[stream_id] = mime_type

    def spoke(self, stream_id: str) -> Optional[dict]:
        """Close a stream: write the audio, hash it, return its record.

        `None` when the stream carried nothing, which is not an error —
        a turn can end without the scribe saying anything.
        """
        chunks = self._speaking.pop(stream_id, None)
        mime = self._mime.pop(stream_id, "")
        if not chunks:
            return None
        raw = b"".join(chunks)
        params = _pcm_params(mime)
        if not self._room_for(len(raw)):
            # The digest still goes in the manifest: what the caller heard
            # is identified even when the ceiling kept it from being kept,
            # and `file: None` is the archive saying so rather than naming
            # a recording that is not there.
            return {"stream_id": stream_id, "file": None,
                    "sha": digest(raw), "bytes": len(raw), "mime": mime}
        if params is None:
            # Not PCM: keep the bytes as they came rather than guessing a
            # container for them.  An unplayable archive still verifies.
            name, payload = f"out_{digest(raw)}.bin", raw
            (self.dir / name).write_bytes(payload)
        else:
            # A WAV header costs 44 bytes and makes the evidence playable
            # by anything; headerless PCM needs the mime to interpret, and
            # the mime is not in the file.
            name = f"out_{digest(raw)}.wav"
            with wave.open(str(self.dir / name), "wb") as w:
                w.setnchannels(int(params["channels"]))
                w.setsampwidth(2 if params["encoding"].endswith("16le") else 1)
                w.setframerate(int(params["rate"]))
                w.writeframes(raw)
        self._wrote((self.dir / name).stat().st_size)
        return {"stream_id": stream_id, "file": name,
                "sha": digest(raw), "bytes": len(raw), "mime": mime}

    # --------------------------------------------------------- manifest
    def _policy(self) -> None:
        """Record, once, the retention this archive was made under.

        WHY THE ARCHIVE CARRIES ITS OWN CLOCKS.  The sweep that prunes
        these recordings must know the resolved `record_keeping:` — after
        `inherits:` and most-restrictive-wins — and that resolution lives
        in the daemon, not in the SDK.  Copying the sealed snapshot's
        block in here, at the time, gives the sweep a stdlib-only read and
        gives an auditor the stronger statement: not "this is the policy
        today" but "this is the policy this conversation was recorded
        under".

        WRITTEN AT THE FIRST TURN, not at `identify`.  The record is saved
        by the daemon as the session runs; at creation there may be
        nothing on disk to read.  One attempt, one honest outcome: a null
        block means the clocks could not be read, and `housekeeping`
        refuses to prune an archive that says so.
        """
        if self._policy_written or self._session_id is None:
            return
        self._policy_written = True
        self.turn(stage="policy", **clocks(self._ws, self._session_id))

    def turn(self, **fields) -> None:
        """Append one turn's record.

        Carries the three identifiers the outbound half needs to be located
        — client id, session id, stream id — because model media never
        enters history and nothing upstream names it.  The inbound half
        needs none of them: its id is in the journal already.
        """
        self._policy()
        row = {"at": datetime.now().isoformat(timespec="seconds"),
               "client_id": self._client_id, "session_id": self._session_id,
               **fields}
        with self._manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

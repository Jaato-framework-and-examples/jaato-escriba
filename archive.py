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
from typing import Dict, Optional

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

    def __init__(self, workspace: Path, client_id: Optional[str] = None) -> None:
        self._started = datetime.now()
        self.dir = workspace / ROOT / self._started.strftime("%Y%m%d_%H%M%S")
        self.dir.mkdir(parents=True, exist_ok=True)
        self._manifest = self.dir / "manifest.jsonl"
        self._client_id = client_id
        self._session_id: Optional[str] = None
        #: Chunks in flight, keyed by `stream_id`.  The provider restarts
        #: `sequence` at 0 per utterance and the session turns that into a
        #: new stream_id, so a key here is exactly one spoken utterance.
        self._speaking: Dict[str, list] = {}
        self._mime: Dict[str, str] = {}

    def identify(self, session_id: str, client_id: Optional[str]) -> None:
        """Record who we are, once the daemon has told us."""
        self._session_id = session_id
        self._client_id = client_id or self._client_id

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
        (self.dir / f"in_{att}.mp3").write_bytes(data)
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
        return {"stream_id": stream_id, "file": name,
                "sha": digest(raw), "bytes": len(raw), "mime": mime}

    # --------------------------------------------------------- manifest
    def turn(self, **fields) -> None:
        """Append one turn's record.

        Carries the three identifiers the outbound half needs to be located
        — client id, session id, stream id — because model media never
        enters history and nothing upstream names it.  The inbound half
        needs none of them: its id is in the journal already.
        """
        row = {"at": datetime.now().isoformat(timespec="seconds"),
               "client_id": self._client_id, "session_id": self._session_id,
               **fields}
        with self._manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

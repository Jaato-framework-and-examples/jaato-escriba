"""One fan-out per person, and never one shared between them.

THE WHOLE POINT IS THE SEPARATION.  Every person authenticated through
the proxy gets their own workspace, their own session and their own hub;
a single global stream would send one person's transcript to everyone
watching, and nothing about that failure is visible from the outside —
both browsers would look like they were working.  So the hub is an
object held per principal rather than a module-level broadcast, and
`tests_web_hub.py` asserts that a publish on one never reaches another.

WHAT A SLOW READER LOSES IS ITS VOICE, NEVER ITS STATE, and that split
is the whole of `Channel`.  A browser that stops reading — a phone with
the tab in the background — must not grow a queue for as long as the
session lives, so something has to give; the question is what.  Audio
can give.  A voice frame that never sounds costs a fragment of a
sentence, and the ring discards the OLDEST so what does sound is the
most recent.  A `status` frame cannot give: lose the one that says the
escriba stopped speaking and the page says "hablando" until somebody
reloads it, while the server believes it answered.

THIS REPLACED A WHOLE-READER DROP THAT COULD NOT RECOVER, and the
reasoning it replaced is worth keeping because it was wrong in an
instructive way.  It said: "dropping a reader costs it nothing —
`EventSource` reconnects on its own and the reconnect is answered with a
full snapshot".  Nothing made that true.  `drop` removed the queue from
`_subs` and the stream that owned it went on awaiting it forever, with
`_sse`'s keepalive holding the connection open, so the browser never saw
an error, never reconnected and never asked for the snapshot that would
have fixed it.

Measured on the deployment on 2026-10-12, on a tablet with several tabs
open: a 25-second spoken reply filled a 256-frame queue, the hub evicted
the reader mid-answer, and the page then showed no audio, no new
transcript rows, and a header stuck on "hablando" — recoverable only by
reloading twice.  Nothing was logged, because `drop` said nothing.
Every symptom was one orphaned queue, because the voice, the transcript
and the status all travel it.

So: voice is lossy by construction, state is not, and a state queue that
overflows anyway ENDS ITS STREAM instead of being orphaned — which is
what the old comment assumed happened.  Both evictions are logged.

THE SNAPSHOT IS BUILT WHEN IT IS ASKED FOR, never stored.  This hub used
to keep the last `state` it had published and hand that to the next
browser — and since `state` is published only at session open, every
reconnect was answered with the world as it looked when the session
started.  Measured on 2026-10-09: a person talked for two minutes, had a
document written, opened it, and came back to a page showing an empty
transcript, no document and a session clock from an hour earlier, while
the conversation, the document and the live session were all exactly
where they should be.  Nothing had restarted; the page had been told
something stale and believed it.

So the hub holds the FUNCTION that builds a snapshot rather than the
result of having called it once.  Staleness then has nowhere to live:
there is no second copy of the state to drift from the first.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

log = logging.getLogger("escriba.web")

#: How many STATE frames may wait before the stream is ended.  These are
#: small and infrequent -- a row, a count, a status -- so reaching this
#: means a reader that has stopped reading entirely, not one that is
#: merely slow.  Since the voice no longer shares this budget, the old
#: cause of reaching it is gone.
BACKLOG = 256

#: How many VOICE frames may wait before the oldest are discarded.  A
#: couple of seconds of speech: enough to ride out a stall, short enough
#: that what finally plays is not a minute behind the conversation.
VOICE_BACKLOG = 128

#: The frames a reader may lose without being wrong about anything.
#: ONLY this one.  Everything else in the stream is a statement about
#: what is true now, and a page that misses one does not degrade -- it
#: contradicts the server until somebody reloads it.
#:
#: THE WHOLE KIND, INCLUDING ITS `final` FRAME, which is a state change
#: wearing an audio frame's clothes -- it is what resets the page's
#: `VOICE.stream` and marks the utterance heard.  Promoting that one to
#: the lossless side would REORDER it: state is served before voice, so
#: the end of an utterance would overtake the audio it terminates and
#: reset the player mid-sentence.  It stays with its own stream and in
#: its own order, and the ring's `maxlen` is what keeps it safe -- the
#: oldest frames are the ones discarded, and `final` is by construction
#: the newest thing in the burst.
LOSSY = ("speech",)


class Channel:
    """One subscriber: a queue for what must arrive, a ring for the voice.

    Shaped like the `asyncio.Queue` it replaces -- `get`, `get_nowait`,
    `empty`, `qsize` -- because every caller and every test was written
    against that and the difference here is the POLICY, not the protocol.
    """

    def __init__(self, backlog: int = BACKLOG,
                 voice: int = VOICE_BACKLOG) -> None:
        self._state: Deque[Optional[bytes]] = deque()
        #: `maxlen` IS the policy: appending to a full deque discards the
        #: oldest, which is the end of the ring a listener can afford to
        #: lose. Dropping the newest instead would hold the stream at the
        #: moment it stalled and never catch up.
        self._voice: Deque[bytes] = deque(maxlen=voice)
        self._wake = asyncio.Event()
        self._backlog = backlog
        #: Voice frames discarded, for the log and for the tests.
        self.lost = 0
        #: Set when STATE overflowed. The stream reads it and ends,
        #: rather than being left on a queue nobody publishes to.
        self.overflowed = False

    def put_nowait(self, payload: bytes, lossy: bool = False) -> None:
        if lossy:
            if len(self._voice) == self._voice.maxlen:
                self.lost += 1
                if self.lost == 1:
                    log.warning("a reader fell behind the voice: discarding "
                                "the oldest of %d frames", self._voice.maxlen)
            self._voice.append(payload)
        else:
            self._state.append(payload)
            if len(self._state) > self._backlog:
                self.overflowed = True
        self._wake.set()

    def close(self) -> None:
        """Wake the stream and tell it there is nothing more."""
        self._state.append(None)
        self._wake.set()

    def qsize(self) -> int:
        return len(self._state) + len(self._voice)

    def empty(self) -> bool:
        return not self._state and not self._voice

    def get_nowait(self):
        # STATE FIRST, ALWAYS. A burst of voice must never hold up the
        # `status` that ends a turn.
        if self._state:
            return self._state.popleft()
        if self._voice:
            return self._voice.popleft()
        raise asyncio.QueueEmpty

    async def get(self):
        # The emptiness check and the `clear` happen with no `await`
        # between them, so a `put_nowait` cannot land in the gap and have
        # its wake-up thrown away.
        while self.empty():
            self._wake.clear()
            if not self.empty():
                break
            await self._wake.wait()
        return self.get_nowait()


def frame(kind: str, data: Any) -> bytes:
    """One SSE event, as the browser's `EventSource` parses it.

    Named events rather than a `{kind, payload}` envelope on a single
    `message`: the page attaches one listener per kind, so a frame it has
    no listener for costs it nothing, and an unknown kind cannot land in
    the wrong handler.
    """
    return (f"event: {kind}\n"
            f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n").encode("utf-8")


class Hub:
    """Everything one person's open browsers are told."""

    def __init__(self, snapshot: Callable[[], Dict[str, Any]]) -> None:
        self._subs: List[asyncio.Queue] = []
        #: Builds the whole of what a browser needs to draw the page.
        #: REQUIRED, and called rather than stored — see the module
        #: docstring.  A hub with no way to answer "what is true now?"
        #: can only answer "what was true once", which is the defect.
        self._snapshot = snapshot

    def subscribe(self,
                  snapshot: Optional[Callable[[], Dict[str, Any]]] = None
                  ) -> asyncio.Queue:
        """A queue, with the current state already in front of it.

        Both halves happen with no `await` between them, which is what
        makes the handover exact: nothing can be published into the gap,
        so a reader sees the snapshot and then every delta after it,
        never a delta that the snapshot already contains and never one
        it missed.  A delta that does arrive twice is harmless anyway —
        the page patches rows by id.

        `snapshot` OVERRIDES what this reader is told it is joining, and
        exists for the one reader who is not the owner: a guest the host
        invited in sees the conversation from the moment they enter, and
        the panels beside it are the host's own memory and references.
        Rendering those away in the guest's browser would not withhold
        them — they would already BE in that browser.  So the narrower
        view is BUILT, here, and the data never leaves this process.
        The override is a function for the same reason the default is:
        it is called at the moment of subscribing, inside the same gap,
        so a guest cannot be handed something stale either.
        """
        q = Channel()
        self._subs.append(q)
        q.put_nowait(frame("state", (snapshot or self._snapshot)()))
        return q

    def drop(self, q: "Channel") -> None:
        if q in self._subs:
            self._subs.remove(q)

    def publish(self, kind: str, data: Any) -> None:
        """Tell everyone watching.  Nothing is kept for the next reader.

        A `state` publish is for browsers already connected — after a
        resume, say.  The next one to arrive builds its own in
        `subscribe`, which is why there is nothing to store here.
        """
        payload = frame(kind, data)
        lossy = kind in LOSSY
        for q in list(self._subs):
            q.put_nowait(payload, lossy=lossy)
            if q.overflowed:
                # NOT ORPHANED. This is the case the old code got wrong:
                # removing the queue and leaving its stream awaiting it
                # meant a page that never reconnected because, as far as
                # the browser could tell, it was still connected. Closing
                # ends the response, `EventSource` reconnects by itself,
                # and the reconnect is answered with a full snapshot.
                log.warning("a reader fell behind on state after %d frames "
                            "(%d voice frames already lost): ending its "
                            "stream so it reconnects", q.qsize(), q.lost)
                self.drop(q)
                q.close()

    @property
    def lost(self) -> int:
        """Voice frames discarded across every reader, for the record."""
        return sum(q.lost for q in self._subs)

    @property
    def watchers(self) -> int:
        return len(self._subs)

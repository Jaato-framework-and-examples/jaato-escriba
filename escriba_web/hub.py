"""One fan-out per person, and never one shared between them.

THE WHOLE POINT IS THE SEPARATION.  Every person authenticated through
the proxy gets their own workspace, their own session and their own hub;
a single global stream would send one person's transcript to everyone
watching, and nothing about that failure is visible from the outside —
both browsers would look like they were working.  So the hub is an
object held per principal rather than a module-level broadcast, and
`tests_web_hub.py` asserts that a publish on one never reaches another.

A SUBSCRIBER THAT CANNOT KEEP UP IS DROPPED, not buffered without limit.
A browser that stops reading — a laptop asleep with the tab open — would
otherwise grow a queue for as long as the session lives.  Dropping it
costs that reader nothing: `EventSource` reconnects on its own and the
reconnect is answered with a full snapshot, which is the one message
that makes any later delta make sense.

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
from typing import Any, Callable, Dict, List

#: How far behind a reader may fall before it is dropped.  Generous
#: enough that a slow network is not an eviction, small enough that a
#: dead tab cannot hold a session's worth of audio refs in memory.
BACKLOG = 256


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

    def subscribe(self) -> asyncio.Queue:
        """A queue, with the current state already in front of it.

        Both halves happen with no `await` between them, which is what
        makes the handover exact: nothing can be published into the gap,
        so a reader sees the snapshot and then every delta after it,
        never a delta that the snapshot already contains and never one
        it missed.  A delta that does arrive twice is harmless anyway —
        the page patches rows by id.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=BACKLOG)
        self._subs.append(q)
        q.put_nowait(frame("state", self._snapshot()))
        return q

    def drop(self, q: asyncio.Queue) -> None:
        if q in self._subs:
            self._subs.remove(q)

    def publish(self, kind: str, data: Any) -> None:
        """Tell everyone watching.  Nothing is kept for the next reader.

        A `state` publish is for browsers already connected — after a
        resume, say.  The next one to arrive builds its own in
        `subscribe`, which is why there is nothing to store here.
        """
        payload = frame(kind, data)
        for q in list(self._subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                self.drop(q)

    @property
    def watchers(self) -> int:
        return len(self._subs)

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
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

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

    def __init__(self) -> None:
        self._subs: List[asyncio.Queue] = []
        #: The last snapshot published, handed to a browser the moment it
        #: connects.  Without it a reader that arrives mid-conversation
        #: sees only the deltas that happen to follow, and a delta against
        #: nothing is not interpretable.
        self.state: Optional[Dict[str, Any]] = None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=BACKLOG)
        self._subs.append(q)
        if self.state is not None:
            q.put_nowait(frame("state", self.state))
        return q

    def drop(self, q: asyncio.Queue) -> None:
        if q in self._subs:
            self._subs.remove(q)

    def publish(self, kind: str, data: Any) -> None:
        if kind == "state":
            self.state = data
        payload = frame(kind, data)
        for q in list(self._subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                self.drop(q)

    @property
    def watchers(self) -> int:
        return len(self._subs)

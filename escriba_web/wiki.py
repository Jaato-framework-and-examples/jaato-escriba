"""Which wiki pages came into this conversation.

THE PANEL CANNOT BE A LISTING OF THE WIKI, and that is a measurement
rather than a preference: `kb_list_nodes` with no filter answers with
counts and nothing else, and on a real kbwiki the total was 829 nodes
across three runs.  829 rows is not a panel, and fetching them on every
snapshot would be a request per reconnect for a list nobody reads.

So this tab answers the question the other two answer.  "De esta
conversación" lists what the judge kept, "aportados" lists what the
person handed over, and this lists the wiki pages the escriba actually
OPENED while you were talking -- the material that came into the
conversation, not the catalogue it came from.

READ FROM THE CALLS, NOT FROM THE WIKI.  Nothing here connects to
kbwiki: the session already holds that connection, with the person's
own token, and a second client in this process would be a second
credential path to get wrong.  The node id arrives in `tool_args` on
`TOOL_CALL_START` and the call's outcome on `TOOL_CALL_END`, which is
the same pair `enrichment` reads for `selectReferences`.

WHAT A ROW CANNOT HOLD, said so nobody waits for it: the page's title
and tags.  `ToolCallEndEvent` carries `success`, `result_status` and
`duration_seconds` -- no result payload (`explain event
TOOL_CALL_END`).  The id is what the librarian itself cites
(`[ref:<id>]`) and what `kb_get_node` takes, so it is the handle, not a
placeholder for one; but it is an id, and a title would need this
process to call kbwiki on its own.

NOTHING IS KEPT.  Not on disk and not past the session, which is the
property the seam this replaces was written for: a grant revoked tomorrow
cannot leave a row behind, because there is no row to leave.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

#: The calls that OPEN a page, as opposed to finding one.  A listing is
#: how the escriba navigates; it is not material that entered the
#: conversation, and putting 22 search hits in the panel would bury the
#: one page it then read.
OPENS = ("mcp__kbwiki__kb_get_node", "mcp__kbwiki__kb_get_node_content")

#: The calls that LOOK without opening anything.  Counted but never
#: listed: the panel stays a list of what came into the conversation,
#: and these are how the escriba got there.
#:
#: COUNTED AT ALL because of what the panel could not answer.  Measured
#: on 2026-10-12: the escriba made two `kb_list_nodes` calls, both
#: returning nothing, and the panel read "0 del wiki" -- identical to
#: never having looked.  The person asked it twice whether it was using
#: the wiki at all, which is a question the page should have answered
#: and could not.
ASKS = ("mcp__kbwiki__ask_librarian", "mcp__kbwiki__kb_list_nodes",
        "mcp__kbwiki__kb_list_bundles")


class Consulted:
    """The wiki pages this session opened, in the order it opened them."""

    def __init__(self, hub=None) -> None:
        self.hub = hub
        #: call_id -> node id, between the start of a call and its end.
        #: A page is listed when the call SUCCEEDED: a refused tool or an
        #: id that is not there says nothing about what the person saw.
        self._pending: Dict[str, str] = {}
        self._rows: Dict[str, dict] = {}
        #: Lookups made, whether or not they found anything.
        self._asked = 0
        #: Calls that came back an error, and the last one's status.
        self._failed = 0
        self._detail = ""
        #: Whether the LAST kbwiki call succeeded.  `None` until one has
        #: been made, which is the state the panel used to report as
        #: "ok" with no evidence whatsoever.
        self._last: Optional[bool] = None

    def attach(self, client) -> None:
        from jaato_sdk import EventType
        client.subscribe(EventType.TOOL_CALL_START, self._started)
        client.subscribe(EventType.TOOL_CALL_END, self._ended)

    def _started(self, ev) -> None:
        name = getattr(ev, "tool_name", None)
        if name not in OPENS and name not in ASKS:
            return
        node = (getattr(ev, "tool_args", None) or {}).get("id")
        self._pending[getattr(ev, "call_id", "")] = (name, str(node or ""))

    def _ended(self, ev) -> None:
        held = self._pending.pop(getattr(ev, "call_id", ""), None)
        if held is None:
            return
        name, node = held
        ok = bool(getattr(ev, "success", False))
        self._last = ok
        if not ok:
            self._failed += 1
            # ALL `ToolCallEndEvent` CARRIES. No result payload, so the
            # status is the whole of what can be said about why.
            self._detail = str(getattr(ev, "result_status", "") or "")
        elif name in ASKS:
            self._asked += 1
        if ok and name in OPENS and node and node not in self._rows:
            # Read twice is one page, not two.
            self._rows[node] = {"id": node, "text": node, "origin": "wiki",
                                "at": datetime.now().strftime("%H:%M"),
                                "tags": []}
        if self.hub is not None:
            self.hub.publish("wiki", self.state())

    def rows(self) -> List[dict]:
        return list(self._rows.values())

    def state(self) -> Dict[str, Any]:
        """What the panel is told, every field of it MEASURED.

        This used to return the literal `"ok"` whenever a wiki was
        configured, which said nothing about whether one could be
        reached: a refused token and an untouched wiki rendered
        identically, and the panel's whole reason for existing is to
        tell two absences apart.  A state is now a fact about calls that
        happened.

        `unused` nothing has been called yet · `ok` the last call
        succeeded · `failing` the last one did not, with `detail`.

        A CODE, NOT A WORD, because the page writes the sentence: these
        reach a person, and `i18n/` is where what a person reads lives.
        """
        code = ("unused" if self._last is None
                else "ok" if self._last else "failing")
        return {"state": code, "rows": self.rows(),
                "asked": self._asked, "opened": len(self._rows),
                "failed": self._failed, "detail": self._detail}

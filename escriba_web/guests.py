"""Who may join whose session, for the few minutes it is useful.

THE SHAPE THIS SERVES.  Somebody is working with their own escriba — an
interview, a working session — and needs a detail only a colleague has.
They invite that colleague in; the colleague listens, says the thing,
maybe drops a document in, and leaves.  Minutes, with the host present
throughout.  Everything here is sized for that and for nothing longer:
there is no persistence, no sharing of a workspace, and no way for a
guest to be in a room the host did not name them into.

AN INVITATION IS NOT A CREDENTIAL, and that is the whole reason this
module is as small as it is.  Every request already arrives through
oauth2-proxy carrying `X-Forwarded-User` and `X-Forwarded-Email`, so a
guest is authenticated before anything here runs.  A token minted by us
would be a second, weaker secret sitting beside a strong one, and it
would only decide who may KNOCK.  So what is held here is a NAME the
host typed, matched against an identity the proxy asserted.

IT DIES WITH THE HOST'S SESSION.  No expiry clock, because there is
nothing for one to be right about: the invitation exists to let somebody
into a conversation that is happening now, and when that conversation is
over the room is gone.  `forget` is called from the one place a person's
session is dropped.

THE ROOM IS PINNED TO WHOEVER WALKS IN.  The host names an address; the
first principal that presents it claims the invitation and is recorded on
it, and from then on only that principal is let in.  Matching on the
address alone would leave the room open to the next person who happens to
hold it after the guest has gone.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

#: What a display name may contribute to a prompt.  The host's escriba is
#: TOLD who is speaking, so a name crosses into the model's context — and
#: although the proxy asserts it rather than the person typing it, a name
#: is still the one piece of this that was not written by us.  Bounded and
#: stripped of the characters that could end the label and start something
#: else.
NAME_CHARS = 60


def label(name: str) -> str:
    """A speaker's name, safe to put in a prompt and short enough to read."""
    clean = "".join(c for c in name if c.isprintable() and c not in "[]{}<>\n\r")
    return clean.strip()[:NAME_CHARS] or "alguien"


def token(principal: str) -> str:
    """A stable, opaque id for one person, for the page to recognise itself.

    The live voice frames say who is speaking so that no browser plays its
    own voice back at its owner.  Comparing DISPLAY NAMES would mute one
    person to another whenever two of them are called the same thing, and
    sending the principal would hand each party the other's identifier for
    nothing.  A digest is neither.
    """
    return hashlib.sha256(principal.encode("utf-8")).hexdigest()[:12]


@dataclass
class Invitation:
    """One standing invitation, from one host, to one address."""
    host: str                                    # the host's principal
    email: str                                   # as the host typed it, folded
    since: datetime
    #: The principal that claimed it, once somebody has.  `None` while the
    #: host is still waiting, and the only identity let in afterwards.
    guest: Optional[str] = None
    name: str = ""                               # how the guest is shown
    #: The guest's live streams, so that cancelling ENDS them.  A guest
    #: route re-checks the invitation on every request, but `/events` is
    #: one request that lasts the whole visit, so the only way to close it
    #: is from this side.
    queues: List[asyncio.Queue] = field(default_factory=list)

    def json(self) -> dict:
        return {"email": self.email, "since": self.since.isoformat(timespec="seconds"),
                "name": self.name or None, "here": self.guest is not None}


class Room:
    """Every host's standing invitation, keyed by the host's principal.

    One at a time, deliberately: a second invitation from the same host
    REPLACES the first. Somebody who needs two colleagues at once is
    holding a meeting, and a second brain that four people talk into is
    a different product from this one.
    """

    def __init__(self) -> None:
        self._by_host: Dict[str, Invitation] = {}

    def invite(self, host: str, email: str) -> Invitation:
        inv = Invitation(host=host, email=email.strip().lower(),
                         since=datetime.now())
        self._by_host[host] = inv
        return inv

    def of_host(self, host: str) -> Optional[Invitation]:
        return self._by_host.get(host)

    def waiting_for(self, email: str) -> List[Invitation]:
        """Every host expecting this address, oldest first.

        A LIST, not one: two people can invite the same colleague at the
        same time, and showing them only one of the two would hide a
        request that was really made. The guest is offered the choice.
        """
        folded = (email or "").strip().lower()
        if not folded:
            return []
        return sorted((i for i in self._by_host.values() if i.email == folded),
                      key=lambda i: i.since)

    def by_token(self, tok: str) -> Optional[Invitation]:
        """The invitation a guest named, found by its opaque id.

        A GUEST NEVER HOLDS THE HOST'S PRINCIPAL.  They have to be able
        to say WHICH room they are entering — two colleagues can invite
        the same person at once — and the host's principal is the real
        key, but it is also Keycloak's subject id and none of a guest's
        business. So the wire carries the same digest the voice frames
        use, and the lookup happens on this side.
        """
        return next((i for i in self._by_host.values()
                     if tok and token(i.host) == tok), None)

    def claim(self, host: str, principal: str, name: str) -> Optional[Invitation]:
        """Let this principal in, if the invitation is theirs to claim.

        Returns the invitation, or `None` — which is the only answer a
        caller needs: every refusal here means the same thing to a guest
        route, and spelling out which one would tell an uninvited caller
        whether a room exists.
        """
        inv = self._by_host.get(host)
        if inv is None:
            return None
        if inv.guest is None:
            inv.guest, inv.name = principal, label(name)
            return inv
        return inv if inv.guest == principal else None

    def cancel(self, host: str) -> Optional[Invitation]:
        """Withdraw it, and end whatever the guest still has open."""
        return self._close(self._by_host.pop(host, None))

    def forget(self, host: str) -> None:
        """The host's session is gone, so the room is too."""
        self.cancel(host)

    @staticmethod
    def _close(inv: Optional[Invitation]) -> Optional[Invitation]:
        if inv is None:
            return None
        for q in list(inv.queues):
            # The stream is woken rather than cancelled: it is sitting on
            # `queue.get()`, and a sentinel it can recognise is the only
            # thing that both ends it and lets it say why.
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
        inv.queues.clear()
        return inv


#: One per process, like `PEOPLE`.  Rooms are in memory because they are
#: worth exactly one conversation: a restart ends every session on the
#: box, so an invitation that outlived one would be an invitation into
#: nothing.
ROOM = Room()

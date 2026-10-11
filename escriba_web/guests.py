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

SEVERAL AT ONCE, BECAUSE AN INTERVIEW HAS TWO SIDES.  A host documenting
a topic often needs two people in the same conversation: one of them
corrects the other, and that correction is the thing worth writing down.
So a host holds a LIST of invitations, one per address, each claimed,
traced and revoked on its own -- when one of the two has said their piece
and goes, the other stays.  The transport was already shaped for this
(the hub fans out, and every page skips only its OWN voice by opaque id,
so each guest hears the other with nothing added); what was shaped for
one was the bookkeeping in this file.

INVITING THE SAME ADDRESS TWICE REPLACES THAT ONE INVITATION AND NO
OTHER.  A host correcting a typo must not evict the colleague who is in
the middle of answering a question.

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
    """Every host's standing invitations, keyed by the host's principal."""

    def __init__(self) -> None:
        self._by_host: Dict[str, List[Invitation]] = {}

    def invite(self, host: str, email: str) -> Invitation:
        """Name somebody, replacing any earlier invitation to that address.

        THE DISPLACED INVITATION IS CLOSED, and the omission of that was
        the worst-shaped bug this feature could have had: replacing an
        entry left the previous guest's `/events` stream subscribed to
        this hub. Their write routes began refusing them -- the new
        invitation's address is not theirs -- so they could no longer
        speak, and they went on HEARING everything, while the band
        showed the new name and the host believed only that person was
        in the room. A guest the host thinks is gone must not still be
        listening.

        ONLY THAT ONE IS REPLACED. A host fixing a typo must not evict
        the colleague who is in the middle of answering a question.
        """
        folded = email.strip().lower()
        held = self._by_host.setdefault(host, [])
        for stale in [i for i in held if i.email == folded]:
            self._close(stale)
            held.remove(stale)
        inv = Invitation(host=host, email=folded, since=datetime.now())
        held.append(inv)
        return inv

    def of_host(self, host: str) -> List[Invitation]:
        """Everybody this host has named, in the order they named them."""
        return list(self._by_host.get(host) or [])

    def present(self, host: str) -> List[Invitation]:
        """Those who are actually here, which is not the same list."""
        return [i for i in self.of_host(host) if i.guest is not None]

    def waiting_for(self, email: str) -> List[Invitation]:
        """Every host expecting this address, oldest first.

        A LIST, not one: two people can invite the same colleague at the
        same time, and showing them only one of the two would hide a
        request that was really made. The guest is offered the choice.
        """
        folded = (email or "").strip().lower()
        if not folded:
            return []
        return sorted((i for held in self._by_host.values() for i in held
                       if i.email == folded), key=lambda i: i.since)

    def find(self, tok: str, email: str) -> Optional[Invitation]:
        """The one invitation in room `tok` that was issued to `email`.

        A GUEST NEVER HOLDS THE HOST'S PRINCIPAL.  They have to say
        WHICH room they are entering -- two colleagues can invite the
        same person at once -- and the host's principal is the real key,
        but it is also Keycloak's subject id and none of a guest's
        business. So the wire carries the same digest the voice frames
        use, and BOTH halves of the lookup happen on this side: the room
        from the token, and then the invitation from the address the
        proxy asserted, because one room now holds several.
        """
        folded = (email or "").strip().lower()
        if not tok or not folded:
            return None
        for host, held in self._by_host.items():
            if token(host) != tok:
                continue
            return next((i for i in held if i.email == folded), None)
        return None

    def claim(self, inv: Invitation, principal: str,
              name: str) -> Optional[Invitation]:
        """Let this principal in, if the invitation is theirs to claim.

        Returns the invitation, or `None` -- which is the only answer a
        caller needs: every refusal here means the same thing to a guest
        route, and spelling out which one would tell an uninvited caller
        whether a room exists.
        """
        if inv.guest is None:
            inv.guest, inv.name = principal, label(name)
            return inv
        return inv if inv.guest == principal else None

    def revoke(self, host: str, email: str) -> Optional[Invitation]:
        """Show ONE guest out, leaving the rest of the room as it was."""
        folded = (email or "").strip().lower()
        held = self._by_host.get(host) or []
        for inv in list(held):
            if inv.email == folded:
                held.remove(inv)
                return self._close(inv)
        return None

    def cancel(self, host: str) -> List[Invitation]:
        """Show everybody out, and end whatever they still have open."""
        held = self._by_host.pop(host, [])
        return [i for i in (self._close(inv) for inv in held) if i is not None]

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

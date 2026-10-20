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

THE HOST NAMES WHO, THE ESCRIBA SAYS WHY, THE HOST SENDS.  Three steps
and three different parties, because each knows something the others do
not.  The host knows which colleague can settle the question and knows
their address -- the escriba cannot know an address and must never be
asked to produce one, since the only thing it could do with a name it
has never read is invent something plausible.  The escriba has the
overview of the conversation, so it is the one party that can say what
the guest is being called in FOR without the host stopping to type a
paragraph mid-interview.  And the host sends, because the justification
is written by a model out of their own session and read by somebody
outside it: they see it before a third person does.  `invite` makes the
invitation, `justify` fills it in, `open_up` is the only thing that lets
anybody see it.

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

#: What the escriba may write about why somebody is wanted.  Longer than
#: a name because it is a sentence, bounded because it is shown in a band
#: beside a button and read by somebody deciding whether to walk in.
#:
#: THIS TEXT GOES THE OTHER WAY from a name.  A name is asserted by the
#: proxy and crosses INTO a prompt; this is written by the model, out of
#: the host's own conversation, and crosses OUT to a third person.  It is
#: the first thing in this feature to do that, which is why the host
#: confirms it before anybody sees it -- see `Room.open_up`.
REASON_CHARS = 240

#: How many demonstrations one guest may record in one visit.
#:
#: A VISIT AND NOT A DAY, because that is the unit this whole module is
#: sized for -- minutes, with the host present throughout -- and the
#: count lives on the invitation, so it dies with the room and there is
#: no clock for it to be wrong about.
#:
#: WHY A CEILING AT ALL.  `demo.MAX_BYTES` bounds ONE demonstration at
#: 192 MB, which is the right unit for the person whose disk it is;
#: repeated demonstrations are bounded by nothing, and `demo`'s own
#: sizing note argues from a host with 2 GB free.  Three is what a
#: colleague called in to show something needs -- the first one, and two
#: more because the first take is often wrong.
DEMOS = 3


def label(name: str) -> str:
    """A speaker's name, safe to put in a prompt and short enough to read."""
    clean = "".join(c for c in name if c.isprintable() and c not in "[]{}<>\n\r")
    return clean.strip()[:NAME_CHARS] or "alguien"


def reason(text: str) -> str:
    """What the escriba wrote about why somebody is needed, fit to show.

    Bounded and stripped of the characters that would let a line of it
    end up looking like the page's own furniture rather than something a
    model wrote.  Empty is returned as empty ON PURPOSE: a missing
    justification is refused by the caller, loudly, rather than filled
    in here with something plausible.
    """
    clean = "".join(c for c in (text or "")
                    if c.isprintable() and c not in "<>\n\r")
    return clean.strip()[:REASON_CHARS]


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
    #: WHY this person is wanted, and WHAT FOR, written by the host's
    #: escriba because it is the one party with the overview of the
    #: conversation -- the host knows who to call, the escriba knows what
    #: the call is about.  Empty until it has written them.
    motivo: str = ""
    tema: str = ""
    #: Whether the guest may be offered this at all.  An invitation is
    #: made the moment the host types an address, and that is NOT the
    #: moment it becomes real: the escriba has not justified it yet, and
    #: the host has not read what a third person is about to be told
    #: about their own session.  `waiting_for` and `find` both ignore an
    #: invitation that is not open, so every door is shut by this one
    #: flag rather than by each route remembering to check.
    open: bool = False
    #: The guest's live streams, so that cancelling ENDS them.  A guest
    #: route re-checks the invitation on every request, but `/events` is
    #: one request that lasts the whole visit, so the only way to close it
    #: is from this side.
    queues: List[asyncio.Queue] = field(default_factory=list)
    #: THE ONE DEMONSTRATION THIS GUEST HAS OPEN, and the only id their
    #: routes will act on.  A demo id arrives from the wire, and the
    #: host's own id is a microsecond timestamp -- unguessable in
    #: practice and therefore exactly the kind of guard this project
    #: refuses to rest on.  Holding it here makes the question
    #: deterministic: a guest can append frames to, narrate and close
    #: THEIR recording, and no other, because no other is written here.
    demo: Optional[str] = None
    #: How many they have opened this visit, against `DEMOS`.  Counted on
    #: open rather than on close, so abandoning one still spends it.
    demos: int = 0

    def json(self) -> dict:
        return {"email": self.email, "since": self.since.isoformat(timespec="seconds"),
                "name": self.name or None, "here": self.guest is not None,
                "motivo": self.motivo or None, "tema": self.tema or None,
                "open": self.open}


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
                       # OPEN ONLY.  An invitation the host has typed but
                       # not yet sent is not an invitation; offering it
                       # would show a guest a room before the host had
                       # read what that guest is about to be told.
                       if i.email == folded and i.open), key=lambda i: i.since)

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
            # `open` HERE rather than in each guest route: `_invited`
            # calls this on every request, so one gate covers the stream,
            # the voice, the recording and the staged document.
            return next((i for i in held
                         if i.email == folded and i.open), None)
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

    def of_address(self, host: str, email: str) -> Optional[Invitation]:
        """This host's invitation to one address, open or not."""
        folded = (email or "").strip().lower()
        return next((i for i in self.of_host(host) if i.email == folded), None)

    def justify(self, host: str, email: str, motivo: str,
                tema: str) -> Optional[Invitation]:
        """Record WHY this person is wanted and WHAT FOR.

        Refuses by returning `None` -- no invitation to that address, or
        nothing written in either field.  BOTH are refused rather than
        one stored half-filled: a guest reading "te necesitamos" with no
        topic has been told nothing and still has to decide whether to
        walk into somebody else's conversation.

        An invitation ALREADY OPEN is refused too.  Once the guest can
        see it, the text they are deciding on is fixed; rewriting it
        underneath them would mean the record and the reading diverge.
        """
        inv = self.of_address(host, email)
        if inv is None or inv.open:
            return None
        why, what = reason(motivo), reason(tema)
        if not why or not what:
            return None
        inv.motivo, inv.tema = why, what
        return inv

    def open_up(self, host: str, email: str) -> Optional[Invitation]:
        """The host has read the justification and is sending it.

        NOTHING REACHES THE GUEST BEFORE THIS.  The justification is
        written by a model out of the host's own conversation and read by
        somebody outside it -- the only text in this feature that travels
        that way -- so the person whose session it describes sees it
        first and decides.  An unjustified invitation cannot be opened:
        the whole point is that the guest is told why.
        """
        inv = self.of_address(host, email)
        if inv is None or not inv.motivo or not inv.tema:
            return None
        inv.open = True
        return inv

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
            # thing that both ends it and lets it say why.  `close` can
            # never fail for want of room, which the `put_nowait` it
            # replaces could -- and a guest whose stream was full was
            # exactly a guest who went on listening after being shown
            # out.
            q.close()
        inv.queues.clear()
        return inv


#: One per process, like `PEOPLE`.  Rooms are in memory because they are
#: worth exactly one conversation: a restart ends every session on the
#: box, so an invitation that outlived one would be an invitation into
#: nothing.
ROOM = Room()

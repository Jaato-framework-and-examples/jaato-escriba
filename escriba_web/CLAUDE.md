# The guest boundary

One person's escriba can be joined, for a few minutes, by colleagues the
host names — more than one, because an interview has two sides: a host
documenting a topic calls in two people so that one can correct the
other, and that correction is the thing worth writing down.

This file is the need-to-know contract of that feature: what is
**enforced** by code, what is **traced** because it cannot be enforced,
and what is only **said**. Anyone changing `app.py`, `guests.py` or the
page's guest mode is changing one of the three, and the difference is the
whole safety story.

## Enforced

Each of these has a leg in `tests_guests.py`, and the leg is the statement
of record:

| Rule | How |
|---|---|
| A guest reaches nothing without a live invitation pinned to their own principal | `_invited` re-checks on **every** request; one 403 for every cause, so an uninvited caller cannot learn whose session exists |
| A guest never sees the conversation from before they arrived | `Person.guest_snapshot` returns `entries: []`. Not trimmed — empty |
| A guest never receives the host's memory, references, documents, spend, archive path or wiki state | the guest's snapshot is **built** key by key, not rendered narrow. A page that received them and declined to draw them would have them anyway |
| A guest's arrival cannot overwrite the host's wiki credential | no guest route calls `person_of`, which takes `x-forwarded-access-token` from every request. Asserted by reading each route's source |
| A guest cannot write a playback claim into the host's audit record | the page returns early for `S.guest`; their speakers are not an account of the host's conversation |
| A guest cannot name a file outside the host's workspace | `Person.stage` is called unchanged, so `staging`'s filename guard covers the second browser for free |
| Withdrawing an invitation ends what the guest has open | `Room._close` puts a sentinel in their queue; `_sse` recognises it and ends the stream |
| Two voices stay two voices | the speaker is part of `Conversation.add`'s fold key, so rows that are identical at creation do not merge |
| Replacing one invitation evicts that guest and no other | `Room.invite` closes only the invitation to the same address. It used to replace the host's single entry **without** closing it, which left the displaced guest's `/events` stream subscribed: refused by every write route, still hearing every word, while the band showed the new name |
| Only one person talks at a time, and the others can see it | `Person._floor` is published; each page disables its own button while somebody else holds it, and a press that races anyway is refused with `FloorTaken` rather than queued. Waiting was right while the only person who could collide with you was yourself with two tabs open |
| A failed recording cannot lock the room | the floor is a context manager (`Person._holding`), not a pair of calls around the turn's `try` — `_to_mp3` and `archive.heard` both sit inside it and both can raise |

## Required of the deployment, because this code cannot check it

**Every account in the realm must have an email address.** The whole
feature matches an identity the proxy asserted against an address the host
typed, so an account carrying no address can be invited and can never
arrive. That failure is silent on both sides, which is the reason it is
written down here: `GET /invitacion` does not refuse such a person —
`Room.waiting_for("")` returns `[]`, so they are offered nothing and never
learn a room was waiting — while the host sees the invitation standing open
beside its cancel, indistinguishable from one not claimed *yet*. It
defeats exactly the promise `invitar` makes in its own docstring, that
naming somebody in advance fails visibly rather than quietly.

It is not fixed in code because there is nothing here to fix: asking
whether an address maps to an account means querying Keycloak from the
invite path, and matching on the principal instead would be inventing an
identity. The state is made impossible upstream instead — the realm
requires the attribute — and `_invited`'s 403 is what a regression looks
like.

So the feature rests on two deployment facts, neither visible from this
process:

| Fact | Where | What a regression looks like |
|---|---|---|
| The proxy forwards the user headers | `escriba-oauth2-proxy`, `--pass-user-headers=true` (confirmed on the deployment 2026-10-12) | every guest gets `_invited`'s 403, and the host waits on an invitation nobody can claim |
| The realm requires an email address | Keycloak realm config, and every account predating it | one particular guest is offered nothing by `/invitacion`, silently, while their host waits |

## Traced, because it cannot be enforced

**The escriba reads kbwiki with the host's grants, whoever asked.** The
session holds one token, written at open; swapping it per turn would force
an MCP reconnect mid-conversation. So a guest can cause a lookup against
pages they may not be entitled to, and the host hears the answer.

This is not hidden and it is not claimed to be solved. It is made
answerable afterwards: every turn taken while somebody else is in the room
writes a `guest-present` row to the manifest **immediately before** the
turn's own row, carrying `guests` (everybody who was listening, not just
one of them — "who could have heard this" has more than one answer now),
`speaker` and `grants: "host"`. The manifest is append-only and in order, so "which wiki reads happened while
somebody else was listening, and which voice asked" is a question the
record answers.

Every transition is recorded the same way — `guest-invited`,
`guest-joined`, `guest-left`, `guest-revoked`, `guest-staged` — each with
`source: "invitation"`, the way the playback claim carries
`source: "browser"`: the host named somebody and the proxy confirmed who
turned up, which is asserted rather than observed.

`Person.guest_here` and `guest_gone` each do all three things — record,
show, tell — in one call. They were two, and a test found that a caller
could do one without the other and leave the manifest saying the opposite
of the truth.

## Said, and therefore only a suggestion

Prose cannot be enforced, so these are in the persona and in the page, and
they are the weakest part of the feature:

- The escriba is told to attribute what a guest says (`agent.guest_joined`
  carries it, and `agents/escriba.md` has the section). `wrote_something`
  cannot tell who spoke, so a memory landing unattributed is possible.
- The escriba is told not to read the host's memory out to a guest.
- The host is told, in the band and for as long as it is true, that the
  guests hear the escriba, that the wiki is being read with their grants,
  and what the room has spent against the ceiling. That last one is said
  because an interview is exactly the long, many-turn session `usd: 10.00`
  was sized against, and the ladder finalises the **host's** session at
  80 % — arriving as a surprise is the avoidable part.
- Each guest is told who else is in the room. Somebody answering into a
  conversation without being told who is listening has not been given the
  chance to decide what to say.

## What the escriba is told, and when

`board.note` publishes to the **transcript** — what the people in the room
read.
The model's context never receives it. So room events queue on
`Person._notices` and ride the next real turn, whichever side takes it.
They are not announced as a turn of their own, for the reason `Person.stage`
gives for not announcing a document: a turn spent saying "somebody
arrived" answers a question nobody asked and puts the escriba's voice
between the host and their own next sentence.

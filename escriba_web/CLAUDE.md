# The guest boundary

One person's escriba can be joined, for a few minutes, by a colleague the
host names. This file is the need-to-know contract of that feature: what
is **enforced** by code, what is **traced** because it cannot be enforced,
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

## Traced, because it cannot be enforced

**The escriba reads kbwiki with the host's grants, whoever asked.** The
session holds one token, written at open; swapping it per turn would force
an MCP reconnect mid-conversation. So a guest can cause a lookup against
pages they may not be entitled to, and the host hears the answer.

This is not hidden and it is not claimed to be solved. It is made
answerable afterwards: every turn taken while somebody else is in the room
writes a `guest-present` row to the manifest **immediately before** the
turn's own row, carrying `guest`, `speaker` and `grants: "host"`. The
manifest is append-only and in order, so "which wiki reads happened while
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
  guest hears the escriba and that the wiki is being read with their
  grants.

## What the escriba is told, and when

`board.note` publishes to the **transcript** — what the two people read.
The model's context never receives it. So room events queue on
`Person._notices` and ride the next real turn, whichever side takes it.
They are not announced as a turn of their own, for the reason `Person.stage`
gives for not announcing a document: a turn spent saying "somebody
arrived" answers a question nobody asked and puts the escriba's voice
between the host and their own next sentence.

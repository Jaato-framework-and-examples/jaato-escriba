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
| A named address lets nobody in until the host sends it | `Invitation.open` is false until `Room.open_up`, and BOTH `waiting_for` and `find` ignore an invitation that is not open — so one flag closes the offer, the stream, the voice, the recording and the staged document, instead of each route remembering to check |
| An invitation cannot be sent without a justification, and cannot be rewritten after | `Room.open_up` refuses one with either field empty; `Room.justify` refuses one already open. What the guest is deciding on is fixed from the moment they can see it |
| The escriba cannot name a guest, and cannot let one in | `justificar_invitado` takes an address and matches it against one the HOST typed (`Room.of_address`); there is no tool that opens anything. An address the model has never read could only be invented |
| An address that belongs to nobody never becomes an invitation | `/invitar` puts it to the realm (`realm.Directory`) BEFORE `Room.invite`, and refuses — 404 for "nobody has that", 503 for "could not ask", never one reported as the other, because they send the host to two different places. Configured with all three `--keycloak-*` flags or none: absent, the address is taken as typed, which is what this did before |
| A failed recording cannot lock the room | the floor is a context manager (`Person._holding`), not a pair of calls around the turn's `try` — `_to_mp3` and `archive.heard` both sit inside it and both can raise |

## The address the host types, and the one the proxy asserts

The whole feature matches an identity the proxy asserted against an
address the host typed. When those two differ, nothing refuses: `GET
/invitacion` offers that person nothing and never tells them a room was
waiting, while the host sees the invitation standing open beside its
cancel, indistinguishable from one not claimed *yet*. It defeats
exactly the promise `invitar` makes in its own docstring, that naming
somebody in advance fails visibly rather than quietly.

**This section used to say the gap could not be closed here, and that
the deployment would close it instead by requiring every account to have
an email address. The realm was changed to require one. It did not
help, and the reason is worth keeping.**

MEASURED, 2026-10-12: a host invited `tester@darumalabs.ai`; that
account's address is `tester@example.invalid` — RFC 2606's reserved
placeholder. Both sides were silent exactly as described above, and the
mismatch was found only by opening `/invitacion` by hand in the guest's
own browser.

PRESENCE WAS NEVER THE PROPERTY THIS FEATURE NEEDS. It needs the address
to be the one a colleague would type, and a placeholder satisfies a
required field perfectly. Requiring the field is in fact a mild pressure
*toward* placeholders: an admin creating an account now has to put
something there. An invariant that was measured true, enforced, and
still let the failure through is the kind worth recording, because the
next reader will otherwise re-derive it.

So it IS asked here now, of the only thing that can answer it, at the
only moment anybody can act on the answer — `realm.Directory`, from
`/invitar`, while the host is still looking at what they typed. The
credential is a service account with `view-users` and nothing else; it
reads no wiki, opens no session and names no person to anybody, so the
rule that kbwiki is read with the PERSON's own token is untouched.

And when it is off, it is off: a deployment that configures no realm
gets what this route did before, stated rather than defaulted. There is
no half-configuration in which a missing piece is guessed at, and a
realm that cannot be reached refuses the invitation rather than waving
it through — an invitation allowed because the check failed is the
silent acceptance this exists to end.

`/invitacion` also logs the asserted address and how many rooms matched.
The route already returned that address to the browser and the page
threw it away, so the two facts that disagreed were never in the same
place. One line puts one of them where an operator can read it.

### The realm audit that prompted all this

AUDITED AND THEN CLOSED ON THE DEPLOYMENT, 2026-10-12, so that the next
reader starts from a measurement rather than from this file's intent.
Realm `jaato-web-coder-shell`, which escriba SHARES with the web coder,
kbwiki and simbus — which is why the change was the owner's to approve
and not escriba's to make alone. Four accounts, none without an address,
so nothing had to be swept. Self-registration is off and there are no
identity providers, so every account is made by an admin.

The attribute carried Keycloak's default, `required={roles:[user]}` —
required when somebody edits their own profile, and NOT required when an
admin creates the account, which left the gap in exactly one shape: an
admin omitting the address on a new account. It now reads
`required={roles:[admin, user]}`, so that shape is gone too and the state
this section exists to prevent can no longer be reached by either route.
What is left here is the record of WHY the realm is configured that way,
because the reason is invisible from the realm: a Keycloak attribute does
not say that somebody's host would otherwise wait in silence.

Verification is NOT part of this contract. `_invited` needs the header to
be present, not the address to be confirmed, so an unverified address
works and a realm that stops verifying breaks nothing here. The audit
counted it; this feature does not read it.

So the feature rests on two deployment facts, neither visible from this
process:

| Fact | Where | What a regression looks like |
|---|---|---|
| The proxy forwards the user headers | `escriba-oauth2-proxy`, `--pass-user-headers=true` (confirmed on the deployment 2026-10-12) | every guest gets `_invited`'s 403, and the host waits on an invitation nobody can claim |
| Every account has an email address | realm `jaato-web-coder-shell`, `required={roles:[admin, user]}` since 2026-10-12 | nothing, by itself — see above: this was measured true while the failure happened anyway. It is kept because an account with no address still cannot be invited, not because it is sufficient |
| The invite lookup is configured | `--keycloak-url`, `--keycloak-realm`, `--keycloak-client-file` | every address is taken as typed again, and a wrong one fails the silent way this feature was changed to stop |

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

## This is the channel, not an adjunct to one

The people in a room hear each other **through this page** — the host's
microphone is tapped twice, once for the model and once as raw PCM fanned
out to whoever else is in the room. They are not expected to be on a
call elsewhere at the same time, and nothing here should be designed as
though they were.

Written down because a control was once built on the opposite
assumption: a mute for "the other person, who is already audible some
other way". Nobody asked for it, nothing established it, and it was
removed. A feature whose premise is not in the requirements is a feature
to delete, not one to find a better argument for.

## The one thing that travels outward

Everything else a guest receives is withheld by construction:
`guest_snapshot` is BUILT key by key, so the host's memory, references,
documents, spend and archive path cannot reach another browser by being
rendered away.

The justification is the exception, and the only one. It is **written by
a model, out of the host's own conversation, and read by somebody outside
it** — "para aclarar la discrepancia del informe que estás preparando
sobre su equipo" is a plausible sentence, useful, and discloses something
the host may not have meant to share. No schema can catch that, because
the field is doing its job.

MEASURED, 2026-10-12 (session 20261012_091640), the first time a real
host used it: the escriba wrote "has trabajado con la implementación de
harness para agentes de IA…" — a claim about the GUEST, asserted to
their face, that the model had no way to know. Not a leak of the host's
session, which is what this section was written expecting, but a
fabrication about the person being invited. The host's review caught
nothing, because it reads as perfectly reasonable.

No schema can catch that, so it is said twice and at the point of use:
in `JUSTIFY_SCHEMA`'s own description of `motivo`, which the model reads
when it calls the tool, and in the persona. Say what is MISSING here —
"hay una discrepancia en las provisiones del cierre" — not what the
guest supposedly knows.

So it is the host who decides, and the flow exists for that one reason:
`POST /invitar` makes the invitation and asks the escriba why that person
is needed; the band shows **the exact text the guest will read**; `POST
/invitar/enviar` is the only thing that lets anybody see it. The
justification is recorded when it is WRITTEN (`guest-justified`) and the
sending is recorded separately (`guest-invited`), because a justification
the host read and chose not to send is exactly the case this allows, and
the record should be able to show it happened.

Three parties, three things only one of them knows: the host knows who
can settle the question and knows their address; the escriba has the
overview of what the question is; the host, again, is the only one who
can judge what their own session may tell a third person.

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

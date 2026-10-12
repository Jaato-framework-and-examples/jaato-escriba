"""Does an address belong to anybody? Asked of the realm that knows.

WHY THIS EXISTS, MEASURED.  On 2026-10-12 a host invited
`tester@darumalabs.ai`; that account's address is `tester@example.invalid`.
`Room.waiting_for` compared the two, they differed, and the guest was
offered nothing -- while the host's band showed the invitation standing
open beside its cancel, indistinguishable from one not claimed YET.
Silent on both sides, which is the one failure mode naming somebody in
advance has, and the one `invitar`'s own docstring promises not to have.

`escriba_web/CLAUDE.md` used to say this could not be fixed here, and
that the deployment would guarantee it instead: every account has an
email address.  The realm was audited and changed to require one.  That
fix holds and did not help, because PRESENCE WAS NEVER THE PROPERTY THIS
FEATURE NEEDS.  It needs the address to be the one a colleague would
type, and `tester@example.invalid` -- RFC 2606's reserved placeholder --
satisfies a required field perfectly.  Requiring the field is in fact a
mild pressure toward placeholders: an admin creating an account now has
to put something there.

So the question is asked of the only thing that can answer it, at the
only moment anybody can act on the answer: while the host is still
looking at the address they typed.

WHAT THIS IS ALLOWED TO KNOW.  One address, one yes or no.  The
credential is a service account with `view-users` and nothing else, and
the only call made with it is a lookup by exact address.  It is NOT the
identity anything else here runs as: kbwiki is read with the person's
own token precisely so a service credential never sees everybody's wiki
at once, and that stays true -- this reads no wiki, opens no session and
names no person to anybody.

BOTH OR NEITHER, AND ABSENT IS OFF.  A deployment that does not
configure this gets the behaviour that existed before it, stated rather
than defaulted: the address is taken as typed.  There is no half-way
configuration in which a missing piece is guessed at.

AND IT NEVER GUESSES WHEN IT IS ON.  A realm that cannot be reached
produces `Unreachable`, which `/invitar` reports as its own refusal with
its own message.  It does not fall through to "probably fine": an
invitation allowed because the check failed is exactly the silent
acceptance this module was written to end, and a host told "could not
check" can press the button again, while a host told nothing cannot.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Tuple

log = logging.getLogger("escriba.realm")

#: Long enough for a realm on the same host, short enough that a realm
#: which has stopped answering refuses an invitation rather than holding
#: the host's click open.  Applied to each of the two calls.
TIMEOUT = 5.0


class Unreachable(Exception):
    """The realm could not be asked, so NOTHING is known about the address.

    Distinct from a "no" on purpose, and carried all the way to the
    band: "nobody has that address" and "I could not find out" send the
    host to two different places, and reporting the second as the first
    would send them to correct an address that may be perfectly right.
    """


def read_client(path: Path) -> Tuple[str, str]:
    """The service account, from a file holding ONE line: `id:secret`.

    A FILE AND NOT AN ARGUMENT, because `--flag secret` puts a live
    credential in `/proc/<pid>/cmdline`, which is world-readable and
    which `ps` prints for anybody with an account on the box.  The same
    reason `--budget-file` holds its integer in a file rather than on
    the command line, and the operator owns the mode of it.
    """
    line = path.read_text(encoding="utf-8").strip()
    client, sep, secret = line.partition(":")
    if not sep or not client or not secret:
        raise ValueError(f"{path}: expected one line of the form "
                         f"<client-id>:<client-secret>")
    return client, secret


class Directory:
    """The realm, asked one question: is this address somebody's?"""

    def __init__(self, base: str, realm: str, client: str, secret: str) -> None:
        self.base = base.rstrip("/")
        self.realm = realm
        self._client = client
        self._secret = secret

    def _token(self) -> str:
        """A fresh service-account token, for this one lookup.

        NOT CACHED.  A cache would have to know when the token expires,
        and would answer a question nobody asked: invitations happen at
        the speed of somebody typing an address, so the saved round trip
        buys nothing and the expiry logic could be wrong.
        """
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self._client,
            "client_secret": self._secret}).encode()
        url = f"{self.base}/realms/{self.realm}/protocol/openid-connect/token"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            return json.loads(res.read())["access_token"]

    def _lookup(self, email: str) -> bool:
        url = (f"{self.base}/admin/realms/{self.realm}/users?"
               + urllib.parse.urlencode({"email": email, "exact": "true"}))
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {self._token()}")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            found = json.loads(res.read())
        # THE ANSWER IS RE-DERIVED FROM WHAT CAME BACK, not taken from
        # the fact that something did.  `exact=true` is what makes this
        # a lookup rather than an infix search -- without it `tester@x`
        # matches `other-tester@x` -- so the flag is load-bearing, and a
        # realm that ignored it would hand us somebody else's account.
        # Comparing the address we asked for costs one line and makes
        # the result independent of the query being honoured.
        #
        # DISABLED IS NOT FOUND: an account that cannot log in cannot
        # arrive, so an invitation to one fails exactly the silent way
        # this module exists to end.
        return any(str(u.get("email", "")).strip().lower() == email
                   and u.get("enabled") is True
                   for u in found)

    async def holds(self, email: str) -> bool:
        """Is `email` an enabled account in this realm?

        Raises `Unreachable` if the realm did not answer.  Runs the two
        blocking calls off the loop: this is a route handler on the same
        loop that carries every open `/events` stream, and five seconds
        of a stalled realm must not be five seconds of a stalled page.
        """
        folded = (email or "").strip().lower()
        if not folded:
            return False
        try:
            return await asyncio.to_thread(self._lookup, folded)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            log.warning("the realm could not be asked about an address: %s: %s",
                        type(exc).__name__, exc)
            raise Unreachable(str(exc)) from exc

"""What it takes to let a person's own escriba reach the kbwiki.

ONE IDENTITY ALL THE WAY DOWN.  kbwiki serves each person only what
they have been granted, keyed on their Keycloak `sub` -- the same `sub`
this application already uses as the workspace key.  So the escriba
must call it AS THE PERSON, with the person's own access token, and not
with a service credential that would see everybody's wiki at once.

WHERE THE TOKEN LIVES, AND WHY NOT IN THE WORKSPACE.  The MCP plugin
resolves a header's `${VAR}` at connect time from the SESSION
ENVIRONMENT, and over IPC the session environment IS the file named by
`env_file` -- `session.reload_session_env` re-reads that same file and
nothing else.  There is no env dict to pass: `create_session` has no
such parameter.  So a token that reaches the header reaches a file, and
the only decision left is WHICH file.

Not the person's `workspace/.env`, which is the directory their own
confined session runs in, which `_reconcile_template` visits, and which
an operator reads when a workspace misbehaves.  A separate file,
0600, under a directory the operator names (`--token-dir`), which on
the deployed host is tmpfs -- so a live bearer token is never written
to a disk and never sits in the tree the person owns.

WHAT THIS DOES NOT BUY, stated so nobody relies on it: the runner is
handed the environment, so the agent's own process holds the token
while the session lives.  That is inherent -- the header is resolved
runner-side by design.  What the private file buys is that the
credential is not PERSISTED where the person's own data is.

COMPOSED, NEVER REPLACED.  The workspace `.env` carries
`JAATO_PROFILE_SET`, without which the daemon cannot resolve the
`escriba` profile at all, and may carry whatever an operator added.
This file is that file plus one line.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import workspace as _workspace

#: What `.mcp.json` reads in the Authorization header.  One name, used
#: by the writer here and by the server entry below, so the two cannot
#: drift.
VAR = "KBWIKI_MCP_TOKEN"

#: What the MCP plugin calls the server, and therefore the prefix of
#: every tool name the profile whitelists: `mcp__kbwiki__<tool>`.
SERVER = "kbwiki"


def env_path(token_dir: Path, principal: str) -> Path:
    """This person's environment file, named the way their workspace is.

    `caller_dir` and not the principal: this becomes a path, the value
    is an authenticated claim rather than a trusted one, and the same
    rule holds here as everywhere else -- nothing a principal contains
    may name a file outside the directory we chose.
    """
    return token_dir / f"{_workspace.caller_dir(principal)}.env"


def compose(workspace_env: Path, token: Optional[str]) -> str:
    """The session's whole environment: the workspace's own, plus the token."""
    base = workspace_env.read_text(encoding="utf-8") if workspace_env.exists() else ""
    if token is None:
        return base
    if base and not base.endswith("\n"):
        base += "\n"
    return (base
            + "\n# El token de acceso de la persona, para que su escriba\n"
            + "# consulte el kbwiki como ella. Lo escribe escriba_web en cada\n"
            + "# petición que trae uno nuevo; no se edita a mano.\n"
            + f"{VAR}={token}\n")


def write_env(path: Path, text: str) -> None:
    """Replace the file atomically, and never leave it readable.

    ATOMIC because a session can be reloading it while this writes: a
    half-written token is a 401 that looks like a revoked grant.  The
    mode is set on the temporary file BEFORE the content goes in, so
    there is no instant at which a complete token sits in a
    world-readable file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name(path.name + ".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


def server_entry(url: str) -> dict:
    """The `.mcp.json` the references the workspace's one remote server.

    `http`, not `sse`: kbwiki speaks streamable HTTP.  The token is a
    `${VAR}`, never a literal -- the plugin resolves it at connect time,
    sends it only to this url's host, and re-resolves it on a 401, which
    is what makes a rotated token work without a new session.
    """
    return {"mcpServers": {SERVER: {
        "type": "http", "url": url,
        "headers": {"Authorization": f"Bearer ${{{VAR}}}"}}}}

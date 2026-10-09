"""Where a person's second brain lives, and how it comes into being.

ONE ROOT, ONE DIRECTORY PER PERSON.  Every workspace is
``<root>/<caller>/workspace``, created the first time that person
authenticates and reused forever after.  The shape is `jaato-mcp`'s
(`per: caller`), and so is the reason: provisioning is the tenant's job,
not the daemon's — over IPC the daemon trusts the socket's uid and
provisions nothing, so the application that authenticated the person is
the one that must give them a directory, as its own user.  The daemon's
check then passes by construction.

THIS FILE IS NOT IN ANY WORKSPACE.  The implementation lives outside the
root on purpose: a `documentalista` writes into its own person's
``docs/``, and that must never be the directory holding our design notes.
The template is the only thing that crosses the boundary, and it crosses
by being copied.

RUNTIME STATE IS NEVER COPIED.  A template carrying one person's sessions,
logs or memories into the next person's workspace would be a data leak
with a tidy explanation, so the skip list is a denylist of kinds rather
than an allowlist of names: anything the framework writes while running
is excluded by prefix, and anything the template author added is copied.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Iterable

#: The implementation's own directory — outside every workspace.
HERE = Path(__file__).resolve().parent

#: What a new workspace is made of.  Must contain `.jaato/`.
TEMPLATE = HERE / "template"

#: Where workspaces live on the host this is deployed to.  One root, owned
#: by the tenant's own account; the daemon drops each runner to that owner
#: (`--runner-uid-policy workspace-owner`), which is what separates this
#: tenant from the others on the box.
DEFAULT_ROOT = Path("/home/escriba/workspaces")

#: Never copied out of the template.  Matched against each path RELATIVE to
#: the template, by prefix, so a whole subtree goes with its parent.
RUNTIME = (
    ".jaato/sessions", ".jaato/logs", ".jaato/.cache", ".jaato/memory",
    ".jaato/references", ".jaato/forgotten", ".jaato/.artifact_tracker.json",
    ".home", ".tmp", ".git", "docs", "audio",
)

#: Everything a workspace needs before a session can open in it.
REQUIRED = (".jaato/agents", ".jaato/profiles")


class NotProvisioned(RuntimeError):
    """The template cannot make a workspace, and silence would be worse."""


def caller_dir(principal: str) -> str:
    """One directory name per authenticated person, collision-free.

    Sanitised so it is a single safe path segment, and suffixed with a
    digest of the ORIGINAL principal so two people whose names sanitise
    the same way never land in one directory — `jaato-mcp` does this and
    the reason is the same: the readable half is for a human reading
    `ls`, and the digest is what actually keeps them apart.
    """
    if not principal or not principal.strip():
        raise NotProvisioned("principal: an authenticated name, not empty")
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", principal.strip()).strip("-.")[:40]
    digest = hashlib.sha256(principal.encode("utf-8")).hexdigest()[:8]
    return f"{stem or 'user'}-{digest}"


def _wanted(root: Path) -> Iterable[Path]:
    """Template paths to copy, runtime state excluded."""
    for src in sorted(root.rglob("*")):
        rel = src.relative_to(root).as_posix()
        if any(rel == skip or rel.startswith(skip + "/") for skip in RUNTIME):
            continue
        if "__pycache__" in src.parts:
            continue
        yield src


def _copy_template(template: Path, dest: Path) -> None:
    """Lay the template down in `dest`, symlinks kept as symlinks."""
    for src in _wanted(template):
        target = dest / src.relative_to(template)
        if src.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(src.readlink())
        elif src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)


def provision(principal: str, root: Path = DEFAULT_ROOT,
              template: Path = TEMPLATE) -> Path:
    """Return this person's workspace, creating it the first time.

    Idempotent: an existing workspace is returned untouched, because it
    holds everything they have ever told the escriba.  A changed template
    reaches an existing person only when their workspace is deleted —
    the same bargain `jaato-mcp` makes, and the only one that cannot
    overwrite somebody's memories with a seed file.
    """
    if not template.is_dir():
        raise NotProvisioned(f"template: {template} is not a directory")
    for need in REQUIRED:
        if not (template / need).exists():
            raise NotProvisioned(f"template: {template} has no {need}")

    # THE ROOT IS NOT OURS TO CREATE.  `mkdir(parents=True)` below would
    # happily make it, and that is how a multi-tenant root comes into
    # being with whatever the umask says: measured 0775 on a developer
    # box, which on a shared host lets every local account list the
    # directory names — and each name is a sanitised authenticated
    # principal, usually somebody's email address.  The deployment's root
    # is created once by whoever owns the account, with the mode they
    # chose (0750), and a missing one is a misconfiguration to report
    # rather than a directory to invent.  A typo would otherwise provision
    # a whole second tree that looks like it worked.
    if not root.is_dir():
        raise NotProvisioned(f"root: {root} does not exist. It is created "
                             f"once, by the account that owns it, with the "
                             f"mode that account chose")
    root = root.resolve()
    ws = (root / caller_dir(principal) / "workspace").resolve()
    # A principal is authenticated, not trusted: the one thing it must
    # never do is name a directory outside the root.
    if root not in ws.parents:
        raise NotProvisioned(f"workspace {ws} would fall outside {root}")
    if ws.exists():
        return ws

    staging = ws.with_name(f".provisioning-{ws.name}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        _copy_template(template, staging)
        # One rename, so a workspace is either absent or complete. A
        # half-copied one would look provisioned and be missing a profile.
        staging.rename(ws)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return ws

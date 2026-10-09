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
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

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

#: Who a directory belongs to, in a form a person can read.
#:
#: OUTSIDE THE WORKSPACE, in the person's directory rather than in it.
#: Every path under `{workspace}/` is writable by the confined session —
#: the deny list covers named framework assets, not the whole tree — so
#: an identity file kept there could be rewritten by the agent, and an
#: operator reading it to find out whose workspace this is would be
#: reading something the workspace itself could have authored.  Here it
#: is written once by the provisioner and never granted to anyone else.
IDENTITY = "person.json"

#: The file that selects the tier-2 overlay.  It is NOT in the template
#: and must not be: it used to hold the provider credential, which is why
#: it is gitignored, and the credential now lives in the daemon's vault as
#: a `pass://` pointer on the provider knob.  What stays is the one line
#: that makes the set's profiles findable.
ENV = ".env"


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


def profile_set(template: Path) -> str:
    """Which tier-2 overlay a workspace made from this template uses.

    DISCOVERED, never named here.  `.jaato/profiles/` holds the
    provider-agnostic `_base_*` files plus one directory per set, and the
    set is what binds a provider and a model.  Exactly one is a choice
    already made; several would be a choice nobody can make on a person's
    behalf, and none means the template cannot open a session at all.
    Both are refused rather than guessed.
    """
    sets = sorted(d.name for d in (template / ".jaato" / "profiles").iterdir()
                  if d.is_dir() and not d.name.startswith("."))
    if len(sets) != 1:
        raise NotProvisioned(
            f"template: {template} has {len(sets)} profile sets ({', '.join(sets) or 'none'}); "
            f"exactly one is needed, because JAATO_PROFILE_SET selects it and "
            f"nothing here can choose for the person")
    return sets[0]


def _write_env(ws: Path, template: Path) -> None:
    """Give a workspace the one variable its session cannot start without.

    WHY THIS EXISTS.  The daemon resolves `profile="escriba"` inside the
    selected set; with no `JAATO_PROFILE_SET` it looks only at the top
    level, finds `_base_escriba` and not `escriba`, and refuses the
    session with `ProfileNotFoundError`.  Measured, on a workspace this
    function had just provisioned.  Every developer's workspace had a
    hand-written `.env` from before any of this, so the gap only appears
    for a person provisioned by the real path — which is every person on
    the deployed host.

    An EXISTING file is left exactly as it is.  Absent, it is written;
    present, it belongs to whoever put it there.
    """
    target = ws / ENV
    if target.exists():
        return
    target.write_text(
        "# Selecciona el overlay de tier-2 en tiempo de ejecución.\n"
        "# Lo escribe el aprovisionamiento: sin esta línea el daemon no\n"
        "# encuentra el perfil 'escriba' y la sesión no abre.\n"
        f"JAATO_PROFILE_SET={profile_set(template)}\n"
        "\n"
        "# La credencial del proveedor NO se pone aquí. Vive en el vault\n"
        "# del daemon y el perfil la nombra con pass://.\n",
        encoding="utf-8")


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


def _write_identity(home: Path, principal: str, identity: Optional[dict]) -> None:
    """Record who this directory is for, once.

    The directory is named from the principal, which on the deployed host
    is Keycloak's `sub` — stable across a rename, and unreadable: `ls`
    shows `a3f1…-9c2b1d04` and nothing about a person.  The readable
    name belongs somewhere an operator can find it WITHOUT it becoming
    the path, because a path is listed by anyone who can stat the root
    and an email in it is a disclosure.

    Written on first provision and never again: the file records who the
    directory was created for, and a later rename does not change that.
    """
    target = home / IDENTITY
    if target.exists():
        return
    row = {"principal": principal,
           "provisioned_at": datetime.now().isoformat(timespec="seconds")}
    row.update({k: v for k, v in (identity or {}).items() if v})
    target.write_text(json.dumps(row, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")


def provision(principal: str, root: Path = DEFAULT_ROOT,
              template: Path = TEMPLATE,
              identity: Optional[dict] = None) -> Path:
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
    profile_set(template)      # refuse early, before anything is copied

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
        _write_identity(ws.parent, principal, identity)
        # Idempotent, with ONE repair: a workspace missing `.env` cannot
        # open a session at all, and writing the file it never had takes
        # nothing away from the person.  Everything else is theirs.
        _write_env(ws, template)
        return ws

    staging = ws.with_name(f".provisioning-{ws.name}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        _copy_template(template, staging)
        _write_env(staging, template)
        # One rename, so a workspace is either absent or complete. A
        # half-copied one would look provisioned and be missing a profile.
        staging.rename(ws)
        _write_identity(ws.parent, principal, identity)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return ws

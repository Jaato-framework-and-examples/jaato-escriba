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
from typing import Iterable, List, Optional

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

#: Which template a workspace's authored assets came from.
#:
#: Beside the workspace rather than in it, like `person.json` and for
#: the same reason: everything under `{workspace}/` is writable by the
#: confined session, and a record the session can rewrite is not a
#: record.  It also gates the refresh — without it every session open
#: would rewrite forty files and churn their mtimes for nothing.
STAMP = "template.json"

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


#: What a person chose, as opposed to who they are.
#:
#: BESIDE `person.json`, NOT IN IT.  That file is written once and never
#: again, deliberately: it records who the directory was created for, and
#: a later rename does not change that.  A preference is the opposite —
#: it exists to be changed — so putting one in a write-once record would
#: mean either breaking that rule or having a setting that cannot be
#: unset.  Same directory, same 0700/0600, different lifetime.
PREFS = "prefs.json"


def _record(home: Path, name: str) -> dict:
    """One of the person's own JSON files, or nothing.

    ABSENT AND UNREADABLE ARE THE SAME ANSWER here, and that is the
    whole reason this is one function: both files it reads are written
    by the provisioner and read by a page that must still render — a
    preference nobody has set and an identity recorded before the proxy
    passed a name are both "we do not know", with nothing to choose
    between them.  What is NOT the same is the caller's reaction, and
    each decides that for itself.
    """
    try:
        row = json.loads((home / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return row if isinstance(row, dict) else {}


def prefs(home: Path) -> dict:
    """Whatever this person has chosen, or nothing."""
    return _record(home, PREFS)


def identity(home: Path) -> dict:
    """Who this directory was created for, as it was recorded.

    The record, not the assertion.  `person.json` is written once, at
    provision, from the headers the proxy sent then — so this is the
    same answer an operator gets when they map a directory to a person,
    and a page showing it cannot disagree with `ls`.  It is also the
    only place to read an identity from: everything under
    `{workspace}/` is writable by the confined session, so a name kept
    there is a name the escriba could have authored.
    """
    return _record(home, IDENTITY)


def set_pref(home: Path, key: str, value) -> dict:
    """Change one preference, keeping the rest."""
    row = prefs(home)
    row[key] = value
    target = home / PREFS
    target.write_text(json.dumps(row, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    target.chmod(0o600)
    return row


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
    # 0700 / 0600, said rather than inherited.  The root above is 0750
    # and already keeps other accounts out, so this is defence in depth
    # with one specific value: it stops being true the day somebody
    # widens the root, and a person's email should not become readable
    # because of a change two directories up.  It is NOT what separates
    # one person from another — every session runs as the same account,
    # and AppArmor is what keeps each inside its own workspace.
    home.chmod(0o700)
    row = {"principal": principal,
           "provisioned_at": datetime.now().isoformat(timespec="seconds")}
    row.update({k: v for k, v in (identity or {}).items() if v})
    target.write_text(json.dumps(row, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    target.chmod(0o600)


def authored_digest(template: Path) -> str:
    """What this template says, as one value.

    Over the same paths `_wanted()` copies, which is the definition that
    matters: the template carries AUTHORED ASSETS only — agents,
    profiles, completion schemas, scripts — because `RUNTIME` excludes
    everything a session writes.  So "what the template holds" and "what
    belongs to us rather than to the person" are the same set, and
    neither has to be listed twice.
    """
    h = hashlib.sha256()
    for src in _wanted(template):
        if not src.is_file() or src.is_symlink():
            continue
        h.update(src.relative_to(template).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(src.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:16]


def _refresh_authored(ws: Path, template: Path) -> List[str]:
    """Replace the authored assets with the template's, wholesale.

    WHY AN EXISTING WORKSPACE IS UPDATED AT ALL, when `provision` was
    written to leave one alone.  That rule is right for what the person
    accumulates — memories, references, documents, recordings — and
    wrong for what we author.  A profile fix, a persona correction, a
    new completion schema: shipped after somebody's first sign-in, none
    of it ever reached them, and nothing else would ever update those
    files, because `.jaato/profiles/` is write-denied to the session
    that lives there.  Found the day a telemetry opt-out shipped and the
    one person already on the host kept exporting.

    WHOLESALE, not file by file: a profile RENAMED in the template would
    otherwise leave the old one behind, still resolvable by name, and a
    workspace would run a recipe this repo no longer has.  Each
    top-level entry the template carries is removed and copied fresh.

    Nothing outside those entries is touched, which is what makes this
    safe: `RUNTIME` keeps the person's own state out of the template, so
    the set replaced here cannot contain any of it.
    """
    replaced = []
    for rel in _authored_units(template):
        dst = ws / rel
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        elif dst.exists() or dst.is_symlink():
            dst.unlink()
        replaced.append(rel.as_posix())
    _copy_template(template, ws)
    return replaced


def _authored_units(template: Path, rel: Optional[Path] = None) -> Iterable[Path]:
    """The deepest paths this template owns OUTRIGHT.

    NOT the template's top-level entries.  The template's only top-level
    entry is `.jaato/`, and a workspace's `.jaato/` also holds the
    person's memories, their session records and their logs — so
    replacing at that level deletes everything they have, which is the
    exact disaster the old leave-it-alone rule existed to prevent.  It
    was written that way first and a test caught it before it shipped.

    A directory is descended into when `RUNTIME` places any of the
    person's own state inside it, and replaced outright when it does
    not.  So `.jaato/` is descended (it contains `sessions`, `memory`,
    `logs`) and `.jaato/profiles/` is replaced whole — which is what
    lets a RENAMED profile disappear instead of lingering, resolvable by
    a name this repo no longer has.  Derived from the one declaration
    that already distinguishes the two kinds, rather than a second list
    to keep in step with it.
    """
    base = template if rel is None else template / rel
    for child in sorted(base.iterdir()):
        here = Path(child.name) if rel is None else rel / child.name
        where = here.as_posix()
        if child.is_dir() and not child.is_symlink() and any(
                skip == where or skip.startswith(where + "/") for skip in RUNTIME):
            yield from _authored_units(template, here)
        else:
            yield here


def _reconcile_template(home: Path, ws: Path, template: Path,
                        on_refresh=None, fresh: bool = False) -> None:
    """Bring a workspace's authored assets up to this template, once.

    `fresh` says the workspace was JUST laid down from this template, so
    the copy has already happened and only the stamp is owed.  It is a
    parameter rather than "is there a stamp?" because those answer
    different questions and the difference is the whole point: a
    workspace provisioned before stamping existed has no stamp AND no
    refresh, and inferring "new" from the missing file skipped exactly
    the workspaces this was written for — including the one person
    already on the deployed host.  Caught by running it against a real
    workspace instead of a fixture.
    """
    stamp = home / STAMP
    want = authored_digest(template)
    try:
        have = json.loads(stamp.read_text(encoding="utf-8")).get("digest")
    except (OSError, json.JSONDecodeError):
        have = None
    if have == want:
        return
    replaced = [] if fresh else _refresh_authored(ws, template)
    stamp.write_text(json.dumps(
        {"digest": want, "at": datetime.now().isoformat(timespec="seconds"),
         "replaced": replaced}, indent=2) + "\n", encoding="utf-8")
    stamp.chmod(0o600)
    if replaced and on_refresh is not None:
        on_refresh(replaced)


def provision(principal: str, root: Path = DEFAULT_ROOT,
              template: Path = TEMPLATE,
              identity: Optional[dict] = None,
              on_refresh=None) -> Path:
    """Return this person's workspace, creating it the first time.

    Idempotent for the PERSON's half: memories, references, documents and
    recordings are never touched, because they hold everything they have
    ever told the escriba.

    The AUTHORED half is reconciled instead of left alone, which is a
    deliberate departure from `jaato-mcp`'s bargain and from what this
    function used to do.  Profiles, personas, schemas and scripts are
    ours, not theirs — they are even write-denied to the session that
    lives there — so leaving them frozen at first sign-in meant every
    later fix reached nobody.  See `_refresh_authored`.
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
        _reconcile_template(ws.parent, ws, template, on_refresh)
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
        _reconcile_template(ws.parent, ws, template, on_refresh, fresh=True)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return ws

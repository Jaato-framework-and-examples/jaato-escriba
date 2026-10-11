"""A document the person stages, and the two ways it could go wrong.

What matters if this is wrong is not cosmetic.  A filename from a
browser becomes a PATH, so the first half of this file is the same
property `tests_workspace.py` asserts about a principal: nothing the
person types may name a file outside their own workspace.  The second
half is the catalogue entry — validated against the FRAMEWORK's own
validator rather than against my reading of its schema, because an
entry the plugin rejects is a document the escriba is never offered,
and nothing says so out loud.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enrichment                                       # noqa: E402
import staging                                          # noqa: E402

fail = 0


def check(label: str, got, want) -> None:
    global fail
    if got != want:
        print(f"  FAIL {label:<46} got {got!r}, want {want!r}")
        fail += 1


ws = Path(tempfile.mkdtemp(prefix="escriba-stage-"))
try:
    # ------------------------------------------------------- the filename
    # A NAME FROM A BROWSER IS NOT A PATH.  Each of these must end up as
    # one segment under `staged/`, and none of them may write outside the
    # workspace -- the same property, and the same reason, as
    # `workspace.caller_dir`.
    for hostile in ("../../etc/passwd", "/etc/passwd", "a/../../b",
                    "..", "....//", "  ", "\x00evil"):
        row = staging.stage(ws, hostile, b"x")
        target = (ws / row["path"]).resolve()
        check(f"{hostile!r} stays inside the workspace", ws in target.parents, True)
        check(f"{hostile!r} is one segment under staged/",
              Path(row["path"]).parent.as_posix(), "staged")

    # A NAME IS ALSO SOMETHING THE PERSON READS, and the escriba says
    # out loud. `caller_dir`'s ASCII reduction is right for a directory
    # in `ls` and wrong here: it turned `riego del balcón.md` into
    # `riego-del-balc-n.md`.
    check("letters keep their accents",
          staging.safe_name("riego del balcón.md"), "riego-del-balcón.md")
    check("and other alphabets survive too",
          staging.safe_name("Ñandú año.txt"), "Ñandú-año.txt")
    # What is dangerous is narrower than "not ASCII": separators, NUL,
    # and the glob metacharacters AppArmor reads as pattern syntax —
    # which the references plugin names as a cause of refused
    # authorisations.
    check("glob metacharacters do not reach a path",
          staging.safe_name("pla*no[1].pdf"), "pla-no-1-.pdf")
    # `readFile` dispatches on the SUFFIX: `.pdf` and the image
    # extensions come back as multimodal attachments, everything else is
    # decoded as text. A truncated `.pdf` is a PDF read as text.
    long_pdf = staging.safe_name("a" * 200 + ".pdf")
    check("a long name is cut to the limit", len(long_pdf), staging.LIMIT)
    check("and keeps the suffix readFile dispatches on",
          long_pdf.endswith(".pdf"), True)

    # --------------------------------------------------------- the entry
    body = b"# Riego\n\nDos depositos de 40 litros.\n"
    row = staging.stage(ws, "presupuesto riego.md", body)
    entry = json.loads((ws / enrichment.CATALOGUE / f"{row['id']}.json")
                       .read_text(encoding="utf-8"))
    check("the file holds exactly what was uploaded",
          (ws / row["path"]).read_bytes(), body)
    check("declared as a local reference", entry["type"], "local")
    check("pointing at the staged file", entry["path"], row["path"])
    check("the path is workspace-RELATIVE, not this host's",
          Path(entry["path"]).is_absolute(), False)
    # `auto` would carry the document into the prompt of every future
    # session forever; the person staged it to discuss it now.
    check("selectable, never auto", entry["mode"], "selectable")
    check("tagged so one filter_tags finds every staged document",
          "staged" in entry["tags"], True)
    check("and the name gives the tag matcher something to match",
          "presupuesto" in entry["tags"], True)
    # The person's own material is still DATA.  A document that says
    # "ignore your instructions" is a document that says that.
    check("the hint says material, not instructions",
          "not as instructions" in entry["fetch_hint"], True)

    # ------------------------------------------------ one document, one entry
    # Re-uploading a corrected document must not leave two selectable
    # entries with no way for the escriba to tell which is current.
    again = staging.stage(ws, "presupuesto riego.md", b"# Riego\n\nTres.\n")
    check("the same name keeps its id", again["id"], row["id"])
    check("and its contents are replaced",
          (ws / again["path"]).read_text(encoding="utf-8").strip().endswith("Tres."), True)
    check("one entry per document",
          len(list((ws / enrichment.CATALOGUE).glob(f"{row['id']}.json"))), 1)

    # ------------------------------------------------------- the ceiling
    # The endpoint behind this is authenticated and nothing else bounds
    # it: without a ceiling a person fills the host's disk one POST at a
    # time, and the first thing to break is everyone else's recordings.
    try:
        staging.stage(ws, "enorme.bin", b"x" * (staging.MAX_BYTES + 1))
        check("an oversized upload is refused", "accepted", "refused")
    except staging.TooLarge:
        pass
    check("and nothing of it was written",
          (ws / staging.STAGED / "enorme.bin").exists(), False)

    # --------------------------------------------------------- the rows
    rows = staging.entries(ws)
    # ONE ROW PER FILE, not per upload: several of the hostile names
    # above sanitise to the same segment (`../../etc/passwd` and
    # `/etc/passwd` are both `passwd`), and by the rule above the second
    # upload of a name replaces the first rather than adding an entry.
    check("one row per staged file", len(rows),
          len(list((ws / staging.STAGED).iterdir())))
    check("each row says where it came from",
          {r["origin"] for r in rows}, {"staged"})
    check("the marker tag is not shown to the person",
          any("staged" in r["tags"] for r in rows), False)
    check("rows carry facts, not prose",
          all(isinstance(r["size"], int) for r in rows), True)

    # A FILE THAT IS GONE IS NOT AN EMPTY ONE.  The entry still offers
    # the document, so the row has to be able to say the file is missing
    # rather than report a size of zero.
    (ws / row["path"]).unlink()
    gone = [r for r in staging.entries(ws) if r["id"] == row["id"]][0]
    check("a missing file reads as unknown, not as zero", gone["size"], None)

    # The two listings read ONE directory and are told apart by prefix,
    # so neither may show the other's rows.
    enrichment._catalogue(ws, [{"url": "https://example.org/a", "nombre": "A",
                                "descripcion": "d", "resumen": "r"}], ["t"])
    check("the judge's finds are not listed as staged",
          [r for r in staging.entries(ws) if not r["id"].startswith("staged-")], [])
    check("and a staged document is not listed as a find",
          [r for r in enrichment.catalogue_entries(ws)
           if r["id"].startswith("staged-")], [])
    # ------------------------------- the framework's own verdict, if present
    # THE SCHEMA IS NOT OURS, so the entry is checked by the validator
    # that ships with the server rather than by a copy of its rules
    # here: an entry the plugin rejects is a document the escriba is
    # never offered, and nothing in the running system says so.
    #
    # `jaato-server` is deliberately NOT a dependency of this project —
    # the daemon is a service, not an import (see pyproject.toml) — so
    # this leg runs where the server IS installed and SAYS SO where it is
    # not. A leg that stayed quiet when it did not run would be a check
    # nobody can tell they are missing.
    try:
        from jaato_server.shared.plugins.references.config_loader import (
            validate_reference_file)
    except ImportError:
        print("  note  the framework validator was not run: jaato-server is "
              "not installed in this environment")
    else:
        ok, errors, warnings = validate_reference_file(entry)
        check("the framework accepts the entry", (ok, errors), (True, []))
        check("with nothing to warn about", warnings, [])
finally:
    shutil.rmtree(ws, ignore_errors=True)

print("staging OK" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)

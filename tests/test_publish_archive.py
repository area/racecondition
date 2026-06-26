"""
Guard the published app tarball.

When a release tag is created, the Tildagon app store downloads `git archive`
of that tag and unpacks it into `apps/<owner>_<title>/` on the badge.  The
firmware then imports `apps.<name>.app` and reads `__app_export__` — so the
entry `app.py` must sit at the *root* of the tarball.  Here it is a thin shim
that re-exports the app class from the `badge/` subpackage; everything else in
the tarball is that subpackage.  `.gitattributes` `export-ignore` entries are
what keep server/dev files out of that tarball.

This test asserts the *actual* archive output is exactly the entry shim, the
`badge/` subpackage, and the store-required `tildagon.toml`, and nothing else.
A failure means either a dev/server file is missing an `export-ignore` line in
.gitattributes, or a new file was added at the repo root and must be
allow-listed below (a deliberate checkpoint: should this ship to the badge?).
"""

import subprocess

# Top-level paths permitted in the published tarball: the entry shim app.py,
# the badge/ subpackage holding the app code, and the store-required
# tildagon.toml.  App modules live inside badge/, so they need no entry here.
ALLOWED_TOP_LEVEL = {
    "app.py",            # entry shim — re-exports __app_export__ from badge.app
    "badge",             # subpackage with the actual app code
    "tildagon.toml",     # required by the app store
}


def _archived_paths():
    # Archive the current index (git write-tree) rather than HEAD, so the
    # check reflects staged-but-uncommitted changes — it validates what the
    # next commit/release tag will ship, not the previous commit.
    # --worktree-attributes picks up un-committed .gitattributes edits too.
    tree = subprocess.run(
        ["git", "write-tree"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    tar = subprocess.run(
        ["git", "archive", "--worktree-attributes", "--format=tar", tree],
        capture_output=True,
        check=True,
    ).stdout
    names = subprocess.run(
        ["tar", "-t"],
        input=tar,
        capture_output=True,
        check=True,
    ).stdout.decode().splitlines()
    # Drop directory entries (trailing slash); keep real files.
    return [n for n in names if n and not n.endswith("/")]


def test_published_archive_is_app_only():
    leaked = sorted(
        p for p in _archived_paths()
        if p.split("/", 1)[0] not in ALLOWED_TOP_LEVEL
    )
    assert not leaked, (
        "git archive would publish files outside the app payload:\n  "
        + "\n  ".join(leaked)
        + "\n\nAdd an `export-ignore` line for each in .gitattributes."
    )


def test_tildagon_toml_is_published():
    # The store cannot list the app without it; must NOT be export-ignored.
    assert "tildagon.toml" in _archived_paths()

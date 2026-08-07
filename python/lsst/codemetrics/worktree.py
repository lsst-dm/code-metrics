"""Obtaining a repository and checking revisions out of it safely."""

import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .revisions import git_output

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "lsst-code-metrics"
"""Location of the mirror clone cache (`~pathlib.Path`)."""

_URL_RE = re.compile(r"^(https?|git|ssh|file)://|^[^/]+@[^/]+:")


def is_url(target: str) -> bool:
    """Report whether a target names a remote repository.

    Parameters
    ----------
    target : `str`
        Repository path or URL.

    Returns
    -------
    remote : `bool`
        `True` if the target is a URL rather than a local path.
    """
    return bool(_URL_RE.search(target))


def repo_name(target: str) -> str:
    """Derive a short name from a repository path or URL.

    A URL keeps its final path segment with any ``.git`` suffix
    removed.  A local path is resolved first, so relative forms such
    as ``.``, ``..``, and ``some/dir/`` all yield the repository's
    actual directory name instead of a literal ``.`` or ``..``.

    Parameters
    ----------
    target : `str`
        Repository path or URL.

    Returns
    -------
    name : `str`
        Final path component without any ``.git`` suffix.

    Raises
    ------
    ValueError
        Raised if a local path resolves to a name-less location, such
        as the filesystem root.
    """
    if is_url(target):
        trimmed = target.rstrip("/")
        base = trimmed.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        return base.removesuffix(".git")

    resolved = Path(target).expanduser().resolve()
    if not resolved.name:
        raise ValueError(f"Cannot derive a repository name from {target!r}: resolves to {resolved}.")
    return resolved.name


def ensure_source(target: str, cache_dir: Path) -> Path:
    """Return a local repository to create worktrees from.

    A local path is used where it stands.  A URL is cloned as a mirror
    into the cache on first use and fetched on later runs.  A plain
    ``--bare`` clone does not configure ``remote.origin.fetch``, so a
    later ``git fetch`` would update tags and ``FETCH_HEAD`` only and
    leave branch refs frozen at whatever the first clone saw.
    ``--mirror`` implies ``--bare`` and additionally sets up a refspec
    that maps every ref, so a later fetch keeps branches current too.

    Parameters
    ----------
    target : `str`
        Repository path or URL.
    cache_dir : `~pathlib.Path`
        Directory holding cached mirror clones.

    Returns
    -------
    source : `~pathlib.Path`
        Repository that worktrees may be created from.
    """
    if not is_url(target):
        return Path(target).expanduser().resolve()

    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{repo_name(target)}.git"
    if cached.exists():
        git_output(cached, "fetch", "--prune", "origin")
    else:
        git_output(cache_dir, "clone", "--mirror", target, str(cached))
    return cached


@contextmanager
def temporary_worktree(source: Path) -> Iterator[Path]:
    """Provide a detached worktree that is removed on exit.

    The caller's own working tree is never touched, so a repository with
    uncommitted changes can be measured safely.

    Parameters
    ----------
    source : `~pathlib.Path`
        Repository to attach the worktree to.

    Yields
    ------
    worktree : `~pathlib.Path`
        Directory containing the detached worktree.
    """
    holder = Path(tempfile.mkdtemp(prefix="code-metrics-"))
    tree = holder / "tree"
    try:
        git_output(source, "worktree", "add", "--detach", "--no-checkout", "-q", str(tree))
        yield tree
    finally:
        try:
            git_output(source, "worktree", "remove", "--force", str(tree))
        finally:
            shutil.rmtree(holder, ignore_errors=True)
            git_output(source, "worktree", "prune")


def checkout(worktree: Path, commit: str) -> None:
    """Check a commit out into a worktree, leaving nothing behind.

    The clean step is required for correctness.  A file that is tracked at
    one commit and untracked at the next survives a checkout, and would
    otherwise be counted at every later sample.

    Parameters
    ----------
    worktree : `~pathlib.Path`
        Worktree to update.
    commit : `str`
        Commit to check out.
    """
    git_output(worktree, "checkout", "--detach", "--force", "-q", commit)
    git_output(worktree, "clean", "-xdff", "-q")

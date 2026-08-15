"""Selection of the revisions at which a repository is measured."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

MODES: tuple[str, ...] = ("tags", "first-parent", "all")
"""Supported sampling modes (`tuple` [ `str` ])."""


class GitError(RuntimeError):
    """Raised when a git command fails."""


class Sample(BaseModel):
    """A single point in a repository's history."""

    commit: str
    date: datetime
    label: str | None = None


def git_output(repo: Path, *args: str) -> str:
    """Run a git command in a repository and return its standard output.

    Parameters
    ----------
    repo : `~pathlib.Path`
        Repository to run in.
    *args : `str`
        Arguments to pass to git.

    Returns
    -------
    output : `str`
        Standard output, with trailing whitespace removed.

    Raises
    ------
    GitError
        Raised if git exits non-zero.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        joined = " ".join(args)
        raise GitError(f"git {joined} failed: {exc.stderr.strip()}") from exc
    return completed.stdout.rstrip()


def default_branch(repo: Path) -> str:
    """Determine the branch to walk when none was given.

    Prefers the remote's HEAD and falls back to the currently checked out
    branch, which is what a repository with no remote will have.

    Parameters
    ----------
    repo : `~pathlib.Path`
        Repository to inspect.

    Returns
    -------
    branch : `str`
        Name of the branch.
    """
    try:
        ref = git_output(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    except GitError:
        return git_output(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return ref


def _commit_samples(repo: Path, rev_args: list[str]) -> list[Sample]:
    """Build samples from a ``git rev-list`` invocation.

    Parameters
    ----------
    repo : `~pathlib.Path`
        Repository to inspect.
    rev_args : `list` [ `str` ]
        Arguments following ``rev-list``.

    Returns
    -------
    samples : `list` [ `Sample` ]
        One sample per commit, unsorted.
    """
    # %cI is the committer date in strict ISO 8601.  The author date is
    # deliberately not used: a rebase preserves it, so it does not record
    # when the work landed on the branch.
    raw = git_output(repo, "rev-list", "--format=%H %cI", "--no-commit-header", *rev_args)
    samples = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        commit, _, date = line.partition(" ")
        samples.append(Sample(commit=commit, date=datetime.fromisoformat(date)))
    return samples


def _tag_samples(repo: Path, pattern: str) -> list[Sample]:
    """Build samples from the repository's tags.

    Parameters
    ----------
    repo : `~pathlib.Path`
        Repository to inspect.
    pattern : `str`
        Glob matched against tag names.

    Returns
    -------
    samples : `list` [ `Sample` ]
        One sample per matching tag, unsorted.
    """
    # creatordate is the tag date for annotated tags and the commit date
    # for lightweight ones, so it orders correctly whatever the tag naming
    # convention happened to be at the time.
    #
    # An annotated tag's objectname is the tag object, not the commit it
    # points at, so it is dereferenced.  Lightweight tags have no
    # dereferenced name and fall through to the plain one.
    fields = (
        "%(refname:short)"
        " %(if)%(*objectname)%(then)%(*objectname)%(else)%(objectname)%(end)"
        " %(creatordate:iso-strict)"
    )
    raw = git_output(
        repo,
        "tag",
        "--list",
        pattern,
        "--sort=creatordate",
        f"--format={fields}",
    )
    samples = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        label, commit, date = line.split(" ")
        samples.append(Sample(commit=commit, date=datetime.fromisoformat(date), label=label))
    return samples


def _as_aware(when: datetime | None) -> datetime | None:
    """Give a bound a time zone if it lacks one.

    Committer dates always carry an offset, so comparing them against a
    naive bound raises.  Command line dates arrive naive, and a user
    writing ``--since 2020-01-01`` means that date, not a date in an
    unspecified zone, so UTC is assumed.

    Parameters
    ----------
    when : `~datetime.datetime` or `None`
        Bound to normalize.

    Returns
    -------
    when : `~datetime.datetime` or `None`
        The bound, guaranteed to carry a time zone.
    """
    if when is None or when.tzinfo is not None:
        return when
    return when.replace(tzinfo=UTC)


def sample_revisions(
    repo: Path,
    mode: str,
    branch: str | None = None,
    tag_pattern: str = "w.*",
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Sample]:
    """Choose the revisions at which a repository will be measured.

    Parameters
    ----------
    repo : `~pathlib.Path`
        Repository to sample.
    mode : `str`
        One of the values in `MODES`.
    branch : `str`, optional
        Branch to walk.  Defaults to the remote HEAD.
    tag_pattern : `str`, optional
        Glob matched against tag names in ``tags`` mode.
    since : `~datetime.datetime`, optional
        Discard samples earlier than this.
    until : `~datetime.datetime`, optional
        Discard samples later than this.

    Returns
    -------
    samples : `list` [ `Sample` ]
        Samples sorted by date, ascending.

    Raises
    ------
    ValueError
        Raised if the mode is not recognized.
    """
    if mode not in MODES:
        known = ", ".join(MODES)
        raise ValueError(f"Unknown mode {mode!r}. Known modes: {known}.")

    if mode == "tags":
        samples = _tag_samples(repo, tag_pattern)
    else:
        target = branch or default_branch(repo)
        rev_args = ["--first-parent", target] if mode == "first-parent" else [target]
        samples = _commit_samples(repo, rev_args)

    lower, upper = _as_aware(since), _as_aware(until)
    if lower is not None:
        samples = [s for s in samples if s.date >= lower]
    if upper is not None:
        samples = [s for s in samples if s.date <= upper]

    return sorted(samples, key=lambda s: (s.date, s.commit))

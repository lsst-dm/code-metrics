"""Reading and writing per-repository line count records.

Records are stored in long format, one row per sample and language, so
that a language appearing for the first time adds rows rather than
columns.  Rows are kept sorted by date.  A normal incremental run,
where every newly collected sample is chronologically later than
everything already stored, therefore appends to the end of the file
and produces a small difference; recounting existing history under a
different counter interleaves old and new rows by date across the
whole file instead.
"""

import contextlib
import csv
import os
import stat
import tempfile
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import IO

import yaml
from pydantic import BaseModel

DEFAULT_FILE_MODE = 0o644
"""Permission mode applied to a newly created output file (`int`).

This is the conventional umask-standard mode rather than a mode read
from the process umask.  Querying the umask safely requires setting
it to zero and restoring it afterward, which is racy in a threaded
process; a fixed default is also deterministic and testable, which
the actual umask, being environment-dependent, is not.
"""

COLUMNS: tuple[str, ...] = (
    "commit",
    "date",
    "label",
    "counter",
    "counter_version",
    "language",
    "n_files",
    "blank",
    "comment",
    "code",
)
"""Column order of the CSV files (`tuple` [ `str` ])."""


class LineRow(BaseModel):
    """One language's counts at one revision."""

    commit: str
    date: datetime
    label: str = ""
    counter: str
    counter_version: str
    language: str
    n_files: int
    blank: int
    comment: int
    code: int


class RepoMeta(BaseModel):
    """Description of how a repository's records were collected."""

    name: str
    url: str
    mode: str
    branch: str
    tag_pattern: str
    exclude_dirs: list[str]


def _sort_key(row: LineRow) -> tuple[datetime, str, str]:
    """Return the canonical sort key for a row.

    Parameters
    ----------
    row : `LineRow`
        Row to key.

    Returns
    -------
    key : `tuple`
        Date, commit, and language.
    """
    return (row.date, row.commit, row.language)


@contextlib.contextmanager
def _atomic_create(path: Path, newline: str | None = None) -> Iterator[IO[str]]:
    """Write to a temporary file, then atomically replace the destination.

    The destination is left untouched until the caller's block
    completes without raising, so a process that dies or an exception
    that is raised partway through serialization cannot truncate or
    corrupt content already persisted at `path`.  The destination's
    permission mode is preserved across the replace; a destination
    that does not yet exist gets `DEFAULT_FILE_MODE`.  Without this,
    the owner-only mode that `tempfile.mkstemp` gives the temporary
    file would carry through the replace and silently narrow the
    destination's permissions.

    Parameters
    ----------
    path : `~pathlib.Path`
        Final destination.  Only replaced once the caller's block
        succeeds.
    newline : `str` or `None`, optional
        Newline argument passed to `open`.

    Yields
    ------
    fd : `~typing.IO`
        Writable text file, backed by a temporary file created in the
        same directory as `path` so the final replace is atomic.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = DEFAULT_FILE_MODE
    handle, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(handle, mode)
        with os.fdopen(handle, "w", newline=newline) as fd:
            yield fd
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    else:
        os.replace(tmp_path, path)


def read_rows(path: Path) -> list[LineRow]:
    """Read stored records.

    Parameters
    ----------
    path : `~pathlib.Path`
        CSV file to read.  A missing file yields no rows.

    Returns
    -------
    rows : `list` [ `LineRow` ]
        Records in file order.
    """
    if not path.exists():
        return []
    with path.open(newline="") as fd:
        return [LineRow(**record) for record in csv.DictReader(fd)]


def write_rows(path: Path, rows: Iterable[LineRow]) -> None:
    """Write records, sorted canonically.

    The write is atomic: it cannot leave `path` truncated or holding a
    partial CSV body if serialization fails or the process is
    interrupted, since the previous content is only replaced once the
    new content has been fully written.

    Parameters
    ----------
    path : `~pathlib.Path`
        CSV file to write.
    rows : `~collections.abc.Iterable` [ `LineRow` ]
        Records to store.
    """
    with _atomic_create(path, newline="") as fd:
        writer = csv.DictWriter(fd, fieldnames=COLUMNS)
        writer.writeheader()
        for row in sorted(rows, key=_sort_key):
            record = row.model_dump()
            record["date"] = row.date.isoformat()
            writer.writerow(record)


def collected_keys(rows: Iterable[LineRow]) -> set[tuple[str, str]]:
    """Report which revisions have already been counted, and by what.

    The key pairs the commit with the counter, so that switching backend
    recounts the history rather than leaving a file whose early rows came
    from one tool and whose later rows came from another.

    Parameters
    ----------
    rows : `~collections.abc.Iterable` [ `LineRow` ]
        Records to inspect.

    Returns
    -------
    keys : `set` [ `tuple` [ `str`, `str` ] ]
        Commit and counter name pairs.
    """
    return {(row.commit, row.counter) for row in rows}


def merge_rows(existing: Iterable[LineRow], new: Iterable[LineRow]) -> list[LineRow]:
    """Combine stored records with freshly collected ones.

    Rows sharing a commit and counter with a new row are replaced, so a
    recount supersedes rather than duplicates.

    Parameters
    ----------
    existing : `~collections.abc.Iterable` [ `LineRow` ]
        Records already on disk.
    new : `~collections.abc.Iterable` [ `LineRow` ]
        Records just collected.

    Returns
    -------
    rows : `list` [ `LineRow` ]
        Merged records, sorted canonically.
    """
    new_rows = list(new)
    superseded = collected_keys(new_rows)
    kept = [row for row in existing if (row.commit, row.counter) not in superseded]
    return sorted([*kept, *new_rows], key=_sort_key)


def write_meta(path: Path, meta: RepoMeta) -> None:
    """Write the sidecar description of a collection run.

    The write is atomic, for the same reason as `write_rows`.

    Parameters
    ----------
    path : `~pathlib.Path`
        YAML file to write.
    meta : `RepoMeta`
        Description to store.
    """
    with _atomic_create(path) as fd:
        fd.write(yaml.safe_dump(meta.model_dump(), sort_keys=False))

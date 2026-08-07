"""Reading and writing per-repository line count records.

Records are stored in long format, one row per sample and language, so
that a language appearing for the first time adds rows rather than
columns.  Rows are kept sorted by date so an incremental run appends to
the end of the file and produces a small difference.
"""

import csv
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel

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

    Parameters
    ----------
    path : `~pathlib.Path`
        CSV file to write.
    rows : `~collections.abc.Iterable` [ `LineRow` ]
        Records to store.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fd:
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

    Parameters
    ----------
    path : `~pathlib.Path`
        YAML file to write.
    meta : `RepoMeta`
        Description to store.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(meta.model_dump(), sort_keys=False))

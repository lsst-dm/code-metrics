"""Orchestration of a per-repository collection run."""

import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel
from rich.progress import (
    BarColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from .counters import CounterError, LineCounter
from .location import repo_dir
from .revisions import GitError, Sample, default_branch, sample_revisions
from .storage import (
    LineRow,
    RepoMeta,
    collected_keys,
    merge_rows,
    read_rows,
    write_meta,
    write_rows,
)
from .worktree import DEFAULT_CACHE_DIR, checkout, ensure_source, repo_name, temporary_worktree

_LOG = logging.getLogger(__name__)


class CollectResult(BaseModel):
    """Summary of what a collection run did.

    ``added + skipped + failed + empty`` always equals the number of
    samples considered for the run.
    """

    added: int
    skipped: int
    failed: int
    empty: int
    total: int
    languages: list[str]
    first_date: datetime | None
    last_date: datetime | None


def _rows_for_sample(
    sample: Sample, counter: LineCounter, tree: Path, exclude_dirs: Sequence[str]
) -> list[LineRow]:
    """Count one revision and convert the result into rows.

    Parameters
    ----------
    sample : `~lsst.codemetrics.revisions.Sample`
        Revision to measure.
    counter : `~lsst.codemetrics.counters.LineCounter`
        Backend to measure with.
    tree : `~pathlib.Path`
        Worktree already positioned at the revision.
    exclude_dirs : `~collections.abc.Sequence` [ `str` ]
        Directory names to skip.

    Returns
    -------
    rows : `list` [ `LineRow` ]
        One row per language reported.
    """
    counts = counter.count(tree, exclude_dirs)
    return [
        LineRow(
            commit=sample.commit,
            date=sample.date,
            label=sample.label or "",
            counter=counter.name,
            counter_version=counter.version,
            language=language,
            n_files=count.n_files,
            blank=count.blank,
            comment=count.comment,
            code=count.code,
        )
        for language, count in counts.items()
    ]


def collect(
    target: str,
    *,
    name: str | None = None,
    data_dir: Path | str | None = None,
    mode: str = "first-parent",
    branch: str | None = None,
    tag_pattern: str = "w.*",
    counter: LineCounter,
    exclude_dirs: Sequence[str] = (),
    since: datetime | None = None,
    until: datetime | None = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    force: bool = False,
    strict: bool = False,
    flush_every: int = 50,
    progress: bool = True,
) -> CollectResult:
    """Measure a repository across its history.

    A revision whose tree has no file in any language the counter
    recognizes stores no row: the CSV records languages, and a
    language-less revision genuinely has none to record.
    Such a revision is therefore re-examined on every later run,
    because the resume check is keyed on the rows a revision
    produced, and an empty revision produced none.
    This is deliberate: recounting an empty tree is inexpensive, and
    fabricating a row to mark it done would corrupt the data instead.

    Parameters
    ----------
    target : `str`
        Repository path or URL.
    name : `str`, optional
        Output base name.  Defaults to the repository's basename.
    data_dir : `~pathlib.Path` or `str`, optional
        Data root to write beneath.  Resolved by
        `~lsst.codemetrics.location.data_root` when not given.
    mode : `str`, optional
        Sampling mode.
    branch : `str`, optional
        Branch to walk.
    tag_pattern : `str`, optional
        Glob matched against tag names in ``tags`` mode.
    counter : `~lsst.codemetrics.counters.LineCounter`
        Backend to measure with.
    exclude_dirs : `~collections.abc.Sequence` [ `str` ], optional
        Directory names to skip.
    since, until : `~datetime.datetime`, optional
        Bound the sampled date range.
    cache_dir : `~pathlib.Path`, optional
        Directory holding cached mirror clones.
    force : `bool`, optional
        Recount revisions that are already stored.
    strict : `bool`, optional
        Abort on the first failure instead of skipping it.
    flush_every : `int`, optional
        Write partial results after this many revisions.  Whatever has
        been collected is also written once the run ends, however it
        ends: normally, on a caught failure, on an exception that
        propagates out of this function, or on a `KeyboardInterrupt`.
    progress : `bool`, optional
        Display a progress bar.

    Returns
    -------
    result : `CollectResult`
        Summary of the run.

    Raises
    ------
    ValueError
        Raised if no revisions were selected.
    """
    resolved_name = name or repo_name(target)
    destination = repo_dir(data_dir)
    csv_path = destination.path / f"{resolved_name}.csv"
    meta_path = destination.path / f"{resolved_name}.meta.yaml"

    source = ensure_source(target, cache_dir)
    resolved_branch = branch or (default_branch(source) if mode != "tags" else "")
    samples = sample_revisions(
        source,
        mode,
        branch=resolved_branch or None,
        tag_pattern=tag_pattern,
        since=since,
        until=until,
    )
    if not samples:
        raise ValueError(f"No revisions selected for {target} in mode {mode!r}.")

    existing = read_rows(csv_path)
    done = set() if force else collected_keys(existing)
    pending = [s for s in samples if (s.commit, counter.name) not in done]
    skipped = len(samples) - len(pending)

    collected: list[LineRow] = []
    failed = 0
    empty = 0

    def flush() -> None:
        write_rows(csv_path, merge_rows(existing, collected))

    columns = (
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    )
    with (
        temporary_worktree(source) as tree,
        Progress(*columns, disable=not progress) as bar,
    ):
        task = bar.add_task(f"Counting {resolved_name}", total=len(pending))
        try:
            for index, sample in enumerate(pending, start=1):
                try:
                    checkout(tree, sample.commit)
                    rows = _rows_for_sample(sample, counter, tree, exclude_dirs)
                except (CounterError, GitError):
                    if strict:
                        raise
                    failed += 1
                    _LOG.warning("Skipping %s: counting failed.", sample.commit[:12])
                else:
                    if rows:
                        collected.extend(rows)
                    else:
                        empty += 1
                        _LOG.info(
                            "%s has no countable languages; it will be re-examined on later runs.",
                            sample.commit[:12],
                        )
                bar.advance(task)
                if index % flush_every == 0:
                    flush()
        finally:
            # Persist whatever was collected on every way out of the loop,
            # not just a normal finish: a strict re-raise, an exception
            # the loop does not catch, and a KeyboardInterrupt must all
            # leave a valid, resumable file instead of losing every
            # sample counted since the last periodic flush.  If flush()
            # itself fails while another exception is already
            # propagating, that original exception is what the caller
            # needs to see, so the flush failure is logged rather than
            # left to replace it.
            active_exception = sys.exc_info()[1]
            try:
                flush()
            except Exception:
                if active_exception is None:
                    raise
                _LOG.exception("Failed to flush results while handling %r.", active_exception)

    final = read_rows(csv_path)
    for_counter = [r for r in final if r.counter == counter.name]
    dates = sorted({r.date for r in for_counter})

    write_meta(
        meta_path,
        RepoMeta(
            name=resolved_name,
            url=target,
            mode=mode,
            branch=resolved_branch,
            tag_pattern=tag_pattern,
            exclude_dirs=list(exclude_dirs),
        ),
    )

    return CollectResult(
        added=len({r.commit for r in collected}),
        skipped=skipped,
        failed=failed,
        empty=empty,
        total=len({r.commit for r in for_counter}),
        languages=sorted({r.language for r in for_counter}),
        first_date=dates[0] if dates else None,
        last_date=dates[-1] if dates else None,
    )

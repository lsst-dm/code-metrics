"""Orchestration of a per-repository collection run."""

import logging
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
    output_dir: Path = Path("data/repos"),
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
    output_dir : `~pathlib.Path`, optional
        Directory to write the CSV and sidecar into.
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
        Write partial results after this many revisions.
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
    csv_path = output_dir / f"{resolved_name}.csv"
    meta_path = output_dir / f"{resolved_name}.meta.yaml"

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

    flush()
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

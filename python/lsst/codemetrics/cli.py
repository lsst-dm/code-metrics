"""Command line interface for the code metrics tools."""

import logging
import subprocess
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .collect import CollectResult, collect
from .counters import COUNTERS, ClocCounter, get_counter
from .location import repo_dir
from .revisions import MODES, GitError
from .stack import (
    bootstrap_distrib,
    build_targets,
    discover_weekly_tags,
    load_legacy_entries,
    load_tags_file,
    lsstsw_paths,
    scan_target,
    should_scan,
)
from .worktree import DEFAULT_CACHE_DIR

_LOG = logging.getLogger(__name__)

_MAX_REPORTED_FAILURES = 20
"""Failed tags shown by name before the summary switches to a count of
the remainder (`int`).
"""


@click.group()
@click.version_option(package_name="lsst-code-metrics")
def main() -> None:
    """Measure the size of a code base over time."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(show_path=False, show_time=False)],
    )


def summary_table(result: CollectResult, name: str) -> Table:
    """Build the end-of-run summary.

    Parameters
    ----------
    result : `~lsst.codemetrics.collect.CollectResult`
        Summary of the run.
    name : `str`
        Repository name.

    Returns
    -------
    table : `rich.table.Table`
        Table ready to print.
    """
    table = Table(title=f"{name} line counts")
    table.add_column("Measure")
    table.add_column("Value", justify="right")
    table.add_row("Revisions added", str(result.added))
    table.add_row("Revisions already present", str(result.skipped))
    table.add_row("Revisions failed", str(result.failed))
    table.add_row(
        "Revisions with no countable code (retried next run)",
        str(result.empty),
    )
    table.add_row("Revisions in file", str(result.total))
    table.add_row("Languages", ", ".join(result.languages) or "-")
    span = "-"
    if result.first_date and result.last_date:
        span = f"{result.first_date.date()} to {result.last_date.date()}"
    table.add_row("Date range", span)
    return table


@main.command("repo-history")
@click.argument("repo")
@click.option("--name", default=None, help="Output base name. Defaults to the repository basename.")
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Data root to write beneath, as <root>/repos/<name>.csv. Falls back to "
        "$CODE_METRICS_DATA_DIR, then the config file, then the current directory."
    ),
)
@click.option(
    "--mode",
    type=click.Choice(MODES),
    default="first-parent",
    show_default=True,
    help="How to choose the revisions to measure.",
)
@click.option("--branch", default=None, help="Branch to walk. Defaults to the remote HEAD.")
@click.option("--tag-pattern", default="w.*", show_default=True, help="Tag glob for --mode tags.")
@click.option(
    "--counter",
    "counter_name",
    type=click.Choice(sorted(COUNTERS)),
    default="cloc",
    show_default=True,
    help="Counting backend.",
)
@click.option("--exclude-dir", multiple=True, help="Directory name to skip. Repeatable.")
@click.option("--since", type=click.DateTime(), default=None, help="Ignore revisions before this date.")
@click.option("--until", type=click.DateTime(), default=None, help="Ignore revisions after this date.")
@click.option(
    "--cache-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=DEFAULT_CACHE_DIR,
    show_default=True,
    help="Directory holding cached bare clones.",
)
@click.option("--force", is_flag=True, help="Recount revisions that are already stored.")
@click.option("--strict", is_flag=True, help="Abort on the first failure instead of skipping it.")
def repo_history(
    repo: str,
    name: str | None,
    data_dir: Path | None,
    mode: str,
    branch: str | None,
    tag_pattern: str,
    counter_name: str,
    exclude_dir: tuple[str, ...],
    since: datetime | None,
    until: datetime | None,
    cache_dir: Path,
    force: bool,
    strict: bool,
) -> None:
    # This docstring is the command's --help text, so it carries no
    # Parameters section; each option documents itself in its help string.
    # numpydoc ignore=PR01
    """Count lines in REPO across its history.

    REPO is a local path or a remote URL.
    """
    try:
        result = collect(
            repo,
            name=name,
            data_dir=data_dir,
            mode=mode,
            branch=branch,
            tag_pattern=tag_pattern,
            counter=get_counter(counter_name),
            exclude_dirs=exclude_dir,
            since=since,
            until=until,
            cache_dir=cache_dir,
            force=force,
            strict=strict,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    console = Console()
    # Say where the counts went. The command that writes and the notebook
    # that reads resolve this the same way, and printing it is what makes
    # a misconfigured root obvious rather than looking like missing data.
    console.print(f"Wrote counts to {repo_dir(data_dir).describe()}")
    console.print(summary_table(result, name or repo))


def _format_tag_list(tags: list[str]) -> str:
    """Render a list of tag names for display in a summary table.

    Parameters
    ----------
    tags : `list` [ `str` ]
        Tags to render, in the order they were attempted.

    Returns
    -------
    text : `str`
        Comma-separated tag names.  If there are more than
        `_MAX_REPORTED_FAILURES`, the list is truncated and the count of
        the remainder is appended instead of printing them all.
    """
    if len(tags) <= _MAX_REPORTED_FAILURES:
        return ", ".join(tags)
    shown = ", ".join(tags[:_MAX_REPORTED_FAILURES])
    remainder = len(tags) - _MAX_REPORTED_FAILURES
    return f"{shown}, and {remainder} more"


@main.command("stack-scan")
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("data"),
    show_default=True,
    help="Directory to write per-tag reports into.",
)
@click.option(
    "--tags-file",
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
    default=None,
    help="Replace the derived tag list, legacy entries included.",
)
@click.option(
    "--legacy/--no-legacy",
    default=True,
    show_default=True,
    help="Include the pre-weekly release tags.",
)
@click.option("--force", is_flag=True, help="Rescan tags whose report already exists.")
@click.option(
    "--force-legacy",
    is_flag=True,
    help="Extend --force to the pre-weekly releases, overwriting curated names.",
)
@click.option("--strict", is_flag=True, help="Abort on the first failure instead of skipping it.")
def stack_scan(
    output_dir: Path,
    tags_file: Path | None,
    legacy: bool,
    force: bool,
    force_legacy: bool,
    strict: bool,
) -> None:
    # This docstring is the command's --help text, so it carries no
    # Parameters section; each option documents itself in its help string.
    # numpydoc ignore=PR01
    """Count lines across lsst_distrib at each release tag.

    Requires an lsstsw environment with LSST_BUILD_DIR set.
    """
    try:
        lsstsw_dir, build_dir, lsst_build_exe = lsstsw_paths()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    if tags_file is not None:
        targets = load_tags_file(tags_file)
    else:
        try:
            distrib = bootstrap_distrib(lsstsw_dir, build_dir, lsst_build_exe)
        except (GitError, subprocess.CalledProcessError) as exc:
            raise click.ClickException(f"Could not bootstrap lsst_distrib: {exc}") from exc
        try:
            weeklies = discover_weekly_tags(distrib)
        except GitError as exc:
            raise click.ClickException(f"Could not discover weekly tags: {exc}") from exc
        targets = build_targets(load_legacy_entries(), weeklies, include_legacy=legacy)

    pending = [t for t in targets if should_scan(t, output_dir, force, force_legacy)]
    console = Console()
    console.print(f"{len(pending)} of {len(targets)} tags need scanning.")

    counter = ClocCounter()
    scanned = 0
    failed_tags: list[str] = []
    for target in pending:
        try:
            scan_target(
                target,
                lsstsw_dir=lsstsw_dir,
                build_dir=build_dir,
                lsst_build_exe=lsst_build_exe,
                output_dir=output_dir,
                counter=counter,
            )
            scanned += 1
        except Exception as exc:
            if strict:
                raise
            failed_tags.append(target.tag)
            _LOG.warning("Skipping %s: %s: %s", target.tag, type(exc).__name__, exc, exc_info=True)

    table = Table(title="Stack scan")
    table.add_column("Measure")
    table.add_column("Value", justify="right")
    table.add_row("Tags scanned", str(scanned))
    table.add_row("Tags skipped", str(len(targets) - len(pending)))
    table.add_row("Tags failed", str(len(failed_tags)))
    if failed_tags:
        table.add_row("Failed tags", _format_tag_list(failed_tags))
    console.print(table)

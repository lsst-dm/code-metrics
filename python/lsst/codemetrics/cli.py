"""Command line interface for the code metrics tools."""

import logging
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .collect import CollectResult, collect
from .counters import COUNTERS, get_counter
from .revisions import MODES
from .worktree import DEFAULT_CACHE_DIR


@click.group()
@click.version_option()
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
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("data/repos"),
    show_default=True,
    help="Directory to write the CSV and sidecar into.",
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
    output_dir: Path,
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
    """Count lines in REPO across its history.

    REPO is a local path or a remote URL.
    """
    try:
        result = collect(
            repo,
            name=name,
            output_dir=output_dir,
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
    Console().print(summary_table(result, name or repo))

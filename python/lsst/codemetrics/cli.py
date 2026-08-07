"""Command line interface for the code metrics tools."""

import logging
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .collect import CollectResult, collect
from .counters import COUNTERS, ClocCounter, get_counter
from .revisions import MODES
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
        distrib = bootstrap_distrib(lsstsw_dir, build_dir, lsst_build_exe)
        targets = build_targets(load_legacy_entries(), discover_weekly_tags(distrib), include_legacy=legacy)

    pending = [t for t in targets if should_scan(t, output_dir, force, force_legacy)]
    console = Console()
    console.print(f"{len(pending)} of {len(targets)} tags need scanning.")

    counter = ClocCounter()
    scanned = 0
    failed = 0
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
        except Exception:
            if strict:
                raise
            failed += 1
            logging.getLogger(__name__).warning("Skipping %s: scan failed.", target.tag)

    table = Table(title="Stack scan")
    table.add_column("Measure")
    table.add_column("Value", justify="right")
    table.add_row("Tags scanned", str(scanned))
    table.add_row("Tags skipped", str(len(targets) - len(pending)))
    table.add_row("Tags failed", str(failed))
    console.print(table)

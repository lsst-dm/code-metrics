import shutil

import pytest
from click.testing import CliRunner
from lsst.codemetrics.cli import main, summary_table
from lsst.codemetrics.collect import CollectResult


def test_repo_history_help_lists_the_options():
    result = CliRunner().invoke(main, ["repo-history", "--help"])
    assert result.exit_code == 0
    for option in ("--mode", "--counter", "--exclude-dir", "--since", "--force", "--strict"):
        assert option in result.output


@pytest.mark.skipif(shutil.which("cloc") is None, reason="cloc not installed")
def test_repo_history_runs_against_a_local_repo(synthetic_repo, tmp_path):
    result = CliRunner().invoke(
        main,
        [
            "repo-history",
            str(synthetic_repo),
            "--output-dir",
            str(tmp_path),
            "--branch",
            "main",
            "--counter",
            "cloc",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "synthetic.csv").exists()
    assert "synthetic" in result.output


def test_unknown_counter_is_rejected():
    result = CliRunner().invoke(main, ["repo-history", ".", "--counter", "nope"])
    assert result.exit_code != 0


def test_empty_range_exits_non_zero(synthetic_repo, tmp_path):
    result = CliRunner().invoke(
        main,
        [
            "repo-history",
            str(synthetic_repo),
            "--output-dir",
            str(tmp_path),
            "--branch",
            "main",
            "--since",
            "2030-01-01",
        ],
    )
    assert result.exit_code != 0


def test_summary_table_shows_empty_revisions():
    result = CollectResult(
        added=1,
        skipped=2,
        failed=3,
        empty=4,
        total=5,
        languages=["Python"],
        first_date=None,
        last_date=None,
    )
    table = summary_table(result, "demo")
    measures = [str(cell) for cell in table.columns[0].cells]
    values = [str(cell) for cell in table.columns[1].cells]
    rows = dict(zip(measures, values, strict=True))
    matches = [label for label in measures if "retried" in label]
    assert matches, f"no row explains the empty count: {measures}"
    assert rows[matches[0]] == "4"

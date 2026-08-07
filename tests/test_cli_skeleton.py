from click.testing import CliRunner
from lsst.codemetrics.cli import main


def test_main_group_runs():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "repo-history" in result.output or "Usage" in result.output

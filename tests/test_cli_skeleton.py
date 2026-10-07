from importlib.metadata import version

from click.testing import CliRunner
from lsst.codemetrics.cli import main


def test_main_group_runs():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "repo-history" in result.output or "Usage" in result.output


def test_main_group_reports_package_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert version("lsst-code-metrics") in result.output

from click.testing import CliRunner
from lsst.codemetrics.cli import main
from lsst.codemetrics.stack import ScanTarget, should_scan


def weekly(name="w.2020.01"):
    return ScanTarget(tag=name, output_name=name, legacy=False)


def legacy(name="9.0", out="w.2014.31"):
    return ScanTarget(tag=name, output_name=out, legacy=True)


def test_missing_output_is_always_scanned(tmp_path):
    assert should_scan(weekly(), tmp_path, force=False, force_legacy=False)


def test_existing_output_is_skipped(tmp_path):
    (tmp_path / "w.2020.01.yaml").write_text("")
    assert not should_scan(weekly(), tmp_path, force=False, force_legacy=False)


def test_force_rescans_a_weekly(tmp_path):
    (tmp_path / "w.2020.01.yaml").write_text("")
    assert should_scan(weekly(), tmp_path, force=True, force_legacy=False)


def test_force_alone_does_not_touch_legacy(tmp_path):
    (tmp_path / "w.2014.31.yaml").write_text("")
    assert not should_scan(legacy(), tmp_path, force=True, force_legacy=False)


def test_force_legacy_rescans_legacy(tmp_path):
    (tmp_path / "w.2014.31.yaml").write_text("")
    assert should_scan(legacy(), tmp_path, force=True, force_legacy=True)


def test_stack_scan_help_lists_the_options():
    result = CliRunner().invoke(main, ["stack-scan", "--help"])
    assert result.exit_code == 0
    for option in ("--tags-file", "--legacy", "--force-legacy", "--strict"):
        assert option in result.output


def test_stack_scan_without_environment_fails_clearly(monkeypatch):
    monkeypatch.delenv("LSST_BUILD_DIR", raising=False)
    result = CliRunner().invoke(main, ["stack-scan"])
    assert result.exit_code != 0
    assert "lsst_build" in result.output

import logging

from click.testing import CliRunner
from lsst.codemetrics import cli
from lsst.codemetrics.cli import main
from lsst.codemetrics.stack import ScanTarget, should_scan


class _BoomError(Exception):
    """Distinctive exception raised by a patched ``scan_target``."""


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


def test_a_failing_tag_is_logged_with_its_exception_and_named_in_the_summary(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("LSST_BUILD_DIR", str(tmp_path / "lsstsw" / ".lsst-build"))
    tags_file = tmp_path / "tags.txt"
    tags_file.write_text("w.2020.01\nw.2020.02\n")

    def fake_scan_target(target, **kwargs):
        if target.tag == "w.2020.02":
            raise _BoomError("kaboom")

    monkeypatch.setattr(cli, "scan_target", fake_scan_target)

    with caplog.at_level(logging.WARNING, logger="lsst.codemetrics.cli"):
        result = CliRunner().invoke(
            main,
            [
                "stack-scan",
                "--tags-file",
                str(tags_file),
                "--output-dir",
                str(tmp_path / "out"),
            ],
        )

    assert result.exit_code == 0, result.output
    assert any("w.2020.02" in record.message and "_BoomError" in record.message for record in caplog.records)
    assert "w.2020.02" in result.output

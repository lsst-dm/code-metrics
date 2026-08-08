import pytest
from lsst.codemetrics import stack
from lsst.codemetrics.stack import (
    EXCLUDED_PRODUCTS,
    INCLUDE_LANGS,
    ScanTarget,
    build_targets,
    discover_weekly_tags,
    load_legacy_entries,
    load_tags_file,
    manifest_products,
    scan_target,
)


def test_include_langs_match_the_historical_filter():
    assert INCLUDE_LANGS == ("Python", "C++", "C/C++ Header")


def test_legacy_entries_load_with_their_recorded_names():
    entries = load_legacy_entries()
    mapping = {e.tag: e.output_name for e in entries}
    assert mapping == {
        "7.2.0.0": "w.2013.25",
        "8.0.0.0": "w.2014.20",
        "b128": "w.2014.30",
        "9.0": "w.2014.31",
        "9.2": "w.2014.32",
        "10.0": "w.2014.50",
        "10.1": "w.2015.20",
    }


def test_legacy_entries_keep_their_recorded_order():
    assert [e.tag for e in load_legacy_entries()][0] == "7.2.0.0"


def test_legacy_comments_are_stripped():
    entries = {e.tag: e for e in load_legacy_entries()}
    assert entries["8.0.0.0"].tag_date == "2014-03-15"


def test_tags_file_accepts_one_or_two_columns(tmp_path):
    path = tmp_path / "tags.txt"
    path.write_text("# comment\nw.2020.01\n9.0  w.2014.31\n\n")
    targets = load_tags_file(path)
    assert [(t.tag, t.output_name) for t in targets] == [
        ("w.2020.01", "w.2020.01"),
        ("9.0", "w.2014.31"),
    ]
    assert [t.legacy for t in targets] == [False, True]


def test_tags_file_protects_a_legacy_output_name(tmp_path):
    path = tmp_path / "tags.txt"
    path.write_text("custom-tag w.2014.31\n")
    assert load_tags_file(path)[0].legacy is True


def test_build_targets_puts_legacy_first():
    legacy = load_legacy_entries()
    targets = build_targets(legacy, ["w.2015.22", "w.2015.30"], include_legacy=True)
    assert targets[0].tag == "7.2.0.0"
    assert targets[0].legacy is True
    assert targets[-1].tag == "w.2015.30"
    assert targets[-1].legacy is False


def test_build_targets_can_omit_legacy():
    legacy = load_legacy_entries()
    targets = build_targets(legacy, ["w.2015.22"], include_legacy=False)
    assert [t.tag for t in targets] == ["w.2015.22"]


def test_discover_weekly_tags_orders_chronologically(synthetic_repo):
    assert discover_weekly_tags(synthetic_repo) == ["w.2020.9", "w.2020.10"]


def test_manifest_products_filters_third_party(tmp_path):
    (tmp_path / "manifest.txt").write_text(
        "# comment\nBUILD=b1\nafw g1\nboost g2\nmetadetect g3\nsconsUtils g4\n"
    )
    (tmp_path / "boost" / "ups").mkdir(parents=True)
    (tmp_path / "boost" / "ups" / "eupspkg.cfg.sh").write_text("")
    (tmp_path / "afw").mkdir()
    (tmp_path / "sconsUtils").mkdir()
    (tmp_path / "metadetect").mkdir()
    assert manifest_products(tmp_path) == ["afw", "sconsUtils"]


def test_manifest_products_filters_upstream_dirs(tmp_path):
    (tmp_path / "manifest.txt").write_text("afw g1\nfftw g2\n")
    (tmp_path / "afw").mkdir()
    (tmp_path / "fftw" / "upstream").mkdir(parents=True)
    assert manifest_products(tmp_path) == ["afw"]


def test_metadetect_is_excluded():
    assert "metadetect" in EXCLUDED_PRODUCTS


def test_scan_target_replaces_report_only_after_success(tmp_path, monkeypatch):
    monkeypatch.setattr(stack, "_prepare", lambda *args: None)
    monkeypatch.setattr(stack, "manifest_products", lambda build_dir: ["afw"])
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    destination = output_dir / "w.2020.01.yaml"
    destination.write_text("complete\n")

    class FailingCounter:
        def write_report(self, paths, output_file, include_langs=None):
            output_file.write_text("partial\n")
            raise RuntimeError("counting failed")

    with pytest.raises(RuntimeError, match="counting failed"):
        scan_target(
            ScanTarget(tag="w.2020.01", output_name="w.2020.01", legacy=False),
            lsstsw_dir=tmp_path,
            build_dir=tmp_path / "build",
            lsst_build_exe=tmp_path / "lsst-build",
            output_dir=output_dir,
            counter=FailingCounter(),
        )

    assert destination.read_text() == "complete\n"


def test_scan_target_atomically_installs_completed_report(tmp_path, monkeypatch):
    monkeypatch.setattr(stack, "_prepare", lambda *args: None)
    monkeypatch.setattr(stack, "manifest_products", lambda build_dir: ["afw"])

    class SuccessfulCounter:
        def write_report(self, paths, output_file, include_langs=None):
            output_file.write_text("complete\n")

    output_dir = tmp_path / "output"
    scan_target(
        ScanTarget(tag="w.2020.01", output_name="w.2020.01", legacy=False),
        lsstsw_dir=tmp_path,
        build_dir=tmp_path / "build",
        lsst_build_exe=tmp_path / "lsst-build",
        output_dir=output_dir,
        counter=SuccessfulCounter(),
    )

    assert (output_dir / "w.2020.01.yaml").read_text() == "complete\n"

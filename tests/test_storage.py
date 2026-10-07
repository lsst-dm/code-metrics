import csv
import os
import stat
from datetime import UTC, datetime

import pytest
import yaml
from lsst.codemetrics.storage import (
    COLUMNS,
    LineRow,
    RepoMeta,
    collected_keys,
    merge_rows,
    read_meta,
    read_rows,
    write_meta,
    write_rows,
)
from pydantic import ValidationError


def make_row(commit="abc", language="Python", counter="cloc", day=1):
    return LineRow(
        commit=commit,
        date=datetime(2020, 1, day, tzinfo=UTC),
        label="",
        counter=counter,
        counter_version="2.10",
        language=language,
        n_files=1,
        blank=2,
        comment=3,
        code=4,
    )


def make_meta(**overrides):
    defaults = {
        "name": "afw",
        "url": "https://github.com/lsst/afw",
        "mode": "first-parent",
        "branch": "main",
        "tag_pattern": "w.*",
        "exclude_dirs": ["vendor"],
    }
    defaults.update(overrides)
    return RepoMeta(**defaults)


def test_columns_are_the_documented_order():
    assert COLUMNS == (
        "commit",
        "date",
        "label",
        "counter",
        "counter_version",
        "language",
        "n_files",
        "blank",
        "comment",
        "code",
    )


def test_round_trip(tmp_path):
    path = tmp_path / "repo.csv"
    rows = [make_row(), make_row(language="C++")]
    write_rows(path, rows)
    assert read_rows(path) == sorted(rows, key=lambda r: (r.date, r.commit, r.language))


def test_reading_a_missing_file_returns_empty(tmp_path):
    assert read_rows(tmp_path / "absent.csv") == []


def test_rows_are_written_sorted(tmp_path):
    path = tmp_path / "repo.csv"
    write_rows(path, [make_row(commit="z", day=5), make_row(commit="a", day=2)])
    assert [r.commit for r in read_rows(path)] == ["a", "z"]


def test_incremental_write_only_appends(tmp_path):
    path = tmp_path / "repo.csv"
    write_rows(path, [make_row(commit="a", day=1)])
    first = path.read_text()
    write_rows(path, [make_row(commit="a", day=1), make_row(commit="b", day=2)])
    second = path.read_text()
    assert second.startswith(first)


def test_write_rows_survives_a_failure_partway_through_serialization(tmp_path, monkeypatch):
    path = tmp_path / "repo.csv"
    write_rows(path, [make_row(commit="a", day=1)])
    original = path.read_text()

    real_writerow = csv.DictWriter.writerow
    calls = {"n": 0}

    def flaky_writerow(self, rowdict):
        # Header is call 1; the third data row (call 4) fails, so two
        # data rows have already been serialized when the error hits.
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("boom")
        return real_writerow(self, rowdict)

    monkeypatch.setattr(csv.DictWriter, "writerow", flaky_writerow)

    with pytest.raises(RuntimeError):
        write_rows(
            path,
            [
                make_row(commit="a", day=1),
                make_row(commit="b", day=2),
                make_row(commit="c", day=3),
            ],
        )

    assert path.read_text() == original
    assert read_rows(path) == [make_row(commit="a", day=1)]


def test_write_rows_creates_a_file_with_the_default_mode(tmp_path):
    path = tmp_path / "repo.csv"
    write_rows(path, [make_row()])
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_write_rows_preserves_an_existing_files_mode(tmp_path):
    path = tmp_path / "repo.csv"
    write_rows(path, [make_row(commit="a", day=1)])
    path.chmod(0o640)
    write_rows(path, [make_row(commit="a", day=1), make_row(commit="b", day=2)])
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_write_rows_does_not_leak_a_descriptor_when_fchmod_fails(tmp_path, monkeypatch):
    path = tmp_path / "repo.csv"
    write_rows(path, [make_row(commit="a", day=1)])
    original = path.read_text()

    def raising_fchmod(fd, mode):
        raise OSError("boom")

    monkeypatch.setattr(os, "fchmod", raising_fchmod)

    fd_count_before = len(os.listdir("/dev/fd"))

    with pytest.raises(OSError):
        write_rows(path, [make_row(commit="a", day=1), make_row(commit="b", day=2)])

    fd_count_after = len(os.listdir("/dev/fd"))

    assert fd_count_after == fd_count_before
    assert path.read_text() == original
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(f".{path.name}.")]
    assert leftovers == []


def test_collected_keys_pairs_commit_with_counter():
    rows = [make_row(commit="a", counter="cloc"), make_row(commit="a", counter="tokei")]
    assert collected_keys(rows) == {("a", "cloc"), ("a", "tokei")}


def test_merge_replaces_matching_commit_and_counter():
    existing = [make_row(commit="a", counter="cloc", language="Python")]
    new = [make_row(commit="a", counter="cloc", language="C++")]
    merged = merge_rows(existing, new)
    assert [r.language for r in merged] == ["C++"]


def test_merge_keeps_other_counters_for_the_same_commit():
    existing = [make_row(commit="a", counter="cloc")]
    new = [make_row(commit="a", counter="tokei")]
    merged = merge_rows(existing, new)
    assert {r.counter for r in merged} == {"cloc", "tokei"}


def test_malformed_row_is_rejected(tmp_path):
    path = tmp_path / "repo.csv"
    path.write_text(",".join(COLUMNS) + "\n" + "abc,2020-01-01T00:00:00+00:00,,cloc,2.10,Python,x,2,3,4\n")
    with pytest.raises(ValidationError):
        read_rows(path)


def test_write_meta_is_readable_yaml(tmp_path):
    path = tmp_path / "repo.meta.yaml"
    meta = RepoMeta(
        name="afw",
        url="https://github.com/lsst/afw",
        mode="first-parent",
        branch="main",
        tag_pattern="w.*",
        exclude_dirs=["vendor"],
    )
    write_meta(path, meta)
    loaded = yaml.safe_load(path.read_text())
    assert loaded["name"] == "afw"
    assert loaded["exclude_dirs"] == ["vendor"]


def test_read_meta_round_trips_written_metadata(tmp_path):
    path = tmp_path / "repo.meta.yaml"
    meta = make_meta()
    write_meta(path, meta)
    assert read_meta(path) == meta


def test_reading_missing_metadata_returns_none(tmp_path):
    assert read_meta(tmp_path / "absent.meta.yaml") is None


def test_write_meta_creates_a_file_with_the_default_mode(tmp_path):
    path = tmp_path / "repo.meta.yaml"
    write_meta(path, make_meta())
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_write_meta_preserves_an_existing_files_mode(tmp_path):
    path = tmp_path / "repo.meta.yaml"
    write_meta(path, make_meta())
    path.chmod(0o640)
    write_meta(path, make_meta(name="obs_base"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o640

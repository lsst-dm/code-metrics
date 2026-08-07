from datetime import UTC, datetime

import pytest
from lsst.codemetrics.collect import collect
from lsst.codemetrics.counters import CounterError, LanguageCount, LineCounter
from lsst.codemetrics.storage import read_rows


class StubCounter(LineCounter):
    """A counter that reports a fixed result without running anything."""

    name = "stub"

    def __init__(self, fail_on=None, empty_on=None):
        super().__init__()
        self.fail_on = fail_on or set()
        self.empty_on = empty_on or set()
        self.calls = 0

    def _parse_version(self, raw):
        return "0.0"

    @property
    def version(self):
        return "0.0"

    def parse(self, raw):
        return {}

    def count(self, path, exclude_dirs=()):
        self.calls += 1
        if self.calls in self.fail_on:
            raise CounterError("stub failure")
        if self.calls in self.empty_on:
            return {}
        return {"Python": LanguageCount(n_files=1, blank=1, comment=2, code=3)}


def test_collects_every_sample(synthetic_repo, tmp_path):
    result = collect(
        str(synthetic_repo),
        name="synthetic",
        output_dir=tmp_path,
        mode="first-parent",
        branch="main",
        counter=StubCounter(),
        progress=False,
    )
    assert result.added == 3
    assert result.total == 3
    assert result.languages == ["Python"]
    rows = read_rows(tmp_path / "synthetic.csv")
    assert len(rows) == 3
    assert {r.counter for r in rows} == {"stub"}


def test_second_run_adds_nothing(synthetic_repo, tmp_path):
    kwargs = {
        "name": "synthetic",
        "output_dir": tmp_path,
        "mode": "first-parent",
        "branch": "main",
        "progress": False,
    }
    collect(str(synthetic_repo), counter=StubCounter(), **kwargs)
    second = collect(str(synthetic_repo), counter=StubCounter(), **kwargs)
    assert second.added == 0
    assert second.skipped == 3
    assert second.total == 3


def test_switching_counter_recounts_and_keeps_both(synthetic_repo, tmp_path):
    kwargs = {
        "name": "synthetic",
        "output_dir": tmp_path,
        "mode": "first-parent",
        "branch": "main",
        "progress": False,
    }
    collect(str(synthetic_repo), counter=StubCounter(), **kwargs)

    class OtherCounter(StubCounter):
        name = "other"

    collect(str(synthetic_repo), counter=OtherCounter(), **kwargs)
    rows = read_rows(tmp_path / "synthetic.csv")
    assert {r.counter for r in rows} == {"stub", "other"}
    assert len(rows) == 6


def test_a_failing_sample_is_skipped(synthetic_repo, tmp_path):
    result = collect(
        str(synthetic_repo),
        name="synthetic",
        output_dir=tmp_path,
        mode="first-parent",
        branch="main",
        counter=StubCounter(fail_on={2}),
        progress=False,
    )
    assert result.added == 2
    assert result.failed == 1


def test_strict_aborts_on_failure(synthetic_repo, tmp_path):
    with pytest.raises(CounterError):
        collect(
            str(synthetic_repo),
            name="synthetic",
            output_dir=tmp_path,
            mode="first-parent",
            branch="main",
            counter=StubCounter(fail_on={2}),
            strict=True,
            progress=False,
        )


def test_empty_sample_list_raises(synthetic_repo, tmp_path):
    with pytest.raises(ValueError, match="No revisions"):
        collect(
            str(synthetic_repo),
            name="synthetic",
            output_dir=tmp_path,
            mode="first-parent",
            branch="main",
            counter=StubCounter(),
            since=datetime(2030, 1, 1, tzinfo=UTC),
            progress=False,
        )


def test_sidecar_metadata_is_written(synthetic_repo, tmp_path):
    collect(
        str(synthetic_repo),
        name="synthetic",
        output_dir=tmp_path,
        mode="first-parent",
        branch="main",
        counter=StubCounter(),
        exclude_dirs=["vendor"],
        progress=False,
    )
    assert (tmp_path / "synthetic.meta.yaml").exists()


def test_name_defaults_to_the_repository_basename(synthetic_repo, tmp_path):
    collect(
        str(synthetic_repo),
        output_dir=tmp_path,
        mode="first-parent",
        branch="main",
        counter=StubCounter(),
        progress=False,
    )
    assert (tmp_path / "synthetic.csv").exists()


def test_force_recounts_everything(synthetic_repo, tmp_path):
    kwargs = {
        "name": "synthetic",
        "output_dir": tmp_path,
        "mode": "first-parent",
        "branch": "main",
        "progress": False,
    }
    collect(str(synthetic_repo), counter=StubCounter(), **kwargs)
    counter = StubCounter()
    result = collect(str(synthetic_repo), counter=counter, force=True, **kwargs)
    assert result.added == 3
    assert counter.calls == 3


def test_a_sample_with_no_languages_is_counted_as_empty(synthetic_repo, tmp_path):
    result = collect(
        str(synthetic_repo),
        name="synthetic",
        output_dir=tmp_path,
        mode="first-parent",
        branch="main",
        counter=StubCounter(empty_on={2}),
        progress=False,
    )
    assert result.empty == 1
    assert result.added == 2
    assert result.failed == 0
    assert result.skipped == 0
    assert result.added + result.skipped + result.failed + result.empty == 3
    rows = read_rows(tmp_path / "synthetic.csv")
    assert len(rows) == 2
    assert len({r.commit for r in rows}) == 2


def test_an_empty_sample_is_re_examined_on_the_next_run(synthetic_repo, tmp_path):
    kwargs = {
        "name": "synthetic",
        "output_dir": tmp_path,
        "mode": "first-parent",
        "branch": "main",
        "progress": False,
    }
    first = collect(str(synthetic_repo), counter=StubCounter(empty_on={2}), **kwargs)
    assert first.empty == 1

    counter = StubCounter()
    second = collect(str(synthetic_repo), counter=counter, **kwargs)
    assert counter.calls == 1
    assert second.added == 1
    assert second.skipped == 2
    assert second.empty == 0
    assert second.added + second.skipped + second.failed + second.empty == 3

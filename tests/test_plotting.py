from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("pandas")

from lsst.codemetrics.plotting import (  # noqa: E402
    CLOC_CPP_ALIASES,
    apply_aliases,
    load_repo,
    load_stack,
    pivot,
    select,
)
from lsst.codemetrics.storage import LineRow, write_rows  # noqa: E402


@pytest.fixture
def repo_csv(tmp_path):
    rows = [
        LineRow(
            commit="a",
            date=datetime(2020, 1, 1, tzinfo=UTC),
            counter="cloc",
            counter_version="2.10",
            language=language,
            n_files=1,
            blank=1,
            comment=2,
            code=3,
        )
        for language in ("Python", "C++", "C/C++ Header")
    ]
    rows.append(
        LineRow(
            commit="a",
            date=datetime(2020, 1, 1, tzinfo=UTC),
            counter="tokei",
            counter_version="14.0.0",
            language="Python",
            n_files=1,
            blank=1,
            comment=2,
            code=3,
        )
    )
    write_rows(tmp_path / "demo.csv", rows)
    return tmp_path


def test_load_repo_derives_lines(repo_csv):
    frame = load_repo("demo", repo_csv)
    assert (frame["lines"] == frame["code"] + frame["comment"]).all()


def test_apply_aliases_folds_headers_into_cpp(repo_csv):
    frame = select(load_repo("demo", repo_csv), counter="cloc")
    folded = apply_aliases(frame, CLOC_CPP_ALIASES)
    assert set(folded["language"]) == {"Python", "C++"}
    cpp = folded[folded["language"] == "C++"]
    assert len(cpp) == 1
    assert cpp["code"].iloc[0] == 6


def test_select_by_counter(repo_csv):
    frame = select(load_repo("demo", repo_csv), counter="tokei")
    assert set(frame["counter"]) == {"tokei"}


def test_select_warns_when_counters_are_mixed(repo_csv):
    with pytest.warns(UserWarning, match="more than one counter"):
        select(load_repo("demo", repo_csv))


def test_select_by_language(repo_csv):
    frame = select(load_repo("demo", repo_csv), languages=["Python"], counter="cloc")
    assert set(frame["language"]) == {"Python"}


def test_pivot_makes_one_column_per_language(repo_csv):
    frame = select(load_repo("demo", repo_csv), counter="cloc")
    wide = pivot(frame, value="code")
    assert set(wide.columns) == {"Python", "C++", "C/C++ Header"}
    assert len(wide) == 1


def test_load_stack_reads_the_existing_yaml():
    # Locate data/ relative to this file so the test does not depend on
    # the working directory pytest was started from.
    data_dir = Path(__file__).parent.parent / "data"
    dates, datasets = load_stack(data_dir)
    assert len(dates) > 500
    assert "python_code" in datasets
    assert len(datasets["python_code"]) == len(dates)
    assert list(dates) == sorted(dates)

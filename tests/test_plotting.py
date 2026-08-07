from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import yaml

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


def _real_data_dir() -> Path:
    # Locate data/ relative to this file so the tests do not depend on
    # the working directory pytest was started from.
    return Path(__file__).parent.parent / "data"


def test_load_stack_produces_one_point_per_yaml_file():
    data_dir = _real_data_dir()
    file_count = len(list(data_dir.glob("w.*.yaml")))
    dates, datasets = load_stack(data_dir)
    assert len(dates) == file_count
    assert "python_code" in datasets
    assert datasets["python_code"][-1] == 593030
    for series in datasets.values():
        assert len(series) == len(dates)


def test_year_over_52_week_fraction_would_have_collided():
    # Negative control for test_load_stack_produces_one_point_per_yaml_file:
    # the previous "year + week / 52" mapping sent week 53 of year Y and
    # week 1 of year Y + 1 to the same key, so it would have produced
    # fewer unique fractions than there are files.  This confirms that
    # test is actually capable of catching that collision.
    data_dir = _real_data_dir()
    paths = list(data_dir.glob("w.*.yaml"))
    old_fractions = {float(p.name.split(".")[1]) + float(p.name.split(".")[2]) / 52.0 for p in paths}
    assert len(old_fractions) < len(paths)


def test_load_stack_dates_are_strictly_increasing():
    dates, _ = load_stack(_real_data_dir())
    assert (np.diff(dates) > 0).all()


def test_load_stack_resolves_the_week_53_collision(tmp_path):
    entry = {
        "Python": {"code": 1, "comment": 1, "blank": 1},
        "C++": {"code": 1, "comment": 1, "blank": 1},
        "C/C++ Header": {"code": 1, "comment": 1, "blank": 1},
        "SUM": {"code": 1, "comment": 1, "blank": 1},
    }
    (tmp_path / "w.2016.53.yaml").write_text(yaml.safe_dump(entry))
    (tmp_path / "w.2017.1.yaml").write_text(yaml.safe_dump(entry))

    dates, _ = load_stack(tmp_path)

    assert len(dates) == 2
    assert dates[0] == pytest.approx(2016.0 + 52.0 / 53.0)
    assert dates[1] == pytest.approx(2017.0)
    assert dates[0] != pytest.approx(dates[1])

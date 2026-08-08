from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import yaml

pytest.importorskip("pandas")

import pandas as pd  # noqa: E402
from lsst.codemetrics.plotting import (  # noqa: E402
    CPP_HEADER_ALIASES,
    apply_aliases,
    load_repo,
    load_stack,
    pivot,
    select,
    top_series,
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
    folded = apply_aliases(frame, CPP_HEADER_ALIASES)
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


def make_history_frame():
    """Two revisions; C++ peaks early then shrinks, YAML stays tiny."""
    rows = []
    # C++ ends smaller than every other language, so ranking on the final
    # revision would drop it; ranking on its peak of 900 must keep it.
    for day, sizes in (
        (1, {"Python": 100, "C++": 900, "YAML": 5, "Shell": 3, "reST": 2, "CMake": 1}),
        (2, {"Python": 800, "C++": 1, "YAML": 6, "Shell": 4, "reST": 3, "CMake": 2}),
    ):
        for language, code in sizes.items():
            rows.append(
                LineRow(
                    commit=f"c{day}",
                    date=datetime(2020, 1, day, tzinfo=UTC),
                    counter="cloc",
                    counter_version="2.10",
                    language=language,
                    n_files=1,
                    blank=1,
                    comment=code // 2,
                    code=code,
                )
            )
    frame = pd.DataFrame([r.model_dump() for r in rows])
    frame["lines"] = frame["code"] + frame["comment"]
    return frame


def header_frame(counter, header_languages):
    """A frame with C++ plus whatever that backend calls its headers."""
    rows = []
    for language in ("C++", *header_languages):
        rows.append(
            LineRow(
                commit="a",
                date=datetime(2020, 1, 1, tzinfo=UTC),
                counter=counter,
                counter_version="1.0",
                language=language,
                n_files=1,
                blank=1,
                comment=2,
                code=10,
            )
        )
    frame = pd.DataFrame([r.model_dump() for r in rows])
    frame["lines"] = frame["code"] + frame["comment"]
    return frame


def test_cpp_header_aliases_fold_cloc_headers():
    folded = apply_aliases(header_frame("cloc", ["C/C++ Header"]), CPP_HEADER_ALIASES)
    assert set(folded["language"]) == {"C++"}
    assert folded["code"].iloc[0] == 20


def test_cpp_header_aliases_fold_tokei_headers():
    folded = apply_aliases(header_frame("tokei", ["C Header", "C++ Header"]), CPP_HEADER_ALIASES)
    assert set(folded["language"]) == {"C++"}
    assert folded["code"].iloc[0] == 30


def test_cpp_header_aliases_fold_scc_headers():
    folded = apply_aliases(header_frame("scc", ["C Header", "C++ Header"]), CPP_HEADER_ALIASES)
    assert set(folded["language"]) == {"C++"}
    assert folded["code"].iloc[0] == 30


def test_cpp_header_aliases_leave_other_languages_alone():
    frame = header_frame("tokei", ["C Header", "Python"])
    folded = apply_aliases(frame, CPP_HEADER_ALIASES)
    assert set(folded["language"]) == {"C++", "Python"}


def mixed_frame():
    """Peaks chosen so ranking by code alone gets it wrong.

    TOML has code but literally no comments, the way JSON cannot have
    them.  Markdown is the mirror image: no code, but real comment
    content.  Ranking languages by code keeps TOML's dead comment series
    and drops Markdown altogether.
    """
    peaks = {
        "YAML": (16290, 543),
        "Python": (60, 590),
        "TOML": (24, 0),
        "Markdown": (0, 6),
        "Forge Config": (4, 0),
    }
    rows = []
    for day in (1, 2):
        for language, (code, comment) in peaks.items():
            rows.append(
                LineRow(
                    commit=f"c{day}",
                    date=datetime(2020, 1, day, tzinfo=UTC),
                    counter="cloc",
                    counter_version="2.10",
                    language=language,
                    n_files=1,
                    blank=0,
                    comment=comment,
                    code=code,
                )
            )
    frame = pd.DataFrame([r.model_dump() for r in rows])
    frame["lines"] = frame["code"] + frame["comment"]
    return frame


def test_top_series_ranks_across_languages_and_measures():
    assert top_series(mixed_frame(), n=3) == [
        ("YAML", "code"),
        ("Python", "comment"),
        ("YAML", "comment"),
    ]


def test_top_series_never_returns_an_all_zero_series():
    every = top_series(mixed_frame(), n=99)
    assert ("TOML", "comment") not in every
    assert ("Forge Config", "comment") not in every


def test_top_series_keeps_a_language_that_has_only_comments():
    # Markdown has no code at all, so ranking languages by code would
    # drop it even though its comment series carries real content.
    assert ("Markdown", "comment") in top_series(mixed_frame(), n=99)


def test_top_series_spends_a_freed_slot_on_real_content():
    # Five languages across two measures is ten nominal slots, but only
    # seven series carry anything. Asking for ten must yield those seven
    # rather than padding with dead comment lines.
    result = top_series(mixed_frame(), n=10)
    assert len(result) == 7
    assert ("TOML", "comment") not in result
    assert ("Forge Config", "comment") not in result
    assert ("Markdown", "code") not in result


def test_top_series_respects_n():
    assert len(top_series(mixed_frame(), n=2)) == 2


def test_top_series_returns_all_when_fewer_than_n():
    assert len(top_series(mixed_frame(), n=99)) == 7


def test_top_series_can_rank_other_measures():
    result = top_series(mixed_frame(), n=2, values=("lines",))
    assert result == [("YAML", "lines"), ("Python", "lines")]


def test_top_series_is_deterministic_under_row_order():
    frame = mixed_frame()
    assert top_series(frame, n=5) == top_series(frame.iloc[::-1].copy(), n=5)


def test_top_series_rejects_a_non_positive_n():
    with pytest.raises(ValueError, match="at least 1"):
        top_series(mixed_frame(), n=0)


def test_top_series_on_an_empty_frame_is_empty():
    assert top_series(pd.DataFrame(), n=5) == []


def same_timestamp_frame():
    """Three revisions sharing one instant, as a history rewrite makes.

    Each revision is a complete measurement of the repository, so the
    series must show one of them, never their total.
    """
    when = datetime(2012, 10, 31, 16, 31, 46, tzinfo=UTC)
    rows = [
        LineRow(
            commit=commit,
            date=when,
            counter="cloc",
            counter_version="2.10",
            language="C++",
            n_files=1,
            blank=0,
            comment=0,
            code=code,
        )
        for commit, code in (("aaa", 49733), ("bbb", 49848), ("ccc", 49917))
    ]
    rows.append(
        LineRow(
            commit="ddd",
            date=datetime(2012, 11, 1, tzinfo=UTC),
            counter="cloc",
            counter_version="2.10",
            language="C++",
            n_files=1,
            blank=0,
            comment=0,
            code=50000,
        )
    )
    frame = pd.DataFrame([r.model_dump() for r in rows])
    frame["lines"] = frame["code"] + frame["comment"]
    return frame


def test_pivot_does_not_sum_revisions_sharing_a_timestamp():
    wide = pivot(same_timestamp_frame(), value="code")
    # Summing would give 149498, roughly three times the real size.
    assert wide["C++"].max() < 60000


def test_pivot_keeps_one_point_per_timestamp():
    wide = pivot(same_timestamp_frame(), value="code")
    assert len(wide) == 2
    assert wide.index.is_unique


def test_pivot_keeps_one_of_the_revisions_unaltered():
    # Whichever is kept must be a value that was actually measured, not
    # a total, an average, or anything else derived from the group.
    wide = pivot(same_timestamp_frame(), value="code")
    assert wide["C++"].iloc[0] in (49733, 49848, 49917)


def test_pivot_choice_is_by_commit_id_not_by_magnitude():
    # Ordering is by commit id, which says nothing about position in
    # history. This pins the documented behavior so nobody later assumes
    # the largest or the newest revision wins.
    wide = pivot(same_timestamp_frame(), value="code")
    assert wide["C++"].iloc[0] == 49917


def test_pivot_is_deterministic_under_row_order():
    frame = same_timestamp_frame()
    assert pivot(frame, value="code").equals(pivot(frame.iloc[::-1].copy(), value="code"))

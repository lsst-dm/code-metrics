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
    top_languages,
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


def test_top_languages_keeps_five_by_default():
    result = top_languages(make_history_frame())
    assert len(set(result["language"])) == 5


def test_top_languages_ranks_by_peak_not_final_value():
    # C++ ends at 1, the smallest of any language, but peaked at 900.
    # Ranking on the final revision would pick YAML instead.
    result = top_languages(make_history_frame(), n=2)
    assert set(result["language"]) == {"Python", "C++"}


def test_top_languages_keeps_every_revision_of_a_kept_language():
    result = top_languages(make_history_frame(), n=2)
    assert len(result[result["language"] == "C++"]) == 2


def test_top_languages_respects_n():
    result = top_languages(make_history_frame(), n=3)
    assert len(set(result["language"])) == 3


def test_top_languages_returns_all_when_fewer_than_n():
    result = top_languages(make_history_frame(), n=99)
    assert len(set(result["language"])) == 6


def test_top_languages_can_rank_by_another_column():
    result = top_languages(make_history_frame(), n=2, value="lines")
    assert set(result["language"]) == {"Python", "C++"}


def test_top_languages_breaks_ties_by_name():
    frame = make_history_frame()
    frame.loc[frame["language"].isin(["Shell", "reST"]), "code"] = 7
    first = top_languages(frame, n=4)
    second = top_languages(frame.iloc[::-1].copy(), n=4)
    assert set(first["language"]) == set(second["language"])


def test_top_languages_rejects_a_non_positive_n():
    with pytest.raises(ValueError, match="at least 1"):
        top_languages(make_history_frame(), n=0)


def test_top_languages_on_an_empty_frame_is_empty():
    assert top_languages(pd.DataFrame(), n=5).empty


def test_select_reports_which_counters_are_available(repo_csv):
    frame = load_repo("demo", repo_csv)
    with pytest.raises(ValueError, match="scc") as excinfo:
        select(frame, counter="scc")
    message = str(excinfo.value)
    # The point of the error is telling the user what they can choose.
    assert "cloc" in message
    assert "tokei" in message


def test_select_still_returns_empty_for_an_absent_language(repo_csv):
    # An absent language is a legitimate empty result, not a mistake.
    frame = select(load_repo("demo", repo_csv), languages=["Fortran"], counter="cloc")
    assert frame.empty


def test_select_accepts_a_counter_that_is_present(repo_csv):
    frame = select(load_repo("demo", repo_csv), counter="tokei")
    assert set(frame["counter"]) == {"tokei"}


def test_load_repo_names_the_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="absent.csv"):
        load_repo("absent", tmp_path)


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

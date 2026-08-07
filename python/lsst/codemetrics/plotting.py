"""Loading and reshaping stored counts for plotting.

This module is the only one that imports pandas, which is an optional
dependency installed by the ``plot`` extra.
"""

import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .storage import read_rows

CLOC_CPP_ALIASES: dict[str, str] = {"C/C++ Header": "C++"}
"""Folds cloc's header language into C++ (`dict` [ `str`, `str` ]).

Provided for convenience only.  Aliasing is never applied automatically,
because different tools classify headers differently and treating that as
a naming difference would misrepresent what they measured.
"""


def load_repo(name: str, output_dir: Path = Path("data/repos")) -> pd.DataFrame:
    """Load one repository's stored counts.

    Parameters
    ----------
    name : `str`
        Repository base name.
    output_dir : `~pathlib.Path`, optional
        Directory holding the CSV files.

    Returns
    -------
    frame : `pandas.DataFrame`
        Long-format counts with a derived ``lines`` column.
    """
    rows = read_rows(output_dir / f"{name}.csv")
    frame = pd.DataFrame([row.model_dump() for row in rows])
    if frame.empty:
        return frame
    frame["lines"] = frame["code"] + frame["comment"]
    return frame


def apply_aliases(frame: pd.DataFrame, alias_map: Mapping[str, str]) -> pd.DataFrame:
    """Rename languages and combine those that collide.

    Parameters
    ----------
    frame : `pandas.DataFrame`
        Long-format counts.
    alias_map : `~collections.abc.Mapping` [ `str`, `str` ]
        Mapping of language name to replacement name.

    Returns
    -------
    frame : `pandas.DataFrame`
        Counts with languages renamed and summed where they now match.
    """
    renamed = frame.copy()
    renamed["language"] = renamed["language"].replace(dict(alias_map))
    grouped = renamed.groupby(["commit", "date", "counter", "language"], as_index=False).agg(
        label=("label", "first"),
        counter_version=("counter_version", "first"),
        n_files=("n_files", "sum"),
        blank=("blank", "sum"),
        comment=("comment", "sum"),
        code=("code", "sum"),
        lines=("lines", "sum"),
    )
    return grouped


def select(
    frame: pd.DataFrame,
    languages: Sequence[str] | None = None,
    counter: str | None = None,
) -> pd.DataFrame:
    """Restrict counts to particular languages or a particular backend.

    Parameters
    ----------
    frame : `pandas.DataFrame`
        Long-format counts.
    languages : `~collections.abc.Sequence` [ `str` ], optional
        Languages to keep.
    counter : `str`, optional
        Backend to keep.

    Returns
    -------
    frame : `pandas.DataFrame`
        Filtered counts.

    Warns
    -----
    UserWarning
        Raised if the frame holds results from more than one backend and
        none was chosen, since summing across backends is meaningless.
    """
    result = frame
    if counter is not None:
        result = result[result["counter"] == counter]
    elif not result.empty and result["counter"].nunique() > 1:
        found = ", ".join(sorted(result["counter"].unique()))
        warnings.warn(
            f"Frame holds results from more than one counter ({found}). Pass counter= to choose one.",
            UserWarning,
            stacklevel=2,
        )
    if languages is not None:
        result = result[result["language"].isin(list(languages))]
    return result


def top_languages(frame: pd.DataFrame, n: int = 5, value: str = "code") -> pd.DataFrame:
    """Keep only the largest languages, so a plot stays readable.

    A repository of any size reports enough languages that plotting them
    all buries the figure under its own legend.

    Languages are ranked by the largest value they ever reach, not by
    their most recent one, so a subsystem that grew and was later removed
    still appears.  That rise and fall is usually the most interesting
    part of a history plot, and ranking on the final revision alone would
    discard it.

    Apply this after `apply_aliases`, so that languages folded together
    are ranked on their combined size.

    Parameters
    ----------
    frame : `pandas.DataFrame`
        Long-format counts.
    n : `int`, optional
        How many languages to keep.  A frame with fewer languages than
        this is returned unchanged.
    value : `str`, optional
        Column to rank on, such as ``code``, ``comment``, or ``lines``.

    Returns
    -------
    frame : `pandas.DataFrame`
        Counts for the kept languages, at every revision they appear in.

    Raises
    ------
    ValueError
        Raised if ``n`` is less than 1.
    """
    if n < 1:
        raise ValueError(f"n must be at least 1, got {n}.")
    if frame.empty:
        return frame
    peaks = frame.groupby("language")[value].max()
    # Sort by name first so that languages tied on their peak are chosen
    # in a stable order rather than by however the rows happened to
    # arrive.
    ranked = peaks.sort_index().sort_values(ascending=False, kind="stable")
    return frame[frame["language"].isin(ranked.head(n).index)]


def pivot(frame: pd.DataFrame, value: str = "code") -> pd.DataFrame:
    """Reshape counts into one column per language.

    Parameters
    ----------
    frame : `pandas.DataFrame`
        Long-format counts.
    value : `str`, optional
        Column to spread, such as ``code``, ``comment``, or ``lines``.

    Returns
    -------
    frame : `pandas.DataFrame`
        Wide counts indexed by date.
    """
    return frame.pivot_table(index="date", columns="language", values=value, aggfunc="sum").sort_index()


def load_stack(data_dir: Path = Path("data")) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Load the stack-wide weekly counts.

    Parameters
    ----------
    data_dir : `~pathlib.Path`, optional
        Directory holding the per-tag YAML reports.

    Returns
    -------
    dates : `numpy.ndarray`
        Approximate week position within the year, ascending.  These are
        not exact dates: each tag is placed at its week number's
        fraction of the way through the year, not at a calendar date.
    datasets : `dict` [ `str`, `numpy.ndarray` ]
        Series keyed as ``<language>_<measure>``, where language is one of
        ``python``, ``cpp``, or ``all``, and measure is one of ``code``,
        ``comment``, or ``lines``.
    """
    results: dict[float, dict[str, dict[str, int]]] = {}
    for path in data_dir.glob("w.*.yaml"):
        year, week = path.name.split(".")[1:3]
        # Divide by 53, not the intuitive 52: these tag names are not
        # true ISO weeks (datetime.fromisocalendar(2016, 53, 1) raises,
        # since ISO year 2016 has only 52 weeks), and some years in this
        # data run to week 53.  Dividing by 52 would map week 53 of year
        # Y to the same fraction as week 1 of year Y + 1, since
        # Y + 53 / 52 == (Y + 1) + 1 / 52; two distinct tags then
        # collide on one dict key below and one is silently dropped.
        # Subtracting 1 and dividing by 53 instead keeps every week
        # within a year on its own fraction strictly below 1.0, so the
        # sequence stays strictly increasing across year boundaries too.
        year_fraction = float(year) + (float(week) - 1.0) / 53.0
        data = yaml.safe_load(path.read_text())

        entry: dict[str, dict[str, int]] = {}
        for measure in ("code", "comment", "blank"):
            entry[measure] = {
                "python": data["Python"][measure],
                "cpp": data["C++"][measure] + data["C/C++ Header"][measure],
                "all": data["SUM"][measure],
            }
        entry["lines"] = {
            language: entry["comment"][language] + entry["code"][language]
            for language in ("python", "cpp", "all")
        }
        results[year_fraction] = entry

    date_keys = sorted(results)
    datasets = {
        f"{language}_{measure}": np.array([results[y][measure][language] for y in date_keys])
        for measure in ("code", "comment", "lines")
        for language in ("python", "cpp", "all")
    }
    return np.array(date_keys), datasets

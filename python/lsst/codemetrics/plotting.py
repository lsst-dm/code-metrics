"""Loading and reshaping stored counts for plotting.

This module is the only one that imports pandas, which is an optional
dependency installed by the ``plot`` extra.

Counts from different backends are not interchangeable, because they
disagree about whether a Python docstring is code or comment.  Measured
over ``daf_butler``'s Python source:

=================  =====  =======  =====
Backend             code  comment  blank
=================  =====  =======  =====
cloc               54373    48500  16291
tokei              55063    52420  11827
scc                67312    41689  10322
=================  =====  =======  =====

cloc and tokei agree on ``code`` to about one percent, because
`~lsst.codemetrics.counters.TokeiCounter` turns on tokei's
``treat_doc_strings_as_comments`` setting by default.  scc classifies
much of the same material as code and runs about a quarter higher, so
its Python figures are not comparable with either.

The totals of all three columns agree to within a tenth of a percent
everywhere, so the disagreement is purely about which column a line
lands in.  `select` refuses to mix backends silently for this reason.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .location import repo_dir
from .storage import read_rows

CPP_HEADER_ALIASES: dict[str, str] = {
    "C/C++ Header": "C++",
    "C Header": "C++",
    "C++ Header": "C++",
}
"""Folds C and C++ headers into C++ (`dict` [ `str`, `str` ]).

Covers all three backends at once.  cloc reports a single
``C/C++ Header``, while scc and tokei split ``C Header`` from
``C++ Header``; the names do not collide, so one mapping serves whichever
backend produced the data.

Provided for convenience only.  Aliasing is never applied automatically,
because the tools genuinely classify headers differently and treating
that as a naming difference would misrepresent what they measured.
"""


def load_repo(name: str, data_dir: Path | str | None = None) -> pd.DataFrame:
    """Load one repository's stored counts.

    Parameters
    ----------
    name : `str`
        Repository base name.
    data_dir : `~pathlib.Path` or `str`, optional
        Data root to read beneath.  Resolved by
        `~lsst.codemetrics.location.data_root` when not given.

    Returns
    -------
    frame : `pandas.DataFrame`
        Long-format counts with a derived ``lines`` column.

    Raises
    ------
    FileNotFoundError
        Raised if no CSV exists for that name.
    """
    source = repo_dir(data_dir)
    path = source.path / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"No counts for {name!r} at {path}. Data root was {source.describe()}. "
            "Collect them with: code-metrics repo-history <repo>"
        )
    rows = read_rows(path)
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

    Raises
    ------
    ValueError
        Raised if the named backend produced none of the stored rows.
        Filtering it away silently would leave an empty frame that plots
        as a blank figure, with nothing to say which backend was wanted
        or which are actually present.
        Also raised if the frame holds results from more than one backend
        and none was chosen, since plotting an arbitrary mixture is
        meaningless.
    """
    result = frame
    if result.empty:
        return result
    if counter is not None:
        result = result[result["counter"] == counter]
        if result.empty:
            available = ", ".join(sorted(frame["counter"].unique()))
            raise ValueError(f"No rows counted by {counter!r}. This data was counted by: {available}.")
    elif result["counter"].nunique() > 1:
        found = ", ".join(sorted(result["counter"].unique()))
        raise ValueError(
            f"Frame holds results from more than one counter ({found}). Pass counter= to choose one."
        )
    if languages is not None:
        result = result[result["language"].isin(list(languages))]
    return result


def top_series(
    frame: pd.DataFrame,
    n: int = 10,
    values: Sequence[str] = ("code", "comment"),
) -> list[tuple[str, str]]:
    """Choose which lines to draw, so a plot stays readable.

    A repository of any size reports enough languages that drawing every
    one buries the figure under its own legend.

    The unit of choice is a series, a language paired with a measure,
    rather than a language alone.  A language is not uniformly
    interesting across measures: JSON cannot carry comments at all, so
    its comment series is flat zero and tells the reader nothing, while
    Markdown is entirely comment and has no code series worth drawing.
    Ranking languages by their code alone would keep the first and drop
    the second, spending a slot on a dead line and discarding real
    content.

    A series whose peak is zero is never returned, whatever ``n`` is.
    It has nothing to show, so the slot goes to the next real series.

    Series are ranked by the largest value they ever reach, not by their
    most recent one, so a subsystem that grew and was later removed still
    appears.  That rise and fall is usually the most interesting part of
    a history plot, and ranking on the final revision alone would discard
    it.

    Apply this after `apply_aliases`, so that languages folded together
    are ranked on their combined size.

    Parameters
    ----------
    frame : `pandas.DataFrame`
        Long-format counts.
    n : `int`, optional
        How many series to draw at most.
    values : `~collections.abc.Sequence` [ `str` ], optional
        Measures to consider, such as ``code``, ``comment``, or
        ``lines``.

    Returns
    -------
    series : `list` [ `tuple` [ `str`, `str` ] ]
        Language and measure pairs, largest peak first.

    Raises
    ------
    ValueError
        Raised if ``n`` is less than 1.
    """
    if n < 1:
        raise ValueError(f"n must be at least 1, got {n}.")
    if frame.empty:
        return []
    ranked = []
    for value in values:
        for language, peak in frame.groupby("language")[value].max().items():
            if peak > 0:
                ranked.append((peak, language, value))
    # Sort by name and measure first so that series tied on their peak
    # are chosen in a stable order rather than by however the rows
    # happened to arrive.
    ranked.sort(key=lambda item: (item[1], item[2]))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [(language, value) for _, language, value in ranked[:n]]


def pivot(frame: pd.DataFrame, value: str = "code") -> pd.DataFrame:
    """Reshape counts into one column per language.

    Several revisions can share a committer timestamp to the second,
    which is what rewriting history produces: a rebase applies a run of
    commits in the same instant, and each of them lands on the branch in
    its own right.  Every one is a legitimate measurement, and all of
    them stay in the stored data and in `top_series`.

    A line indexed by time cannot show more than one value at an
    instant, so this collapses them for drawing only.  Combining them
    arithmetically is not an option: each revision measures the whole
    repository, so adding them would report it at several times its real
    size and draw a spike that looks like a real event.

    One revision is therefore kept, the last by commit id.  That order is
    arbitrary -- a commit id says nothing about position in history, and
    the stored data records no ordering finer than the timestamp -- but
    it is deterministic, so a plot does not change between runs.  The
    choice does not matter in practice: revisions sharing an instant come
    from a single rebase and sit next to each other in history, so they
    differ trivially.  Across ``afw`` the widest such group spans 184
    lines out of 49917, under half a percent.

    Parameters
    ----------
    frame : `pandas.DataFrame`
        Long-format counts.
    value : `str`, optional
        Column to spread, such as ``code``, ``comment``, or ``lines``.

    Returns
    -------
    frame : `pandas.DataFrame`
        Wide counts indexed by date, one row per distinct timestamp.
        Where revisions share a timestamp, only one is represented.
    """
    if frame.empty:
        return frame
    ordered = frame.sort_values(["date", "commit"])
    latest = ordered.drop_duplicates(subset=["date", "language"], keep="last")
    return latest.pivot(index="date", columns="language", values=value).sort_index()


def insert_gaps(
    frame: pd.DataFrame,
    max_gap: pd.Timedelta | str = pd.Timedelta(days=30),
    offset: pd.Timedelta | str = pd.Timedelta(seconds=1),
) -> pd.DataFrame:
    """Break the drawn line across long stretches without a commit.

    A step line carried across a quiet year is not wrong -- no commits
    means the counts really did not change -- but it reads as a measured
    plateau rather than as an absence of development.  This inserts a row
    of missing values inside each long gap, which matplotlib draws as no
    line at all, so a quiet stretch looks quiet.

    The break has to be an extra row, rather than a mask over the
    revision that ends the gap.  With ``drawstyle="steps-post"`` the
    horizontal segment leaving a point is drawn at that point's own
    value, so blanking the far end of the gap removes only the riser at
    its end: the line still runs flat all the way across, and the real
    count measured there is lost as well.  A row of its own between the
    two blanks the horizontal instead.

    Gaps are a property of the commit timeline rather than of any one
    language, so every column breaks at the same place.  Applying this to
    each measure of one repository puts the breaks at the same dates in
    all of them, since they share the revisions they were pivoted from.

    Parameters
    ----------
    frame : `pandas.DataFrame`
        Wide counts indexed by date, as `pivot` returns.
    max_gap : `pandas.Timedelta` or `str`, optional
        Longest stretch between consecutive revisions to draw through.
        Anything longer is broken.  Accepts anything
        `pandas.Timedelta` does, such as ``"90D"``.
    offset : `pandas.Timedelta` or `str`, optional
        How far after the revision that starts a gap to place the break.
        The default holds the last measured value for a moment and then
        stops, so the break sits where development did.  Raising it to
        ``max_gap`` instead draws the value as known for that long before
        the line goes blank.

    Returns
    -------
    frame : `pandas.DataFrame`
        The counts with a missing-value row added inside each long gap.
        Columns holding whole numbers become floating point, since only
        floats carry a missing value.

    Raises
    ------
    ValueError
        Raised if ``max_gap`` is not positive, or if ``offset`` is not
        positive and smaller than ``max_gap``.  A break must land
        strictly between the revisions it separates; a larger ``offset``
        could place it on or beyond the revision that ends the gap.
    """
    span = pd.Timedelta(max_gap)
    step = pd.Timedelta(offset)
    if span <= pd.Timedelta(0):
        raise ValueError(f"max_gap must be positive, got {span}.")
    if not pd.Timedelta(0) < step < span:
        raise ValueError(f"offset must be positive and smaller than max_gap ({span}), got {step}.")
    if frame.empty:
        return frame
    dates = frame.index
    starts = dates[:-1][(dates[1:] - dates[:-1]) > span]
    if len(starts) == 0:
        return frame
    return frame.reindex(dates.union(starts + step))


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

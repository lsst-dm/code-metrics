from datetime import UTC, datetime

import pytest
from lsst.codemetrics.revisions import (
    MODES,
    GitError,
    default_branch,
    git_output,
    sample_revisions,
)


def test_modes_are_the_three_documented_ones():
    assert MODES == ("tags", "first-parent", "all")


def test_all_mode_returns_every_commit(synthetic_repo):
    samples = sample_revisions(synthetic_repo, "all", branch="main")
    # first, feature, merge, direct
    assert len(samples) == 4


def test_first_parent_skips_the_merged_branch(synthetic_repo):
    samples = sample_revisions(synthetic_repo, "first-parent", branch="main")
    # first, merge, direct -- the feature commit is not on the chain
    assert len(samples) == 3


def test_committer_date_is_used_not_author_date(synthetic_repo):
    samples = sample_revisions(synthetic_repo, "all", branch="main")
    dates = {s.date for s in samples}
    # The feature commit was authored 2020-01-05 but committed 2020-02-10.
    assert datetime(2020, 2, 10, tzinfo=UTC) in dates
    assert datetime(2020, 1, 5, tzinfo=UTC) not in dates


def test_samples_are_sorted_by_date_ascending(synthetic_repo):
    samples = sample_revisions(synthetic_repo, "all", branch="main")
    assert [s.date for s in samples] == sorted(s.date for s in samples)


def test_tag_mode_orders_chronologically_not_lexically(synthetic_repo):
    samples = sample_revisions(synthetic_repo, "tags", tag_pattern="w.*")
    # Lexically "w.2020.10" sorts before "w.2020.9"; by date it does not.
    assert [s.label for s in samples] == ["w.2020.9", "w.2020.10"]


def test_tag_mode_records_commits_not_tag_objects(synthetic_repo):
    # An annotated tag's objectname is the tag object, not the commit.
    # Storing that would break the incremental key, which compares against
    # commit ids from rev-list.
    git_output(synthetic_repo, "tag", "-a", "w.2020.20", "-m", "annotated")
    samples = sample_revisions(synthetic_repo, "tags", tag_pattern="w.2020.20")
    expected = git_output(synthetic_repo, "rev-parse", "w.2020.20^{commit}")
    assert samples[0].commit == expected


def test_since_and_until_bound_the_range(synthetic_repo):
    samples = sample_revisions(
        synthetic_repo,
        "all",
        branch="main",
        since=datetime(2020, 2, 1, tzinfo=UTC),
        until=datetime(2020, 2, 28, tzinfo=UTC),
    )
    assert len(samples) == 2


def test_default_branch_is_detected(synthetic_repo):
    assert default_branch(synthetic_repo) == "main"


def test_unknown_mode_is_rejected(synthetic_repo):
    with pytest.raises(ValueError, match="nosuchmode"):
        sample_revisions(synthetic_repo, "nosuchmode")


def test_git_output_raises_on_failure(synthetic_repo):
    with pytest.raises(GitError):
        git_output(synthetic_repo, "no-such-subcommand")

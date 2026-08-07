import os
import subprocess
from pathlib import Path

import pytest


def _git(
    repo: Path,
    *args: str,
    authored: str | None = None,
    committed: str | None = None,
) -> str:
    env = dict(os.environ)
    if authored is not None:
        env["GIT_AUTHOR_DATE"] = authored
    if committed is not None:
        env["GIT_COMMITTER_DATE"] = committed
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


@pytest.fixture
def synthetic_repo(tmp_path: Path) -> Path:
    """A repository with a merge, a rebase-like commit, and mixed tags.

    The feature commit deliberately has an author date well before its
    committer date, which is what a rebase produces.  Tags are named with
    inconsistent zero padding so that lexical and chronological ordering
    disagree.
    """
    repo = tmp_path / "synthetic"
    repo.mkdir()
    _git(repo, "init", "-b", "main", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    first = "2020-01-01T00:00:00+0000"
    (repo / "a.py").write_text("import os\n")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "first", authored=first, committed=first)
    _git(repo, "tag", "w.2020.9")

    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "b.py").write_text("import sys\n")
    _git(repo, "add", "b.py")
    # Author date in January, committer date in February, which is what a
    # rebase produces.  Only the committer date says when this landed.
    _git(
        repo,
        "commit",
        "-q",
        "-m",
        "feature work",
        authored="2020-01-05T00:00:00+0000",
        committed="2020-02-10T00:00:00+0000",
    )

    merged = "2020-02-15T00:00:00+0000"
    _git(repo, "checkout", "-q", "main")
    _git(
        repo,
        "merge",
        "-q",
        "--no-ff",
        "-m",
        "merge feature",
        "feature",
        authored=merged,
        committed=merged,
    )
    _git(repo, "tag", "w.2020.10")

    direct = "2020-03-01T00:00:00+0000"
    (repo / "c.py").write_text("import json\n")
    _git(repo, "add", "c.py")
    _git(repo, "commit", "-q", "-m", "direct", authored=direct, committed=direct)

    return repo

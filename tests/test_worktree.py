from pathlib import Path

import pytest
from lsst.codemetrics.revisions import GitError, git_output
from lsst.codemetrics.worktree import (
    checkout,
    ensure_source,
    is_url,
    repo_name,
    temporary_worktree,
)


def test_is_url_recognises_remotes():
    assert is_url("https://github.com/lsst/afw")
    assert is_url("git@github.com:lsst/afw.git")
    assert not is_url("/Users/timj/work/lsst/afw")
    assert not is_url("../afw")


def test_repo_name_strips_dot_git():
    assert repo_name("https://github.com/lsst/daf_butler.git") == "daf_butler"
    assert repo_name("https://github.com/lsst/daf_butler") == "daf_butler"
    assert repo_name("/some/path/afw/") == "afw"


def test_repo_name_keeps_url_behaviour_unchanged():
    assert repo_name("https://github.com/lsst/daf_butler.git") == "daf_butler"
    assert repo_name("git@github.com:lsst/afw.git") == "afw"


def test_repo_name_resolves_dot_and_dot_dot():
    assert repo_name(".") == Path.cwd().name
    assert repo_name("..") == Path.cwd().parent.name


def test_repo_name_resolves_relative_path_with_trailing_slash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "afw").mkdir()
    assert repo_name("afw/") == "afw"


def test_repo_name_root_path_is_rejected():
    with pytest.raises(ValueError):
        repo_name("/")


def test_ensure_source_returns_local_path_unchanged(synthetic_repo, tmp_path):
    assert ensure_source(str(synthetic_repo), tmp_path / "cache") == synthetic_repo


def test_ensure_source_clones_a_url_into_the_cache(synthetic_repo, tmp_path):
    cache = tmp_path / "cache"
    # A local path is a valid clone URL, so file:// exercises the clone path.
    source = ensure_source(f"file://{synthetic_repo}", cache)
    assert source.exists()
    assert cache in source.parents
    assert git_output(source, "rev-parse", "--is-bare-repository") == "true"


def test_ensure_source_refetches_new_commits_into_the_cache(synthetic_repo, tmp_path):
    cache = tmp_path / "cache"
    source_url = f"file://{synthetic_repo}"
    ensure_source(source_url, cache)

    # Advance the upstream branch after the cache already holds a clone,
    # so reuse has to actually refresh branch refs, not just tags.
    (synthetic_repo / "d.py").write_text("import re\n")
    git_output(synthetic_repo, "add", "d.py")
    git_output(synthetic_repo, "commit", "-q", "-m", "advance main")
    new_head = git_output(synthetic_repo, "rev-parse", "main")

    cached = ensure_source(source_url, cache)

    assert git_output(cached, "rev-parse", "main") == new_head


def test_ensure_source_separates_remotes_with_the_same_name(synthetic_repo, tmp_path):
    other_parent = tmp_path / "other"
    other_parent.mkdir()
    git_output(other_parent, "clone", "-q", str(synthetic_repo), "synthetic")
    other_repo = other_parent / "synthetic"
    git_output(other_repo, "config", "user.email", "test@example.com")
    git_output(other_repo, "config", "user.name", "Test")
    (other_repo / "unique.py").write_text("value = 1\n")
    git_output(other_repo, "add", "unique.py")
    git_output(other_repo, "commit", "-q", "-m", "make repositories distinct")

    cache = tmp_path / "cache"
    first_url = f"file://{synthetic_repo}"
    second_url = f"file://{other_repo}"
    first = ensure_source(first_url, cache)
    second = ensure_source(second_url, cache)

    assert first != second
    assert git_output(first, "remote", "get-url", "origin") == first_url
    assert git_output(second, "remote", "get-url", "origin") == second_url
    assert git_output(first, "rev-parse", "main") != git_output(second, "rev-parse", "main")


def test_worktree_is_created_and_removed(synthetic_repo):
    with temporary_worktree(synthetic_repo) as tree:
        assert tree.exists()
        captured = tree
    assert not captured.exists()


def test_checkout_switches_content(synthetic_repo):
    first = git_output(synthetic_repo, "rev-list", "--max-parents=0", "main")
    head = git_output(synthetic_repo, "rev-parse", "main")
    with temporary_worktree(synthetic_repo) as tree:
        checkout(tree, head)
        assert (tree / "c.py").exists()
        checkout(tree, first)
        assert not (tree / "c.py").exists()


def test_checkout_removes_untracked_leftovers(synthetic_repo):
    head = git_output(synthetic_repo, "rev-parse", "main")
    with temporary_worktree(synthetic_repo) as tree:
        checkout(tree, head)

        # A linked worktree shares the common git directory's
        # info/exclude, so a rule written there makes a name genuinely
        # ignored without touching any tracked content.  This is what
        # distinguishes the ignored case from a plain untracked file:
        # only "-x" removes something git considers ignored.
        common_dir = Path(git_output(tree, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        exclude_file = common_dir / "info" / "exclude"
        with exclude_file.open("a") as handle:
            handle.write("build.log\n")

        stray = tree / "stray.py"
        stray.write_text("x = 1\n")
        ignored = tree / "build.log"
        ignored.write_text("noise\n")
        assert git_output(tree, "check-ignore", "build.log") == "build.log"

        checkout(tree, head)

        assert not stray.exists()
        assert not ignored.exists()


def test_checkout_of_unknown_commit_raises(synthetic_repo):
    with temporary_worktree(synthetic_repo) as tree:
        with pytest.raises(GitError):
            checkout(tree, "0" * 40)

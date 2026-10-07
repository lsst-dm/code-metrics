import shutil
from pathlib import Path

import pytest
from lsst.codemetrics.counters import (
    COUNTERS,
    ClocCounter,
    LanguageCount,
    SccCounter,
    TokeiCounter,
    get_counter,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_scc_json():
    result = SccCounter().parse((FIXTURES / "scc.json").read_text())
    assert result == {
        "Python": LanguageCount(n_files=2, blank=3, comment=2, code=4),
        "C Header": LanguageCount(n_files=1, blank=0, comment=1, code=2),
        "C++": LanguageCount(n_files=1, blank=1, comment=1, code=2),
    }


def test_parses_tokei_json_and_drops_total():
    result = TokeiCounter().parse((FIXTURES / "tokei.json").read_text())
    assert "Total" not in result
    assert result == {
        "C Header": LanguageCount(n_files=1, blank=0, comment=1, code=2),
        "Python": LanguageCount(n_files=1, blank=3, comment=1, code=4),
    }


def test_scc_version_parsing():
    assert SccCounter()._parse_version("scc version 3.7.0\n") == "3.7.0"


def test_tokei_version_parsing():
    raw = "tokei 14.0.0 compiled with serialization support: json, cbor, yaml\n"
    assert TokeiCounter()._parse_version(raw) == "14.0.0"


def test_registry_contains_all_backends():
    assert set(COUNTERS) == {"cloc", "scc", "tokei"}
    assert isinstance(get_counter("cloc"), ClocCounter)
    assert isinstance(get_counter("tokei"), TokeiCounter)


def test_get_counter_rejects_unknown_name():
    with pytest.raises(KeyError, match="nosuchtool"):
        get_counter("nosuchtool")


@pytest.mark.skipif(shutil.which("scc") is None, reason="scc not installed")
def test_scc_counts_a_real_tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "vendor").mkdir()
    (tmp_path / "src" / "a.py").write_text("# c\nimport os\n")
    (tmp_path / "vendor" / "b.py").write_text("x = 1\n")
    counter = SccCounter()
    assert counter.count(tmp_path)["Python"].n_files == 2
    trimmed = counter.count(tmp_path, exclude_dirs=["vendor"])
    assert trimmed["Python"].n_files == 1


@pytest.mark.skipif(shutil.which("tokei") is None, reason="tokei not installed")
def test_tokei_counts_a_real_tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "vendor").mkdir()
    (tmp_path / "src" / "a.py").write_text("# c\nimport os\n")
    (tmp_path / "vendor" / "b.py").write_text("x = 1\n")
    counter = TokeiCounter()
    assert counter.count(tmp_path)["Python"].n_files == 2
    trimmed = counter.count(tmp_path, exclude_dirs=["vendor"])
    assert trimmed["Python"].n_files == 1


DOCSTRING_MODULE = '''"""Module docstring.

Spans several lines.
"""

# A real comment.
import os


def f(x):
    """Do a thing.

    Parameters
    ----------
    x : `int`
        The thing.
    """
    return x
'''


@pytest.mark.skipif(shutil.which("tokei") is None, reason="tokei not installed")
def test_tokei_counts_docstrings_as_comments_by_default(tmp_path):
    # cloc and scc both call a docstring a comment. Left to itself tokei
    # calls it code, which would put this module's 12 docstring lines in a
    # different column from every other backend.
    (tmp_path / "mod.py").write_text(DOCSTRING_MODULE)
    counts = TokeiCounter().count(tmp_path)["Python"]
    assert counts.code == 3
    assert counts.comment == 12


@pytest.mark.skipif(shutil.which("tokei") is None, reason="tokei not installed")
def test_tokei_can_restore_its_native_docstring_handling(tmp_path):
    (tmp_path / "mod.py").write_text(DOCSTRING_MODULE)
    counts = TokeiCounter(docstrings_as_comments=False).count(tmp_path)["Python"]
    assert counts.code == 14
    assert counts.comment == 1


@pytest.mark.skipif(shutil.which("tokei") is None, reason="tokei not installed")
def test_tokei_ignores_a_config_shipped_by_the_scanned_repository(tmp_path):
    # A repository carrying its own tokei.toml must not silently change
    # how its history is counted.
    (tmp_path / "mod.py").write_text(DOCSTRING_MODULE)
    (tmp_path / "tokei.toml").write_text("treat_doc_strings_as_comments = false\n")
    counts = TokeiCounter().count(tmp_path)["Python"]
    assert counts.code == 3
    assert counts.comment == 12


@pytest.mark.skipif(shutil.which("tokei") is None, reason="tokei not installed")
def test_tokei_still_excludes_directories_with_the_config_in_place(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "vendor").mkdir()
    (tmp_path / "src" / "a.py").write_text("import os\n")
    (tmp_path / "vendor" / "b.py").write_text("import sys\n")
    counter = TokeiCounter()
    assert counter.count(tmp_path)["Python"].n_files == 2
    assert counter.count(tmp_path, exclude_dirs=["vendor"])["Python"].n_files == 1


@pytest.mark.skipif(
    shutil.which("cloc") is None or shutil.which("tokei") is None, reason="needs cloc and tokei"
)
def test_cloc_and_tokei_agree_on_code_lines(tmp_path):
    # The point of the default. These two are the comparable pair, and
    # the stack-wide history in data/ was counted with cloc.  scc is
    # deliberately not asserted here: it classifies much of the same
    # material as code and runs about a quarter higher on real numpydoc
    # source, so a three-way assertion would only hold for toy inputs.
    (tmp_path / "mod.py").write_text(DOCSTRING_MODULE)
    assert ClocCounter().count(tmp_path)["Python"].code == 3
    assert TokeiCounter().count(tmp_path)["Python"].code == 3

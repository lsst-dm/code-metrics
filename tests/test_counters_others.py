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

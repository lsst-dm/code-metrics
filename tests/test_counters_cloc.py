import shutil
from pathlib import Path

import pytest
from lsst.codemetrics.counters import ClocCounter, CounterError, LanguageCount

FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_cloc_yaml():
    counter = ClocCounter()
    result = counter.parse((FIXTURES / "cloc.yaml").read_text())
    assert result == {
        "Python": LanguageCount(n_files=2, blank=3, comment=2, code=4),
        "C++": LanguageCount(n_files=1, blank=1, comment=1, code=2),
        "C/C++ Header": LanguageCount(n_files=1, blank=0, comment=1, code=2),
    }


def test_parse_drops_header_and_sum():
    counter = ClocCounter()
    result = counter.parse((FIXTURES / "cloc.yaml").read_text())
    assert "header" not in result
    assert "SUM" not in result


def test_parse_empty_output_is_empty_mapping():
    assert ClocCounter().parse("") == {}


def test_missing_executable_raises_naming_the_tool():
    counter = ClocCounter(executable="definitely-not-cloc")
    with pytest.raises(CounterError, match="definitely-not-cloc"):
        counter.version


@pytest.mark.skipif(shutil.which("cloc") is None, reason="cloc not installed")
def test_counts_a_real_tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "vendor").mkdir()
    (tmp_path / "src" / "a.py").write_text("# c\nimport os\n")
    (tmp_path / "vendor" / "b.py").write_text("x = 1\n")

    counter = ClocCounter()
    assert counter.version
    full = counter.count(tmp_path)
    assert full["Python"].n_files == 2
    trimmed = counter.count(tmp_path, exclude_dirs=["vendor"])
    assert trimmed["Python"].n_files == 1


@pytest.mark.skipif(shutil.which("cloc") is None, reason="cloc not installed")
def test_write_report_matches_historical_format(tmp_path):
    (tmp_path / "a.py").write_text("import os\n")
    out = tmp_path / "report.yaml"
    ClocCounter().write_report([tmp_path], out, include_langs=["Python"])
    text = out.read_text()
    assert "cloc_version" in text
    assert "SUM" in text

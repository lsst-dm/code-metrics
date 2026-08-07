# Per-repository Line Count History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `code-metrics repo-history` command that counts lines in a single git repository across its history, and fold the existing stack-wide weekly scan into the same installable package.

**Architecture:** A `LineCounter` abstraction wraps cloc, scc, and tokei behind one interface. A sampler turns a repository into a list of `(commit, date, label)` samples by tag, first-parent chain, or every commit. A collector checks each sample out into a throwaway git worktree, counts it, and merges the result into a long-format CSV keyed on `(commit, counter)` so re-runs are incremental. The ported stack scan reuses the cloc backend but keeps its own YAML output format unchanged.

**Tech Stack:** Python 3.12+, click (CLI), pydantic (models and validation), rich (progress and summary tables), PyYAML, pytest. pandas and matplotlib are optional, for plotting only.

## Global Constraints

- Python `requires-python = ">=3.12"`; use `X | None`, not `Optional[X]`.
- Package is a native namespace package at `python/lsst/codemetrics/`. **Do not create `python/lsst/__init__.py`.**
- Runtime dependencies are exactly: `click`, `pydantic`, `pyyaml`, `rich`. `pandas` and `matplotlib` go in the `plot` extra only, and must never be imported from a non-plotting module.
- All models are pydantic `BaseModel` subclasses, not dataclasses.
- ruff must pass with the repo's existing config: line length 110, docstring lines 79, numpy docstring convention, isort, pyupgrade. Every module, class, and public function needs a docstring.
- Use top-level imports everywhere, including `pandas` and `numpy` inside `plotting.py`. The optional dependency is kept optional by nothing else importing `plotting.py`, not by deferring its imports.
- One sentence per line in Markdown and in docstring prose.
- American English spelling.
- Never normalize language names between backends. Store what the tool reports.
- Committer dates only, never author dates.
- Existing files under `data/` (the `w.*.yaml` files) must not change. New files may be added.

---

### Task 1: Package scaffolding and CLI skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `python/lsst/codemetrics/__init__.py`
- Create: `python/lsst/codemetrics/cli.py`
- Create: `tests/test_cli_skeleton.py`
- Delete: `setup.cfg`
- Delete: `.github/workflows/lint.yaml`

**Interfaces:**
- Consumes: nothing.
- Produces: `lsst.codemetrics.cli:main`, a `click.Group` registered as the `code-metrics` console script. Later tasks attach subcommands to it with `@main.command()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_skeleton.py`:

```python
from click.testing import CliRunner

from lsst.codemetrics.cli import main


def test_main_group_runs():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "repo-history" in result.output or "Commands" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_skeleton.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lsst.codemetrics'`

- [ ] **Step 3: Replace `pyproject.toml`**

Keep the existing `[tool.ruff]` block exactly as it is and add the rest around it.
The full file becomes:

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "lsst-code-metrics"
version = "0.1.0"
description = "Tools for measuring the size of the LSST Science Pipelines code base over time."
requires-python = ">=3.12"
dependencies = [
    "click",
    "pydantic",
    "pyyaml",
    "rich",
]

[project.optional-dependencies]
plot = ["matplotlib", "pandas"]
test = ["pytest"]

[project.scripts]
code-metrics = "lsst.codemetrics.cli:main"

[tool.setuptools]
package-dir = {"" = "python"}

[tool.setuptools.packages.find]
where = ["python"]

[tool.setuptools.package-data]
"lsst.codemetrics" = ["data/*.txt"]

[tool.ruff]
line-length = 110
target-version = "py312"

[tool.ruff.lint]
select = [
    "E",  # pycodestyle
    "F",  # pyflakes
    "N",  # pep8-naming
    "W",  # pycodestyle
    "D",  # pydocstyle
    "I",  # isort
    "C4",  # comprehensions
    "UP"  # pyupgrade
]
ignore = [
    "D205"
]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["D"]

[tool.ruff.lint.pycodestyle]
max-doc-length = 79

[tool.ruff.lint.pydocstyle]
convention = "numpy"

[tool.ruff.format]
docstring-code-format = true
docstring-code-line-length = 79
```

The `per-file-ignores` entry is new: pydocstyle would otherwise demand a
docstring on every test function.

- [ ] **Step 4: Create the package**

Create `python/lsst/codemetrics/__init__.py`:

```python
"""Tools for measuring the size of a code base over time."""
```

Do not create `python/lsst/__init__.py`.
`lsst` is a native namespace package, and an `__init__.py` there would shadow
the stack's own `lsst` namespace.

Create `python/lsst/codemetrics/cli.py`:

```python
"""Command line interface for the code metrics tools."""

import click


@click.group()
@click.version_option()
def main() -> None:
    """Measure the size of a code base over time."""
```

- [ ] **Step 5: Delete the obsolete flake8 configuration and workflow**

`setup.cfg` configures flake8 and `.github/workflows/lint.yaml` invokes the
shared lint workflow that consumes it.
Both are superseded by ruff, which `.github/workflows/formatting.yaml` and
the pre-commit hooks already run.

```bash
git rm setup.cfg .github/workflows/lint.yaml
```

Leave `.github/workflows/formatting.yaml` and
`.github/workflows/rebase_checker.yaml` in place.

- [ ] **Step 6: Install and run the test**

Run:
```bash
python -m pip install -e '.[test]'
python -m pytest tests/test_cli_skeleton.py -v
code-metrics --help
```
Expected: install succeeds, test PASSES, `code-metrics --help` prints the usage
message.

- [ ] **Step 7: Verify ruff is clean**

Run: `ruff check . && ruff format --check .`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add -A pyproject.toml python tests setup.cfg .github/workflows/lint.yaml
git commit -m "Add installable package scaffolding and CLI skeleton"
```

---

### Task 2: Counter abstraction and the cloc backend

**Files:**
- Create: `python/lsst/codemetrics/counters.py`
- Create: `tests/fixtures/cloc.yaml`
- Create: `tests/test_counters_cloc.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `LanguageCount(BaseModel)` with fields `n_files: int`, `blank: int`, `comment: int`, `code: int`.
  - `CounterError(RuntimeError)`.
  - `LineCounter(ABC)` with `name: ClassVar[str]`, `__init__(self, executable: str | None = None)`, `version` property returning `str`, abstract `count(self, path: Path, exclude_dirs: Sequence[str] = ()) -> dict[str, LanguageCount]`, and helper `_run(self, args: Sequence[str]) -> str`.
  - `ClocCounter(LineCounter)` with `name = "cloc"` and additionally `write_report(self, paths: Sequence[Path], output_file: Path, include_langs: Sequence[str] | None = None, exclude_dirs: Sequence[str] = ()) -> None`.

- [ ] **Step 1: Save the real cloc fixture**

Create `tests/fixtures/cloc.yaml` with output captured from cloc 2.10:

```yaml
---
# github.com/AlDanial/cloc
header :
  cloc_url           : github.com/AlDanial/cloc
  cloc_version       : 2.10
  elapsed_seconds    : 0.0111749172210693
  n_files            : 4
  n_lines            : 16
  files_per_second   : 357.944485929466
  lines_per_second   : 1431.77794371786
'Python' :
  nFiles: 2
  blank: 3
  comment: 2
  code: 4
'C++' :
  nFiles: 1
  blank: 1
  comment: 1
  code: 2
'C/C++ Header' :
  nFiles: 1
  blank: 0
  comment: 1
  code: 2
SUM:
  blank: 4
  comment: 4
  code: 8
  nFiles: 4
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_counters_cloc.py`:

```python
import shutil
import subprocess
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_counters_cloc.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lsst.codemetrics.counters'`

- [ ] **Step 4: Write the implementation**

Create `python/lsst/codemetrics/counters.py`:

```python
"""Line counting backends.

Each backend shells out to an external tool and converts its structured
output into a mapping of language name to `LanguageCount`.  Language names
are whatever the tool itself reports; they are never translated between
backends.
"""

import subprocess
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import yaml
from pydantic import BaseModel


class CounterError(RuntimeError):
    """Raised when a counting backend cannot be run or its output parsed."""


class LanguageCount(BaseModel):
    """Line counts reported for a single language."""

    n_files: int
    blank: int
    comment: int
    code: int


class LineCounter(ABC):
    """Base class for line counting backends.

    Parameters
    ----------
    executable : `str`, optional
        Name or path of the external program.  Defaults to the backend
        name.
    """

    name: ClassVar[str]

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or self.name
        self._version: str | None = None

    @property
    def version(self) -> str:
        """Version reported by the external tool (`str`, read-only)."""
        if self._version is None:
            self._version = self._parse_version(self._run(["--version"]))
        return self._version

    def _run(self, args: Sequence[str]) -> str:
        """Run the external tool and return its standard output.

        Parameters
        ----------
        args : `~collections.abc.Sequence` [ `str` ]
            Arguments to pass to the tool.

        Returns
        -------
        output : `str`
            Standard output from the tool.

        Raises
        ------
        CounterError
            Raised if the tool is missing or exits non-zero.
        """
        try:
            completed = subprocess.run(
                [self.executable, *args],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise CounterError(
                f"Counting tool {self.executable!r} was not found on PATH."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise CounterError(
                f"Counting tool {self.executable!r} failed: {exc.stderr.strip()}"
            ) from exc
        return completed.stdout

    @abstractmethod
    def _parse_version(self, raw: str) -> str:
        """Extract a version number from the tool's version output.

        Parameters
        ----------
        raw : `str`
            Raw standard output of the version command.

        Returns
        -------
        version : `str`
            Bare version number.
        """
        raise NotImplementedError

    @abstractmethod
    def parse(self, raw: str) -> dict[str, LanguageCount]:
        """Convert the tool's report into per-language counts.

        Parameters
        ----------
        raw : `str`
            Raw standard output of the counting command.

        Returns
        -------
        counts : `dict` [ `str`, `LanguageCount` ]
            Counts keyed by the tool's own language names.
        """
        raise NotImplementedError

    @abstractmethod
    def count(
        self, path: Path, exclude_dirs: Sequence[str] = ()
    ) -> dict[str, LanguageCount]:
        """Count lines beneath a directory.

        Parameters
        ----------
        path : `~pathlib.Path`
            Directory to scan.
        exclude_dirs : `~collections.abc.Sequence` [ `str` ], optional
            Directory names to skip.

        Returns
        -------
        counts : `dict` [ `str`, `LanguageCount` ]
            Counts keyed by the tool's own language names.
        """
        raise NotImplementedError


class ClocCounter(LineCounter):
    """Line counter backed by cloc."""

    name = "cloc"

    def _parse_version(self, raw: str) -> str:
        # cloc --version prints the bare number, such as "2.10".
        return raw.strip()

    def parse(self, raw: str) -> dict[str, LanguageCount]:
        # Documented in the base class.
        data = yaml.safe_load(raw)
        if not data:
            return {}
        return {
            language: LanguageCount(
                n_files=values["nFiles"],
                blank=values["blank"],
                comment=values["comment"],
                code=values["code"],
            )
            for language, values in data.items()
            if language not in ("header", "SUM")
        }

    def count(
        self, path: Path, exclude_dirs: Sequence[str] = ()
    ) -> dict[str, LanguageCount]:
        # Documented in the base class.
        args = ["--yaml", "--quiet"]
        if exclude_dirs:
            args.append(f"--exclude-dir={','.join(exclude_dirs)}")
        args.append(str(path))
        return self.parse(self._run(args))

    def write_report(
        self,
        paths: Sequence[Path],
        output_file: Path,
        include_langs: Sequence[str] | None = None,
        exclude_dirs: Sequence[str] = (),
    ) -> None:
        """Write a cloc YAML report directly to a file.

        The stack scan preserves cloc's own report format, including its
        header and SUM blocks, so that files written today match those
        already in ``data/``.

        Parameters
        ----------
        paths : `~collections.abc.Sequence` [ `~pathlib.Path` ]
            Directories to scan.
        output_file : `~pathlib.Path`
            File to write the report to.
        include_langs : `~collections.abc.Sequence` [ `str` ], optional
            Restrict counting to these languages.
        exclude_dirs : `~collections.abc.Sequence` [ `str` ], optional
            Directory names to skip.
        """
        args = ["--yaml", f"--report-file={output_file}"]
        if include_langs:
            args.append(f"--include-lang={','.join(include_langs)}")
        if exclude_dirs:
            args.append(f"--exclude-dir={','.join(exclude_dirs)}")
        args.extend(str(p) for p in paths)
        self._run(args)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_counters_cloc.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Verify ruff is clean**

Run: `ruff check . && ruff format --check .`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add python/lsst/codemetrics/counters.py tests/fixtures/cloc.yaml tests/test_counters_cloc.py
git commit -m "Add line counter abstraction and cloc backend"
```

---

### Task 3: The scc and tokei backends and the registry

**Files:**
- Modify: `python/lsst/codemetrics/counters.py`
- Create: `tests/fixtures/scc.json`
- Create: `tests/fixtures/tokei.json`
- Create: `tests/test_counters_others.py`

**Interfaces:**
- Consumes: `LineCounter`, `LanguageCount`, `CounterError` from Task 2.
- Produces:
  - `SccCounter(LineCounter)` with `name = "scc"`.
  - `TokeiCounter(LineCounter)` with `name = "tokei"`.
  - `COUNTERS: dict[str, type[LineCounter]]` mapping `"cloc"`, `"scc"`, `"tokei"` to their classes.
  - `get_counter(name: str, executable: str | None = None) -> LineCounter`.

- [ ] **Step 1: Save the real fixtures**

Create `tests/fixtures/scc.json`, captured from scc 3.7.0:

```json
[{"Name":"Python","Bytes":62,"CodeBytes":0,"Lines":9,"Code":4,"Comment":2,"Blank":3,"Complexity":0,"Count":2,"WeightedComplexity":0,"Files":[],"LineLength":null,"ULOC":0},{"Name":"C Header","Bytes":29,"CodeBytes":0,"Lines":3,"Code":2,"Comment":1,"Blank":0,"Complexity":0,"Count":1,"WeightedComplexity":0,"Files":[],"LineLength":null,"ULOC":0},{"Name":"C++","Bytes":46,"CodeBytes":0,"Lines":4,"Code":2,"Comment":1,"Blank":1,"Complexity":0,"Count":1,"WeightedComplexity":0,"Files":[],"LineLength":null,"ULOC":0}]
```

Create `tests/fixtures/tokei.json`, captured from tokei 14.0.0:

```json
{
  "C Header": {
    "blanks": 0,
    "code": 2,
    "comments": 1,
    "reports": [
      {"stats": {"blanks": 0, "code": 2, "comments": 1, "blobs": {}}, "name": "/tmp/fx/src/b.h"}
    ],
    "children": {},
    "inaccurate": false
  },
  "Python": {
    "blanks": 3,
    "code": 4,
    "comments": 1,
    "reports": [
      {"stats": {"blanks": 3, "code": 4, "comments": 1, "blobs": {}}, "name": "/tmp/fx/src/a.py"}
    ],
    "children": {},
    "inaccurate": false
  },
  "Total": {
    "blanks": 3,
    "code": 6,
    "comments": 2,
    "reports": [],
    "children": {},
    "inaccurate": false
  }
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_counters_others.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_counters_others.py -v`
Expected: FAIL with `ImportError: cannot import name 'SccCounter'`

- [ ] **Step 4: Add `json` to the imports in `counters.py`**

Change the import block at the top of `python/lsst/codemetrics/counters.py`
from:

```python
import subprocess
```

to:

```python
import json
import subprocess
```

- [ ] **Step 5: Append the two backends and the registry**

Add to the end of `python/lsst/codemetrics/counters.py`:

```python
class SccCounter(LineCounter):
    """Line counter backed by scc."""

    name = "scc"

    def _parse_version(self, raw: str) -> str:
        # scc --version prints "scc version 3.7.0".
        return raw.strip().split()[-1]

    def parse(self, raw: str) -> dict[str, LanguageCount]:
        # Documented in the base class.
        data = json.loads(raw)
        return {
            entry["Name"]: LanguageCount(
                n_files=entry["Count"],
                blank=entry["Blank"],
                comment=entry["Comment"],
                code=entry["Code"],
            )
            for entry in data
        }

    def count(
        self, path: Path, exclude_dirs: Sequence[str] = ()
    ) -> dict[str, LanguageCount]:
        # Documented in the base class.
        args = ["--format", "json"]
        if exclude_dirs:
            args.extend(["--exclude-dir", ",".join(exclude_dirs)])
        args.append(str(path))
        return self.parse(self._run(args))


class TokeiCounter(LineCounter):
    """Line counter backed by tokei."""

    name = "tokei"

    def _parse_version(self, raw: str) -> str:
        # tokei --version prints "tokei 14.0.0 compiled with ...".
        return raw.strip().split()[1]

    def parse(self, raw: str) -> dict[str, LanguageCount]:
        # Documented in the base class.  tokei reports no file count, so it
        # is taken from the length of the per-file report list.
        data = json.loads(raw)
        return {
            language: LanguageCount(
                n_files=len(values["reports"]),
                blank=values["blanks"],
                comment=values["comments"],
                code=values["code"],
            )
            for language, values in data.items()
            if language != "Total"
        }

    def count(
        self, path: Path, exclude_dirs: Sequence[str] = ()
    ) -> dict[str, LanguageCount]:
        # Documented in the base class.
        args = ["--output", "json"]
        for name in exclude_dirs:
            args.extend(["--exclude", name])
        args.append(str(path))
        return self.parse(self._run(args))


COUNTERS: dict[str, type[LineCounter]] = {
    ClocCounter.name: ClocCounter,
    SccCounter.name: SccCounter,
    TokeiCounter.name: TokeiCounter,
}


def get_counter(name: str, executable: str | None = None) -> LineCounter:
    """Construct a counting backend by name.

    Parameters
    ----------
    name : `str`
        Backend name, one of the keys of `COUNTERS`.
    executable : `str`, optional
        Override the program to run.

    Returns
    -------
    counter : `LineCounter`
        Newly constructed backend.

    Raises
    ------
    KeyError
        Raised if the name is not a known backend.
    """
    if name not in COUNTERS:
        known = ", ".join(sorted(COUNTERS))
        raise KeyError(f"Unknown counter {name!r}. Known counters: {known}.")
    return COUNTERS[name](executable=executable)
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: PASS.

- [ ] **Step 7: Verify ruff is clean**

Run: `ruff check . && ruff format --check .`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add python/lsst/codemetrics/counters.py tests/
git commit -m "Add scc and tokei backends and the counter registry"
```

---

### Task 4: Revision sampling

**Files:**
- Create: `python/lsst/codemetrics/revisions.py`
- Create: `tests/conftest.py`
- Create: `tests/test_revisions.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Sample(BaseModel)` with `commit: str`, `date: datetime`, `label: str | None = None`.
  - `GitError(RuntimeError)`.
  - `git_output(repo: Path, *args: str) -> str`.
  - `default_branch(repo: Path) -> str`.
  - `sample_revisions(repo: Path, mode: str, branch: str | None = None, tag_pattern: str = "w.*", since: datetime | None = None, until: datetime | None = None) -> list[Sample]`.
  - `MODES: tuple[str, ...]` equal to `("tags", "first-parent", "all")`.
- Also produces the shared pytest fixture `synthetic_repo` in `tests/conftest.py`, used by Tasks 5 and 7.

- [ ] **Step 1: Write the shared repository fixture**

Create `tests/conftest.py`:

```python
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
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_revisions.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_revisions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lsst.codemetrics.revisions'`

- [ ] **Step 4: Write the implementation**

Create `python/lsst/codemetrics/revisions.py`:

```python
"""Selection of the revisions at which a repository is measured."""

import subprocess
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

MODES: tuple[str, ...] = ("tags", "first-parent", "all")
"""Supported sampling modes (`tuple` [ `str` ])."""


class GitError(RuntimeError):
    """Raised when a git command fails."""


class Sample(BaseModel):
    """A single point in a repository's history."""

    commit: str
    date: datetime
    label: str | None = None


def git_output(repo: Path, *args: str) -> str:
    """Run a git command in a repository and return its standard output.

    Parameters
    ----------
    repo : `~pathlib.Path`
        Repository to run in.
    *args : `str`
        Arguments to pass to git.

    Returns
    -------
    output : `str`
        Standard output, with trailing whitespace removed.

    Raises
    ------
    GitError
        Raised if git exits non-zero.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        joined = " ".join(args)
        raise GitError(f"git {joined} failed: {exc.stderr.strip()}") from exc
    return completed.stdout.rstrip()


def default_branch(repo: Path) -> str:
    """Determine the branch to walk when none was given.

    Prefers the remote's HEAD and falls back to the currently checked out
    branch, which is what a repository with no remote will have.

    Parameters
    ----------
    repo : `~pathlib.Path`
        Repository to inspect.

    Returns
    -------
    branch : `str`
        Name of the branch.
    """
    try:
        ref = git_output(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    except GitError:
        return git_output(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return ref


def _commit_samples(repo: Path, rev_args: list[str]) -> list[Sample]:
    """Build samples from a ``git rev-list`` invocation.

    Parameters
    ----------
    repo : `~pathlib.Path`
        Repository to inspect.
    rev_args : `list` [ `str` ]
        Arguments following ``rev-list``.

    Returns
    -------
    samples : `list` [ `Sample` ]
        One sample per commit, unsorted.
    """
    # %cI is the committer date in strict ISO 8601.  The author date is
    # deliberately not used: a rebase preserves it, so it does not record
    # when the work landed on the branch.
    raw = git_output(repo, "rev-list", "--format=%H %cI", "--no-commit-header", *rev_args)
    samples = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        commit, _, date = line.partition(" ")
        samples.append(Sample(commit=commit, date=datetime.fromisoformat(date)))
    return samples


def _tag_samples(repo: Path, pattern: str) -> list[Sample]:
    """Build samples from the repository's tags.

    Parameters
    ----------
    repo : `~pathlib.Path`
        Repository to inspect.
    pattern : `str`
        Glob matched against tag names.

    Returns
    -------
    samples : `list` [ `Sample` ]
        One sample per matching tag, unsorted.
    """
    # creatordate is the tag date for annotated tags and the commit date
    # for lightweight ones, so it orders correctly whatever the tag naming
    # convention happened to be at the time.
    #
    # An annotated tag's objectname is the tag object, not the commit it
    # points at, so it is dereferenced.  Lightweight tags have no
    # dereferenced name and fall through to the plain one.
    fields = (
        "%(refname:short)"
        " %(if)%(*objectname)%(then)%(*objectname)%(else)%(objectname)%(end)"
        " %(creatordate:iso-strict)"
    )
    raw = git_output(
        repo,
        "tag",
        "--list",
        pattern,
        "--sort=creatordate",
        f"--format={fields}",
    )
    samples = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        label, commit, date = line.split(" ")
        samples.append(
            Sample(commit=commit, date=datetime.fromisoformat(date), label=label)
        )
    return samples


def sample_revisions(
    repo: Path,
    mode: str,
    branch: str | None = None,
    tag_pattern: str = "w.*",
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Sample]:
    """Choose the revisions at which a repository will be measured.

    Parameters
    ----------
    repo : `~pathlib.Path`
        Repository to sample.
    mode : `str`
        One of the values in `MODES`.
    branch : `str`, optional
        Branch to walk.  Defaults to the remote HEAD.
    tag_pattern : `str`, optional
        Glob matched against tag names in ``tags`` mode.
    since : `~datetime.datetime`, optional
        Discard samples earlier than this.
    until : `~datetime.datetime`, optional
        Discard samples later than this.

    Returns
    -------
    samples : `list` [ `Sample` ]
        Samples sorted by date, ascending.

    Raises
    ------
    ValueError
        Raised if the mode is not recognized.
    """
    if mode not in MODES:
        known = ", ".join(MODES)
        raise ValueError(f"Unknown mode {mode!r}. Known modes: {known}.")

    if mode == "tags":
        samples = _tag_samples(repo, tag_pattern)
    else:
        target = branch or default_branch(repo)
        rev_args = ["--first-parent", target] if mode == "first-parent" else [target]
        samples = _commit_samples(repo, rev_args)

    if since is not None:
        samples = [s for s in samples if s.date >= since]
    if until is not None:
        samples = [s for s in samples if s.date <= until]

    return sorted(samples, key=lambda s: (s.date, s.commit))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_revisions.py -v`
Expected: PASS (10 tests).

- [ ] **Step 6: Verify ruff is clean**

Run: `ruff check . && ruff format --check .`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add python/lsst/codemetrics/revisions.py tests/conftest.py tests/test_revisions.py
git commit -m "Add revision sampling by tag, first-parent chain, or all commits"
```

---

### Task 5: Clone cache and throwaway worktree

**Files:**
- Create: `python/lsst/codemetrics/worktree.py`
- Create: `tests/test_worktree.py`

**Interfaces:**
- Consumes: `git_output`, `GitError` from Task 4.
- Produces:
  - `DEFAULT_CACHE_DIR: Path` equal to `Path.home() / ".cache" / "lsst-code-metrics"`.
  - `is_url(target: str) -> bool`.
  - `repo_name(target: str) -> str`.
  - `ensure_source(target: str, cache_dir: Path) -> Path`.
  - `temporary_worktree(source: Path) -> AbstractContextManager[Path]` (a `@contextmanager`).
  - `checkout(worktree: Path, commit: str) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_worktree.py`:

```python
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


def test_ensure_source_returns_local_path_unchanged(synthetic_repo, tmp_path):
    assert ensure_source(str(synthetic_repo), tmp_path / "cache") == synthetic_repo


def test_ensure_source_clones_a_url_into_the_cache(synthetic_repo, tmp_path):
    cache = tmp_path / "cache"
    # A local path is a valid clone URL, so file:// exercises the clone path.
    source = ensure_source(f"file://{synthetic_repo}", cache)
    assert source.exists()
    assert cache in source.parents
    assert git_output(source, "rev-parse", "--is-bare-repository") == "true"


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
        stray = tree / "stray.py"
        stray.write_text("x = 1\n")
        ignored = tree / "build.log"
        ignored.write_text("noise\n")
        checkout(tree, head)
        assert not stray.exists()
        assert not ignored.exists()


def test_checkout_of_unknown_commit_raises(synthetic_repo):
    with temporary_worktree(synthetic_repo) as tree:
        with pytest.raises(GitError):
            checkout(tree, "0" * 40)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worktree.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lsst.codemetrics.worktree'`

- [ ] **Step 3: Write the implementation**

Create `python/lsst/codemetrics/worktree.py`:

```python
"""Obtaining a repository and checking revisions out of it safely."""

import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .revisions import git_output

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "lsst-code-metrics"
"""Location of the mirror clone cache (`~pathlib.Path`)."""

_URL_RE = re.compile(r"^(https?|git|ssh|file)://|^[^/]+@[^/]+:")


def is_url(target: str) -> bool:
    """Report whether a target names a remote repository.

    Parameters
    ----------
    target : `str`
        Repository path or URL.

    Returns
    -------
    remote : `bool`
        `True` if the target is a URL rather than a local path.
    """
    return bool(_URL_RE.search(target))


def repo_name(target: str) -> str:
    """Derive a short name from a repository path or URL.

    Parameters
    ----------
    target : `str`
        Repository path or URL.

    Returns
    -------
    name : `str`
        Final path component without any ``.git`` suffix.
    """
    trimmed = target.rstrip("/")
    base = trimmed.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return base.removesuffix(".git")


def ensure_source(target: str, cache_dir: Path) -> Path:
    """Return a local repository to create worktrees from.

    A local path is used where it stands.  A URL is cloned as a mirror
    into the cache on first use and fetched on later runs.  A plain
    ``--bare`` clone does not configure ``remote.origin.fetch``, so a
    later ``git fetch`` would update tags and ``FETCH_HEAD`` only and
    leave branch refs frozen at whatever the first clone saw.
    ``--mirror`` implies ``--bare`` and additionally sets up a refspec
    that maps every ref, so a later fetch keeps branches current too.

    Parameters
    ----------
    target : `str`
        Repository path or URL.
    cache_dir : `~pathlib.Path`
        Directory holding cached mirror clones.

    Returns
    -------
    source : `~pathlib.Path`
        Repository that worktrees may be created from.
    """
    if not is_url(target):
        return Path(target).expanduser().resolve()

    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{repo_name(target)}.git"
    if cached.exists():
        git_output(cached, "fetch", "--prune", "origin")
    else:
        git_output(cache_dir, "clone", "--mirror", target, str(cached))
    return cached


@contextmanager
def temporary_worktree(source: Path) -> Iterator[Path]:
    """Provide a detached worktree that is removed on exit.

    The caller's own working tree is never touched, so a repository with
    uncommitted changes can be measured safely.

    Parameters
    ----------
    source : `~pathlib.Path`
        Repository to attach the worktree to.

    Yields
    ------
    worktree : `~pathlib.Path`
        Directory containing the detached worktree.
    """
    holder = Path(tempfile.mkdtemp(prefix="code-metrics-"))
    tree = holder / "tree"
    try:
        git_output(source, "worktree", "add", "--detach", "--no-checkout", "-q", str(tree))
        yield tree
    finally:
        try:
            git_output(source, "worktree", "remove", "--force", str(tree))
        finally:
            shutil.rmtree(holder, ignore_errors=True)
            git_output(source, "worktree", "prune")


def checkout(worktree: Path, commit: str) -> None:
    """Check a commit out into a worktree, leaving nothing behind.

    The clean step is required for correctness.  A file that is tracked at
    one commit and untracked at the next survives a checkout, and would
    otherwise be counted at every later sample.

    Parameters
    ----------
    worktree : `~pathlib.Path`
        Worktree to update.
    commit : `str`
        Commit to check out.
    """
    git_output(worktree, "checkout", "--detach", "--force", "-q", commit)
    git_output(worktree, "clean", "-xdff", "-q")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_worktree.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Verify ruff is clean**

Run: `ruff check . && ruff format --check .`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add python/lsst/codemetrics/worktree.py tests/test_worktree.py
git commit -m "Add clone cache and throwaway detached worktree handling"
```

---

### Task 6: CSV storage and incremental merge

**Files:**
- Create: `python/lsst/codemetrics/storage.py`
- Create: `tests/test_storage.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `COLUMNS: tuple[str, ...]` equal to `("commit", "date", "label", "counter", "counter_version", "language", "n_files", "blank", "comment", "code")`.
  - `LineRow(BaseModel)` with those fields; `label: str = ""`, `date: datetime`, the four counts `int`, the rest `str`.
  - `RepoMeta(BaseModel)` with `name: str`, `url: str`, `mode: str`, `branch: str`, `tag_pattern: str`, `exclude_dirs: list[str]`.
  - `read_rows(path: Path) -> list[LineRow]`.
  - `write_rows(path: Path, rows: Iterable[LineRow]) -> None`.
  - `merge_rows(existing: Iterable[LineRow], new: Iterable[LineRow]) -> list[LineRow]`.
  - `collected_keys(rows: Iterable[LineRow]) -> set[tuple[str, str]]`.
  - `write_meta(path: Path, meta: RepoMeta) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_storage.py`:

```python
from datetime import UTC, datetime

import pytest
import yaml
from pydantic import ValidationError

from lsst.codemetrics.storage import (
    COLUMNS,
    LineRow,
    RepoMeta,
    collected_keys,
    merge_rows,
    read_rows,
    write_meta,
    write_rows,
)


def make_row(commit="abc", language="Python", counter="cloc", day=1):
    return LineRow(
        commit=commit,
        date=datetime(2020, 1, day, tzinfo=UTC),
        label="",
        counter=counter,
        counter_version="2.10",
        language=language,
        n_files=1,
        blank=2,
        comment=3,
        code=4,
    )


def test_columns_are_the_documented_order():
    assert COLUMNS == (
        "commit",
        "date",
        "label",
        "counter",
        "counter_version",
        "language",
        "n_files",
        "blank",
        "comment",
        "code",
    )


def test_round_trip(tmp_path):
    path = tmp_path / "repo.csv"
    rows = [make_row(), make_row(language="C++")]
    write_rows(path, rows)
    assert read_rows(path) == sorted(rows, key=lambda r: (r.date, r.commit, r.language))


def test_reading_a_missing_file_returns_empty(tmp_path):
    assert read_rows(tmp_path / "absent.csv") == []


def test_rows_are_written_sorted(tmp_path):
    path = tmp_path / "repo.csv"
    write_rows(path, [make_row(commit="z", day=5), make_row(commit="a", day=2)])
    assert [r.commit for r in read_rows(path)] == ["a", "z"]


def test_incremental_write_only_appends(tmp_path):
    path = tmp_path / "repo.csv"
    write_rows(path, [make_row(commit="a", day=1)])
    first = path.read_text()
    write_rows(path, [make_row(commit="a", day=1), make_row(commit="b", day=2)])
    second = path.read_text()
    assert second.startswith(first)


def test_collected_keys_pairs_commit_with_counter():
    rows = [make_row(commit="a", counter="cloc"), make_row(commit="a", counter="tokei")]
    assert collected_keys(rows) == {("a", "cloc"), ("a", "tokei")}


def test_merge_replaces_matching_commit_and_counter():
    existing = [make_row(commit="a", counter="cloc", language="Python")]
    new = [make_row(commit="a", counter="cloc", language="C++")]
    merged = merge_rows(existing, new)
    assert [r.language for r in merged] == ["C++"]


def test_merge_keeps_other_counters_for_the_same_commit():
    existing = [make_row(commit="a", counter="cloc")]
    new = [make_row(commit="a", counter="tokei")]
    merged = merge_rows(existing, new)
    assert {r.counter for r in merged} == {"cloc", "tokei"}


def test_malformed_row_is_rejected(tmp_path):
    path = tmp_path / "repo.csv"
    path.write_text(
        ",".join(COLUMNS) + "\n" + "abc,2020-01-01T00:00:00+00:00,,cloc,2.10,Python,x,2,3,4\n"
    )
    with pytest.raises(ValidationError):
        read_rows(path)


def test_write_meta_is_readable_yaml(tmp_path):
    path = tmp_path / "repo.meta.yaml"
    meta = RepoMeta(
        name="afw",
        url="https://github.com/lsst/afw",
        mode="first-parent",
        branch="main",
        tag_pattern="w.*",
        exclude_dirs=["vendor"],
    )
    write_meta(path, meta)
    loaded = yaml.safe_load(path.read_text())
    assert loaded["name"] == "afw"
    assert loaded["exclude_dirs"] == ["vendor"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lsst.codemetrics.storage'`

- [ ] **Step 3: Write the implementation**

Create `python/lsst/codemetrics/storage.py`:

```python
"""Reading and writing per-repository line count records.

Records are stored in long format, one row per sample and language, so
that a language appearing for the first time adds rows rather than
columns.  Rows are kept sorted by date so an incremental run appends to
the end of the file and produces a small difference.
"""

import csv
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel

COLUMNS: tuple[str, ...] = (
    "commit",
    "date",
    "label",
    "counter",
    "counter_version",
    "language",
    "n_files",
    "blank",
    "comment",
    "code",
)
"""Column order of the CSV files (`tuple` [ `str` ])."""


class LineRow(BaseModel):
    """One language's counts at one revision."""

    commit: str
    date: datetime
    label: str = ""
    counter: str
    counter_version: str
    language: str
    n_files: int
    blank: int
    comment: int
    code: int


class RepoMeta(BaseModel):
    """Description of how a repository's records were collected."""

    name: str
    url: str
    mode: str
    branch: str
    tag_pattern: str
    exclude_dirs: list[str]


def _sort_key(row: LineRow) -> tuple[datetime, str, str]:
    """Return the canonical sort key for a row.

    Parameters
    ----------
    row : `LineRow`
        Row to key.

    Returns
    -------
    key : `tuple`
        Date, commit, and language.
    """
    return (row.date, row.commit, row.language)


def read_rows(path: Path) -> list[LineRow]:
    """Read stored records.

    Parameters
    ----------
    path : `~pathlib.Path`
        CSV file to read.  A missing file yields no rows.

    Returns
    -------
    rows : `list` [ `LineRow` ]
        Records in file order.
    """
    if not path.exists():
        return []
    with path.open(newline="") as fd:
        return [LineRow(**record) for record in csv.DictReader(fd)]


```

> **Correction applied during implementation.** The `path.open("w")` below
> truncates the destination before rewriting it, so a crash mid-write
> destroys all previously collected history — which contradicts this plan's
> own promise that an interrupted run stays resumable, given Task 7 flushes
> every 50 samples for exactly that reason. The shipped implementation
> writes through a temporary file in the destination's directory and
> `os.replace()`s it into place, and preserves an existing file's mode
> (defaulting to `0o644` for new files) so the atomic path does not silently
> narrow permissions. See `python/lsst/codemetrics/storage.py` for the final
> form; the code below is retained only to show the original intent.

```python
def write_rows(path: Path, rows: Iterable[LineRow]) -> None:
    """Write records, sorted canonically.

    Parameters
    ----------
    path : `~pathlib.Path`
        CSV file to write.
    rows : `~collections.abc.Iterable` [ `LineRow` ]
        Records to store.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fd:
        writer = csv.DictWriter(fd, fieldnames=COLUMNS)
        writer.writeheader()
        for row in sorted(rows, key=_sort_key):
            record = row.model_dump()
            record["date"] = row.date.isoformat()
            writer.writerow(record)


def collected_keys(rows: Iterable[LineRow]) -> set[tuple[str, str]]:
    """Report which revisions have already been counted, and by what.

    The key pairs the commit with the counter, so that switching backend
    recounts the history rather than leaving a file whose early rows came
    from one tool and whose later rows came from another.

    Parameters
    ----------
    rows : `~collections.abc.Iterable` [ `LineRow` ]
        Records to inspect.

    Returns
    -------
    keys : `set` [ `tuple` [ `str`, `str` ] ]
        Commit and counter name pairs.
    """
    return {(row.commit, row.counter) for row in rows}


def merge_rows(
    existing: Iterable[LineRow], new: Iterable[LineRow]
) -> list[LineRow]:
    """Combine stored records with freshly collected ones.

    Rows sharing a commit and counter with a new row are replaced, so a
    recount supersedes rather than duplicates.

    Parameters
    ----------
    existing : `~collections.abc.Iterable` [ `LineRow` ]
        Records already on disk.
    new : `~collections.abc.Iterable` [ `LineRow` ]
        Records just collected.

    Returns
    -------
    rows : `list` [ `LineRow` ]
        Merged records, sorted canonically.
    """
    new_rows = list(new)
    superseded = collected_keys(new_rows)
    kept = [row for row in existing if (row.commit, row.counter) not in superseded]
    return sorted([*kept, *new_rows], key=_sort_key)


def write_meta(path: Path, meta: RepoMeta) -> None:
    """Write the sidecar description of a collection run.

    Parameters
    ----------
    path : `~pathlib.Path`
        YAML file to write.
    meta : `RepoMeta`
        Description to store.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(meta.model_dump(), sort_keys=False))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_storage.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Verify ruff is clean**

Run: `ruff check . && ruff format --check .`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add python/lsst/codemetrics/storage.py tests/test_storage.py
git commit -m "Add CSV storage with incremental merge keyed on commit and counter"
```

---

### Task 7: Collection orchestration

**Files:**
- Create: `python/lsst/codemetrics/collect.py`
- Create: `tests/test_collect.py`

**Interfaces:**
- Consumes: `LineCounter`, `CounterError`, `LanguageCount` (Task 2/3); `Sample`, `sample_revisions`, `default_branch`, `GitError` (Task 4); `ensure_source`, `temporary_worktree`, `checkout`, `repo_name`, `is_url`, `DEFAULT_CACHE_DIR` (Task 5); `LineRow`, `RepoMeta`, `read_rows`, `write_rows`, `merge_rows`, `collected_keys`, `write_meta` (Task 6).
- Produces:
  - `CollectResult(BaseModel)` with `added: int`, `skipped: int`, `failed: int`, `total: int`, `languages: list[str]`, `first_date: datetime | None`, `last_date: datetime | None`.
  - `collect(target: str, *, name: str | None = None, output_dir: Path = Path("data/repos"), mode: str = "first-parent", branch: str | None = None, tag_pattern: str = "w.*", counter: LineCounter, exclude_dirs: Sequence[str] = (), since: datetime | None = None, until: datetime | None = None, cache_dir: Path = DEFAULT_CACHE_DIR, force: bool = False, strict: bool = False, flush_every: int = 50, progress: bool = True) -> CollectResult`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collect.py`:

```python
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lsst.codemetrics.collect import collect
from lsst.codemetrics.counters import CounterError, LanguageCount, LineCounter
from lsst.codemetrics.storage import read_rows


class StubCounter(LineCounter):
    """A counter that reports a fixed result without running anything."""

    name = "stub"

    def __init__(self, fail_on=None):
        super().__init__()
        self.fail_on = fail_on or set()
        self.calls = 0

    def _parse_version(self, raw):
        return "0.0"

    @property
    def version(self):
        return "0.0"

    def parse(self, raw):
        return {}

    def count(self, path, exclude_dirs=()):
        self.calls += 1
        if self.calls in self.fail_on:
            raise CounterError("stub failure")
        return {"Python": LanguageCount(n_files=1, blank=1, comment=2, code=3)}


def test_collects_every_sample(synthetic_repo, tmp_path):
    result = collect(
        str(synthetic_repo),
        name="synthetic",
        output_dir=tmp_path,
        mode="first-parent",
        branch="main",
        counter=StubCounter(),
        progress=False,
    )
    assert result.added == 3
    assert result.total == 3
    assert result.languages == ["Python"]
    rows = read_rows(tmp_path / "synthetic.csv")
    assert len(rows) == 3
    assert {r.counter for r in rows} == {"stub"}


def test_second_run_adds_nothing(synthetic_repo, tmp_path):
    kwargs = dict(
        name="synthetic",
        output_dir=tmp_path,
        mode="first-parent",
        branch="main",
        progress=False,
    )
    collect(str(synthetic_repo), counter=StubCounter(), **kwargs)
    second = collect(str(synthetic_repo), counter=StubCounter(), **kwargs)
    assert second.added == 0
    assert second.skipped == 3
    assert second.total == 3


def test_switching_counter_recounts_and_keeps_both(synthetic_repo, tmp_path):
    kwargs = dict(
        name="synthetic",
        output_dir=tmp_path,
        mode="first-parent",
        branch="main",
        progress=False,
    )
    collect(str(synthetic_repo), counter=StubCounter(), **kwargs)

    class OtherCounter(StubCounter):
        name = "other"

    collect(str(synthetic_repo), counter=OtherCounter(), **kwargs)
    rows = read_rows(tmp_path / "synthetic.csv")
    assert {r.counter for r in rows} == {"stub", "other"}
    assert len(rows) == 6


def test_a_failing_sample_is_skipped(synthetic_repo, tmp_path):
    result = collect(
        str(synthetic_repo),
        name="synthetic",
        output_dir=tmp_path,
        mode="first-parent",
        branch="main",
        counter=StubCounter(fail_on={2}),
        progress=False,
    )
    assert result.added == 2
    assert result.failed == 1


def test_strict_aborts_on_failure(synthetic_repo, tmp_path):
    with pytest.raises(CounterError):
        collect(
            str(synthetic_repo),
            name="synthetic",
            output_dir=tmp_path,
            mode="first-parent",
            branch="main",
            counter=StubCounter(fail_on={2}),
            strict=True,
            progress=False,
        )


def test_empty_sample_list_raises(synthetic_repo, tmp_path):
    with pytest.raises(ValueError, match="No revisions"):
        collect(
            str(synthetic_repo),
            name="synthetic",
            output_dir=tmp_path,
            mode="first-parent",
            branch="main",
            counter=StubCounter(),
            since=datetime(2030, 1, 1, tzinfo=UTC),
            progress=False,
        )


def test_sidecar_metadata_is_written(synthetic_repo, tmp_path):
    collect(
        str(synthetic_repo),
        name="synthetic",
        output_dir=tmp_path,
        mode="first-parent",
        branch="main",
        counter=StubCounter(),
        exclude_dirs=["vendor"],
        progress=False,
    )
    assert (tmp_path / "synthetic.meta.yaml").exists()


def test_name_defaults_to_the_repository_basename(synthetic_repo, tmp_path):
    collect(
        str(synthetic_repo),
        output_dir=tmp_path,
        mode="first-parent",
        branch="main",
        counter=StubCounter(),
        progress=False,
    )
    assert (tmp_path / "synthetic.csv").exists()


def test_force_recounts_everything(synthetic_repo, tmp_path):
    kwargs = dict(
        name="synthetic",
        output_dir=tmp_path,
        mode="first-parent",
        branch="main",
        progress=False,
    )
    collect(str(synthetic_repo), counter=StubCounter(), **kwargs)
    counter = StubCounter()
    result = collect(str(synthetic_repo), counter=counter, force=True, **kwargs)
    assert result.added == 3
    assert counter.calls == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_collect.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lsst.codemetrics.collect'`

- [ ] **Step 3: Write the implementation**

Create `python/lsst/codemetrics/collect.py`:

```python
"""Orchestration of a per-repository collection run."""

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel
from rich.progress import (
    BarColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from .counters import CounterError, LineCounter
from .revisions import GitError, Sample, default_branch, sample_revisions
from .storage import (
    LineRow,
    RepoMeta,
    collected_keys,
    merge_rows,
    read_rows,
    write_meta,
    write_rows,
)
from .worktree import DEFAULT_CACHE_DIR, checkout, ensure_source, repo_name, temporary_worktree

_LOG = logging.getLogger(__name__)


class CollectResult(BaseModel):
    """Summary of what a collection run did."""

    added: int
    skipped: int
    failed: int
    total: int
    languages: list[str]
    first_date: datetime | None
    last_date: datetime | None


def _rows_for_sample(sample: Sample, counter: LineCounter, tree: Path, exclude_dirs: Sequence[str]) -> list[LineRow]:
    """Count one revision and convert the result into rows.

    Parameters
    ----------
    sample : `~lsst.codemetrics.revisions.Sample`
        Revision to measure.
    counter : `~lsst.codemetrics.counters.LineCounter`
        Backend to measure with.
    tree : `~pathlib.Path`
        Worktree already positioned at the revision.
    exclude_dirs : `~collections.abc.Sequence` [ `str` ]
        Directory names to skip.

    Returns
    -------
    rows : `list` [ `LineRow` ]
        One row per language reported.
    """
    counts = counter.count(tree, exclude_dirs)
    return [
        LineRow(
            commit=sample.commit,
            date=sample.date,
            label=sample.label or "",
            counter=counter.name,
            counter_version=counter.version,
            language=language,
            n_files=count.n_files,
            blank=count.blank,
            comment=count.comment,
            code=count.code,
        )
        for language, count in counts.items()
    ]


def collect(
    target: str,
    *,
    name: str | None = None,
    output_dir: Path = Path("data/repos"),
    mode: str = "first-parent",
    branch: str | None = None,
    tag_pattern: str = "w.*",
    counter: LineCounter,
    exclude_dirs: Sequence[str] = (),
    since: datetime | None = None,
    until: datetime | None = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    force: bool = False,
    strict: bool = False,
    flush_every: int = 50,
    progress: bool = True,
) -> CollectResult:
    """Measure a repository across its history.

    Parameters
    ----------
    target : `str`
        Repository path or URL.
    name : `str`, optional
        Output base name.  Defaults to the repository's basename.
    output_dir : `~pathlib.Path`, optional
        Directory to write the CSV and sidecar into.
    mode : `str`, optional
        Sampling mode.
    branch : `str`, optional
        Branch to walk.
    tag_pattern : `str`, optional
        Glob matched against tag names in ``tags`` mode.
    counter : `~lsst.codemetrics.counters.LineCounter`
        Backend to measure with.
    exclude_dirs : `~collections.abc.Sequence` [ `str` ], optional
        Directory names to skip.
    since, until : `~datetime.datetime`, optional
        Bound the sampled date range.
    cache_dir : `~pathlib.Path`, optional
        Directory holding cached mirror clones.
    force : `bool`, optional
        Recount revisions that are already stored.
    strict : `bool`, optional
        Abort on the first failure instead of skipping it.
    flush_every : `int`, optional
        Write partial results after this many revisions.
    progress : `bool`, optional
        Display a progress bar.

    Returns
    -------
    result : `CollectResult`
        Summary of the run.

    Raises
    ------
    ValueError
        Raised if no revisions were selected.
    """
    resolved_name = name or repo_name(target)
    csv_path = output_dir / f"{resolved_name}.csv"
    meta_path = output_dir / f"{resolved_name}.meta.yaml"

    source = ensure_source(target, cache_dir)
    resolved_branch = branch or (default_branch(source) if mode != "tags" else "")
    samples = sample_revisions(
        source,
        mode,
        branch=resolved_branch or None,
        tag_pattern=tag_pattern,
        since=since,
        until=until,
    )
    if not samples:
        raise ValueError(f"No revisions selected for {target} in mode {mode!r}.")

    existing = read_rows(csv_path)
    done = set() if force else collected_keys(existing)
    pending = [s for s in samples if (s.commit, counter.name) not in done]
    skipped = len(samples) - len(pending)

    collected: list[LineRow] = []
    failed = 0

    def flush() -> None:
        write_rows(csv_path, merge_rows(existing, collected))

    columns = (
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    )
    with (
        temporary_worktree(source) as tree,
        Progress(*columns, disable=not progress) as bar,
    ):
        task = bar.add_task(f"Counting {resolved_name}", total=len(pending))
        for index, sample in enumerate(pending, start=1):
            try:
                checkout(tree, sample.commit)
                collected.extend(_rows_for_sample(sample, counter, tree, exclude_dirs))
            except (CounterError, GitError):
                if strict:
                    raise
                failed += 1
                _LOG.warning("Skipping %s: counting failed.", sample.commit[:12])
            bar.advance(task)
            if index % flush_every == 0:
                flush()

    flush()
    final = read_rows(csv_path)
    for_counter = [r for r in final if r.counter == counter.name]
    dates = sorted({r.date for r in for_counter})

    write_meta(
        meta_path,
        RepoMeta(
            name=resolved_name,
            url=target,
            mode=mode,
            branch=resolved_branch,
            tag_pattern=tag_pattern,
            exclude_dirs=list(exclude_dirs),
        ),
    )

    return CollectResult(
        added=len({r.commit for r in collected}),
        skipped=skipped,
        failed=failed,
        total=len({r.commit for r in for_counter}),
        languages=sorted({r.language for r in for_counter}),
        first_date=dates[0] if dates else None,
        last_date=dates[-1] if dates else None,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_collect.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Verify ruff is clean**

Run: `ruff check . && ruff format --check .`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add python/lsst/codemetrics/collect.py tests/test_collect.py
git commit -m "Add collection orchestration with incremental resume and progress"
```

---

### Task 8: The repo-history command

**Files:**
- Modify: `python/lsst/codemetrics/cli.py`
- Create: `tests/test_cli_repo_history.py`

**Interfaces:**
- Consumes: `collect`, `CollectResult` (Task 7); `get_counter`, `COUNTERS` (Task 3); `MODES` (Task 4); `DEFAULT_CACHE_DIR` (Task 5).
- Produces: the `repo-history` subcommand on `main`, and `summary_table(result: CollectResult, name: str) -> rich.table.Table`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_repo_history.py`:

```python
import shutil

import pytest
from click.testing import CliRunner

from lsst.codemetrics.cli import main


def test_repo_history_help_lists_the_options():
    result = CliRunner().invoke(main, ["repo-history", "--help"])
    assert result.exit_code == 0
    for option in ("--mode", "--counter", "--exclude-dir", "--since", "--force", "--strict"):
        assert option in result.output


@pytest.mark.skipif(shutil.which("cloc") is None, reason="cloc not installed")
def test_repo_history_runs_against_a_local_repo(synthetic_repo, tmp_path):
    result = CliRunner().invoke(
        main,
        [
            "repo-history",
            str(synthetic_repo),
            "--output-dir",
            str(tmp_path),
            "--branch",
            "main",
            "--counter",
            "cloc",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "synthetic.csv").exists()
    assert "synthetic" in result.output


def test_unknown_counter_is_rejected():
    result = CliRunner().invoke(main, ["repo-history", ".", "--counter", "nope"])
    assert result.exit_code != 0


def test_empty_range_exits_non_zero(synthetic_repo, tmp_path):
    result = CliRunner().invoke(
        main,
        [
            "repo-history",
            str(synthetic_repo),
            "--output-dir",
            str(tmp_path),
            "--branch",
            "main",
            "--since",
            "2030-01-01",
        ],
    )
    assert result.exit_code != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_repo_history.py -v`
Expected: FAIL, because `repo-history` is not a registered command.

- [ ] **Step 3: Write the implementation**

Replace `python/lsst/codemetrics/cli.py` with:

```python
"""Command line interface for the code metrics tools."""

import logging
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .collect import CollectResult, collect
from .counters import COUNTERS, get_counter
from .revisions import MODES
from .worktree import DEFAULT_CACHE_DIR


@click.group()
@click.version_option()
def main() -> None:
    """Measure the size of a code base over time."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(show_path=False, show_time=False)],
    )


def summary_table(result: CollectResult, name: str) -> Table:
    """Build the end-of-run summary.

    Parameters
    ----------
    result : `~lsst.codemetrics.collect.CollectResult`
        Summary of the run.
    name : `str`
        Repository name.

    Returns
    -------
    table : `rich.table.Table`
        Table ready to print.
    """
    table = Table(title=f"{name} line counts")
    table.add_column("Measure")
    table.add_column("Value", justify="right")
    table.add_row("Revisions added", str(result.added))
    table.add_row("Revisions already present", str(result.skipped))
    table.add_row("Revisions failed", str(result.failed))
    table.add_row("Revisions in file", str(result.total))
    table.add_row("Languages", ", ".join(result.languages) or "-")
    span = "-"
    if result.first_date and result.last_date:
        span = f"{result.first_date.date()} to {result.last_date.date()}"
    table.add_row("Date range", span)
    return table


@main.command("repo-history")
@click.argument("repo")
@click.option("--name", default=None, help="Output base name. Defaults to the repository basename.")
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("data/repos"),
    show_default=True,
    help="Directory to write the CSV and sidecar into.",
)
@click.option(
    "--mode",
    type=click.Choice(MODES),
    default="first-parent",
    show_default=True,
    help="How to choose the revisions to measure.",
)
@click.option("--branch", default=None, help="Branch to walk. Defaults to the remote HEAD.")
@click.option("--tag-pattern", default="w.*", show_default=True, help="Tag glob for --mode tags.")
@click.option(
    "--counter",
    "counter_name",
    type=click.Choice(sorted(COUNTERS)),
    default="cloc",
    show_default=True,
    help="Counting backend.",
)
@click.option("--exclude-dir", multiple=True, help="Directory name to skip. Repeatable.")
@click.option("--since", type=click.DateTime(), default=None, help="Ignore revisions before this date.")
@click.option("--until", type=click.DateTime(), default=None, help="Ignore revisions after this date.")
@click.option(
    "--cache-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=DEFAULT_CACHE_DIR,
    show_default=True,
    help="Directory holding cached bare clones.",
)
@click.option("--force", is_flag=True, help="Recount revisions that are already stored.")
@click.option("--strict", is_flag=True, help="Abort on the first failure instead of skipping it.")
def repo_history(
    repo: str,
    name: str | None,
    output_dir: Path,
    mode: str,
    branch: str | None,
    tag_pattern: str,
    counter_name: str,
    exclude_dir: tuple[str, ...],
    since: datetime | None,
    until: datetime | None,
    cache_dir: Path,
    force: bool,
    strict: bool,
) -> None:
    """Count lines in REPO across its history.

    REPO is a local path or a remote URL.
    """
    try:
        result = collect(
            repo,
            name=name,
            output_dir=output_dir,
            mode=mode,
            branch=branch,
            tag_pattern=tag_pattern,
            counter=get_counter(counter_name),
            exclude_dirs=exclude_dir,
            since=since,
            until=until,
            cache_dir=cache_dir,
            force=force,
            strict=strict,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    Console().print(summary_table(result, name or repo))
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Try it by hand**

Run:
```bash
check=$(mktemp -d)
code-metrics repo-history . --output-dir "$check" --branch main --mode all
head -3 "$check/code-metrics.csv"
rm -rf "$check"
```
Expected: a progress bar, a summary table, and a CSV whose first line is the
column header.

- [ ] **Step 6: Verify ruff is clean**

Run: `ruff check . && ruff format --check .`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add python/lsst/codemetrics/cli.py tests/test_cli_repo_history.py
git commit -m "Add the repo-history command"
```

---

### Task 9: Stack scan port

**Files:**
- Create: `python/lsst/codemetrics/data/legacy-tags.txt`
- Create: `python/lsst/codemetrics/stack.py`
- Create: `tests/test_stack.py`
- Delete: `bin/countlines.py`

**Interfaces:**
- Consumes: `ClocCounter`, `CounterError` (Task 2); `git_output`, `GitError` (Task 4).
- Produces:
  - `PRODUCT: str` equal to `"lsst_distrib"`.
  - `INCLUDE_LANGS: tuple[str, ...]` equal to `("Python", "C++", "C/C++ Header")`.
  - `EXCLUDED_PRODUCTS: frozenset[str]` equal to `frozenset({"metadetect"})`.
  - `LegacyEntry(BaseModel)` with `tag: str`, `output_name: str`, `tag_date: str`.
  - `ScanTarget(BaseModel)` with `tag: str`, `output_name: str`, `legacy: bool`.
  - `load_legacy_entries(path: Path | None = None) -> list[LegacyEntry]`.
  - `load_tags_file(path: Path) -> list[ScanTarget]`.
  - `lsstsw_paths() -> tuple[Path, Path, Path]` returning `(lsstsw_dir, build_dir, lsst_build_exe)`.
  - `bootstrap_distrib(lsstsw_dir: Path, build_dir: Path, lsst_build_exe: Path) -> Path`.
  - `discover_weekly_tags(distrib: Path) -> list[str]`.
  - `manifest_products(build_dir: Path) -> list[str]`.
  - `build_targets(legacy: list[LegacyEntry], weeklies: list[str], include_legacy: bool) -> list[ScanTarget]`.
  - `scan_target(target: ScanTarget, *, lsstsw_dir: Path, build_dir: Path, lsst_build_exe: Path, output_dir: Path, counter: ClocCounter) -> None`.

- [ ] **Step 1: Create the legacy tag mapping**

Create `python/lsst/codemetrics/data/legacy-tags.txt`:

```
# Pre-weekly formal releases, and the weekly-style names their results were
# recorded under in data/.
#
# Columns: release-tag  output-name  tag-date
# A '#' begins a comment and runs to end of line.
#
# output-name is authoritative. It reproduces the file names already in data/,
# and where it disagrees with the ISO week of tag-date the comment says so.
#
# lsst_distrib is a metapackage that rarely commits, so several of these tags
# point at the same commit. What separates the releases is how lsst-build
# resolves each tag name across the package repositories, which is why this
# mapping is transcribed rather than derived.

7.2.0.0  w.2013.25  2013-06-17
8.0.0.0  w.2014.20  2014-03-15  # tag date is week 11; w.2014.20 is the recorded name
b128     w.2014.30  2014-07-22  # build tag, not a formal release
9.0      w.2014.31  2014-07-31  # 9.1 falls in the same week
9.2      w.2014.32  2014-08-08
10.0     w.2014.50  2014-12-09
10.1     w.2015.20  2015-05-12
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_stack.py`:

```python
from pathlib import Path

import pytest

from lsst.codemetrics.stack import (
    EXCLUDED_PRODUCTS,
    INCLUDE_LANGS,
    build_targets,
    discover_weekly_tags,
    load_legacy_entries,
    load_tags_file,
    manifest_products,
)


def test_include_langs_match_the_historical_filter():
    assert INCLUDE_LANGS == ("Python", "C++", "C/C++ Header")


def test_legacy_entries_load_with_their_recorded_names():
    entries = load_legacy_entries()
    mapping = {e.tag: e.output_name for e in entries}
    assert mapping == {
        "7.2.0.0": "w.2013.25",
        "8.0.0.0": "w.2014.20",
        "b128": "w.2014.30",
        "9.0": "w.2014.31",
        "9.2": "w.2014.32",
        "10.0": "w.2014.50",
        "10.1": "w.2015.20",
    }


def test_legacy_entries_keep_their_recorded_order():
    assert [e.tag for e in load_legacy_entries()][0] == "7.2.0.0"


def test_legacy_comments_are_stripped():
    entries = {e.tag: e for e in load_legacy_entries()}
    assert entries["8.0.0.0"].tag_date == "2014-03-15"


def test_tags_file_accepts_one_or_two_columns(tmp_path):
    path = tmp_path / "tags.txt"
    path.write_text("# comment\nw.2020.01\n9.0  w.2014.31\n\n")
    targets = load_tags_file(path)
    assert [(t.tag, t.output_name) for t in targets] == [
        ("w.2020.01", "w.2020.01"),
        ("9.0", "w.2014.31"),
    ]


def test_build_targets_puts_legacy_first():
    legacy = load_legacy_entries()
    targets = build_targets(legacy, ["w.2015.22", "w.2015.30"], include_legacy=True)
    assert targets[0].tag == "7.2.0.0"
    assert targets[0].legacy is True
    assert targets[-1].tag == "w.2015.30"
    assert targets[-1].legacy is False


def test_build_targets_can_omit_legacy():
    legacy = load_legacy_entries()
    targets = build_targets(legacy, ["w.2015.22"], include_legacy=False)
    assert [t.tag for t in targets] == ["w.2015.22"]


def test_discover_weekly_tags_orders_chronologically(synthetic_repo):
    assert discover_weekly_tags(synthetic_repo) == ["w.2020.9", "w.2020.10"]


def test_manifest_products_filters_third_party(tmp_path):
    (tmp_path / "manifest.txt").write_text(
        "# comment\nBUILD=b1\nafw g1\nboost g2\nmetadetect g3\nsconsUtils g4\n"
    )
    (tmp_path / "boost" / "ups").mkdir(parents=True)
    (tmp_path / "boost" / "ups" / "eupspkg.cfg.sh").write_text("")
    (tmp_path / "afw").mkdir()
    (tmp_path / "sconsUtils").mkdir()
    (tmp_path / "metadetect").mkdir()
    assert manifest_products(tmp_path) == ["afw", "sconsUtils"]


def test_manifest_products_filters_upstream_dirs(tmp_path):
    (tmp_path / "manifest.txt").write_text("afw g1\nfftw g2\n")
    (tmp_path / "afw").mkdir()
    (tmp_path / "fftw" / "upstream").mkdir(parents=True)
    assert manifest_products(tmp_path) == ["afw"]


def test_metadetect_is_excluded():
    assert "metadetect" in EXCLUDED_PRODUCTS
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_stack.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lsst.codemetrics.stack'`

- [ ] **Step 4: Write the implementation**

Create `python/lsst/codemetrics/stack.py`:

```python
"""The stack-wide scan of lsst_distrib at each release tag.

This is the successor to ``bin/countlines.py``.  It is deliberately fixed
to cloc, and writes cloc's own report format, so that files produced today
match those already in ``data/``.
"""

import logging
import os
import subprocess
from importlib import resources
from pathlib import Path

from pydantic import BaseModel

from .counters import ClocCounter
from .revisions import git_output

_LOG = logging.getLogger(__name__)

PRODUCT = "lsst_distrib"
"""EUPS product whose dependency tree is measured (`str`)."""

INCLUDE_LANGS: tuple[str, ...] = ("Python", "C++", "C/C++ Header")
"""Languages counted by the stack scan (`tuple` [ `str` ])."""

EXCLUDED_PRODUCTS = frozenset({"metadetect"})
"""Third-party products that are not LSST code (`frozenset` [ `str` ])."""


class LegacyEntry(BaseModel):
    """A pre-weekly release and the name its results were recorded under."""

    tag: str
    output_name: str
    tag_date: str


class ScanTarget(BaseModel):
    """A tag to scan and the file its report is written to."""

    tag: str
    output_name: str
    legacy: bool


def _strip_comment(line: str) -> str:
    """Remove a trailing comment from a line.

    Parameters
    ----------
    line : `str`
        Raw line.

    Returns
    -------
    text : `str`
        Line with any comment and surrounding whitespace removed.
    """
    return line.split("#", 1)[0].strip()


def load_legacy_entries(path: Path | None = None) -> list[LegacyEntry]:
    """Read the pre-weekly release mapping.

    Parameters
    ----------
    path : `~pathlib.Path`, optional
        File to read.  Defaults to the copy shipped with the package.

    Returns
    -------
    entries : `list` [ `LegacyEntry` ]
        Entries in file order, which is chronological.
    """
    if path is None:
        text = resources.files("lsst.codemetrics").joinpath("data/legacy-tags.txt").read_text()
    else:
        text = path.read_text()
    entries = []
    for line in text.splitlines():
        stripped = _strip_comment(line)
        if not stripped:
            continue
        tag, output_name, tag_date = stripped.split()
        entries.append(LegacyEntry(tag=tag, output_name=output_name, tag_date=tag_date))
    return entries


def load_tags_file(path: Path) -> list[ScanTarget]:
    """Read an explicit list of tags to scan.

    Each line is a tag, optionally followed by the name to record its
    report under.

    Parameters
    ----------
    path : `~pathlib.Path`
        File to read.

    Returns
    -------
    targets : `list` [ `ScanTarget` ]
        Targets in file order.
    """
    targets = []
    for line in path.read_text().splitlines():
        stripped = _strip_comment(line)
        if not stripped:
            continue
        parts = stripped.split()
        tag = parts[0]
        output_name = parts[1] if len(parts) > 1 else tag
        targets.append(ScanTarget(tag=tag, output_name=output_name, legacy=False))
    return targets


def lsstsw_paths() -> tuple[Path, Path, Path]:
    """Locate the deployed lsstsw tree.

    Returns
    -------
    lsstsw_dir : `~pathlib.Path`
        Root of the lsstsw checkout.
    build_dir : `~pathlib.Path`
        Directory that lsst-build clones sources into.
    lsst_build_exe : `~pathlib.Path`
        The lsst-build program.

    Raises
    ------
    RuntimeError
        Raised if the environment has not been set up.
    """
    if "LSST_BUILD_DIR" not in os.environ:
        raise RuntimeError(
            "lsst_build has not been set up. Source lsstsw/bin/envconfig first."
        )
    lsst_build_dir = Path(os.environ["LSST_BUILD_DIR"])
    lsstsw_dir = lsst_build_dir.parent
    return lsstsw_dir, lsstsw_dir / "build", lsst_build_dir / "bin" / "lsst-build"


def _prepare(
    lsstsw_dir: Path, build_dir: Path, lsst_build_exe: Path, ref: str | None
) -> None:
    """Run ``lsst-build prepare``.

    Parameters
    ----------
    lsstsw_dir : `~pathlib.Path`
        Root of the lsstsw checkout.
    build_dir : `~pathlib.Path`
        Directory that lsst-build clones sources into.
    lsst_build_exe : `~pathlib.Path`
        The lsst-build program.
    ref : `str`, optional
        Git ref to check out.  The default ref is used when this is `None`.
    """
    args = [
        str(lsst_build_exe),
        "prepare",
        "--repos",
        str(lsstsw_dir / "etc" / "repos.yaml"),
        "--exclusion-map",
        str(lsstsw_dir / "etc" / "exclusions.txt"),
    ]
    if ref is not None:
        args.extend(["--ref", ref])
    args.extend([str(build_dir), PRODUCT])
    subprocess.run(args, check=True)


def bootstrap_distrib(lsstsw_dir: Path, build_dir: Path, lsst_build_exe: Path) -> Path:
    """Ensure an lsst_distrib checkout exists to read tags from.

    A freshly deployed lsstsw has an empty build directory, so there is
    nothing to read tags from until lsst-build has run at least once.
    Preparing the default ref breaks that cycle without this project
    needing to know how a product name maps to a clone URL, which is
    lsst_build's responsibility.

    Parameters
    ----------
    lsstsw_dir : `~pathlib.Path`
        Root of the lsstsw checkout.
    build_dir : `~pathlib.Path`
        Directory that lsst-build clones sources into.
    lsst_build_exe : `~pathlib.Path`
        The lsst-build program.

    Returns
    -------
    distrib : `~pathlib.Path`
        The lsst_distrib checkout.
    """
    distrib = build_dir / PRODUCT
    if distrib.exists():
        git_output(distrib, "fetch", "--prune", "--tags", "origin")
    else:
        _LOG.info("Preparing %s at its default ref to discover tags.", PRODUCT)
        _prepare(lsstsw_dir, build_dir, lsst_build_exe, None)
    return distrib


def discover_weekly_tags(distrib: Path, pattern: str = "w.*") -> list[str]:
    """List weekly tags in chronological order.

    Sorting by creation date rather than by name is what makes the
    inconsistent zero padding of the older tags harmless.

    Parameters
    ----------
    distrib : `~pathlib.Path`
        The lsst_distrib checkout.
    pattern : `str`, optional
        Glob matched against tag names.

    Returns
    -------
    tags : `list` [ `str` ]
        Tag names, oldest first.
    """
    raw = git_output(distrib, "tag", "--list", pattern, "--sort=creatordate")
    return [line.strip() for line in raw.splitlines() if line.strip()]


def build_targets(
    legacy: list[LegacyEntry], weeklies: list[str], include_legacy: bool
) -> list[ScanTarget]:
    """Assemble the full ordered list of tags to scan.

    Parameters
    ----------
    legacy : `list` [ `LegacyEntry` ]
        Pre-weekly releases.
    weeklies : `list` [ `str` ]
        Weekly tags, oldest first.
    include_legacy : `bool`
        Whether to place the pre-weekly releases at the front.

    Returns
    -------
    targets : `list` [ `ScanTarget` ]
        Targets in chronological order.
    """
    targets = []
    if include_legacy:
        targets.extend(
            ScanTarget(tag=e.tag, output_name=e.output_name, legacy=True) for e in legacy
        )
    targets.extend(ScanTarget(tag=tag, output_name=tag, legacy=False) for tag in weeklies)
    return targets


def manifest_products(build_dir: Path) -> list[str]:
    """List the products that contribute to the line count.

    Products carrying an ``ups/eupspkg.cfg.sh`` file or an ``upstream``
    directory are third-party code built from a release tarball, and named
    third-party products are excluded outright.

    Parameters
    ----------
    build_dir : `~pathlib.Path`
        Directory containing ``manifest.txt`` and the checked out sources.

    Returns
    -------
    products : `list` [ `str` ]
        Product names to count.
    """
    products = []
    with (build_dir / "manifest.txt").open() as fd:
        for line in fd:
            if line.startswith(("#", "BUILD")):
                continue
            product = line.split(" ")[0].strip()
            if not product:
                continue
            source = build_dir / product
            if (source / "ups" / "eupspkg.cfg.sh").exists() or (source / "upstream").exists():
                continue
            if product in EXCLUDED_PRODUCTS:
                continue
            products.append(product)
    return products


def scan_target(
    target: ScanTarget,
    *,
    lsstsw_dir: Path,
    build_dir: Path,
    lsst_build_exe: Path,
    output_dir: Path,
    counter: ClocCounter,
) -> None:
    """Check out one tag and write its report.

    Parameters
    ----------
    target : `ScanTarget`
        Tag to scan and the name to record it under.
    lsstsw_dir : `~pathlib.Path`
        Root of the lsstsw checkout.
    build_dir : `~pathlib.Path`
        Directory that lsst-build clones sources into.
    lsst_build_exe : `~pathlib.Path`
        The lsst-build program.
    output_dir : `~pathlib.Path`
        Directory to write reports into.
    counter : `~lsst.codemetrics.counters.ClocCounter`
        Backend to measure with.

    Raises
    ------
    RuntimeError
        Raised if the manifest yields no products to count.
    """
    _prepare(lsstsw_dir, build_dir, lsst_build_exe, target.tag)
    products = manifest_products(build_dir)
    if not products:
        raise RuntimeError(f"No products found with ref {target.tag}.")
    _LOG.info("Counting %d products at %s.", len(products), target.tag)
    output_dir.mkdir(parents=True, exist_ok=True)
    counter.write_report(
        [build_dir / product for product in products],
        (output_dir / f"{target.output_name}.yaml").resolve(),
        include_langs=INCLUDE_LANGS,
    )
```

- [ ] **Step 5: Remove the superseded script**

```bash
git rm bin/countlines.py
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_stack.py -v`
Expected: PASS (11 tests).

- [ ] **Step 7: Verify ruff is clean**

Run: `ruff check . && ruff format --check .`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add python/lsst/codemetrics/stack.py python/lsst/codemetrics/data tests/test_stack.py
git commit -m "Port the stack scan into the package with derived tag discovery"
```

---

### Task 10: The stack-scan command

**Files:**
- Modify: `python/lsst/codemetrics/cli.py`
- Modify: `python/lsst/codemetrics/stack.py`
- Create: `tests/test_cli_stack_scan.py`

**Interfaces:**
- Consumes: everything produced by Task 9.
- Produces:
  - `should_scan(target: ScanTarget, output_dir: Path, force: bool, force_legacy: bool) -> bool` in `stack.py`.
  - the `stack-scan` subcommand on `main`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_stack_scan.py`:

```python
from pathlib import Path

from click.testing import CliRunner

from lsst.codemetrics.cli import main
from lsst.codemetrics.stack import ScanTarget, should_scan


def weekly(name="w.2020.01"):
    return ScanTarget(tag=name, output_name=name, legacy=False)


def legacy(name="9.0", out="w.2014.31"):
    return ScanTarget(tag=name, output_name=out, legacy=True)


def test_missing_output_is_always_scanned(tmp_path):
    assert should_scan(weekly(), tmp_path, force=False, force_legacy=False)


def test_existing_output_is_skipped(tmp_path):
    (tmp_path / "w.2020.01.yaml").write_text("")
    assert not should_scan(weekly(), tmp_path, force=False, force_legacy=False)


def test_force_rescans_a_weekly(tmp_path):
    (tmp_path / "w.2020.01.yaml").write_text("")
    assert should_scan(weekly(), tmp_path, force=True, force_legacy=False)


def test_force_alone_does_not_touch_legacy(tmp_path):
    (tmp_path / "w.2014.31.yaml").write_text("")
    assert not should_scan(legacy(), tmp_path, force=True, force_legacy=False)


def test_force_legacy_rescans_legacy(tmp_path):
    (tmp_path / "w.2014.31.yaml").write_text("")
    assert should_scan(legacy(), tmp_path, force=True, force_legacy=True)


def test_stack_scan_help_lists_the_options():
    result = CliRunner().invoke(main, ["stack-scan", "--help"])
    assert result.exit_code == 0
    for option in ("--tags-file", "--legacy", "--force-legacy", "--strict"):
        assert option in result.output


def test_stack_scan_without_environment_fails_clearly(monkeypatch):
    monkeypatch.delenv("LSST_BUILD_DIR", raising=False)
    result = CliRunner().invoke(main, ["stack-scan"])
    assert result.exit_code != 0
    assert "lsst_build" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_stack_scan.py -v`
Expected: FAIL with `ImportError: cannot import name 'should_scan'`

- [ ] **Step 3: Add the skip rule to `stack.py`**

Append to `python/lsst/codemetrics/stack.py`:

```python
def should_scan(
    target: ScanTarget, output_dir: Path, force: bool, force_legacy: bool
) -> bool:
    """Decide whether a target needs scanning.

    Legacy results are protected from ``--force`` alone.  Their mapping
    from release tag to recorded name cannot be re-derived, so overwriting
    them has to be asked for explicitly.

    Parameters
    ----------
    target : `ScanTarget`
        Target under consideration.
    output_dir : `~pathlib.Path`
        Directory reports are written to.
    force : `bool`
        Rescan targets whose report already exists.
    force_legacy : `bool`
        Extend ``force`` to the pre-weekly releases.

    Returns
    -------
    scan : `bool`
        `True` if the target should be scanned.
    """
    if not (output_dir / f"{target.output_name}.yaml").exists():
        return True
    if target.legacy:
        return force and force_legacy
    return force
```

- [ ] **Step 4: Add the command to `cli.py`**

Add these imports to the existing import block in
`python/lsst/codemetrics/cli.py`:

```python
from .counters import COUNTERS, ClocCounter, get_counter
from .stack import (
    bootstrap_distrib,
    build_targets,
    discover_weekly_tags,
    load_legacy_entries,
    load_tags_file,
    lsstsw_paths,
    scan_target,
    should_scan,
)
```

Note the `ClocCounter` addition to the existing `.counters` import line.

Append the command:

```python
@main.command("stack-scan")
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("data"),
    show_default=True,
    help="Directory to write per-tag reports into.",
)
@click.option(
    "--tags-file",
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
    default=None,
    help="Replace the derived tag list, legacy entries included.",
)
@click.option(
    "--legacy/--no-legacy",
    default=True,
    show_default=True,
    help="Include the pre-weekly release tags.",
)
@click.option("--force", is_flag=True, help="Rescan tags whose report already exists.")
@click.option(
    "--force-legacy",
    is_flag=True,
    help="Extend --force to the pre-weekly releases, overwriting curated names.",
)
@click.option("--strict", is_flag=True, help="Abort on the first failure instead of skipping it.")
def stack_scan(
    output_dir: Path,
    tags_file: Path | None,
    legacy: bool,
    force: bool,
    force_legacy: bool,
    strict: bool,
) -> None:
    """Count lines across lsst_distrib at each release tag.

    Requires an lsstsw environment with LSST_BUILD_DIR set.
    """
    try:
        lsstsw_dir, build_dir, lsst_build_exe = lsstsw_paths()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    if tags_file is not None:
        targets = load_tags_file(tags_file)
    else:
        distrib = bootstrap_distrib(lsstsw_dir, build_dir, lsst_build_exe)
        targets = build_targets(
            load_legacy_entries(), discover_weekly_tags(distrib), include_legacy=legacy
        )

    pending = [t for t in targets if should_scan(t, output_dir, force, force_legacy)]
    console = Console()
    console.print(f"{len(pending)} of {len(targets)} tags need scanning.")

    counter = ClocCounter()
    scanned = 0
    failed = 0
    for target in pending:
        try:
            scan_target(
                target,
                lsstsw_dir=lsstsw_dir,
                build_dir=build_dir,
                lsst_build_exe=lsst_build_exe,
                output_dir=output_dir,
                counter=counter,
            )
            scanned += 1
        except Exception:
            if strict:
                raise
            failed += 1
            logging.getLogger(__name__).warning("Skipping %s: scan failed.", target.tag)

    table = Table(title="Stack scan")
    table.add_column("Measure")
    table.add_column("Value", justify="right")
    table.add_row("Tags scanned", str(scanned))
    table.add_row("Tags skipped", str(len(targets) - len(pending)))
    table.add_row("Tags failed", str(failed))
    console.print(table)
```

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: PASS.

- [ ] **Step 6: Verify ruff is clean**

Run: `ruff check . && ruff format --check .`
Expected: no errors.

- [ ] **Step 7: Confirm the existing data is untouched**

Run: `git status --porcelain data/`
Expected: no output. The scan has not run, so no `data/w.*.yaml` file changed.

- [ ] **Step 8: Commit**

```bash
git add python/lsst/codemetrics tests/test_cli_stack_scan.py
git commit -m "Add the stack-scan command with legacy results protected from --force"
```

---

### Task 11: Plotting helpers

**Files:**
- Create: `python/lsst/codemetrics/plotting.py`
- Create: `tests/test_plotting.py`

**Interfaces:**
- Consumes: `read_rows` (Task 6).
- Produces:
  - `load_repo(name: str, output_dir: Path = Path("data/repos")) -> pandas.DataFrame` with columns `commit`, `date`, `label`, `counter`, `counter_version`, `language`, `n_files`, `blank`, `comment`, `code`, `lines`.
  - `apply_aliases(frame, alias_map: Mapping[str, str]) -> pandas.DataFrame`.
  - `select(frame, languages: Sequence[str] | None = None, counter: str | None = None) -> pandas.DataFrame`.
  - `pivot(frame, value: str = "code") -> pandas.DataFrame`.
  - `load_stack(data_dir: Path = Path("data")) -> tuple[numpy.ndarray, dict[str, numpy.ndarray]]`.
  - `CLOC_CPP_ALIASES: dict[str, str]` mapping `"C/C++ Header"` to `"C++"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_plotting.py`:

```python
from datetime import UTC, datetime

import pytest

pytest.importorskip("pandas")

from lsst.codemetrics.plotting import (  # noqa: E402
    CLOC_CPP_ALIASES,
    apply_aliases,
    load_repo,
    load_stack,
    pivot,
    select,
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
    folded = apply_aliases(frame, CLOC_CPP_ALIASES)
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


def test_load_stack_reads_the_existing_yaml():
    # Locate data/ relative to this file so the test does not depend on
    # the working directory pytest was started from.
    data_dir = Path(__file__).parent.parent / "data"
    dates, datasets = load_stack(data_dir)
    assert len(dates) > 500
    assert "python_code" in datasets
    assert len(datasets["python_code"]) == len(dates)
    assert list(dates) == sorted(dates)
```

Add `from pathlib import Path` to the imports at the top of the file.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plotting.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lsst.codemetrics.plotting'`

- [ ] **Step 3: Install the plotting extra**

Run: `python -m pip install -e '.[plot,test]'`

- [ ] **Step 4: Write the implementation**

Create `python/lsst/codemetrics/plotting.py`:

```python
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
    grouped = (
        renamed.groupby(["commit", "date", "counter", "language"], as_index=False)
        .agg(
            label=("label", "first"),
            counter_version=("counter_version", "first"),
            n_files=("n_files", "sum"),
            blank=("blank", "sum"),
            comment=("comment", "sum"),
            code=("code", "sum"),
            lines=("lines", "sum"),
        )
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
            f"Frame holds results from more than one counter ({found}). "
            "Pass counter= to choose one.",
            UserWarning,
            stacklevel=2,
        )
    if languages is not None:
        result = result[result["language"].isin(list(languages))]
    return result


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
    return frame.pivot_table(
        index="date", columns="language", values=value, aggfunc="sum"
    ).sort_index()


def load_stack(data_dir: Path = Path("data")) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Load the stack-wide weekly counts.

    Parameters
    ----------
    data_dir : `~pathlib.Path`, optional
        Directory holding the per-tag YAML reports.

    Returns
    -------
    dates : `numpy.ndarray`
        Year plus week fraction, ascending.
    datasets : `dict` [ `str`, `numpy.ndarray` ]
        Series keyed as ``<language>_<measure>``, where language is one of
        ``python``, ``cpp``, or ``all``, and measure is one of ``code``,
        ``comment``, or ``lines``.
    """
    results: dict[float, dict[str, dict[str, int]]] = {}
    for path in data_dir.glob("w.*.yaml"):
        year, week = path.name.split(".")[1:3]
        # Approximating the date from the week number is close enough for
        # a plot spanning more than a decade.
        year_fraction = float(year) + (float(week) / 52.0)
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_plotting.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Verify ruff is clean**

Run: `ruff check . && ruff format --check .`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add python/lsst/codemetrics/plotting.py tests/test_plotting.py
git commit -m "Add plotting helpers for repository and stack data"
```

---

### Task 12: Notebooks and documentation

**Files:**
- Create: `plot-repo-lines.ipynb`
- Modify: `plot-line-counts.ipynb` (cells 6 and 7)
- Modify: `README.md`

**Interfaces:**
- Consumes: `load_repo`, `select`, `pivot`, `apply_aliases`, `CLOC_CPP_ALIASES`, `load_stack` (Task 11).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Replace the parsing cells in the existing notebook**

The notebook's current cells are:

| Cell | Contents | Change |
|---|---|---|
| 2 | `import glob`, `import os`, `import yaml` | replaced by the `load_stack` import |
| 3 | `import matplotlib.pyplot as plt`, `import numpy as np` | numpy is no longer used, so it goes |
| 4 | `yfiles = glob.glob("data/w.*.yaml")` | cleared |
| 6 | builds `results` by parsing YAML | replaced by the `load_stack` call |
| 7 | builds `dates` and `datasets` | cleared |

Cells 10 and 11 hold the plot and the PDF save, and are untouched, so the
figure is unchanged.

Apply the edit with:

```python
import json
from pathlib import Path

path = Path("plot-line-counts.ipynb")
nb = json.loads(path.read_text())
nb["cells"][2]["source"] = ["from lsst.codemetrics.plotting import load_stack"]
nb["cells"][3]["source"] = ["import matplotlib.pyplot as plt"]
nb["cells"][4]["source"] = []
nb["cells"][6]["source"] = ["dates, datasets = load_stack()"]
nb["cells"][7]["source"] = []
for index in (2, 3, 4, 6, 7):
    nb["cells"][index]["outputs"] = []
    nb["cells"][index]["execution_count"] = None
path.write_text(json.dumps(nb, indent=1) + "\n")
```

Confirm the cells were the expected ones before running it:

```bash
python -c "
import json
nb = json.load(open('plot-line-counts.ipynb'))
for i in (2, 3, 4, 6, 7):
    print(i, repr(''.join(nb['cells'][i]['source'])[:60]))
"
```

- [ ] **Step 2: Verify the notebook still produces the same plot**

Run:
```bash
python -c "
from lsst.codemetrics.plotting import load_stack
dates, datasets = load_stack()
print(len(dates), datasets['python_code'][-1], datasets['cpp_code'][-1])
"
```
Expected: a count above 500 and two positive integers.
Compare `datasets['python_code'][-1]` against the `code` value under
`Python` in `data/w.2026.31.yaml`, which is 593030. They must match.

- [ ] **Step 3: Create the new notebook**

Create `plot-repo-lines.ipynb` with these cells:

Cell 0, markdown:

```
Plot line counts for a single repository over time. Collect the data first with:

    code-metrics repo-history https://github.com/lsst/daf_butler
```

Cell 1, code:

```python
%matplotlib widget
```

Cell 2, code:

```python
import matplotlib.pyplot as plt

from lsst.codemetrics.plotting import (
    CLOC_CPP_ALIASES,
    apply_aliases,
    load_repo,
    pivot,
    select,
)
```

Cell 3, code:

```python
name = "daf_butler"
frame = load_repo(name)
frame.head()
```

Cell 4, code:

```python
# Fold cloc's header language into C++ so the two plot as one series.
folded = apply_aliases(select(frame, counter="cloc"), CLOC_CPP_ALIASES)
code = pivot(folded, value="code")
comment = pivot(folded, value="comment")
code.tail()
```

Cell 5, code:

```python
fig, ax = plt.subplots()
for language in code.columns:
    ax.plot(code.index, code[language], label=f"{language} code")
    ax.plot(comment.index, comment[language], label=f"{language} comment")
ax.set_title(f"Lines of {name} code and comments")
ax.set_ylim(bottom=0)
ax.legend()
```

Cell 6, code:

```python
# Uncomment to save a PDF for publications.
# ax.set_title("")
# fig.savefig(f"{name}-lines.pdf", bbox_inches="tight")
```

Build it with:

```python
import json
from pathlib import Path

sources = [
    ("markdown", "Plot line counts for a single repository over time. Collect the data first with:\n\n    code-metrics repo-history https://github.com/lsst/daf_butler"),
    ("code", "%matplotlib widget"),
    ("code", "import matplotlib.pyplot as plt\n\nfrom lsst.codemetrics.plotting import (\n    CLOC_CPP_ALIASES,\n    apply_aliases,\n    load_repo,\n    pivot,\n    select,\n)"),
    ("code", 'name = "daf_butler"\nframe = load_repo(name)\nframe.head()'),
    ("code", '# Fold cloc\'s header language into C++ so the two plot as one series.\nfolded = apply_aliases(select(frame, counter="cloc"), CLOC_CPP_ALIASES)\ncode = pivot(folded, value="code")\ncomment = pivot(folded, value="comment")\ncode.tail()'),
    ("code", 'fig, ax = plt.subplots()\nfor language in code.columns:\n    ax.plot(code.index, code[language], label=f"{language} code")\n    ax.plot(comment.index, comment[language], label=f"{language} comment")\nax.set_title(f"Lines of {name} code and comments")\nax.set_ylim(bottom=0)\nax.legend()'),
    ("code", '# Uncomment to save a PDF for publications.\n# ax.set_title("")\n# fig.savefig(f"{name}-lines.pdf", bbox_inches="tight")'),
]
cells = []
for kind, text in sources:
    cell = {"cell_type": kind, "metadata": {}, "source": text.splitlines(keepends=True)}
    if kind == "code":
        cell["outputs"] = []
        cell["execution_count"] = None
    cells.append(cell)
nb = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.12.0"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}
Path("plot-repo-lines.ipynb").write_text(json.dumps(nb, indent=1) + "\n")
```

- [ ] **Step 4: Rewrite the README**

Replace `README.md` with:

```markdown
# code-metrics

Data and tools for calculating metrics about the science pipelines code base.

## Installation

    pip install -e '.[plot]'

This provides the `code-metrics` command.

## Counting a single repository

    code-metrics repo-history https://github.com/lsst/daf_butler

Samples the first-parent chain of the default branch, counts each revision
with `cloc`, and writes `data/repos/daf_butler.csv`.
Re-running counts only revisions that are not already recorded.

Use `--mode tags` to sample weekly tags instead, `--mode all` for every
commit, and `--counter scc` or `--counter tokei` for a different backend.
Counts from different backends coexist in one file, so they can be
compared over identical revisions.

Results are stored one row per revision and language, so a language
appearing for the first time adds rows rather than columns.

Plot them with `plot-repo-lines.ipynb`.

## Counting the whole stack

    code-metrics stack-scan

Checks out each `lsst_distrib` release tag with `lsst-build` and runs
`cloc` over the result, writing one YAML report per tag into `data/`.
Requires an lsstsw environment with `LSST_BUILD_DIR` set, and `cloc` from
https://github.com/AlDanial/cloc.

Tags whose report already exists are skipped, so a routine update only
scans what is new.

Plot the results with `plot-line-counts.ipynb`.

### Pre-weekly releases

Weekly tags begin at `w.2015.22`.
Earlier points on the curve come from formal releases, recorded under
weekly-style names matching each release date.
`python/lsst/codemetrics/data/legacy-tags.txt` holds that mapping, which
was transcribed rather than derived and cannot be reconstructed from tag
metadata.
Those results are protected from `--force`; overwriting them requires
`--force-legacy` as well.
```

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest tests/ -v`
Expected: PASS.

- [ ] **Step 6: Verify ruff and pre-commit are clean**

Run: `ruff check . && ruff format --check . && pre-commit run --all-files`
Expected: no errors.

- [ ] **Step 7: Confirm the existing data is still untouched**

Run: `git status --porcelain data/`
Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add plot-repo-lines.ipynb plot-line-counts.ipynb README.md
git commit -m "Add repository plotting notebook and update documentation"
```

---

## Verification

After all tasks, confirm end to end:

```bash
python -m pip install -e '.[plot,test]'
python -m pytest tests/ -v
ruff check . && ruff format --check .
code-metrics --help
code-metrics repo-history --help
code-metrics stack-scan --help
git status --porcelain data/
```

The last command must print nothing: no file under `data/` may have
changed.

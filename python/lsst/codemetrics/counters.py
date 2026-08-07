"""Line counting backends.

Each backend shells out to an external tool and converts its structured
output into a mapping of language name to `LanguageCount`.  Language names
are whatever the tool itself reports; they are never translated between
backends.
"""

import json
import subprocess
import tempfile
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

    def _run(self, args: Sequence[str], cwd: Path | None = None) -> str:
        """Run the external tool and return its standard output.

        Parameters
        ----------
        args : `~collections.abc.Sequence` [ `str` ]
            Arguments to pass to the tool.
        cwd : `~pathlib.Path`, optional
            Directory to run the tool from.  Used by backends that read a
            configuration file from the working directory.

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
                cwd=cwd,
            )
        except FileNotFoundError as exc:
            raise CounterError(f"Counting tool {self.executable!r} was not found on PATH.") from exc
        except subprocess.CalledProcessError as exc:
            raise CounterError(f"Counting tool {self.executable!r} failed: {exc.stderr.strip()}") from exc
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
    def count(self, path: Path, exclude_dirs: Sequence[str] = ()) -> dict[str, LanguageCount]:
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
        """Convert cloc's YAML report into per-language counts.

        See `LineCounter.parse` for the parameters and return value.
        """
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

    def count(self, path: Path, exclude_dirs: Sequence[str] = ()) -> dict[str, LanguageCount]:
        """Count lines beneath a directory using cloc.

        See `LineCounter.count` for the parameters and return value.
        """
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
        header and SUM blocks, so that files written today have the same
        shape as those already in ``data/``.

        The per-language and SUM blocks reproduce exactly, which is what
        every consumer reads.  The header does not: ``elapsed_seconds``,
        ``files_per_second``, and ``lines_per_second`` are timings that
        differ on every run, and ``report_file`` records the path this
        method was given.

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


class SccCounter(LineCounter):
    """Line counter backed by scc."""

    name = "scc"

    def _parse_version(self, raw: str) -> str:
        # scc --version prints "scc version 3.7.0".
        return raw.strip().split()[-1]

    def parse(self, raw: str) -> dict[str, LanguageCount]:
        """Convert scc's JSON report into per-language counts.

        See `LineCounter.parse` for the parameters and return value.
        """
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

    def count(self, path: Path, exclude_dirs: Sequence[str] = ()) -> dict[str, LanguageCount]:
        """Count lines beneath a directory using scc.

        See `LineCounter.count` for the parameters and return value.
        """
        args = ["--format", "json"]
        if exclude_dirs:
            args.extend(["--exclude-dir", ",".join(exclude_dirs)])
        args.append(str(path))
        return self.parse(self._run(args))


class TokeiCounter(LineCounter):
    """Line counter backed by tokei.

    Parameters
    ----------
    executable : `str`, optional
        Name or path of the tokei program.
    docstrings_as_comments : `bool`, optional
        Count a Python docstring as a comment rather than as code.

    Notes
    -----
    Left to itself, tokei counts a Python docstring as code, while cloc
    counts it as a comment.  On docstring-heavy code that moves a large
    share of the lines between columns: counted natively, tokei reports
    roughly twice the code of cloc for this project's own source.

    Enabling tokei's own ``treat_doc_strings_as_comments`` setting brings
    it in line with cloc, which is what the stack-wide history in
    ``data/`` was counted with.  Measured over ``daf_butler``'s Python,
    cloc reports 54373 lines of code and tokei 55063, a difference of
    about one percent.  It is on by default here for that reason.  Pass
    `False` to get tokei's native behavior.

    This does not make every backend agree.  scc classifies much of the
    same material as code regardless, reporting 67312 over that same
    source, so its Python figures are not comparable with either.

    tokei reads that setting from a ``tokei.toml`` in its working
    directory, and does not search parent directories, so the file is
    written to a scratch directory that tokei is run from.  Running from
    elsewhere also means a ``tokei.toml`` committed to the repository
    being measured cannot change how its own history is counted.
    """

    name = "tokei"

    def __init__(self, executable: str | None = None, docstrings_as_comments: bool = True) -> None:
        super().__init__(executable=executable)
        self.docstrings_as_comments = docstrings_as_comments

    def _parse_version(self, raw: str) -> str:
        # tokei --version prints "tokei 14.0.0 compiled with ...".
        return raw.strip().split()[1]

    def parse(self, raw: str) -> dict[str, LanguageCount]:
        """Convert tokei's JSON report into per-language counts.

        tokei reports no file count, so it is taken from the length of
        the per-file report list.

        See `LineCounter.parse` for the parameters and return value.
        """
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

    def count(self, path: Path, exclude_dirs: Sequence[str] = ()) -> dict[str, LanguageCount]:
        """Count lines beneath a directory using tokei.

        See `LineCounter.count` for the parameters and return value.
        """
        args = ["--output", "json"]
        for name in exclude_dirs:
            args.extend(["--exclude", name])
        # Absolute, because tokei is run from a different directory.
        args.append(str(Path(path).resolve()))

        if not self.docstrings_as_comments:
            return self.parse(self._run(args))

        with tempfile.TemporaryDirectory(prefix="code-metrics-tokei-") as scratch:
            config = Path(scratch) / "tokei.toml"
            config.write_text("treat_doc_strings_as_comments = true\n")
            return self.parse(self._run(args, cwd=Path(scratch)))


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

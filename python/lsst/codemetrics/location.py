"""Where collected counts live.

The command line writes counts and the plotting helpers read them back.
If those two resolved a location by different rules, a run could collect
into one directory and plot from another without saying so, which is the
kind of mismatch that looks like missing data rather than a
misconfiguration.  Both therefore go through `data_root` here.

Per-repository counts are general: any git repository can be measured,
so the collection naturally belongs in its own repository rather than
alongside the LSST specific stack data.  A data root is the top of that
repository, and per-repository counts sit in its ``repos`` directory,
leaving room beside them for other kinds of data later.
"""

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel

ENV_VAR = "CODE_METRICS_DATA_DIR"
"""Environment variable naming the data root (`str`)."""

REPOS_SUBDIR = "repos"
"""Directory under the data root holding per-repository counts (`str`)."""

CONFIG_KEY = "data_dir"
"""Key read from the configuration file (`str`)."""


class DataRoot(BaseModel):
    """A resolved data root, and the rule that chose it."""

    path: Path
    source: str

    def describe(self) -> str:
        """Render the root and its origin for a message.

        Returns
        -------
        description : `str`
            Text naming both the directory and why it was chosen.
        """
        return f"{self.path} (from {self.source})"


def config_file() -> Path:
    """Locate the user's configuration file.

    Returns
    -------
    path : `~pathlib.Path`
        Configuration file, which need not exist.
    """
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "code-metrics" / "config.toml"


def _from_config() -> Path | None:
    """Read the data root from the configuration file.

    Returns
    -------
    path : `~pathlib.Path` or `None`
        Configured root, or `None` if the file or key is absent.

    Raises
    ------
    ValueError
        Raised if the file exists but cannot be parsed.  A malformed
        configuration is reported rather than skipped, so that a typo
        does not silently send counts somewhere unexpected.
    """
    path = config_file()
    if not path.exists():
        return None
    try:
        parsed = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Could not parse {path}: {exc}") from exc
    configured = parsed.get(CONFIG_KEY)
    return Path(configured) if configured else None


def data_root(explicit: Path | str | None = None) -> DataRoot:
    """Resolve the directory holding collected counts.

    Consulted in order: the argument, the ``CODE_METRICS_DATA_DIR``
    environment variable, the configuration file, and finally the
    current directory, so that working inside the data repository needs
    no configuration at all.

    Parameters
    ----------
    explicit : `~pathlib.Path` or `str`, optional
        Root given directly by a caller or on the command line.

    Returns
    -------
    root : `DataRoot`
        Resolved root and the rule that chose it.
    """
    if explicit:
        return DataRoot(path=Path(explicit).expanduser(), source="argument")

    from_env = os.environ.get(ENV_VAR)
    if from_env:
        return DataRoot(path=Path(from_env).expanduser(), source=ENV_VAR)

    configured = _from_config()
    if configured:
        return DataRoot(path=configured.expanduser(), source=f"config file {config_file()}")

    return DataRoot(path=Path.cwd(), source="current directory")


def repo_dir(explicit: Path | str | None = None) -> DataRoot:
    """Resolve the directory holding per-repository counts.

    Parameters
    ----------
    explicit : `~pathlib.Path` or `str`, optional
        Data root given directly by a caller or on the command line.

    Returns
    -------
    root : `DataRoot`
        The ``repos`` directory beneath the resolved root, and the rule
        that chose that root.
    """
    root = data_root(explicit)
    return DataRoot(path=root.path / REPOS_SUBDIR, source=root.source)

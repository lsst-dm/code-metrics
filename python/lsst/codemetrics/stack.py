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
        raise RuntimeError("lsst_build has not been set up. Source lsstsw/bin/envconfig first.")
    lsst_build_dir = Path(os.environ["LSST_BUILD_DIR"])
    lsstsw_dir = lsst_build_dir.parent
    return lsstsw_dir, lsstsw_dir / "build", lsst_build_dir / "bin" / "lsst-build"


def _prepare(lsstsw_dir: Path, build_dir: Path, lsst_build_exe: Path, ref: str | None) -> None:
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


def build_targets(legacy: list[LegacyEntry], weeklies: list[str], include_legacy: bool) -> list[ScanTarget]:
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
        targets.extend(ScanTarget(tag=e.tag, output_name=e.output_name, legacy=True) for e in legacy)
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

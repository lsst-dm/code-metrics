# Per-repository line counts as a function of time

## Problem

`bin/countlines.py` counts lines across the whole `lsst_distrib` stack at each
weekly tag.
There is no way to ask the same question of a single repository.

A single repository has a richer history than the weekly tags expose.
Sampling it needs to work for repositories that carry weekly tags, for
repositories whose history is a series of merges from rebased branches, and for
repositories with linear history.

## Goals

Collect line counts for one repository over its history, store them in a form
that supports cheap incremental updates, and plot them.

Fold the existing stack-wide scan into the same package so both share the
counting abstraction.

## Non-goals

Normalizing language names across counting tools.
Comparing repositories automatically.
Changing the numbers the existing stack-wide data reports.

## Package layout

The repository becomes an installable Python project.

```
pyproject.toml                    # gains [project], deps, console script
python/lsst/codemetrics/
    counters.py       # LineCounter ABC, cloc/scc/tokei backends, registry
    revisions.py      # Sample model and the three sampling modes
    worktree.py       # clone cache and throwaway detached worktree
    collect.py        # orchestration: repo x sampler x counter -> records
    storage.py        # CSV read/write, incremental merge
    plotting.py       # load CSV into a tidy table, filter/aggregate helpers
    stack.py          # the countlines.py logic, ported
    cli.py            # click command group
    data/legacy-tags.txt          # package data, not the top-level data/
tests/
data/repos/<name>.csv             # new
data/w.*.yaml                     # unchanged
bin/countlines.py                 # removed
```

A single console script `code-metrics` exposes two commands, `repo-history` and
`stack-scan`.

Runtime dependencies are `pyyaml`, `pydantic`, `click`, and `rich`.
`matplotlib` and `pandas` live in a `plot` optional-dependency group so
collection does not require them.

Models are pydantic `BaseModel` subclasses throughout, which also validates data
read back from disk.

## Counting backends

```python
class LanguageCount(BaseModel):
    n_files: int
    blank: int
    comment: int
    code: int


class LineCounter(ABC):
    name: ClassVar[str]

    @property
    def version(self) -> str:
        """Version string reported by the underlying tool."""

    @abstractmethod
    def count(
        self, path: Path, exclude_dirs: Sequence[str] = ()
    ) -> dict[str, LanguageCount]:
        """Count lines under ``path``, keyed by language name."""
```

Three implementations shell out and parse structured output:

| Backend | Command | Output |
|---|---|---|
| `ClocCounter` | `cloc --yaml --quiet` | YAML on stdout |
| `SccCounter` | `scc --format json` | JSON array |
| `TokeiCounter` | `tokei --output json` | JSON object |

Each maps `exclude_dirs` onto its own exclusion flag.
A `COUNTERS` registry maps a name to a class, and `--counter` selects one.
The default is `cloc`.

### Language names are not normalized

Each backend's native language names are stored verbatim, and every stored row
records which counter produced it.

A canonical taxonomy would silently reinterpret the tools' own judgments.
cloc's `C/C++ Header` against tokei's separate `C Header` and `C++ Header` is
not a rename; the tools classify differently.
Reconciliation belongs at plot time, where `plotting.py` offers an opt-in alias
map.

### No stored totals

No `SUM` row is written.
Totals are computed by summing languages at read time, so a total always matches
whatever language subset was selected.

## Sampling revisions

```python
class Sample(BaseModel):
    commit: str            # full sha
    date: datetime         # committer date, UTC
    label: str | None      # tag name, when the sample came from a tag
```

`--mode` chooses among three strategies.

| Mode | Command | Purpose |
|---|---|---|
| `tags` | `git tag --list <pattern> --sort=creatordate` | Align with the weekly tags |
| `first-parent` (default) | `git rev-list --first-parent <branch>` | Repositories built from merges |
| `all` | `git rev-list <branch>` | Repositories with linear history |

`--tag-pattern` defaults to `w.*` and makes the `tags` mode usable on
repositories that tag differently.

`first-parent` covers the merge-commit case.
Walking the first-parent chain yields the state of the branch after each merge
and also catches commits pushed directly to the branch, which `--merges` would
drop.
Staying on that chain is what avoids the timestamps of rebased branch commits.

Dates are committer dates (`%cI`), never author dates.
A rebase preserves the author date but resets the committer date, so only the
committer date records when code landed on the branch.

`--since` and `--until` bound a run.
Samples are returned sorted by date, ascending.
`--branch` defaults to the clone's `origin/HEAD`.

## Fetching and collection

Given a URL, `worktree.py` clones `--bare` into a cache directory, by default
under `~/.cache/lsst-code-metrics/`, overridable with `--cache-dir`.
Later runs `git fetch --prune --tags` into that cache.
Given a local path, it uses that repository as the worktree source.

Either way, counting happens in a throwaway detached worktree in a temporary
directory, removed in a `finally` block.
The user's own working tree is never modified.

For each sample:

```
git -C <worktree> checkout --detach --force <sha>
git -C <worktree> clean -xdff
counter.count(worktree, exclude_dirs)
```

The `clean -xdff` is required for correctness.
A file tracked at one commit but untracked at the next survives a checkout and
would otherwise be counted at every later sample.

Samples are walked in date order, so adjacent checkouts are close in history and
git writes only the differences between them.

Submodules are not initialized, so their contents are not counted.

### Progress and failure

A `rich.progress` bar reports sample count, elapsed time, and estimated time
remaining.
A `rich.table` summary at the end reports samples added, samples skipped, total
samples in the file, languages seen, and the date range covered.

A failed checkout or a backend crash on one sample logs a warning and skips that
sample.
`--strict` turns either into an abort.

An empty sample list is an error with a non-zero exit status.

The CSV is flushed every 50 samples, so an interrupted run leaves a valid file
that a later run resumes from.

## Storage

Per-repository results go in `data/repos/<name>.csv`, one row per sample and
language:

```
commit,date,label,counter,counter_version,language,n_files,blank,comment,code
```

Rows are sorted by `(date, commit, language)`.
Incremental runs therefore append at the end of the file and produce small git
diffs.

The column set is fixed.
A language appearing for the first time adds rows, never columns.

`counter` and `counter_version` are recorded per row rather than per file, which
is honest about a history accumulated across tool upgrades.

`label` is empty unless the sample came from a tag.

### Incremental updates

The key for "already collected" is `(commit, counter)`, not commit alone.

Switching `--counter` therefore recounts the history with the new backend, and
both sets of rows coexist, which allows comparing backends over identical
commits.
The alternative, keying on commit alone, would leave a file whose early points
came from one tool and whose later points came from another.

`--force` recomputes everything regardless.

### Sidecar metadata

`data/repos/<name>.meta.yaml` records what describes the run rather than a data
point: repository name, URL, sampling mode, branch, tag pattern, and excluded
directories.

## Porting the stack scan

`countlines.py` becomes `code-metrics stack-scan`.

It uses the `LineCounter` abstraction but is hard-wired to `cloc` with the
existing `--include-lang=Python,C++,C/C++ Header` filter, and writes the same
per-tag YAML into `data/`.
`--counter` is not offered on this command.
The historical record stays consistent.

Two changes come with the rewrite.

### The hardcoded weekly tag list goes away

The 570-line `TAGS_STR` existed because tag names changed padding partway
through the project's life, so `w.2017.1` and `w.2018.01` do not sort sensibly
by name.

That is a lexical sort problem, and it disappears when names are never sorted:

```
git tag --list 'w.*' --sort=creatordate
```

`creatordate` is the tag date for annotated tags and the commit date for
lightweight ones, giving true chronological order whatever the padding.
That order is also the one that minimizes checkout churn.

#### Getting the tags before anything is built

The list was hardcoded partly to escape a chicken-and-egg problem.
`countlines.py` is typically run against a freshly deployed `lsstsw`:

```
git clone https://github.com/lsst/lsstsw
cd lsstsw && ./bin/deploy && . ./bin/envconfig
```

At that point `build/` is empty.
There is no `lsst_distrib` checkout to read tags from, yet a tag is needed
before the first `lsst-build` invocation can run.

Running `lsst-build` once against `main` to populate `build/lsst_distrib` would
break the cycle, but it costs a full prepare pass and requires knowing the
deployed tree's layout.

The cheaper route is that `stack-scan` already locates `etc/repos.yaml` to pass
to `lsst-build`, and that file names the source of every product:

```yaml
lsst_distrib: https://github.com/lsst/lsst_distrib.git
```

So tag discovery resolves `lsst_distrib` from `etc/repos.yaml` and clones it
`--bare` into the same cache `repo-history` uses, then reads
`git tag --list 'w.*' --sort=creatordate`.
`lsst_distrib` is a metapackage that rarely commits, so the clone is small.

Values in `repos.yaml` may be a plain URL string or a mapping with a `url` key,
and the parser accepts both.

If `build/lsst_distrib` already exists, it is fetched and used directly rather
than cloned a second time.

`--tags-file` overrides the whole list, legacy entries included, when a fixed
list is needed, and skips tag discovery entirely.

The derived list may contain weeklies that `TAGS_STR` omitted, since that list
has gaps.
Skip-existing means the previously scanned tags are untouched, and any newly
included tag that `lsst-build` cannot prepare warns and skips.
New data files are therefore additive; no existing file changes.

### The pre-weekly release tags stay listed, with their weekly mapping

`OLD_TAGS_STR` remains, as `python/lsst/codemetrics/data/legacy-tags.txt`.
Names such as `10.1`, `9.2`, `8.0.0.0`, and `6.1.0.4` follow no pattern and
cannot be recovered from context.

These releases predate the weekly tags but are already part of the historical
record.
Their results were written under weekly-style names matching each release date,
which is why `data/` holds seven files from `w.2013.25` to `w.2015.20` even
though `TAGS_STR` began at `w.2015.22`.
That renaming is what lets the plot treat the whole timeline uniformly, and it
is curated knowledge that must not be lost.

So `legacy-tags.txt` records the mapping literally, not just the names:

```
# release-tag  weekly-equivalent
7.2.0.0        w.2013.25
...
```

The mapping is transcribed, not derived.
Checking the tag history in `lsst_distrib` shows why no rule reproduces it.

`lsst_distrib` is a metapackage that rarely commits, so its tags record when
tagging happened rather than when anything changed, and most of these tags point
at the same commit.
`10.1.rc3`, `b128`, `9.0`, and `10.1` all resolve to the commit of 2014-07-13,
and `7.2.0.0`, `6.2.0.0`, and `6.1.0.4` all resolve to the commit of
2012-11-16.
What separates those releases is how `lsst-build` resolves each tag name across
the package repositories, so `lsst_distrib`'s own commit date carries no
information about them.

Taking the ISO week of each tag's `taggerdate` accounts for five of the seven
files: `7.2.0.0` to `w.2013.25`, `9.0` or `9.1` to `w.2014.31`, `9.2` to
`w.2014.32`, `10.0` to `w.2014.50`, and `10.1` to `w.2015.20`.
It leaves two unexplained.
`w.2014.30` matches `b128` exactly, but `b128` is a build tag absent from
`OLD_TAGS_STR`.
No tag falls in the week of `w.2014.20` at all; the nearest release, `8.0.0.0`,
was tagged in week 11.
`6.1.0.x` and `6.2.0.0` produced no file.

The pairing is therefore editorial judgment about when each release happened,
and only the author of `data/` can supply it.
The file is written by hand, with uncertain entries commented as such.

### Legacy results are frozen

These entries sit at the front of the timeline; `--no-legacy` skips them.

Because their outputs already exist, skip-existing means they never re-run.
`--force` alone does not regenerate them: doing so requires `--force-legacy` as
well.

The seven files are the only record of a mapping that cannot be reconstructed
from tag metadata, so a careless `--force` must not be able to overwrite them.
Any legacy tag that current `lsst-build` cannot prepare warns and skips rather
than ending the run.

### Existing outputs are skipped

Tags whose `data/<tag>.yaml` already exists are skipped unless `--force`.
Every run currently redoes all 570.

## Plotting

`plotting.py` holds the loading and reshaping logic; the notebooks stay thin.

- `load_repo(name) -> DataFrame` returns a tidy long frame, one row per date,
  commit, language, and counter, with `lines = code + comment` derived.
- `apply_aliases(df, alias_map)` performs opt-in language reconciliation, for
  example folding cloc's `C/C++ Header` into `C++`. It is never automatic.
- `select(df, languages=..., counter=...)` warns when the frame mixes counters
  and no counter was chosen.
- `pivot(df, value="code")` returns a wide frame indexed by date with one column
  per language.
- `load_stack()` parses the existing `data/w.*.yaml` files.

`plot-repo-lines.ipynb` plots code and comment lines over time for one or more
repositories, in the style of the existing plot.

`plot-line-counts.ipynb` is changed only to call `load_stack()` instead of
parsing YAML inline.
Its plot is unchanged.

## Testing

`pytest`, built around a synthetic git repository created in a temporary
directory.
Its history contains a merge, a rebased branch whose author dates precede its
committer dates, and a tag.
`GIT_COMMITTER_DATE` is pinned so assertions are exact.

| Area | Coverage |
|---|---|
| `counters` | Checked-in fixture output from all three tools parses into `LanguageCount` maps with no binaries present; integration tests skip when a binary is absent |
| `revisions` | Each mode returns the expected commits; the rebase case proves committer dates are used; `creatordate` ordering survives mixed tag padding |
| `worktree` | Worktree is created and removed; an untracked leftover is cleaned between samples |
| `storage` | CSV round-trip, `(commit, counter)` merge, stable sort, pydantic rejecting malformed rows |
| `collect` | End-to-end against the synthetic repository with a stub counter |

A missing backend binary raises an error naming the tool.

All code is ruff-clean under the repository's existing configuration.

## Command line

### `code-metrics repo-history REPO`

`REPO` is a local path or a remote URL.

| Option | Default | Meaning |
|---|---|---|
| `--name` | basename of `REPO`, minus `.git` | Output is `<output-dir>/<name>.csv` |
| `--output-dir` | `data/repos` | Where the CSV and sidecar are written |
| `--mode` | `first-parent` | `tags`, `first-parent`, or `all` |
| `--branch` | clone's `origin/HEAD` | Branch to walk |
| `--tag-pattern` | `w.*` | Used by `--mode tags` |
| `--counter` | `cloc` | `cloc`, `scc`, or `tokei` |
| `--exclude-dir` | none | Repeatable; passed to the backend's exclusion flag |
| `--since` / `--until` | none | Bound the sampled date range |
| `--cache-dir` | `~/.cache/lsst-code-metrics` | Bare clone cache |
| `--force` | off | Recompute rather than merge incrementally |
| `--strict` | off | Abort on a sample failure instead of skipping |

### `code-metrics stack-scan`

Requires an enabled `lsstsw` environment with `LSST_BUILD_DIR` set, as
`countlines.py` does today.

| Option | Default | Meaning |
|---|---|---|
| `--output-dir` | `data` | Where per-tag YAML is written |
| `--tags-file` | none | Replace the derived tag list, legacy entries included |
| `--legacy / --no-legacy` | `--legacy` | Include the pre-weekly release tags |
| `--force` | off | Rescan tags whose YAML already exists, legacy excluded |
| `--force-legacy` | off | Also rescan the legacy entries; required with `--force` to touch them |
| `--strict` | off | Abort on a tag failure instead of skipping |

### Examples

```
pip install -e '.[plot]'

code-metrics repo-history https://github.com/lsst/daf_butler
code-metrics repo-history ~/work/lsst/daf_butler --mode tags
code-metrics repo-history https://github.com/lsst/afw --counter tokei --since 2020-01-01

code-metrics stack-scan
```

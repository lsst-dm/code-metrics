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

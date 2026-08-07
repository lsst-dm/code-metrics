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

### Docstrings, and how far the backends agree

The backends disagree about whether a Python docstring is code or
comment, which matters a great deal for numpydoc-heavy code.
Counted over `daf_butler`'s Python source:

| Backend | code | comment | blank |
|---|---|---|---|
| cloc | 54,373 | 48,500 | 16,291 |
| tokei | 55,063 | 52,420 | 11,827 |
| scc | 67,312 | 41,689 | 10,322 |

**cloc and tokei agree on `code` to about one percent.**
They only do so because the tokei backend enables tokei's own
`treat_doc_strings_as_comments` setting by default; counted natively
tokei reports roughly twice the code, since it treats every docstring
line as code.
`TokeiCounter(docstrings_as_comments=False)` restores that behavior.

**scc is not comparable with either**, running about a quarter higher on
`code`.
It classifies much of the same material as code however it is
configured, so do not mix its Python figures with the others.

The totals of all three columns agree everywhere to within a tenth of a
percent, so this is purely about which column a line lands in.
`select()` raises rather than mixing backends silently.

`cloc --docstring-as-code` would move cloc the other way, but every file
in `data/` was counted with cloc's default, so the stack-wide scan keeps
it.

The backends also name C and C++ headers differently: cloc reports one
`C/C++ Header`, while scc and tokei split `C Header` from `C++ Header`.
`CPP_HEADER_ALIASES` folds all of them into `C++`, and covers whichever
backend produced the data.

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

# code-metrics

Data and tools for calculating metrics about the science pipelines code base.

`countlines.py` uses `lsst-build` to check out weekly releases and runs
`cloc.py` on the result.

The results of running this command can be found in the `data/` directory.

Use `plot-line-counts.ipynb` to plot the data.

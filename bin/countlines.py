#!/usr/bin/env python3

"""
Checks out different versions of lsst_distrib, filters out non-LSST code,
calculates the lines for that version and records the information.

Assumes an lsstsw environment has been enabled (by sourcing bin/setup.sh).

Runs something equivalent to:

% ./lsst_build/bin/lsst-build prepare --repos ./etc/repos.yaml \
    --exclusion-map ./etc/exclusions.txt "`pwd`/" lsst_apps

for a range of weekly tags. It is recommended that afwdata and testdata_*
packages are added to the exclusions file.

Then runs the cloc.pl command on the contents of the manifest.txt file,
filtering out packages that have an ups/eupspkg.cfg.sh.

Obtain cloc from: https://github.com/AlDanial/cloc
"""

import os
import sys
import subprocess

# Set to the location of the cloc.pl executable
CLOC_EXE = "/Users/timj/Downloads/cloc-1.86.pl"

# We cannot guess tags so easiest to list them
# eg in lsst_distrib git repo: git tag | grep w.
# If non-weeklies are used, rename them as weeklies
# once created.
TAGS_STR = """
w.2015.22
w.2015.30
w.2015.33
w.2015.35
w.2015.36
w.2015.37
w.2015.38
w.2015.39
w.2015.40
w.2015.43
w.2015.44
w.2015.45
w.2015.47
w.2016.03
w.2016.05
w.2016.06
w.2016.08
w.2016.10
w.2016.12
w.2016.15
w.2016.19
w.2016.20
w.2016.28
w.2016.32
w.2016.34
w.2016.36
w.2016.37
w.2016.39
w.2016.40
w.2016.41
w.2016.42
w.2016.43
w.2016.44
w.2016.45
w.2016.46
w.2016.47
w.2016.48
w.2016.49
w.2016.50
w.2016.51
w.2016.52
w.2016.53
w.2017.1
w.2017.10
w.2017.11
w.2017.12
w.2017.13
w.2017.14
w.2017.15
w.2017.16
w.2017.17
w.2017.18
w.2017.2
w.2017.20
w.2017.21
w.2017.22
w.2017.23
w.2017.24
w.2017.25
w.2017.26
w.2017.27
w.2017.28
w.2017.29
w.2017.3
w.2017.30
w.2017.31
w.2017.32
w.2017.33
w.2017.34
w.2017.35
w.2017.36
w.2017.37
w.2017.38
w.2017.39
w.2017.4
w.2017.40
w.2017.41
w.2017.42
w.2017.43
w.2017.44
w.2017.45
w.2017.46
w.2017.47
w.2017.48
w.2017.49
w.2017.5
w.2017.50
w.2017.51
w.2017.52
w.2017.6
w.2017.7
w.2017.8
w.2017.9
w.2018.01
w.2018.02
w.2018.03
w.2018.04
w.2018.05
w.2018.06
w.2018.07
w.2018.08
w.2018.09
w.2018.10
w.2018.11
w.2018.12
w.2018.13
w.2018.14
w.2018.15
w.2018.16
w.2018.17
w.2018.18
w.2018.19
w.2018.20
w.2018.21
w.2018.22
w.2018.23
w.2018.24
w.2018.25
w.2018.26
w.2018.27
w.2018.28
w.2018.29
w.2018.30
w.2018.31
w.2018.32
w.2018.33
w.2018.34
w.2018.35
w.2018.36
w.2018.37
w.2018.38
w.2018.39
w.2018.40
w.2018.41
w.2018.42
w.2018.43
w.2018.44
w.2018.45
w.2018.46
w.2018.47
w.2018.48
w.2018.49
w.2018.50
w.2018.51
w.2018.52
w.2019.01
w.2019.02
w.2019.03
w.2019.04
w.2019.05
w.2019.06
w.2019.07
w.2019.08
w.2019.09
w.2019.10
w.2019.11
w.2019.12
w.2019.13
w.2019.14
w.2019.15
w.2019.16
w.2019.17
w.2019.18
w.2019.19
w.2019.20
w.2019.21
w.2019.22
w.2019.23
w.2019.24
w.2019.25
w.2019.26
w.2019.27
w.2019.28
w.2019.29
w.2019.31
w.2019.32
w.2019.33
w.2019.34
w.2019.35
w.2019.36
w.2019.37
w.2019.38
w.2019.40
w.2019.41
w.2019.42
w.2019.43
w.2019.44
w.2019.45
w.2019.46
w.2019.47
w.2019.48
w.2019.49
w.2019.50
w.2019.51
w.2019.52
w.2020.01
w.2020.02
w.2020.03
w.2020.04
w.2020.05
w.2020.06
w.2020.07
w.2020.08
w.2020.09
w.2020.10
w.2020.11
w.2020.12
w.2020.13
w.2020.14
w.2020.15
w.2020.16
w.2020.17
w.2020.18
w.2020.19
w.2020.20
w.2020.21
w.2020.22
w.2020.23
w.2020.24
"""

# Older release tags
OLD_TAGS_STR = """
10.1
10.0
9.2
9.1
9.0
8.0.0.0
7.2.0.0
6.2.0.0
6.1.0.4
6.1.0.0
"""

TAGS = [t for t in TAGS_STR.split("\n") if len(t)]

# The EUPS product for which we are counting lines
PRODUCT = "lsst_distrib"

# Work out where we are going to be building
if "LSST_BUILD_DIR" not in os.environ:
    print("lsst_build has not been setup", file=sys.stderr)
    sys.exit(1)

lsst_build_exe = os.path.join(os.environ["LSST_BUILD_DIR"], "bin", "lsst-build")
lsstsw_dir = os.path.normpath(os.path.join(os.environ["LSST_BUILD_DIR"], os.path.pardir))
sources_dir = os.path.join(lsstsw_dir, "build")

# Must be in the sources directory when we run cloc
os.chdir(sources_dir)

for tag in TAGS:
    print(f"Checking out source with git ref {tag}")
    # Run lsst_build with this ref
    # lsst_build prepare --repos repos.yaml
    #                    --exclusion-map ... build_dir product
    subprocess.run([lsst_build_exe,
                    "prepare",
                    "--repos", os.path.join(lsstsw_dir, "etc", "repos.yaml"),
                    "--exclusion-map", os.path.join(lsstsw_dir, "etc", "exclusions.txt"),
                    "--ref", tag,
                    sources_dir,
                    PRODUCT
                    ])

    # Read the manifest file to work out which packages could contribute
    # to the count.
    with open(os.path.join(sources_dir, "manifest.txt")) as fd:
        products = []
        for line in fd:
            if line.startswith("#"):
                continue
            if line.startswith("BUILD"):
                continue
            # We only care about the first word
            prod = line.split(" ")[0]

            # Do not bother to count if this product has an eupspkg file
            if (os.path.exists(os.path.join(sources_dir, prod, "ups", "eupspkg.cfg.sh")) or
                    os.path.exists(os.path.join(sources_dir, prod, "upstream"))):
                continue

            # Consider filtering out ndarray (was LSST, then thirdparty)

            # This is a product we should analyze
            products.append(prod)

    print(f"Found {len(products)} products")

    if not products:
        print(f"No products find with ref {tag}. Please investigate.", file=sys.stderr)
        sys.exit(1)

    # We are only interested in the line counts for Python and C++
    output_file = "{}.yaml".format(tag)
    output = subprocess.run([CLOC_EXE,
                             '--include-lang=Python,C++,C/C++ Header',
                             "--yaml",
                             f"--report-file={output_file}",
                             *products])

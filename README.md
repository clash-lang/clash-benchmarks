# clash-benchmarks

Compile-time measurements of [Clash](https://github.com/clash-lang/clash-compiler),
with the graphs that show them:

**https://clash-lang.github.io/clash-benchmarks/**

This repository holds the data, the scripts that make it, and the script
that renders the site. The data is plain JSON on the `main` branch, one
file for each machine and commit.

## What a benchmark run measures

1. **The normalization suite in clash-compiler.** `clash-benchmark-normalization`
   runs seven source files through criterion. Criterion gives the mean time
   and the standard deviation of each file.
2. **wireDemo.** The run builds
   [bittide-hardware](https://github.com/bittide/bittide-hardware) against
   the clash-compiler checkout under test, generates HDL for
   `Bittide.Instances.Hitl.WireDemo.wireDemoTest` one time, and reads the
   `Clash: ... took` lines. This is the big, real-world number: minutes,
   not milliseconds. The headline metric is the normalization time.

   bittide-hardware does not build against every clash commit. Then the
   result says `wire_demo: skipped`, and the normalization numbers are
   still there.

## How to read the graphs

- **Machine.** Numbers from different machines are not comparable, so the
  page shows one machine at a time. Pick the machine at the top.
- **Branch.** The default is clash-lang `master`. For another branch, the
  x-axis is master up to the branch point, then the commits of the branch.
  The commits of the branch are in green, behind a green band, after a
  dashed rule at the branch point.
- **Holes.** The x-axis holds every first-parent commit of master, also
  the commits that have no measurement. A short hole is a straight line
  between two measurements; a long hole breaks the line.
- A point with a light band around it shows the standard deviation of
  criterion. Click a point to open the commit or its pull request.
- The table view at the end of the page holds all numbers of the current
  selection.

## How to start a run

Both entry points are the `Benchmark` workflow:

```console
# One branch of clash-lang/clash-compiler
gh workflow run benchmark.yml -R clash-lang/clash-benchmarks -f ref=perf/faster-strings

# One branch of a fork
gh workflow run benchmark.yml -R clash-lang/clash-benchmarks \
  -f repo=someone/clash-compiler -f ref=my-experiment

# One exact commit
gh workflow run benchmark.yml -R clash-lang/clash-benchmarks -f sha=<sha>

# The newest master commits that have no result yet, at most five
gh workflow run benchmark.yml -R clash-lang/clash-benchmarks -f count=5
```

Every six hours the workflow also runs by itself, for the master commits
that have no result. A run of the full suite takes 45 to 90 minutes for
each commit. The new result appears on `main`, and the site renders again.

## Layout

    machines/<id>.json                  the machines that measure
    results/<machine>/<ab>/<sha>.json   one result for each machine and commit
    branches/<owner>/<repo>/<ref>.json  the commits of a benchmarked branch
    bench/                              the run side: measure, collect, push
    render.py                           makes site/index.html from the data
    tools/                              migration and the publish helper
    docs/ops.md                         how to operate all of this

`bench/result_schema.py` is the reference for the format of a result file
and of a branch snapshot. It is also the validator:

```console
./bench/result_schema.py results/volthe/0d/0d32*.json
```

To render the site on your own machine, in a clone that has the data:

```console
./render.py --clash-repo ~/code/clash-compiler --out site/index.html
```

Master comes from a clash-compiler clone, because the graph must also show
the commits that have no result. Without `--clash-repo`, the script makes a
clone of its own in `~/.cache/clash-benchmarks`.

## History

The bot first ran in a fork of clash-compiler, as `.ci/bench` plus two
workflows. `tools/migrate_v1.py` moved those results here: 62 master
commits and one branch commit, all measured on `volthe`. The pull request
part of the bot (a sticky comment with a table of differences) did not move
yet.

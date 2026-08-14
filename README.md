# clash-benchmarks

Compile-time measurements of [Clash](https://github.com/clash-lang/clash-compiler),
with the graphs that show them:

**https://clash-lang.github.io/clash-benchmarks/**

This repository holds the data, the scripts that make it, and the script
that renders the site. The data is plain JSON on the `main` branch, one
file for each machine and commit.

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
clone of its own in `~/.cache/clash-benchmarks`. Add `--all-branches` to
see the branches whose pull request is closed as well.

## Maintained
This is a low-stakes repository and is mostly maintained by LLMs.
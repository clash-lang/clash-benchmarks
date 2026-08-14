# Operations

How to run, repair and extend the benchmark bot. For what the numbers mean
and how to read the site, see the [README](../README.md).

## The two workflows

| Workflow | When | What it does |
|---|---|---|
| `benchmark.yml` | every six hours, and on demand | measures one or more commits and pushes each result to `main` |
| `publish.yml` | after a push to `main`, and on demand | renders the site and pushes it to the `pages` branch |

`benchmark.yml` has no concurrency group on purpose: a concurrency group
cancels queued runs, and a cancelled run is a lost datapoint. The runner
has one job slot, so the runs wait for each other in the runner queue.

One run does this for each commit (`bench/run_one.sh`):

1. check the commit out;
2. `bench/run_clash_benchmarks.sh` builds and runs the normalization suite;
3. `bench/run_bittide.sh` builds bittide-hardware and generates HDL one
   time. A build failure is not an error: the leg becomes `skipped`;
4. `bench/collect_result.py` makes the result file;
5. `bench/branch_snapshot.py` records the commits of the branch, if the
   commit does not come from clash-lang master;
6. `bench/push_result.sh` pushes the result to `main`.

Each commit pushes its own result. A run that stops in the middle keeps the
results of the commits that are done.

### The schedule

`bench/catchup.py` walks the first-parent commits of master, from the
newest one back, until it finds a commit that has a result for this
machine. The commits after that one need work, and the oldest of them goes
first. The search stops after 200 commits: a machine with no result in that
window starts at the newest commit only. Use a dispatch for a backfill of
more than that.

GitHub disables a schedule after 60 days without activity in the
repository. A result push is activity, so the schedule keeps itself alive
while it works. After a long stop, enable it again in the Actions tab.

## The runner

The jobs want a self-hosted runner with the labels `self-hosted` and
`benchmark`, and docker.

- Keep the machine quiet: no other work. Set the CPU governor to
  `performance` and turn turbo boost off. Numbers from a busy machine are
  noise.
- The jobs run in the container
  `ghcr.io/clash-lang/nixos-bittide-hardware:<tag>`. The image is Ubuntu
  with nix and **no toolchain on the default PATH**: each step runs through
  `shell: git-nix-shell {0} /build`, the bittide dev shell that is
  pre-built into the image (GHC 9.10.3, cabal, python3 without requests,
  sbt, verilator).
- The cabal store lives on the runner in `/var/cache/clash-bench`, mounted
  into the container as `/bench-cache`. The store is disposable: a wipe
  costs build time, not numbers.
- A slow or filtered network can serve a stale hackage index over plain
  http. The workflow forces `https://hackage.haskell.org/` and fails with a
  clear message when the index does not reach the `index-state` of
  `cabal.project`. The marker file `.bench-cache-v2` in the cache
  directory discards a poisoned index; bump the version to discard again.

### Repository variables

| Variable | Effect |
|---|---|
| `BENCH_RUNS_ON` | JSON array that replaces the `runs-on` labels, for example `["ubuntu-latest"]`. Leave it unset in production. |
| `BENCH_QUICK` | `1` trims the suite to `examples/FIR.hs` and skips the wireDemo leg. Fast, and the result is marked as partial. |
| `BENCH_MACHINE` | machine id. The default is the runner name. |
| `BENCH_CATCHUP_MAX` | most commits per scheduled run (default 5). |

## Add a machine

1. Register the runner with the labels above.
2. Write `machines/<id>.json` and push it to `main`:

   ```json
   {
     "id": "volthe", "label": "volthe", "hostname": "volthe",
     "cpu": "12th Gen Intel(R) Core(TM) i5-1240P",
     "threads": 16, "ram_gib": 32, "default": true,
     "notes": "Dedicated benchmark runner."
   }
   ```

   `push_result.sh` refuses a result of a machine that has no file here: an
   unknown machine in the data gives graphs that nobody can interpret. Only
   one machine has `"default": true`; the site opens on that machine.
3. Start a run. The new machine appears in the machine selector after the
   site renders again.

The machine id is part of the path of every result, so a new id starts a
new series. Do not change an id after it has results.

## Backfill

Dispatch one run for each commit. The runs queue at the runner:

```console
git rev-list --first-parent --reverse <from>^..<to> | while read -r sha; do
  gh workflow run benchmark.yml -R clash-lang/clash-benchmarks -f sha=$sha
  sleep 2
done
```

Set `BENCH_QUICK` to `0` first: a quick run stores a quick result. GitHub
cancels a job that waits more than 24 hours for a runner, so dispatch a
long campaign in portions.

`push_result.sh --replace-skipped` (what `run_one.sh` uses) may replace a
result whose wireDemo leg was skipped with a complete one. It never
replaces a complete result. To measure a commit again, for example after an
outlier, delete its result file from `main` first:

```console
git rm results/<machine>/<ab>/<sha>.json && git commit && git push
gh workflow run benchmark.yml -R clash-lang/clash-benchmarks -f sha=<sha>
```

## Patch overlays

An old clash commit can predate an API change that the pinned
bittide-hardware needs. Each directory `bench/patches.d/<name>/` repairs
one such break:

    bench/patches.d/<name>/applies-before   one clash-compiler sha B
    bench/patches.d/<name>/<repo>/*.patch   an extra git am series

`run_bittide.sh` applies the overlay when the checkout under test does not
contain B. The names of the applied overlays go into the result
(`wire_demo.overlays`) and into the tooltip.

Keep an overlay minimal: bound relaxations and shims only. wireDemo numbers
are compared across overlays, so an overlay must not change what Clash
compiles.

## Bump the bittide-hardware pins

The wireDemo leg builds pinned revisions with a patch series, because
bittide-hardware does not always build against the newest clash master. The
pins are `bench/bittide-rev`, `bench/clash-cores-rev` and
`bench/clash-vexriscv-rev`; the patches are in `bench/patches/`.

1. Update the three `*-rev` files.
2. Make the patches again from a scratch branch on bittide-hardware: the
   same `cabal.project` rewrite (sibling `packages:` paths for
   clash-compiler, clash-cores and clash-vexriscv) plus the compile fixes,
   then `git format-patch --zero-commit --no-signature`.
3. Verify it on your own machine: `bench/run_bittide.sh` from a
   clash-compiler checkout, in a directory with the name `clash-compiler`,
   against clash master.

A bump changes what wireDemo measures. Do not compare numbers across
different `bittide_rev` values.

## The site

GitHub Pages serves the `pages` branch of this repository from its root
(the legacy, branch-based build). `tools/push_site.sh` replaces the whole
content of that branch with the rendered site, plus `.nojekyll`. Never edit
the `pages` branch by hand.

`publish.yml` makes a `--filter=tree:0` clone of clash-compiler: the graph
needs the order of the master commits, not their content.

To see the site before it goes out:

```console
./render.py --clash-repo ~/code/clash-compiler --out site/index.html
xdg-open site/index.html
```

`site/` is in `.gitignore`: the rendered page belongs on the `pages`
branch, not on `main`.

## Data hygiene

- `bench/result_schema.py` validates a result and a branch snapshot.
  `collect_result.py` and `render.py` both call it, so a bad file stops the
  render instead of making a wrong graph.
- A branch snapshot is replaced by the newest run for that branch. After a
  force-push, the points of commits that are no longer on the branch go
  away with the old chain.
- `tools/migrate_v1.py` converted the results of the first bot, in the
  clash-compiler fork, to the format of this repository. It is a one-time
  script; it stays here to document where the old data comes from.

#!/usr/bin/env python3
"""List the commits of a branch that still need a benchmark on one machine.

Usage:
  catchup.py --clash-repo PATH --machine ID [options]

  --clash-repo PATH  clone of clash-lang/clash-compiler
  --machine ID       machine to look at. The default comes from
                     BENCH_MACHINE, else RUNNER_NAME, else the hostname.
  --results DIR      repository root with results/ (default: this repository)
  --ref REF          ref of the branch in the clone (default origin/master)
  --upstream-ref REF ref of the branch that --ref left, clash-lang master
                     for a release branch. The walk stops at the commit
                     where it left. Without this, --ref is master itself.
  --max N            most commits to print (default 5)
  --search N         how far back to look for the newest result (default 200)
  --dry-run          write a readable list to standard error instead

The script prints one sha for each commit, oldest first. The scheduled
workflow benchmarks them in that order.

It walks the first-parent commits of the branch, from the newest one back,
until it finds a commit that has a result for this machine. The commits
after that one need work. The script prints the oldest ones first, because
a benchmark run pushes its result immediately: a run that stops early
keeps the datapoints that it has, and there is no hole in the middle.

For a release branch, the walk ends at the commit where the branch left
master (--upstream-ref). That commit is the last one that master and the
branch share; the ones before it are master commits, and master looks
after those itself. The branch point counts: when it has a result and no
commit of the branch has one, every commit of the branch needs work.

The search stops after --search commits. A machine with no result in that
window starts at the newest commit only. This keeps a new machine, or a
machine that was away for months, from a backfill of all of history. Use
the dispatch workflow for a backfill.
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from result_schema import machine_id, result_path  # noqa: E402


def git(repo, *args):
    """Run one git command in a repository and return its stdout."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return proc.stdout


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--clash-repo", required=True, type=Path)
    parser.add_argument("--machine", default=machine_id())
    parser.add_argument("--results", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--ref", default="origin/master")
    parser.add_argument("--upstream-ref")
    parser.add_argument("--max", type=int, default=5)
    parser.add_argument("--search", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    shas = git(
        args.clash_repo, "rev-list", "--first-parent",
        f"--max-count={args.search}", args.ref,
    ).split()

    if args.upstream_ref:
        base = git(args.clash_repo, "merge-base",
                   args.upstream_ref, args.ref).strip()
        if base in shas:
            shas = shas[:shas.index(base) + 1]
        print(f"catchup.py: {args.ref} left {args.upstream_ref} at "
              f"{base[:9]}; looking at {len(shas)} commits", file=sys.stderr)

    missing = []
    frontier = None
    for sha in shas:
        if (args.results / result_path(args.machine, sha)).exists():
            frontier = sha
            break
        missing.append(sha)

    if frontier is None:
        print(f"catchup.py: no result for {args.machine} in the newest "
              f"{len(shas)} commits of {args.ref}; start at the newest commit",
              file=sys.stderr)
        missing = missing[:1]
    else:
        print(f"catchup.py: newest result for {args.machine} is {frontier[:9]}, "
              f"{len(missing)} commits after it", file=sys.stderr)

    missing.reverse()
    todo = missing[:args.max]
    if len(missing) > len(todo):
        print(f"catchup.py: {len(missing) - len(todo)} more commits wait for "
              f"a later run (--max {args.max})", file=sys.stderr)

    for sha in todo:
        if args.dry_run:
            line = git(args.clash_repo, "log", "-1", "--format=%h %cs %s", sha).strip()
            print(f"  {line}", file=sys.stderr)
        else:
            print(sha)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
r"""List the pull request commits that still need a benchmark.

Usage:
  pr_catchup.py --clash-repo PATH --prs FILE [options]

  --clash-repo PATH  clone of clash-lang/clash-compiler to fetch into
  --prs FILE         open pull requests, from bench/list_prs.py
  --label NAME       label that asks for a benchmark (default performance)
  --machine ID       machine to look at. The default comes from
                     BENCH_MACHINE, else RUNNER_NAME, else the hostname.
  --results DIR      repository root with results/ (default: this repository)
  --upstream-ref REF ref of clash-lang master in the clone
                     (default refs/bench/upstream-master)
  --max N            most commits to print (default 5)
  --chain-max N      most commits of one pull request to look at (default 500)
  --dry-run          write a readable list to standard error instead

The script prints one line for each commit that needs work:

    <sha>\t<head repo>\t<head ref>\t<ref of the branch head>

The workflow hands those four fields to bench/run_one.sh.

Every commit of a labelled pull request is a datapoint: a pull request
that claims to make Clash faster has to show which commit did it. The
head commit of each pull request goes first, because that is the number
somebody is waiting for; the rest of the commits follow oldest first.
The script takes turns between the pull requests, so when there is more
work than budget every pull request gets its head measured before any
of them gets a second commit.

The commits of a pull request are not in the clone, also not when the
branch lives in a fork. GitHub publishes them as refs/pull/<n>/head in
the base repository, so the script fetches each one into
refs/bench/pr/<n> and reads the chain of the branch from there. The
benchmark then checks the commit out of the same clone.

The fourth field is that same fetched ref. bench/branch_snapshot.py
records the whole branch from it, and not from the commit under test:
the head of the branch goes first, so a later run on an older commit of
the same branch must not shorten the snapshot again.
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from list_prs import load, with_label  # noqa: E402
from result_schema import machine_id, result_path  # noqa: E402


def git(repo, *args, check=True):
    """Run one git command in a repository and return its stdout, or None."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        if check:
            sys.exit(f"pr_catchup.py: git {' '.join(args)} failed: "
                     f"{proc.stderr.strip()}")
        print(f"pr_catchup.py: git {' '.join(args)} failed: "
              f"{proc.stderr.strip()}", file=sys.stderr)
        return None
    return proc.stdout


def commits_of(args, pr, base_url):
    """Return the first-parent commits of one pull request, oldest first.

    Returns an empty list when the pull request cannot be read: a broken
    one must not stop the work on the others.
    """
    ref = f"refs/bench/pr/{pr['number']}"
    if git(args.clash_repo, "fetch", "--no-tags", "--force", base_url,
           f"refs/pull/{pr['number']}/head:{ref}", check=False) is None:
        return []
    # Take the head from the fetched ref, not from the list: the branch
    # can have moved since the list was made.
    base = git(args.clash_repo, "merge-base", args.upstream_ref, ref, check=False)
    if base is None:
        return []
    out = git(args.clash_repo, "rev-list", "--first-parent", "--reverse",
              f"--max-count={args.chain_max}", f"{base.strip()}..{ref}",
              check=False)
    return out.split() if out else []


def order(chain, missing):
    """Return the commits that need work, head first, then oldest first."""
    head = chain[-1]
    todo = [head] if head in missing else []
    todo.extend(sha for sha in chain if sha in missing and sha != head)
    return todo


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--clash-repo", required=True, type=Path)
    parser.add_argument("--prs", required=True, type=Path)
    parser.add_argument("--label", default="performance")
    parser.add_argument("--machine", default=machine_id())
    parser.add_argument("--results", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--upstream-ref", default="refs/bench/upstream-master")
    parser.add_argument("--max", type=int, default=5)
    parser.add_argument("--chain-max", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data = load(args.prs)
    base_url = f"https://github.com/{data['repo']}.git"
    labelled = with_label(data, args.label)
    if not labelled:
        print(f"pr_catchup.py: no open pull request of {data['repo']} carries "
              f"the label {args.label!r}", file=sys.stderr)
        return 0

    # One queue for each pull request, so the loop below can take turns.
    queues = []
    for pr in labelled:
        chain = commits_of(args, pr, base_url)
        if not chain:
            print(f"pr_catchup.py: #{pr['number']} has no commits on top of "
                  f"{args.upstream_ref}, skipping", file=sys.stderr)
            continue
        missing = {sha for sha in chain
                   if not (args.results / result_path(args.machine, sha)).exists()}
        todo = order(chain, missing)
        print(f"pr_catchup.py: #{pr['number']} {pr['head_repo']}@"
              f"{pr['head_ref']}: {len(chain)} commits, {len(todo)} without a "
              f"result for {args.machine}", file=sys.stderr)
        if todo:
            queues.append((pr, todo))

    work = []
    while queues and len(work) < args.max:
        for pr, todo in queues:
            work.append((todo.pop(0), pr))
            if len(work) >= args.max:
                break
        queues = [(pr, todo) for pr, todo in queues if todo]

    waiting = sum(len(todo) for _, todo in queues)
    if waiting:
        print(f"pr_catchup.py: {waiting} more pull request commit(s) wait for "
              f"a later run (--max {args.max})", file=sys.stderr)

    for sha, pr in work:
        if args.dry_run:
            line = git(args.clash_repo, "log", "-1", "--format=%h %cs %s",
                       sha).strip()
            print(f"  #{pr['number']} {line}", file=sys.stderr)
        else:
            print(f"{sha}\t{pr['head_repo']}\t{pr['head_ref']}"
                  f"\trefs/bench/pr/{pr['number']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

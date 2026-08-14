#!/usr/bin/env python3
"""Write the commit snapshot of a benchmarked branch.

Usage:
  branch_snapshot.py --repo REPO --ref REF --upstream-ref REF --out DIR

  --repo          owner/name of the repository that holds the branch
  --ref           branch name, for example "perf/faster-strings"
  --upstream-ref  ref of clash-lang master in this clone. The default is
                  "upstream/master".
  --out DIR       repository root to write to (default: this repository).
                  The path in it comes from the repository and the branch.
  --out-file PATH exact file to write, instead of --out

Run the script with the benchmarked clash-compiler checkout as the working
directory. HEAD must be the commit that was benchmarked.

A branch is not durable: it moves, it goes away after a merge, and it can
live in a fork. The graphs therefore do not read the branch from a clone.
They read this snapshot instead: the branch point plus the first-parent
chain from there to HEAD. See bench/result_schema.py for the format.

The script replaces an earlier snapshot of the same branch. The newest
run wins. After a force-push, points of commits that are no longer on the
branch go away with the old chain. That is better than one graph with the
commits of two different versions of the same branch.

Master needs no snapshot: the graphs read master from a clone.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from result_schema import branch_path, now, validate_branch  # noqa: E402

# How far back the script looks for the branch point.
MAX_BRANCH_LENGTH = 500


def git(*cmd):
    """Run one git command and return its stdout."""
    res = subprocess.run(["git", *cmd], capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"branch_snapshot.py: git {' '.join(cmd)} failed: {res.stderr.strip()}")
    return res.stdout.strip()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--upstream-ref", default="upstream/master")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--out-file", type=Path)
    args = parser.parse_args()

    if args.ref == "master" and args.repo == "clash-lang/clash-compiler":
        sys.exit("branch_snapshot.py: master needs no snapshot")

    base = git("merge-base", args.upstream_ref, "HEAD")
    shas = git(
        "rev-list", "--first-parent", "--reverse",
        f"--max-count={MAX_BRANCH_LENGTH}", f"{base}..HEAD",
    ).split()
    if not shas:
        sys.exit(f"branch_snapshot.py: HEAD is on {args.upstream_ref}, "
                 f"there is no branch to record")

    commits = []
    for sha in shas:
        subject, date = git("log", "-1", "--format=%s%n%cs", sha).splitlines()[:2]
        commits.append({"sha": sha, "subject": subject, "date": date})

    snapshot = {
        "repo": args.repo,
        "ref": args.ref,
        "base": base,
        "updated": now(),
        "commits": commits,
    }

    problems = validate_branch(snapshot)
    for problem in problems:
        print(f"branch_snapshot.py: {problem}", file=sys.stderr)
    if problems:
        return 1

    path = args.out_file or args.out / branch_path(args.repo, args.ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"branch_snapshot.py: wrote {path}: {len(commits)} commits "
          f"on top of {base[:9]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

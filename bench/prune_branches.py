#!/usr/bin/env python3
"""Point each branch snapshot at its pull request, drop the dead ones.

Usage:
  prune_branches.py --prs FILE [--root DIR] [--dry-run]

  --prs FILE   open pull requests, from bench/list_prs.py
  --root DIR   repository root with branches/ (default: this repository)
  --dry-run    say what would happen, change nothing

A branch is only interesting while somebody works on it, so this script
keeps the "pr" field of every snapshot in branches/ in step with GitHub:

- the branch is the head of an open pull request: "pr" is that number,
  and render.py puts the branch in the report.
- the branch still exists, but no open pull request points at it: "pr"
  becomes null and the report leaves the branch out. The file stays.
  The branch can get a pull request again, and a snapshot costs a
  benchmark run to make.
- the branch is gone from its repository, or the whole fork is gone: the
  file goes away. There is nothing left for the snapshot to describe.
  The results of its commits stay where they are, because a result is
  keyed by machine and commit, not by branch.

Whether a branch still exists is a question for the remote, not for the
pull request list: a branch without a pull request is invisible in that
list, and a merged branch is often deleted. The script asks with git
ls-remote. When that call fails for a reason other than "no such branch"
or "no such repository", a network problem for example, the snapshot
stays as it is: a snapshot is worth more than a guess.

bench/push_branches.sh runs this against a worktree of main and pushes
the result.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from list_prs import by_head, load  # noqa: E402
from result_schema import validate_branch  # noqa: E402

TIMEOUT = 60


def is_gone(error):
    """Say whether a git error means that the repository is not there.

    GitHub answers a request for a repository that it does not have with
    "Repository not found", and it answers one that an anonymous reader
    may not see by asking for a user name. Every other error, a network
    problem for example, says nothing about the repository.
    """
    error = error.lower()
    return ("could not read username" in error
            or ("not found" in error and "repository" in error))


def branch_state(repo, ref):
    """Return "present", "gone" or "unknown" for one branch on GitHub."""
    url = f"https://github.com/{repo}.git"
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--heads", url, f"refs/heads/{ref}"],
            capture_output=True, text=True, timeout=TIMEOUT,
            # Without this, git waits for a user name on a repository
            # that it cannot read.
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired:
        print(f"prune_branches.py: ls-remote {repo} timed out", file=sys.stderr)
        return "unknown"
    if proc.returncode == 0:
        return "present" if proc.stdout.strip() else "gone"
    error = proc.stderr.strip()
    if is_gone(error):
        return "gone"
    print(f"prune_branches.py: ls-remote {repo} failed: {error}", file=sys.stderr)
    return "unknown"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--prs", required=True, type=Path)
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    heads = by_head(load(args.prs))
    shown = hidden = removed = kept = 0

    for path in sorted((args.root / "branches").glob("**/*.json")):
        snapshot = json.loads(path.read_text())
        repo, ref = snapshot.get("repo"), snapshot.get("ref")
        if not isinstance(repo, str) or not isinstance(ref, str):
            sys.exit(f"prune_branches.py: {path} has no repo or no ref")
        # A snapshot from before this field existed has no "pr" yet.
        was = snapshot.get("pr")
        pr = heads.get((repo, ref))

        if pr is None:
            state = branch_state(repo, ref)
            if state == "gone":
                print(f"prune_branches.py: {repo}@{ref} is gone, "
                      f"removing {path.relative_to(args.root)}")
                if not args.dry_run:
                    path.unlink()
                removed += 1
                continue
            if state == "unknown":
                print(f"prune_branches.py: {repo}@{ref} cannot be checked, "
                      f"leaving it alone")
                kept += 1
                continue
            hidden += 1
        else:
            shown += 1

        if pr == was and "pr" in snapshot:
            continue
        snapshot["pr"] = pr
        problems = validate_branch(snapshot)
        for problem in problems:
            print(f"{path}: {problem}", file=sys.stderr)
        if problems:
            return 1
        print(f"prune_branches.py: {repo}@{ref}: pr {was} -> {pr}")
        if not args.dry_run:
            with open(path, "w") as f:
                json.dump(snapshot, f, indent=2, sort_keys=True)
                f.write("\n")

    print(f"prune_branches.py: {shown} branch(es) with an open pull request, "
          f"{hidden} without one, {removed} removed, {kept} left alone")
    return 0


if __name__ == "__main__":
    sys.exit(main())

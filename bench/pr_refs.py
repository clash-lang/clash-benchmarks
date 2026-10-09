#!/usr/bin/env python3
"""Bring the commits of a pull request into a clash-compiler clone.

A pull request is not a branch of the repository that holds it: the
branch can live in a fork that the clone knows nothing about. GitHub
publishes the commits in the base repository instead, as
refs/pull/<n>/head.

Two scripts need those commits under a name of our own:
bench/pr_catchup.py, which decides what to benchmark, and
bench/pr_snapshots.py, which records the branch for the site. The name
lives here so that the two cannot drift apart.

A fetch is idempotent and forced, so it costs little to ask twice.

The branch point of a pull request is relative to the branch that it
targets. That is master for most of them, but a backport targets a
release branch, and the merge base with master would then pull in every
commit of that release branch. upstream_for() names the ref to measure
the branch point against.

This module is not a script.
"""

import subprocess
import sys


def pr_ref(number):
    """Return the local ref that holds the head commit of a pull request."""
    return f"refs/bench/pr/{number}"


def upstream_for(clash_repo, pr, default):
    """Return the ref of the branch that a pull request targets.

    The workflow fetches master and the release branches into
    refs/bench/upstream-<name>. A pull request into one of those gets
    that ref; any other target, a stacked pull request for example,
    gets the default.
    """
    ref = f"refs/bench/upstream-{pr.get('base_ref') or 'master'}"
    proc = subprocess.run(
        ["git", "-C", str(clash_repo), "rev-parse", "--verify", "--quiet",
         f"{ref}^{{commit}}"],
        capture_output=True, text=True,
    )
    return ref if proc.returncode == 0 else default


def fetch_pr(clash_repo, base_url, number):
    """Fetch the head of one pull request into the clone.

    Returns the local ref, or None when the fetch fails. One pull
    request that cannot be read must not stop the work on the others.
    """
    ref = pr_ref(number)
    proc = subprocess.run(
        ["git", "-C", str(clash_repo), "fetch", "--no-tags", "--force",
         base_url, f"refs/pull/{number}/head:{ref}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"pr_refs.py: cannot fetch #{number}: {proc.stderr.strip()}",
              file=sys.stderr)
        return None
    return ref

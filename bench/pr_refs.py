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

This module is not a script.
"""

import subprocess
import sys


def pr_ref(number):
    """Return the local ref that holds the head commit of a pull request."""
    return f"refs/bench/pr/{number}"


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

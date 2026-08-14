#!/usr/bin/env bash
# Prune the branch snapshots on the main branch of this repository.
#
# Usage: push_branches.sh <prs.json>
#
#   <prs.json>  open pull requests, from bench/list_prs.py
#
# Run this script in a clone of clash-benchmarks that has an
# authenticated origin remote, for example an actions/checkout
# workspace. It runs bench/prune_branches.py against a worktree of
# origin/main and pushes what that changes: the "pr" field of each
# snapshot, and the removal of the snapshots whose branch is gone.
#
# Nothing to change is a success. If the push is rejected, the script
# starts over from a new fetch, with three attempts maximum.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <prs.json>" >&2
  exit 1
fi

prs=$(realpath "$1")
script_dir=$(dirname "$(realpath "$0")")

wt=$(mktemp -d)
cleanup() {
  git worktree remove --force "${wt}" 2>/dev/null || true
  rm -rf "${wt}"
}
trap cleanup EXIT

for attempt in 1 2 3; do
  git fetch origin main
  git worktree remove --force "${wt}" 2>/dev/null || true
  git worktree add --detach "${wt}" FETCH_HEAD

  "${script_dir}/prune_branches.py" --prs "${prs}" --root "${wt}"

  # The pathspec matches the tracked files, so a snapshot that the
  # pruner removed is staged as a deletion. Removing a file leaves its
  # directory behind, so the test below only fails when main has no
  # branches/ at all, and then there is nothing to stage either.
  if [[ -d "${wt}/branches" ]]; then
    git -C "${wt}" add -A -- branches
  fi
  if git -C "${wt}" diff --cached --quiet; then
    echo "push_branches.sh: the branch snapshots are up to date"
    exit 0
  fi

  git -C "${wt}" \
    -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -q -m "Prune the branch snapshots"

  if git -C "${wt}" push origin HEAD:refs/heads/main; then
    echo "push_branches.sh: pushed the branch snapshots"
    exit 0
  fi

  echo "push_branches.sh: push rejected, retrying (attempt ${attempt}/3)" >&2
  sleep "${attempt}"
done

echo "push_branches.sh: giving up after 3 attempts" >&2
exit 1

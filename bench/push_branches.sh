#!/usr/bin/env bash
# Bring the branch snapshots on the main branch of this repository up to
# date with GitHub.
#
# Usage: push_branches.sh <prs.json> [<clash-dir>]
#
#   <prs.json>   open pull requests, from bench/list_prs.py
#   <clash-dir>  clash-compiler clone. With it, the script also records
#                the branch of every labelled pull request. Without it,
#                it only prunes.
#
# Run this script in a clone of clash-benchmarks that has an
# authenticated origin remote, for example an actions/checkout
# workspace. Against a worktree of origin/main it runs:
#
#   bench/pr_snapshots.py    the branch of each labelled pull request
#   bench/prune_branches.py  the "pr" field, and the branches that died
#
# and pushes the result as one commit. The clone must have the ref
# refs/bench/upstream-master, which is master of clash-lang/clash-compiler.
#
# Environment: BENCH_PR_LABEL is the label that asks for a benchmark.
#
# Nothing to change is a success. If the push is rejected, the script
# starts over from a new fetch, with three attempts maximum.

set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 <prs.json> [<clash-dir>]" >&2
  exit 1
fi

prs=$(realpath "$1")
clash_dir=${2:+$(realpath "$2")}
script_dir=$(dirname "$(realpath "$0")")

label_args=()
if [[ -n "${BENCH_PR_LABEL:-}" ]]; then
  label_args=(--label "${BENCH_PR_LABEL}")
fi

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

  if [[ -n "${clash_dir}" ]]; then
    "${script_dir}/pr_snapshots.py" \
      --clash-repo "${clash_dir}" \
      --prs "${prs}" \
      --root "${wt}" \
      "${label_args[@]}"
  fi
  "${script_dir}/prune_branches.py" --prs "${prs}" --root "${wt}"

  # -A stages a new snapshot, a changed one and a removed one alike.
  # Removing a file leaves its directory behind, so the test below only
  # fails when main has no branches/ and this run made none either, and
  # then there is nothing to stage.
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
    commit -q -m "Update the branch snapshots"

  if git -C "${wt}" push origin HEAD:refs/heads/main; then
    echo "push_branches.sh: pushed the branch snapshots"
    exit 0
  fi

  echo "push_branches.sh: push rejected, retrying (attempt ${attempt}/3)" >&2
  sleep "${attempt}"
done

echo "push_branches.sh: giving up after 3 attempts" >&2
exit 1

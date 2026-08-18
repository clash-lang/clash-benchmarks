#!/usr/bin/env bash
# Commit one benchmark result to the main branch of this repository.
#
# Usage: push_result.sh [--replace-skipped] <result.json> [<branch.json>]
#
# Run this script in a clone of clash-benchmarks that has an authenticated
# origin remote, for example an actions/checkout workspace. The script
# reads the target paths from the files:
#
#   <result.json>   goes to results/<machine>/<sha[0:2]>/<sha>.json
#   <branch.json>   goes to branches/<owner>/<repo>/<ref>.json
#
# The script refuses to push a result of a machine that has no file in
# machines/. Register the machine first: an unknown machine in the data
# gives graphs that nobody can interpret.
#
# An identical result that is already there is a success. A different
# result that is already there is an error. With --replace-skipped, a
# result with a skipped leg (normalization or wireDemo) is replaced
# instead. A backfill run upgrades earlier incomplete results this way.
#
# A branch snapshot always replaces the one that is there: the newest run
# has the newest view of the branch.
#
# If the push is rejected, the script fetches main again and retries, with
# three attempts maximum.

set -euo pipefail

replace_skipped=0
if [[ "${1:-}" == "--replace-skipped" ]]; then
  replace_skipped=1
  shift
fi

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 [--replace-skipped] <result.json> [<branch.json>]" >&2
  exit 1
fi

result=$(realpath "$1")
branch=${2:+$(realpath "$2")}

field() {
  python3 -c 'import json,sys
data = json.load(open(sys.argv[1]))
for key in sys.argv[2].split("."):
  data = data[key]
print(data)' "$@"
}

machine=$(field "${result}" machine)
sha=$(field "${result}" clash.commit)
rel="results/${machine}/${sha:0:2}/${sha}.json"

branch_rel=""
if [[ -n "${branch}" ]]; then
  branch_repo=$(field "${branch}" repo)
  branch_ref=$(field "${branch}" ref)
  branch_rel="branches/${branch_repo}/${branch_ref}.json"
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

  if [[ ! -f "${wt}/machines/${machine}.json" ]]; then
    echo "push_result.sh: machine ${machine} is not in machines/; register it first" >&2
    exit 1
  fi

  if [[ -f "${wt}/${rel}" ]]; then
    if cmp -s "${wt}/${rel}" "${result}"; then
      # Go on: the branch snapshot can still need an update.
      echo "push_result.sh: identical result for ${sha} already present"
    else
      old_wire=$(field "${wt}/${rel}" wire_demo.status)
      old_norm=$(field "${wt}/${rel}" normalization.status)
      if [[ ${replace_skipped} -eq 1 \
            && ( "${old_wire}" == "skipped" || "${old_norm}" == "skipped" ) ]]; then
        echo "push_result.sh: replacing result for ${sha} (a leg was skipped)"
      else
        echo "push_result.sh: different result for ${sha} already present; refusing to overwrite" >&2
        exit 1
      fi
    fi
  fi

  mkdir -p "${wt}/$(dirname "${rel}")"
  cp "${result}" "${wt}/${rel}"
  git -C "${wt}" add "${rel}"
  if [[ -n "${branch_rel}" ]]; then
    mkdir -p "${wt}/$(dirname "${branch_rel}")"
    cp "${branch}" "${wt}/${branch_rel}"
    git -C "${wt}" add "${branch_rel}"
  fi

  if git -C "${wt}" diff --cached --quiet; then
    echo "push_result.sh: nothing to commit for ${sha}"
    exit 0
  fi

  git -C "${wt}" \
    -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -q -m "Add benchmark result for ${sha:0:9} on ${machine}"

  if git -C "${wt}" push origin HEAD:refs/heads/main; then
    echo "push_result.sh: pushed ${rel}"
    exit 0
  fi

  echo "push_result.sh: push rejected, retrying (attempt ${attempt}/3)" >&2
  sleep "${attempt}"
done

echo "push_result.sh: giving up after 3 attempts" >&2
exit 1

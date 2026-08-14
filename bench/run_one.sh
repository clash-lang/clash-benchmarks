#!/usr/bin/env bash
# Benchmark one commit and push the result to the main branch.
#
# Usage: run_one.sh <clash-dir> <out-dir> <sha> <repo> <ref> <trigger>
#
#   <clash-dir>  clash-compiler checkout to benchmark. The directory must
#                have the name "clash-compiler", see run_bittide.sh.
#   <out-dir>    directory for the output of this commit
#   <sha>        commit to benchmark
#   <repo>       owner/name of the repository that holds the commit
#   <ref>        branch that the commit comes from
#   <trigger>    "schedule" or "dispatch"
#
# The script does all work for one commit: it checks the commit out, runs
# both benchmark legs, makes the result file, and pushes it. The workflow
# calls the script one time for each commit. A run that stops early keeps
# the results of the commits that are done.
#
# For a branch other than clash-lang master, the script also writes the
# branch snapshot. The clone must then have the ref
# refs/bench/upstream-master, which is master of
# clash-lang/clash-compiler. The workflow fetches it.
#
# Environment: see run_clash_benchmarks.sh, run_bittide.sh and
# collect_result.py. BENCH_QUICK=1 makes a quick, partial run.

set -euo pipefail

if [[ $# -ne 6 ]]; then
  echo "usage: $0 <clash-dir> <out-dir> <sha> <repo> <ref> <trigger>" >&2
  exit 1
fi

clash_dir=$(realpath "$1")
out_dir=$(realpath -m "$2")
sha=$3
repo=$4
ref=$5
trigger=$6

script_dir=$(dirname "$(realpath "$0")")
repo_root=$(dirname "${script_dir}")

mkdir -p "${out_dir}"

cd "${clash_dir}"
git checkout -f "${sha}"
# Keep the build artifacts. Cabal finds stale artifacts itself, and a
# catch-up run visits commits that are next to each other.
git clean -dfxq -e dist-newstyle -e '.ghc.environment.*'
git log -1 --format='run_one.sh: benchmarking %H %s'

"${script_dir}/run_clash_benchmarks.sh" "${out_dir}/norm.json"
"${script_dir}/run_bittide.sh" "${out_dir}/wiredemo.json"

"${script_dir}/collect_result.py" \
  --trigger "${trigger}" \
  --clash-repo "${repo}" \
  --clash-ref "${ref}" \
  --normalization "${out_dir}/norm.json" \
  --wire-demo "${out_dir}/wiredemo.json" \
  --out "${out_dir}/result.json"

snapshot=()
if [[ "${repo}/${ref}" != "clash-lang/clash-compiler/master" ]]; then
  "${script_dir}/branch_snapshot.py" \
    --repo "${repo}" \
    --ref "${ref}" \
    --upstream-ref refs/bench/upstream-master \
    --out-file "${out_dir}/branch.json"
  snapshot=("${out_dir}/branch.json")
fi

cd "${repo_root}"
"${script_dir}/push_result.sh" --replace-skipped \
  "${out_dir}/result.json" "${snapshot[@]}"

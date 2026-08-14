#!/usr/bin/env bash
# Build and run the in-repo normalization benchmark suite.
#
# Usage: run_clash_benchmarks.sh <output.json>
#
# Set the working directory to the root of the clash-compiler checkout that
# you benchmark. The default benchmark file list is relative to that root.
# This script can be in a different checkout. The script finds its sibling
# scripts relative to its own location.
#
# Environment:
#   BENCH_QUICK=1   run only one small benchmark (examples/FIR.hs)
#   THREADS         number of parallel GHC build jobs (default: effective CPUs)
#   CABAL_JOBS      number of parallel cabal package builds (default: THREADS)

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <output.json>" >&2
  exit 1
fi

out=$1
script_dir=$(dirname "$(realpath "$0")")

# effective_cpus.sh knows about cgroup limits, but it fails on machines
# without a cgroup CPU controller. Then use nproc.
THREADS=${THREADS:-$("${script_dir}/effective_cpus.sh" 2>/dev/null || nproc)}
CABAL_JOBS=${CABAL_JOBS:-${THREADS}}
export THREADS CABAL_JOBS

cabal v2-build -j"${CABAL_JOBS}" --ghc-options=-j"${THREADS}" \
  clash-benchmark:clash-benchmark-normalization

# Put the file arguments before the first dash argument. The benchmark
# gives all subsequent arguments to criterion.
# See benchmark/benchmark-normalization.hs.
files=()
if [[ "${BENCH_QUICK:-}" == "1" ]]; then
  files+=("examples/FIR.hs")
fi

mkdir -p "$(dirname "${out}")"
# Use "cabal v2-run", not the binary from "cabal list-bin". A direct start
# of the binary does not find the blackbox primitive definitions.
cabal v2-run clash-benchmark:clash-benchmark-normalization -- \
  "${files[@]}" --json "${out}"

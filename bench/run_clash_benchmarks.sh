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
# The script overwrites cabal.project.local in the checkout: the benchmark
# wants the Clash that users get, and the cabal.project of clash-compiler
# builds a debug one.
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

# The cabal.project of clash-compiler builds clash-lib with its debug
# flag, which adds checks and output that a user of a released Clash does
# not have. Turn it off. cabal reads cabal.project.local after
# cabal.project, and the last assignment of a flag wins. run_one.sh cleans
# the checkout before every run, so write the file every time. The wireDemo
# leg needs no counterpart: the freeze file of bittide-hardware already
# pins clash-lib without the flag.
printf 'package clash-lib\n  flags: -debug\n' > cabal.project.local

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
# "+RTS -T" lets criterion read the GC statistics, which puts the memory
# numbers into the report. The RTS takes the flags out of argv before the
# benchmark sees them.
cabal v2-run clash-benchmark:clash-benchmark-normalization -- \
  "${files[@]}" --json "${out}" +RTS -T -RTS

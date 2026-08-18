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
# A build or run failure is not an error: the commit under test can be
# broken. Then the output records the normalization leg as skipped and
# the script exits with code 0, so run_one.sh still stores a result and
# the catch-up logic does not pick the commit again. The build and the
# suite also run under a timeout, because a broken commit can make GHC
# or Clash hang.
#
# Environment:
#   BENCH_QUICK=1   run only one small benchmark (examples/FIR.hs)
#   THREADS         number of parallel GHC build jobs (default: effective CPUs)
#   CABAL_JOBS      number of parallel cabal package builds (default: THREADS)
#   BENCH_NORM_BUILD_TIMEOUT   seconds the build may take (default 3600)
#   BENCH_NORM_RUN_TIMEOUT     seconds the suite may take (default 1800)

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <output.json>" >&2
  exit 1
fi

out=$(realpath -m "$1")
script_dir=$(dirname "$(realpath "$0")")
mkdir -p "$(dirname "${out}")"

skip() {
  local reason=$1
  python3 - "$out" "$reason" <<'EOF'
import json, sys
json.dump({'status': 'skipped', 'skip_reason': sys.argv[2]},
          open(sys.argv[1], 'w'), indent=2)
EOF
  echo "run_clash_benchmarks.sh: skipped: ${reason}" >&2
}

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

# The timeouts turn a commit that makes GHC or Clash hang into a skipped
# leg instead of a stuck job. Without the --foreground option, timeout
# signals the whole process group, so the children of cabal die too.
# Exit code 124 means the time ran out; the KILL a minute later gives a
# different code and lands in the plain failure skip.
build_timeout="${BENCH_NORM_BUILD_TIMEOUT:-3600}"
run_timeout="${BENCH_NORM_RUN_TIMEOUT:-1800}"

build_status=0
timeout -k 60 "${build_timeout}" \
  cabal v2-build -j"${CABAL_JOBS}" --ghc-options=-j"${THREADS}" \
    clash-benchmark:clash-benchmark-normalization || build_status=$?
if [[ ${build_status} -eq 124 ]]; then
  skip "clash-benchmark build timed out after ${build_timeout}s"
  exit 0
elif [[ ${build_status} -ne 0 ]]; then
  skip "clash-benchmark does not build"
  exit 0
fi

# Put the file arguments before the first dash argument. The benchmark
# gives all subsequent arguments to criterion.
# See benchmark/benchmark-normalization.hs.
files=()
if [[ "${BENCH_QUICK:-}" == "1" ]]; then
  files+=("examples/FIR.hs")
fi

# Use "cabal v2-run", not the binary from "cabal list-bin". A direct start
# of the binary does not find the blackbox primitive definitions.
# "+RTS -T" lets criterion read the GC statistics, which puts the memory
# numbers into the report. The RTS takes the flags out of argv before the
# benchmark sees them. A skip below overwrites the partial criterion
# output of a run that stopped in the middle.
run_status=0
timeout -k 60 "${run_timeout}" \
  cabal v2-run clash-benchmark:clash-benchmark-normalization -- \
    "${files[@]}" --json "${out}" +RTS -T -RTS || run_status=$?
if [[ ${run_status} -eq 124 ]]; then
  skip "the normalization suite timed out after ${run_timeout}s"
  exit 0
elif [[ ${run_status} -ne 0 ]]; then
  skip "the normalization suite failed"
  exit 0
fi

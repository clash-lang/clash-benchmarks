#!/usr/bin/env bash
# Build bittide-hardware against the clash-compiler checkout under test
# and measure the Clash compilation time of wireDemoTest.
#
# Usage: run_bittide.sh <output.json>
#
# Set the working directory to the clash-compiler checkout under test.
# The directory must have the name "clash-compiler": the patched
# bittide-hardware cabal.project points to sibling directories with
# fixed names. The script clones bittide-hardware, clash-cores and
# clash-vexriscv next to it, at the revisions from the pin files in the
# bench directory of this repository, and applies the patches from
# bench/patches.
#
# A build failure is not an error: bittide-hardware does not always
# build against the newest clash-compiler. Then the output records the
# wireDemo leg as skipped and the script exits with code 0. The build
# and the HDL generation also run under a timeout, because a commit
# under test can make Clash hang (a normalization that does not
# terminate, for example). A timeout is a skip with its own reason: the
# stored result keeps the commit from being picked up again.
#
# Patch overlays: each directory bench/patches.d/<name>/ holds a
# file "applies-before" with one clash-compiler sha B, plus one
# directory per target repository (bittide-hardware, clash-cores or
# clash-vexriscv) with an extra git am series. The overlay applies when
# the checkout under test does not contain B. This repairs builds
# against clash commits that predate an API change. Keep overlays
# minimal: wireDemo results are compared across overlays.
#
# Environment:
#   BENCH_QUICK=1   do not build; write a skipped result
#   BENCH_BITTIDE_BUILD_TIMEOUT   seconds the build may take (default 3600)
#   BENCH_BITTIDE_RUN_TIMEOUT     seconds the wireDemo run may take
#                                 (default 900)

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <output.json>" >&2
  exit 1
fi

out=$(realpath -m "$1")
script_dir=$(dirname "$(realpath "$0")")
mkdir -p "$(dirname "${out}")"

bittide_rev=$(cat "${script_dir}/bittide-rev")
cores_rev=$(cat "${script_dir}/clash-cores-rev")
vexriscv_rev=$(cat "${script_dir}/clash-vexriscv-rev")

skip() {
  local reason=$1
  python3 - "$out" "$reason" "$bittide_rev" <<'EOF'
import json, sys
json.dump({'status': 'skipped', 'skip_reason': sys.argv[2],
           'bittide_rev': sys.argv[3], 'runs': []},
          open(sys.argv[1], 'w'), indent=2)
EOF
  echo "run_bittide.sh: skipped: ${reason}" >&2
}

if [[ "${BENCH_QUICK:-}" == "1" ]]; then
  skip "BENCH_QUICK is set"
  exit 0
fi

if [[ "$(basename "$(pwd)")" != "clash-compiler" ]]; then
  echo "run_bittide.sh: the working directory must be named clash-compiler" >&2
  exit 1
fi
ws=$(dirname "$(pwd)")

# Get a repository at an exact revision, in a directory next to the
# clash-compiler checkout. Reuse an existing clone when there is one.
checkout_pin() {
  local url=$1 dir=$2 rev=$3
  if [[ ! -d "${ws}/${dir}/.git" ]]; then
    git clone "${url}" "${ws}/${dir}"
  fi
  git -C "${ws}/${dir}" rev-parse --verify --quiet "${rev}^{commit}" \
    || git -C "${ws}/${dir}" fetch origin "${rev}"
  git -C "${ws}/${dir}" checkout -f "${rev}"
  # Keep the build artifacts. Cabal detects stale artifacts itself, and
  # backfill runs visit many adjacent commits.
  git -C "${ws}/${dir}" clean -dfxq -e dist-newstyle -e '.ghc.environment.*'
}

checkout_pin https://github.com/bittide/bittide-hardware.git bittide-hardware "${bittide_rev}"
checkout_pin https://github.com/clash-lang/clash-cores.git clash-cores "${cores_rev}"
checkout_pin https://github.com/clash-lang/clash-vexriscv.git clash-vexriscv "${vexriscv_rev}"

apply_patches() {
  local repo=$1
  shift
  git -C "${ws}/${repo}" \
    -c user.name="clash-benchmark-bot" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    am "$@"
}

apply_patches bittide-hardware "${script_dir}"/patches/*.patch

# Apply the overlays whose API-change commit is not in this checkout.
overlays=""
for overlay in "${script_dir}"/patches.d/*/; do
  [[ -d "${overlay}" ]] || continue
  name=$(basename "${overlay}")
  before=$(cat "${overlay}/applies-before")
  if ! git merge-base --is-ancestor "${before}" HEAD; then
    echo "run_bittide.sh: applying overlay ${name} (checkout predates ${before:0:7})"
    for repo_dir in "${overlay}"*/; do
      [[ -d "${repo_dir}" ]] || continue
      apply_patches "$(basename "${repo_dir}")" "${repo_dir}"*.patch
    done
    overlays="${overlays} ${name}"
  fi
done

# A commit under test can make the build or the HDL generation hang,
# with a normalization that does not terminate for example. Without the
# --foreground option, timeout signals the whole process group, so the
# clash child of cabal dies too and does not stay behind to disturb the
# next measurement. Exit code 124 means the time ran out; the KILL a
# minute later gives a different code and lands in the plain failure
# skip, which is still a stored result.
build_timeout="${BENCH_BITTIDE_BUILD_TIMEOUT:-3600}"
run_timeout="${BENCH_BITTIDE_RUN_TIMEOUT:-900}"

build_log="${out%.json}-build.log"
echo "run_bittide.sh: building bittide-instances:exe:clash (log: ${build_log})"
build_status=0
(cd "${ws}/bittide-hardware" \
  && timeout -k 60 "${build_timeout}" \
       cabal build bittide-instances:exe:clash) &> "${build_log}" \
  || build_status=$?
if [[ ${build_status} -eq 124 ]]; then
  skip "bittide-hardware build timed out after ${build_timeout}s"
  exit 0
elif [[ ${build_status} -ne 0 ]]; then
  tail -n 50 "${build_log}" >&2
  skip "bittide-hardware does not build"
  exit 0
fi

hdl_dir=$(mktemp -d)
run_log="${out%.json}-run.log"
echo "run_bittide.sh: running wireDemoTest (log: ${run_log})"
# "+RTS -t" prints one line of GC statistics to stderr when the process
# exits, into the run log that the parser below reads. "-N4" gives Clash
# four capabilities, for the concurrent normalization of
# clash-compiler#3196; on a commit without it the flag changes nothing
# but the parallel GC. The clash executable of bittide-instances is
# built with -threaded but without -rtsopts; both flags are among the
# safe RTS flags that such a binary still accepts (-N only up to the
# processor count of the machine, see rts/RtsFlags.c in GHC).
run_status=0
(cd "${ws}/bittide-hardware" \
  && timeout -k 60 "${run_timeout}" \
       cabal run --offline bittide-instances:clash -- \
         Bittide.Instances.Hitl.WireDemo.TopEntity \
         -fclash-hdldir "${hdl_dir}" -main-is wireDemoTest \
         --verilog -fclash-clear -fclash-spec-limit=100 \
         +RTS -t -N4 -RTS) &> "${run_log}" \
  || run_status=$?
if [[ ${run_status} -eq 124 ]]; then
  skip "wireDemo HDL generation timed out after ${run_timeout}s"
  rm -rf "${hdl_dir}"
  exit 0
elif [[ ${run_status} -ne 0 ]]; then
  tail -n 50 "${run_log}" >&2
  # The build was fine, so the PR probably broke HDL generation for
  # bittide. Record this as a skip with its own reason.
  skip "wireDemo HDL generation failed"
  rm -rf "${hdl_dir}"
  exit 0
fi
rm -rf "${hdl_dir}"

# Parse the three unconditional "Clash: ... took <time>" lines. The time
# format is [Nd][Nh][Nm]N[.fff]s (see reportTimeDiff in clash-lib). Also
# parse the "<<ghc: ... :ghc>>" line that "+RTS -t" prints on exit (see
# report_one_line in the GHC RTS).
if ! python3 - "$out" "$run_log" "$bittide_rev" ${overlays} <<'EOF'
import json
import re
import sys

TIME_RE = re.compile(
  r'(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(\d+(?:\.\d+)?)s$')

def to_seconds(text):
  m = TIME_RE.match(text)
  if not m:
    sys.exit(f'run_bittide.sh: cannot parse time {text!r}')
  d, h, mins, s = m.groups()
  return (int(d or 0) * 86400 + int(h or 0) * 3600
          + int(mins or 0) * 60 + float(s))

GHC_RE = re.compile(
  r'<<ghc: (\d+) bytes, (\d+) GCs, '
  r'(\d+)/(\d+) avg/max bytes residency \((\d+) samples\), '
  r'(\d+)M in use, (-?[\d.]+) INIT \((-?[\d.]+) elapsed\), '
  r'(-?[\d.]+) MUT \((-?[\d.]+) elapsed\), '
  r'(-?[\d.]+) GC \((-?[\d.]+) elapsed\) :ghc>>')

def seconds(text):
  # The RTS can print a tiny negative time as -0.000.
  return max(0.0, float(text))

wanted = {
  'Clash: Normalization took ': 'normalization_s',
  'Clash: Netlist generation took ': 'netlist_s',
  'Clash: Total compilation took ': 'total_s',
}
run = {}
for line in open(sys.argv[2]):
  # Remove ANSI color codes. Clash glues a color reset to the start of
  # some lines.
  line = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
  for prefix, key in wanted.items():
    if line.startswith(prefix):
      run[key] = to_seconds(line[len(prefix):])
  m = GHC_RE.search(line)
  if m:
    run.update({
      'alloc_bytes': int(m.group(1)),
      'num_gcs': int(m.group(2)),
      'max_live_bytes': int(m.group(4)),
      'peak_mb': int(m.group(6)),
      'mut_cpu_s': seconds(m.group(9)),
      'mut_wall_s': seconds(m.group(10)),
      'gc_cpu_s': seconds(m.group(11)),
      'gc_wall_s': seconds(m.group(12)),
    })

needed = set(wanted.values()) | {
  'alloc_bytes', 'num_gcs', 'max_live_bytes', 'peak_mb',
  'mut_cpu_s', 'mut_wall_s', 'gc_cpu_s', 'gc_wall_s'}
missing = needed - set(run)
if missing:
  sys.exit(f'run_bittide.sh: missing in log: {sorted(missing)}')

json.dump({'status': 'ok', 'skip_reason': None,
           'bittide_rev': sys.argv[3], 'overlays': sys.argv[4:],
           'runs': [run]},
          open(sys.argv[1], 'w'), indent=2)
print(f'run_bittide.sh: {run}')
EOF
then
  skip "wireDemo output could not be parsed"
  exit 0
fi

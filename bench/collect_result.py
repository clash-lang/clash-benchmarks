#!/usr/bin/env python3
"""Combine the output of the benchmark legs into one result file.

Usage:
  collect_result.py --trigger TRIGGER --clash-repo REPO --clash-ref REF \
      --normalization norm.json --wire-demo wiredemo.json --out result.json

  --trigger      "schedule", "dispatch" or "migration"
  --clash-repo   owner/name of the repository under test
  --clash-ref    branch that was benchmarked, for example "master"
  --normalization  criterion --json output of run_clash_benchmarks.sh
  --wire-demo    output of run_bittide.sh, or "none" when the leg did not run
  --out          path of the result file

Run the script with the benchmarked clash-compiler checkout as the working
directory: it reads the commit metadata from git.

Environment:
  BENCH_MACHINE     machine id. The default comes from RUNNER_NAME.
  BENCH_CONTAINER   container image of the run, if there is one
  BENCH_QUICK       1 marks the result as a quick, partial run
  GITHUB_*          GitHub Actions gives the link to the workflow run

The result format is schema version 3, see bench/result_schema.py.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from result_schema import SCHEMA_VERSION, machine_id, now, validate_result  # noqa: E402

# Criterion names each benchmark after the source file. Remove the prefix.
NAME_PREFIX = "normalization of "

WIRE_DEMO_NONE = {
    "status": "skipped",
    "skip_reason": "the wireDemo leg did not run",
    "bittide_rev": None,
    "overlays": [],
    "runs": [],
}


def git(*cmd):
    """Run one git command and return its stdout."""
    res = subprocess.run(["git", *cmd], capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"collect_result.py: git {' '.join(cmd)} failed: {res.stderr.strip()}")
    return res.stdout.strip()


def criterion_reports(path):
    """Extract the list of reports from the criterion --json output.

    The file holds a JSON array. The last element is the report list. Do
    not depend on the exact shape: find the element that looks like a
    list of reports.
    """
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        for element in reversed(data):
            if (isinstance(element, list)
                    and all(isinstance(r, dict) and "reportName" in r for r in element)):
                return element
    sys.exit(f"collect_result.py: unrecognized criterion JSON in {path}")


def measured_means(report, name):
    """Read the memory numbers of one benchmark, per run.

    Criterion writes the raw measurements as rows whose column order is in
    reportKeys. Each row covers a batch of runs (iters), so the number for
    one run is the total of a column over the whole series divided by the
    total number of runs. The GC columns are null when the benchmark did
    not run with "+RTS -T".
    """
    keys = report.get("reportKeys")
    rows = report.get("reportMeasured")
    if not keys or not rows:
        sys.exit(f"collect_result.py: no measurements for {name}")
    wanted = {"allocated": "alloc_bytes",
              "mutatorWallSeconds": "mut_wall_s",
              "gcWallSeconds": "gc_wall_s"}
    try:
        iters = keys.index("iters")
        columns = {out: keys.index(key) for key, out in wanted.items()}
    except ValueError as missing:
        sys.exit(f"collect_result.py: {name}: {missing} not in reportKeys")
    runs = sum(row[iters] for row in rows)
    means = {}
    for out, column in columns.items():
        values = [row[column] for row in rows]
        if any(value is None for value in values):
            sys.exit(f"collect_result.py: {name} has no GC statistics; "
                     "did the benchmark run with +RTS -T?")
        means[out] = sum(values) / runs
    return means


def normalization(path):
    """Read the time and memory of each benchmark."""
    results = {}
    for report in criterion_reports(path):
        name = report["reportName"]
        if name.startswith(NAME_PREFIX):
            name = name[len(NAME_PREFIX):]
        analysis = report["reportAnalysis"]
        results[name] = {
            "mean_s": analysis["anMean"]["estPoint"],
            "stddev_s": analysis["anStdDev"]["estPoint"],
            **measured_means(report, name),
        }
    if not results:
        sys.exit(f"collect_result.py: no benchmark reports found in {path}")
    return results


def cpu_model():
    """Return the CPU model of this machine, for the log."""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def ghc_version():
    """Return the version of GHC that built the benchmark."""
    res = subprocess.run(["ghc", "--numeric-version"], capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit("collect_result.py: ghc --numeric-version failed")
    return res.stdout.strip()


def workflow_run_url():
    """Return the link to the workflow run, or None outside of Actions."""
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--trigger", required=True,
                        choices=("schedule", "dispatch", "migration"))
    parser.add_argument("--clash-repo", required=True)
    parser.add_argument("--clash-ref", required=True)
    parser.add_argument("--normalization", required=True)
    parser.add_argument("--wire-demo", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    if args.wire_demo == "none":
        wire_demo = WIRE_DEMO_NONE
    else:
        with open(args.wire_demo) as f:
            wire_demo = json.load(f)
        wire_demo.setdefault("overlays", [])
        wire_demo.setdefault("skip_reason", None)
        wire_demo.setdefault("bittide_rev", None)
        wire_demo.setdefault("runs", [])

    subject = git("log", "-1", "--format=%s")
    date = git("log", "-1", "--format=%cs")
    result = {
        "schema_version": SCHEMA_VERSION,
        "machine": machine_id(),
        "run": {
            "date": now(),
            "trigger": args.trigger,
            "quick": os.environ.get("BENCH_QUICK") == "1",
            "workflow_run_url": workflow_run_url(),
        },
        "clash": {
            "repo": args.clash_repo,
            "ref": args.clash_ref,
            "commit": git("rev-parse", "HEAD"),
            "parents": git("rev-list", "--parents", "-n1", "HEAD").split()[1:],
            "subject": subject,
            "committer_date": date,
        },
        "toolchain": {
            "ghc_version": ghc_version(),
            "container": os.environ.get("BENCH_CONTAINER") or None,
        },
        "normalization": normalization(args.normalization),
        "wire_demo": wire_demo,
    }

    problems = validate_result(result)
    for problem in problems:
        print(f"collect_result.py: {problem}", file=sys.stderr)
    if problems:
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"collect_result.py: wrote {args.out} for machine "
          f"{result['machine']} ({cpu_model()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

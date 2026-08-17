#!/usr/bin/env python3
"""Shape of a benchmark result file, schema version 3.

A result file holds the measurements of one benchmark run: one machine,
one clash-compiler commit. The file name comes from the machine and the
commit, see result_path(). The layout of the file is:

    {
      "schema_version": 3,
      "machine": "oele",
      "run": {
        "date": "2026-08-13T08:12:24+00:00",
        "trigger": "schedule" | "dispatch" | "migration",
        "quick": false,
        "workflow_run_url": "https://github.com/.../actions/runs/123" | null
      },
      "clash": {
        "repo": "clash-lang/clash-compiler",
        "ref": "master",
        "commit": "<40 hex>",
        "parents": ["<40 hex>"],
        "subject": "Merge pull request #3331 from ...",
        "committer_date": "2026-08-11"
      },
      "toolchain": {"ghc_version": "9.10.3", "container": "ghcr.io/..." | null},
      "normalization": {"examples/FIR.hs": {
        "mean_s": 1.2, "stddev_s": 0.01,
        "alloc_bytes": 2.1e9, "mut_wall_s": 1.1, "gc_wall_s": 0.1
      }},
      "wire_demo": {
        "status": "ok" | "skipped",
        "skip_reason": null | "...",
        "bittide_rev": "<40 hex>" | null,
        "overlays": ["0001-quickcheck-bound"],
        "runs": [{
          "normalization_s": 291.0, "netlist_s": 1.8, "total_s": 311.0,
          "alloc_bytes": 1.9e12, "max_live_bytes": 2.3e9, "peak_mb": 7000,
          "num_gcs": 1200, "mut_cpu_s": 250.0, "mut_wall_s": 240.0,
          "gc_cpu_s": 80.0, "gc_wall_s": 70.0
        }]
      }
    }

The "normalization" and "wire_demo" parts are the measurements. The other
parts say what was measured, on which machine, and with which toolchain.
Hardware facts are not in the result: they are in machines/<machine>.json.

The memory numbers come from the GHC runtime. In "normalization" they are
means over one run of the benchmark; in "wire_demo" they cover the whole
clash process. "alloc_bytes" is the total allocation, "max_live_bytes" the
largest live heap that a major collection saw, and "peak_mb" the most
memory (in MiB) the process ever took from the OS. The MUT and GC times
split the runtime into work and collection, in CPU and in wall seconds.

The second kind of file is a branch snapshot. A branch does not stay where
it is, so the graphs read the commits of a branch from here and not from a
clone. The path comes from the repository and the branch, see
branch_path(). The layout is:

    {
      "repo": "someone/clash-compiler",
      "ref": "perf/faster-strings",
      "base": "<40 hex>",
      "pr": 3345 | null,
      "updated": "2026-08-13T08:12:24+00:00",
      "commits": [{"sha": "<40 hex>", "subject": "...", "date": "2026-08-11"}]
    }

"base" is the branch point on clash-lang master; "commits" is the
first-parent chain from there to the tip of the branch, oldest first.
"pr" is the open pull request that has this branch as its head, and null
when there is none. render.py shows the branches that have one, and only
those. See bench/branch_snapshot.py and bench/prune_branches.py.

Use this module as a script to validate files of either kind:

    result_schema.py results/volthe/0d/0d32dde3....json
"""

import datetime
import json
import os
import re
import socket
import sys

SCHEMA_VERSION = 3

TRIGGERS = ("schedule", "dispatch", "migration")
WIRE_DEMO_STATUSES = ("ok", "skipped")
NORMALIZATION_KEYS = ("mean_s", "stddev_s", "alloc_bytes", "mut_wall_s",
                      "gc_wall_s")
RUN_KEYS = ("normalization_s", "netlist_s", "total_s", "alloc_bytes",
            "max_live_bytes", "peak_mb", "num_gcs", "mut_cpu_s",
            "mut_wall_s", "gc_cpu_s", "gc_wall_s")

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def now(precision="seconds"):
    """Return the time now, in UTC, as a text timestamp."""
    return (datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec=precision))


def slugify(name):
    """Make a machine id from a free-form name.

    Machine ids are directory names, so keep them simple: lower case,
    with hyphens instead of the other characters.
    """
    slug = re.sub(r"[^a-z0-9._-]+", "-", name.strip().lower()).strip("-._")
    return slug or "unknown"


def machine_id():
    """Return the id of the machine that runs the benchmark.

    In a container job the hostname is an ephemeral container id, so
    prefer the two names that come from the environment.
    """
    name = (os.environ.get("BENCH_MACHINE")
            or os.environ.get("RUNNER_NAME")
            or socket.gethostname())
    return slugify(name)


def result_path(machine, sha):
    """Return the path of one result file, relative to the repository."""
    return f"results/{machine}/{sha[:2]}/{sha}.json"


def branch_path(repo, ref):
    """Return the path of one branch snapshot, relative to the repository."""
    return f"branches/{repo}/{ref}.json"


def _num(problems, where, value, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        problems.append(f"{where}: not a number: {value!r}")
    elif minimum is not None and value < minimum:
        problems.append(f"{where}: less than {minimum}: {value!r}")


def _keys(problems, where, obj, required):
    if not isinstance(obj, dict):
        problems.append(f"{where}: not an object")
        return False
    missing = sorted(set(required) - set(obj))
    extra = sorted(set(obj) - set(required))
    if missing:
        problems.append(f"{where}: missing keys: {', '.join(missing)}")
    if extra:
        problems.append(f"{where}: unknown keys: {', '.join(extra)}")
    return not missing


def validate_result(result):
    """Return the problems of one result. An empty list means it is good."""
    problems = []
    if not _keys(problems, "result", result, (
            "schema_version", "machine", "run", "clash", "toolchain",
            "normalization", "wire_demo")):
        return problems

    if result["schema_version"] != SCHEMA_VERSION:
        problems.append(
            f"schema_version: expected {SCHEMA_VERSION}, "
            f"got {result['schema_version']!r}")

    machine = result["machine"]
    if not isinstance(machine, str) or not SLUG_RE.match(machine):
        problems.append(f"machine: not a slug: {machine!r}")

    run = result["run"]
    if _keys(problems, "run", run, ("date", "trigger", "quick", "workflow_run_url")):
        if not isinstance(run["date"], str) or not run["date"]:
            problems.append("run.date: not a timestamp")
        if run["trigger"] not in TRIGGERS:
            problems.append(f"run.trigger: not one of {TRIGGERS}: {run['trigger']!r}")
        if not isinstance(run["quick"], bool):
            problems.append("run.quick: not a boolean")
        url = run["workflow_run_url"]
        if url is not None and not isinstance(url, str):
            problems.append("run.workflow_run_url: not a string or null")

    clash = result["clash"]
    if _keys(problems, "clash", clash, (
            "repo", "ref", "commit", "parents", "subject", "committer_date")):
        if not isinstance(clash["repo"], str) or not REPO_RE.match(clash["repo"]):
            problems.append(f"clash.repo: not owner/name: {clash['repo']!r}")
        if not isinstance(clash["ref"], str) or not clash["ref"]:
            problems.append("clash.ref: empty")
        if not isinstance(clash["commit"], str) or not SHA_RE.match(clash["commit"]):
            problems.append(f"clash.commit: not a full sha: {clash['commit']!r}")
        if not isinstance(clash["parents"], list) or not all(
                isinstance(p, str) and SHA_RE.match(p) for p in clash["parents"]):
            problems.append("clash.parents: not a list of full shas")
        if not isinstance(clash["subject"], str):
            problems.append("clash.subject: not a string")
        if not isinstance(clash["committer_date"], str) or not DATE_RE.match(
                clash["committer_date"]):
            problems.append(
                f"clash.committer_date: not YYYY-MM-DD: {clash['committer_date']!r}")

    toolchain = result["toolchain"]
    if _keys(problems, "toolchain", toolchain, ("ghc_version", "container")):
        if not isinstance(toolchain["ghc_version"], str) or not toolchain["ghc_version"]:
            problems.append("toolchain.ghc_version: empty")
        if toolchain["container"] is not None and not isinstance(toolchain["container"], str):
            problems.append("toolchain.container: not a string or null")

    norm = result["normalization"]
    if not isinstance(norm, dict) or not norm:
        problems.append("normalization: not a non-empty object")
    else:
        for name, entry in norm.items():
            where = f"normalization[{name!r}]"
            if _keys(problems, where, entry, NORMALIZATION_KEYS):
                for key in NORMALIZATION_KEYS:
                    _num(problems, f"{where}.{key}", entry[key], minimum=0)

    wd = result["wire_demo"]
    if _keys(problems, "wire_demo", wd, (
            "status", "skip_reason", "bittide_rev", "overlays", "runs")):
        if wd["status"] not in WIRE_DEMO_STATUSES:
            problems.append(
                f"wire_demo.status: not one of {WIRE_DEMO_STATUSES}: {wd['status']!r}")
        if wd["skip_reason"] is not None and not isinstance(wd["skip_reason"], str):
            problems.append("wire_demo.skip_reason: not a string or null")
        if wd["bittide_rev"] is not None and (
                not isinstance(wd["bittide_rev"], str) or not SHA_RE.match(wd["bittide_rev"])):
            problems.append(f"wire_demo.bittide_rev: not a full sha: {wd['bittide_rev']!r}")
        if not isinstance(wd["overlays"], list) or not all(
                isinstance(o, str) for o in wd["overlays"]):
            problems.append("wire_demo.overlays: not a list of strings")
        if not isinstance(wd["runs"], list):
            problems.append("wire_demo.runs: not a list")
        else:
            for i, run in enumerate(wd["runs"]):
                if _keys(problems, f"wire_demo.runs[{i}]", run, RUN_KEYS):
                    for key in RUN_KEYS:
                        _num(problems, f"wire_demo.runs[{i}].{key}", run[key], minimum=0)
            if wd["status"] == "ok" and not wd["runs"]:
                problems.append("wire_demo: status is ok but there are no runs")
            if wd["status"] == "skipped":
                if wd["runs"]:
                    problems.append("wire_demo: status is skipped but there are runs")
                if not wd["skip_reason"]:
                    problems.append("wire_demo: status is skipped without a skip_reason")

    return problems


def validate_branch(snapshot):
    """Return the problems of one branch snapshot."""
    problems = []
    if not _keys(problems, "branch", snapshot,
                 ("repo", "ref", "base", "pr", "updated", "commits")):
        return problems
    if not isinstance(snapshot["repo"], str) or not REPO_RE.match(snapshot["repo"]):
        problems.append(f"repo: not owner/name: {snapshot['repo']!r}")
    if not isinstance(snapshot["ref"], str) or not snapshot["ref"]:
        problems.append("ref: empty")
    if not isinstance(snapshot["base"], str) or not SHA_RE.match(snapshot["base"]):
        problems.append(f"base: not a full sha: {snapshot['base']!r}")
    pr = snapshot["pr"]
    if pr is not None and (isinstance(pr, bool) or not isinstance(pr, int) or pr < 1):
        problems.append(f"pr: not a pull request number or null: {pr!r}")
    if not isinstance(snapshot["updated"], str) or not snapshot["updated"]:
        problems.append("updated: not a timestamp")
    if not isinstance(snapshot["commits"], list) or not snapshot["commits"]:
        problems.append("commits: not a non-empty list")
        return problems
    for i, commit in enumerate(snapshot["commits"]):
        where = f"commits[{i}]"
        if _keys(problems, where, commit, ("sha", "subject", "date")):
            if not SHA_RE.match(commit["sha"]):
                problems.append(f"{where}.sha: not a full sha: {commit['sha']!r}")
            if not isinstance(commit["subject"], str):
                problems.append(f"{where}.subject: not a string")
            if not DATE_RE.match(commit.get("date", "")):
                problems.append(f"{where}.date: not YYYY-MM-DD: {commit.get('date')!r}")
    return problems


def main(argv):
    bad = 0
    for path in argv:
        with open(path) as f:
            data = json.load(f)
        if "commits" in data:
            problems = validate_branch(data)
        else:
            problems = validate_result(data)
        for problem in problems:
            print(f"{path}: {problem}", file=sys.stderr)
        bad += bool(problems)
    print(f"{len(argv) - bad} of {len(argv)} files are good")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

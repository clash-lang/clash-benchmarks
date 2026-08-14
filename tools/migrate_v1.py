#!/usr/bin/env python3
"""Convert schema v1 results from the clash-compiler fork to schema v2.

The benchmark bot first ran in the martijnbastiaan/clash-compiler fork. It
stored schema v1 results on the orphan branch benchmark-results, one file
for each commit: results/<sha[0:2]>/<sha>.json. This script reads those
files and writes schema v2 results into this repository.

Usage:
  migrate_v1.py --fork-repo PATH --clash-repo PATH [options]

  --fork-repo PATH    clone of the fork. It holds the v1 data branch and
                      the branches of the commits that are not upstream.
  --clash-repo PATH   clone of clash-lang/clash-compiler. The script asks
                      it which commits are on upstream master.
  --data-ref REF      ref of the v1 data (default origin/benchmark-results)
  --upstream-ref REF  ref of upstream master (default origin/master)
  --machine ID        machine id for all results (default volthe)
  --out PATH          repository root to write to (default: this repository)
  --dry-run           show what the script does; write nothing

Each v1 commit becomes one of three things:

- a master result, if the commit is on upstream master;
- a branch result, if the commit is on a branch of the fork. The script
  also writes the branch snapshot, see bench/result_schema.py;
- nothing, if the commit only exists on the master branch of the fork.
  Those are commits of the bot itself, not of the compiler.

The script is repeatable: it writes the same files each time it runs.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))

from result_schema import (  # noqa: E402
    SCHEMA_VERSION, branch_path, result_path, validate_branch, validate_result,
)

# The machine that produced all v1 results. The v1 files hold the CPU
# model; the facts below are not in them.
MACHINE_EXTRA = {
    "threads": 16,
    "ram_gib": 32,
    "default": True,
    "notes": (
        "Dedicated benchmark runner. GitHub Actions labels: self-hosted, "
        "benchmark."
    ),
}

# How far back the script looks for the branch point of a branch.
MAX_BRANCH_LENGTH = 200


def git(repo, *args):
    """Run one git command in a repository and return its stdout."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return proc.stdout


def git_ok(repo, *args):
    """Run one git command and return True when it succeeds."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    ).returncode == 0


def repo_slug(repo):
    """Return the owner/name of the origin remote of a clone."""
    url = git(repo, "remote", "get-url", "origin").strip()
    match = re.search(r"[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?$", url)
    if not match:
        sys.exit(f"migrate_v1.py: cannot read owner/name from {url!r}")
    return f"{match.group(1)}/{match.group(2)}"


def commit_meta(repo, sha):
    """Return the subject, the committer date and the parents of a commit."""
    out = git(repo, "log", "-1", "--format=%s%n%cs%n%P", sha).splitlines()
    subject, date = out[0], out[1]
    parents = out[2].split() if len(out) > 2 else []
    return subject, date, parents


def v1_results(fork_repo, data_ref):
    """Read all v1 results from the data branch, oldest date first."""
    names = git(fork_repo, "ls-tree", "-r", "--name-only", data_ref).split()
    results = []
    for name in names:
        if not name.startswith("results/") or not name.endswith(".json"):
            continue
        results.append(json.loads(git(fork_repo, "show", f"{data_ref}:{name}")))
    results.sort(key=lambda r: r["date"])
    return results


def branch_of(fork_repo, sha):
    """Return the fork branch that holds a commit, or None.

    The master branch of the fork does not count: a commit that only sits
    there is a commit of the bot, and there is nothing to compare it to.
    """
    out = git(
        fork_repo, "for-each-ref", "--contains", sha,
        "--format=%(refname:short)", "refs/remotes/origin",
    ).split()
    branches = [
        name[len("origin/"):] for name in out
        if name.startswith("origin/") and name not in ("origin/master", "origin/HEAD")
    ]
    # A commit can be on more than one branch. The shortest name is the
    # branch that the benchmark ran for.
    return min(branches, key=lambda n: (len(n), n)) if branches else None


def is_upstream(clash_repo, sha, upstream_ref):
    """Say if a commit is on upstream master."""
    if not git_ok(clash_repo, "cat-file", "-e", f"{sha}^{{commit}}"):
        return False
    return git_ok(clash_repo, "merge-base", "--is-ancestor", sha, upstream_ref)


def is_quick(v1):
    """Say if a v1 result comes from a quick run.

    A quick run measures one file and skips the wireDemo leg. It is a
    partial result: the numbers are good, but most benchmarks are absent.
    """
    wd = v1["wire_demo"]
    if wd.get("skip_reason") == "BENCH_QUICK is set":
        return True
    return len(v1["normalization"]) == 1 and wd["status"] == "skipped"


def to_v2(v1, machine, repo, ref, subject, date, parents):
    """Build a v2 result from a v1 result and its commit metadata."""
    wd = v1["wire_demo"]
    return {
        "schema_version": SCHEMA_VERSION,
        "machine": machine,
        "run": {
            "date": v1["date"],
            "trigger": "migration",
            "quick": is_quick(v1),
            "workflow_run_url": None,
        },
        "clash": {
            "repo": repo,
            "ref": ref,
            "commit": v1["clash_commit"],
            "parents": parents,
            "subject": subject,
            "committer_date": date,
        },
        "toolchain": {
            "ghc_version": v1["ghc_version"],
            "container": v1["machine"].get("container"),
        },
        "normalization": v1["normalization"],
        "wire_demo": {
            "status": wd["status"],
            "skip_reason": wd.get("skip_reason"),
            "bittide_rev": wd.get("bittide_rev"),
            "overlays": wd.get("overlays") or [],
            "runs": wd.get("runs") or [],
        },
    }


def branch_snapshot(fork_repo, clash_repo, args, repo, ref, updated):
    """Build the commit snapshot of a fork branch.

    The base is the newest commit of the branch that is also on upstream
    master. The commits are the first-parent chain from the base to the
    tip of the branch, oldest first.
    """
    chain = git(
        fork_repo, "rev-list", "--first-parent",
        f"--max-count={MAX_BRANCH_LENGTH}", f"origin/{ref}",
    ).split()
    own = []
    base = None
    for sha in chain:
        if is_upstream(clash_repo, sha, args.upstream_ref):
            base = sha
            break
        own.append(sha)
    if base is None:
        sys.exit(f"migrate_v1.py: no branch point for {ref} in {MAX_BRANCH_LENGTH} commits")
    commits = []
    for sha in reversed(own):
        subject, date, _ = commit_meta(fork_repo, sha)
        commits.append({"sha": sha, "subject": subject, "date": date})
    return {
        "repo": repo,
        "ref": ref,
        "base": base,
        "updated": updated,
        "commits": commits,
    }


def write_json(path, data, dry_run):
    """Write one JSON file, with a stable layout."""
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if dry_run:
        print(f"would write {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--fork-repo", required=True, type=Path)
    parser.add_argument("--clash-repo", required=True, type=Path)
    parser.add_argument("--data-ref", default="origin/benchmark-results")
    parser.add_argument("--upstream-ref", default="origin/master")
    parser.add_argument("--machine", default="volthe")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    fork_slug = repo_slug(args.fork_repo)
    upstream_slug = repo_slug(args.clash_repo)

    results = v1_results(args.fork_repo, args.data_ref)
    print(f"read {len(results)} v1 results from {args.fork_repo}:{args.data_ref}")

    written = 0
    dropped = []
    branch_dates = {}
    problems = 0
    cpu = None

    for v1 in results:
        sha = v1["clash_commit"]
        cpu = cpu or v1["machine"].get("cpu")
        if is_upstream(args.clash_repo, sha, args.upstream_ref):
            repo, ref = upstream_slug, "master"
        else:
            branch = branch_of(args.fork_repo, sha)
            if branch is None:
                dropped.append((sha, "only on the master branch of the fork"))
                continue
            repo, ref = fork_slug, branch
            branch_dates[(repo, ref)] = max(
                branch_dates.get((repo, ref), ""), v1["date"]
            )
        subject, date, parents = commit_meta(args.fork_repo, sha)
        v2 = to_v2(v1, args.machine, repo, ref, subject, date, parents)
        found = validate_result(v2)
        for problem in found:
            print(f"{sha[:9]}: {problem}", file=sys.stderr)
        problems += len(found)
        write_json(args.out / result_path(args.machine, sha), v2, args.dry_run)
        written += 1
        print(f"  {sha[:9]}  {ref:<24} {'quick' if v2['run']['quick'] else 'full'}")

    for (repo, ref), updated in sorted(branch_dates.items()):
        snapshot = branch_snapshot(
            args.fork_repo, args.clash_repo, args, repo, ref, updated
        )
        found = validate_branch(snapshot)
        for problem in found:
            print(f"{ref}: {problem}", file=sys.stderr)
        problems += len(found)
        write_json(args.out / branch_path(repo, ref), snapshot, args.dry_run)
        print(f"branch {repo}@{ref}: {len(snapshot['commits'])} commits "
              f"on top of {snapshot['base'][:9]}")

    machine = {"id": args.machine, "label": args.machine, "hostname": args.machine,
               "cpu": cpu or "unknown", **MACHINE_EXTRA}
    write_json(args.out / f"machines/{args.machine}.json", machine, args.dry_run)

    for sha, reason in dropped:
        print(f"dropped {sha[:9]}: {reason}")
    print(f"wrote {written} results, dropped {len(dropped)}, {problems} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

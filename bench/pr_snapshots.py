#!/usr/bin/env python3
"""Record the branch of every labelled pull request.

Usage:
  pr_snapshots.py --clash-repo PATH --prs FILE --root DIR [options]

  --clash-repo PATH  clone of clash-lang/clash-compiler to fetch into
  --prs FILE         open pull requests, from bench/list_prs.py
  --root DIR         repository root with branches/ to write into
  --label NAME       label that asks for a benchmark (default performance)
  --upstream-ref REF ref of clash-lang master in the clone
                     (default refs/bench/upstream-master)

A branch snapshot used to come only as a side effect of a benchmark:
bench/run_one.sh wrote one for each commit that it measured. A pull
request whose commits all had a result already was therefore invisible on
the site, and it had no way to become visible, because there was nothing
left to measure. That is not a corner case: it happens as soon as
somebody labels a pull request whose commits this machine has measured
before under another name, or as a part of master.

So the poll records the branch of every labelled pull request, whether or
not anything needs measuring. The work is one fetch and one git log for
each pull request; the measurements are what takes hours.

The snapshot itself comes from bench/branch_snapshot.py, taken from the
head of the branch, so it holds the whole chain and not only the part
that this machine has measured.

bench/push_branches.sh runs this against a worktree of main, together
with bench/prune_branches.py, and pushes the result.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from list_prs import load, with_label  # noqa: E402
from pr_refs import fetch_pr  # noqa: E402
from result_schema import branch_path  # noqa: E402


def unchanged(path, snapshot):
    """Say whether the file already holds this snapshot.

    Every write gives a snapshot a new "updated" stamp. The poll runs on
    a schedule, so writing that stamp alone would put a commit on main
    every six hours and say nothing. Compare everything else.
    """
    if not path.exists():
        return False
    old = json.loads(path.read_text())
    return ({k: v for k, v in old.items() if k != "updated"}
            == {k: v for k, v in snapshot.items() if k != "updated"})


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--clash-repo", required=True, type=Path)
    parser.add_argument("--prs", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--label", default="performance")
    parser.add_argument("--upstream-ref", default="refs/bench/upstream-master")
    args = parser.parse_args()

    data = load(args.prs)
    base_url = f"https://github.com/{data['repo']}.git"
    labelled = with_label(data, args.label)
    if not labelled:
        print(f"pr_snapshots.py: no open pull request of {data['repo']} carries "
              f"the label {args.label!r}")
        return 0

    script = Path(__file__).resolve().parent / "branch_snapshot.py"
    written = same = skipped = 0
    with tempfile.TemporaryDirectory() as tmp:
        for pr in labelled:
            ref = fetch_pr(args.clash_repo, base_url, pr["number"])
            if ref is None:
                skipped += 1
                continue
            # Write next to the clone first, to see whether this says
            # anything new. branch_snapshot.py reads the clone from the
            # working directory.
            out = Path(tmp) / f"{pr['number']}.json"
            proc = subprocess.run([
                str(script),
                "--repo", pr["head_repo"],
                "--ref", pr["head_ref"],
                "--upstream-ref", args.upstream_ref,
                "--head", ref,
                "--prs", str(args.prs.resolve()),
                "--out-file", str(out),
            ], cwd=args.clash_repo, text=True)
            if proc.returncode != 0:
                # branch_snapshot.py has said why. A pull request whose
                # head sits on master has no branch to record, which is a
                # fact about that pull request, not a failure of this run.
                print(f"pr_snapshots.py: no snapshot for #{pr['number']}",
                      file=sys.stderr)
                skipped += 1
                continue
            snapshot = json.loads(out.read_text())
            path = args.root / branch_path(snapshot["repo"], snapshot["ref"])
            if unchanged(path, snapshot):
                same += 1
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out, path)
            written += 1

    print(f"pr_snapshots.py: {len(labelled)} labelled pull request(s): "
          f"{written} branch(es) recorded, {same} already right, "
          f"{skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

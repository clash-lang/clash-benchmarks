#!/usr/bin/env python3
"""Render the benchmark history to one self-contained HTML file.

Usage:
  render.py [--data DIR] [--clash-repo PATH] [--out site/index.html]

  --data DIR         repository root with results/, machines/ and
                     branches/ (default: this repository)
  --clash-repo PATH  clone of clash-lang/clash-compiler. The default is
                     $CLASH_REPO, else a clone in the cache directory.
  --clash-ref REF    master ref in that clone (default origin/master)
  --cache-dir DIR    where to make a clone if there is none
  --out PATH         output file (default site/index.html)
  --all-branches     also show the branches without an open pull request

The page holds all data. It gives the reader three selectors:

- the machine. Numbers from different machines are not comparable, so the
  page shows one machine at a time.
- the branch. The default is master. For another branch, the x-axis is
  master up to the branch point, then the commits of the branch. The
  commits of the branch have their own colour.
- the metric. The default is the compile time. The other choices show
  the memory of the same runs: the live heap and the memory taken from
  the OS, the total allocation, and the wall time split into mutator
  and collector. See "What a run measures" in docs/ops.md.

Master comes from the clone, not from the results: this way the graph also
shows the commits that have no result yet, as holes. A branch comes from
its snapshot in branches/, because a branch does not stay where it is.
See bench/result_schema.py.

Each panel carries an "Export" button. It hands the reader the panel as
one self-contained <svg> element to paste into a blog post, or to save
as a file: the palette, the title and a link back to the view are inside
the element, so it needs nothing from this page. See exportFigure() in
the template.

The branch selector holds the branches that are the head of an open pull
request, and no others: a branch whose pull request is closed says nothing
about Clash today, and the list stays short enough to use.
bench/prune_branches.py keeps the "pr" field of the snapshots up to date.
Use --all-branches to see the rest as well, on your own machine.

The page names its view in the query string: ?machine=<id> with either
&branch=<key> or &commit=<sha>, plus &metric=<m> when the metric is not
the default. A branch link follows the branch as it advances, which is
what a reader wants while the pull request is open. The "Pin to commit"
button, or the "y" key, turns it into a commit link: a commit does not
move, so that link stays good after the branch advances, loses its pull
request, or goes away. A hand-written &branch= may also give the name of
the branch alone, when only one repository has a branch of that name; the
page writes the whole key back.

For a commit link the page looks for a branch that carries the commit;
without one it shows the commit and its ancestors as "detached -- <sha>".
For that fallback, every commit record carries its first parent, taken
from the snapshots and from the results themselves - so the chain of a
measured commit survives the pruning of its branch. Older links name the
view in the fragment, as #machine=<id>&head=<sha>; those still work.

A branch link outlives the branch as far as the data allows. A branch
that leaves the selector, because its pull request closed, keeps its
snapshot, and the link lands on the newest commit of that snapshot as a
detached view. Once prune_branches.py removes the snapshot, because the
branch itself is gone, the page no longer knows what the name meant and
the link falls back to master; a commit link to a measured commit of that
branch keeps working, because a result carries its own commit. A link
that has to stay good for a long time is a commit link.

"Commit details" under the selectors shows what the link names: the
commit and its subject, the branch that carries it, its pull request, and
the run of this machine, if there is one.
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "bench"))

from result_schema import now, validate_branch, validate_result  # noqa: E402

UPSTREAM_REPO = "clash-lang/clash-compiler"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPO}.git"

# The release branches of clash-compiler, by name. A release branch comes
# from the clone, the way master does, and not from a snapshot in
# branches/: it is durable, so there is nothing for a snapshot to protect
# against, and the clone carries the tags that mark its releases as well.
RELEASE_BRANCHES = ["1.10"]

# Where the page lives. An exported figure links back to the view it came
# from; a page opened from a file has no address to link to, so the link
# goes here instead.
SITE_URL = "https://clash-lang.github.io/clash-benchmarks/"

# Colours: categorical slots 1 to 3 of the reference palette. Slot 3 is
# the branch accent. Both modes are selected, not flipped. The palette is
# here, and not in the stylesheet of the template, because an exported
# figure carries a copy of it: the page writes it as CSS for itself and as
# JSON for the exporter, and the two cannot drift apart.
PALETTE = {
    "light": {
        "surface-1": "#fcfcfb",
        "page": "#f9f9f7",
        "ink-1": "#0b0b0b",
        "ink-2": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "series-1": "#2a78d6",
        "series-2": "#eb6834",
        "branch": "#1baf7a",
        "border": "rgba(11,11,11,0.10)",
    },
    "dark": {
        "surface-1": "#1a1a19",
        "page": "#0d0d0d",
        "ink-1": "#ffffff",
        "ink-2": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "series-1": "#3987e5",
        "series-2": "#d95926",
        "branch": "#199e70",
        "border": "rgba(255,255,255,0.10)",
    },
}


def palette_css():
    """Return the three palette blocks of the page.

    Light, then dark by preference, then dark by choice: data-theme on the
    root element wins over the preference of the reader, in both
    directions.
    """
    def body(mode, indent):
        lines = [f"{indent}color-scheme: {mode};"]
        lines += [f"{indent}--{k}: {v};" for k, v in PALETTE[mode].items()]
        return "\n".join(lines)

    return "\n".join([
        "  .viz-root {",
        body("light", "    "),
        "  }",
        "  @media (prefers-color-scheme: dark) {",
        '    :root:where(:not([data-theme="light"])) .viz-root {',
        body("dark", "      "),
        "    }",
        "  }",
        '  :root[data-theme="dark"] .viz-root {',
        body("dark", "    "),
        "  }",
    ])

# Criterion measures short benchmarks in milliseconds. This is the limit
# in seconds below which a panel changes its unit.
MS_LIMIT = 5.0

RECORD = "\x1f"


def git(repo, *args):
    """Run one git command in a repository and return its stdout."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return proc.stdout


def pr_number(subject):
    """Return the pull request number of a commit subject, or None.

    A squash commit ends with "(#N)". A merge commit starts with
    "Merge pull request #N".
    """
    match = re.search(r"\(#(\d+)\)$", subject) or re.match(
        r"Merge pull request #(\d+)", subject
    )
    return int(match.group(1)) if match else None


def clash_clone(args):
    """Return the path of a clash-compiler clone; make one if necessary."""
    if args.clash_repo:
        return args.clash_repo
    from_env = os.environ.get("CLASH_REPO")
    if from_env:
        return Path(from_env)
    path = args.cache_dir / "clash-compiler"
    if not (path / "HEAD").exists() and not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"render.py: cloning {UPSTREAM_URL} into {path}")
        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--no-checkout", UPSTREAM_URL,
             str(path)],
            check=True,
        )
    else:
        git(path, "fetch", "--no-tags", "origin", "master")
    # A release branch moves while a release is being prepared, and a
    # release adds a tag. Neither comes along with the fetch of master.
    for name in RELEASE_BRANCHES:
        try:
            git(path, "fetch", "--tags", "origin",
                f"+refs/heads/{name}:refs/remotes/origin/{name}")
        except subprocess.CalledProcessError:
            print(f"render.py: could not fetch the release branch {name}; "
                  f"using what the clone has", file=sys.stderr)
    return path


def release_chain(repo, master_ref, name):
    """Return the first-parent commits of a release branch, oldest first.

    The chain starts at the commit that the branch shares with master.
    That commit is where the release branch left master, and it is the
    last point at which the two lines mean the same thing; before it the
    graph of the branch would only repeat master. It ends at the head of
    the branch, so the graph also shows the commits that have no result
    yet, as holes.

    Return None when the clone does not have the branch.
    """
    head = ""
    for candidate in (f"refs/remotes/origin/{name}", name):
        try:
            head = git(repo, "rev-parse", "--verify", "--quiet",
                       f"{candidate}^{{commit}}").strip()
        except subprocess.CalledProcessError:
            continue
        if head:
            break
    if not head:
        return None

    fmt = f"--format=%H{RECORD}%s{RECORD}%cs"

    def record(line):
        sha, subject, date = line.split(RECORD)
        return {"sha": sha, "subject": subject, "date": date}

    base = git(repo, "merge-base", master_ref, head).strip()
    # The base and the commits after it, in two calls: "base^.." would
    # name the parent of the base, which a root commit does not have.
    chain = [record(git(repo, "log", "-1", fmt, base).strip())]
    chain += [record(line) for line in
              git(repo, "log", "--first-parent", "--reverse", fmt,
                  f"{base}..{head}").splitlines()]
    return chain


def tags_on(repo, shas):
    """Return the name of the tag on each of shas that has one.

    An annotated tag points at a tag object, which "*objectname" peels to
    the commit; a lightweight tag has the commit in "objectname" already.
    Two tags on one commit is rare enough that the newest simply wins.
    """
    wanted = set(shas)
    tags = {}
    fmt = (f"%(refname:short){RECORD}%(objectname){RECORD}%(*objectname)")
    for line in git(repo, "for-each-ref", "--sort=creatordate",
                    f"--format={fmt}", "refs/tags").splitlines():
        name, obj, peeled = line.split(RECORD)
        sha = peeled or obj
        if sha in wanted:
            tags[sha] = name
    return tags


def load_results(root):
    """Read all results, grouped by machine. Bad files stop the render."""
    results = {}
    paths = sorted((root / "results").glob("*/*/*.json"))
    for path in paths:
        result = json.loads(path.read_text())
        problems = validate_result(result)
        if problems:
            for problem in problems:
                print(f"{path}: {problem}", file=sys.stderr)
            sys.exit("render.py: bad result file")
        results.setdefault(result["machine"], {})[result["clash"]["commit"]] = result
    print(f"render.py: read {len(paths)} results of {len(results)} machines")
    return results


def load_machines(root, results):
    """Read the machine registry, for the machines that have results."""
    machines = []
    for machine, own in sorted(results.items()):
        path = root / "machines" / f"{machine}.json"
        if path.exists():
            entry = json.loads(path.read_text())
        else:
            print(f"render.py: machine {machine} is not in machines/", file=sys.stderr)
            entry = {"id": machine, "label": machine}
        entry["results"] = len(own)
        machines.append(entry)
    # The default machine comes first, then the machine with the most
    # results. The first machine in the list is what a reader sees first.
    machines.sort(key=lambda m: (not m.get("default"), -m["results"], m["id"]))
    return machines


def load_branches(root, all_branches):
    """Read all branch snapshots, and say which go into the selector.

    A snapshot without an open pull request stays out of the selector,
    but its commits still go into the page: a permalink to one of them
    must keep working, as a detached view.
    """
    snapshots = []
    hidden = 0
    for path in sorted((root / "branches").glob("**/*.json")):
        snapshot = json.loads(path.read_text())
        problems = validate_branch(snapshot)
        if problems:
            for problem in problems:
                print(f"{path}: {problem}", file=sys.stderr)
            sys.exit("render.py: bad branch snapshot")
        # A release branch comes from the clone. A benchmark run of one
        # writes a snapshot all the same, and that snapshot would be a
        # second entry for the same branch. See RELEASE_BRANCHES.
        if (snapshot["repo"] == UPSTREAM_REPO
                and snapshot["ref"] in RELEASE_BRANCHES):
            continue
        snapshot["shown"] = all_branches or snapshot["pr"] is not None
        hidden += not snapshot["shown"]
        snapshots.append(snapshot)
    if hidden:
        print(f"render.py: left {hidden} branch(es) without an open pull "
              f"request out of the selector (--all-branches shows them)")
    return snapshots


def master_chain(repo, ref, known, anchors=()):
    """Return the first-parent commits of master, oldest first.

    The chain starts at the oldest commit that has a result: older
    commits say nothing. It ends at the head of master, so the graph also
    shows the newest commits that have no result yet.

    A commit in anchors is a start as well, with or without a result: the
    master graph marks the commits where the release branches left, and a
    mark beyond the left edge of the graph marks nothing.
    """
    lines = git(repo, "log", "--first-parent", f"--format=%H{RECORD}%s{RECORD}%cs",
                ref).splitlines()
    commits = []
    for line in lines:
        sha, subject, date = line.split(RECORD)
        commits.append({"sha": sha, "subject": subject, "date": date})
    oldest = None
    for i, commit in enumerate(commits):
        if commit["sha"] in known:
            oldest = i
    if oldest is None:
        sys.exit(f"render.py: no result for any commit of {ref}")
    anchors = set(anchors)
    for i, commit in enumerate(commits):
        if commit["sha"] in anchors:
            oldest = max(oldest, i)
    return list(reversed(commits[: oldest + 1]))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    here = Path(__file__).resolve().parent
    parser.add_argument("--data", type=Path, default=here)
    parser.add_argument("--clash-repo", type=Path)
    parser.add_argument("--clash-ref", default="origin/master")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path.home() / ".cache" / "clash-benchmarks")
    parser.add_argument("--out", type=Path, default=here / "site" / "index.html")
    parser.add_argument("--all-branches", action="store_true")
    parser.add_argument("--release-branches", action="store_true",
                        help="print the release branches, one per line, and "
                             "exit. A caller that prepares the clone needs "
                             "them; see .github/workflows/publish.yml")
    args = parser.parse_args()

    if args.release_branches:
        print("\n".join(RELEASE_BRANCHES))
        return

    results = load_results(args.data)
    machines = load_machines(args.data, results)
    snapshots = load_branches(args.data, args.all_branches)

    known = {sha for own in results.values() for sha in own}
    clone = clash_clone(args)

    # The release branches come first: the master chain reaches back to
    # the commit where the oldest of them left, so that the mark of that
    # branch lands on the graph instead of beyond its left edge.
    releases = []
    for name in RELEASE_BRANCHES:
        chain = release_chain(clone, args.clash_ref, name)
        if chain is None:
            print(f"render.py: the clone has no release branch {name}; "
                  f"leaving it out of the selector. A clone of one branch "
                  f"does not carry the others: fetch it, or drop it from "
                  f"RELEASE_BRANCHES", file=sys.stderr)
            continue
        releases.append((name, chain))
    branch_offs = {chain[0]["sha"]: name for name, chain in releases}

    master = master_chain(clone, args.clash_ref, known, branch_offs)
    master_index = {c["sha"]: i for i, c in enumerate(master)}

    # One metadata record for each commit, for the tooltips and the table.
    # "p" is the first parent: the page follows it to draw the chain of a
    # commit whose branch is not in the selector any more.
    commits = {}

    def add_commit(commit, parent=None):
        entry = commits.setdefault(commit["sha"], {
            "s": commit["subject"],
            "d": commit["date"],
            "pr": pr_number(commit["subject"]),
        })
        if parent and "p" not in entry:
            entry["p"] = parent

    for i, commit in enumerate(master):
        add_commit(commit, master[i - 1]["sha"] if i else None)

    refs = [{
        "key": "master",
        "label": "master",
        "repo": UPSTREAM_REPO,
        "ref": "master",
        "pr": None,
        "commits": [c["sha"] for c in master],
        "branchPoint": None,
        # Where a release branch left master. See "marks" below.
        "marks": {sha: name for sha, name in branch_offs.items()
                  if sha in master_index},
    }]

    # The release branches, right after master in the selector: like
    # master they are always there, and unlike a pull request they do not
    # come and go.
    #
    # "marks" are the commits of a chain that are worth a rule and a name
    # on the graph. On a release branch those are its releases; on master
    # they are the commits where the release branches left.
    for name, chain in releases:
        for i, commit in enumerate(chain):
            add_commit(commit, chain[i - 1]["sha"] if i else None)
        shas = [c["sha"] for c in chain]
        refs.append({
            "key": f"{UPSTREAM_REPO}@{name}",
            "label": name,
            "repo": UPSTREAM_REPO,
            "ref": name,
            "pr": None,
            "commits": shas,
            # The shared commit is the first of the chain, so everything
            # after index 0 is on the branch.
            "branchPoint": 0,
            "marks": tags_on(clone, shas),
        })

    # The branches that are out of the selector, with the newest commit
    # that their snapshot saw. A link that names such a branch lands on
    # that commit; see readUrl() in the template.
    pruned = []

    for snapshot in snapshots:
        # The snapshot is a first-parent chain from the base, so the chain
        # itself gives the parent of each commit.
        parent = snapshot["base"]
        for commit in snapshot["commits"]:
            add_commit(commit, parent)
            parent = commit["sha"]
        if not snapshot["shown"]:
            if snapshot["commits"]:
                pruned.append({
                    "key": f"{snapshot['repo']}@{snapshot['ref']}",
                    "ref": snapshot["ref"],
                    "head": snapshot["commits"][-1]["sha"],
                })
            continue
        base = snapshot["base"]
        if base in master_index:
            head = [c["sha"] for c in master[: master_index[base] + 1]]
            branch_point = len(head) - 1
        else:
            # The branch point is not on master any more. Show the branch
            # alone: master and the branch have no common point to align.
            print(f"render.py: branch point {base[:9]} of {snapshot['ref']} is not "
                  f"on {args.clash_ref}; the view shows the branch alone",
                  file=sys.stderr)
            head = []
            branch_point = -1
        refs.append({
            "key": f"{snapshot['repo']}@{snapshot['ref']}",
            "label": snapshot["ref"],
            "repo": snapshot["repo"],
            "ref": snapshot["ref"],
            "pr": snapshot["pr"],
            "commits": head + [c["sha"] for c in snapshot["commits"]],
            "branchPoint": branch_point,
        })

    # The measurements, in a small form: one entry for each machine and
    # commit that has a result. A result also carries the subject, date
    # and parents of its commit: with those, a measured commit stays on
    # the page after bench/prune_branches.py removes its branch.
    benchmarks = set()
    packed = {}
    for machine, own in results.items():
        for sha, result in own.items():
            clash = result["clash"]
            add_commit(
                {"sha": sha, "subject": clash["subject"],
                 "date": clash["committer_date"]},
                clash["parents"][0] if clash["parents"] else None,
            )
            norm = result["normalization"]
            benchmarks.update(norm["benchmarks"])
            wd = result["wire_demo"]
            entry = {
                # The page reads these by position: mean, stddev,
                # allocation, mutator wall, gc wall. The allocation is a
                # per-run mean; whole bytes are enough.
                "norm": {name: [v["mean_s"], v["stddev_s"],
                                round(v["alloc_bytes"]),
                                v["mut_wall_s"], v["gc_wall_s"]]
                         for name, v in norm["benchmarks"].items()},
                "wire": {"status": wd["status"]},
                "quick": result["run"]["quick"],
                "url": result["run"]["workflow_run_url"],
            }
            if norm["status"] != "ok":
                # The commit table shows why the examples have no value.
                entry["normReason"] = norm["skip_reason"]
            if wd["status"] == "ok" and wd["runs"]:
                run = wd["runs"][0]
                entry["wire"].update({
                    "norm_s": run["normalization_s"],
                    "netlist_s": run["netlist_s"],
                    "total_s": run["total_s"],
                    "alloc_bytes": run["alloc_bytes"],
                    "max_live_bytes": run["max_live_bytes"],
                    "peak_mb": run["peak_mb"],
                    "mut_wall_s": run["mut_wall_s"],
                    "gc_wall_s": run["gc_wall_s"],
                    "overlays": wd["overlays"],
                })
            else:
                entry["wire"]["reason"] = wd["skip_reason"]
            packed.setdefault(machine, {})[sha] = entry

    data = {
        "generated": now("minutes"),
        "upstreamRepo": UPSTREAM_REPO,
        "siteUrl": SITE_URL,
        "msLimit": MS_LIMIT,
        "machines": machines,
        "commits": commits,
        "refs": refs,
        "pruned": pruned,
        "results": packed,
        "benchmarks": sorted(benchmarks),
    }

    subtitle = (f"{sum(len(o) for o in results.values())} results · "
                f"{len(machines)} machine(s) · {len(refs) - 1} branch(es) · "
                f"master {master[0]['sha'][:9]}..{master[-1]['sha'][:9]}")
    page = (TEMPLATE
            .replace("__DATA__", json.dumps(data, separators=(",", ":")))
            .replace("__PALETTE_CSS__", palette_css())
            .replace("__PALETTE__", json.dumps(PALETTE, separators=(",", ":")))
            .replace("__SUBTITLE__", html.escape(subtitle)))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page)
    print(f"render.py: wrote {args.out} ({len(page) // 1024} KiB): {subtitle}")
    return 0


# A raw string: every backslash in here belongs to the CSS, the JavaScript
# or the HTML, and none to Python.
TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clash benchmark history</title>
<style>
  /* The palette comes from PALETTE in render.py, so that the page and an
     exported figure show the same colours. */
__PALETTE_CSS__
  body.viz-root {
    margin: 0;
    background: var(--page);
    color: var(--ink-1);
    font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  .wrap { max-width: 1200px; margin: 0 auto; padding: 28px 20px 60px; }
  h1 { font-size: 20px; font-weight: 650; margin: 0 0 4px; }
  .subtitle { color: var(--ink-2); font-size: 13px; margin: 0 0 8px; }
  .card {
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 16px 8px;
    margin-bottom: 16px;
  }
  /* Two rows: the machine with the date range, then the branch. The
     branch goes on its own row because the name of a branch is long. */
  .filterbar { margin: 16px 0; }
  .filters {
    display: flex; flex-wrap: wrap; gap: 10px 14px; align-items: center;
    font-size: 12.5px; color: var(--ink-2);
  }
  .filters + .filters { margin-top: 10px; }
  .filters button, .filters select, .filters input[type="date"] {
    font: inherit; color: var(--ink-1); background: var(--surface-1);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 4px 8px;
  }
  .filters button { color: var(--ink-2); cursor: pointer; }
  .filters button:hover { background: var(--page); }
  .filters button[aria-pressed="true"] {
    color: var(--ink-1); font-weight: 600; border-color: var(--series-1);
  }
  .filters label { display: flex; gap: 6px; align-items: center; max-width: 100%; }
  /* A select is as wide as its widest option. "owner/repo @ branch (#N)"
     would push the page wider than a phone; let it shrink instead. */
  .filters select { min-width: 0; }
  .filters .sep { width: 1px; height: 20px; background: var(--border); }
  /* The commit that the link names, under the selectors. */
  .commitbox { margin: 10px 0 0; }
  .commitbox summary { font-size: 12.5px; font-weight: 600; color: var(--ink-2); }
  .commitbox summary .head { color: var(--muted); font-weight: 400; }
  .kv {
    display: grid; grid-template-columns: max-content minmax(0, 1fr);
    gap: 3px 14px; margin: 8px 0 0; font-size: 12.5px;
  }
  .kv dt { color: var(--muted); }
  .kv dd { margin: 0; color: var(--ink-1); overflow-wrap: anywhere; }
  .kv .mono { font-family: ui-monospace, monospace; font-size: 12px; }
  .kv a { color: var(--series-1); text-decoration: none; }
  .kv a:hover { text-decoration: underline; }
  .card h2 { font-size: 13px; font-weight: 600; margin: 0 0 2px; color: var(--ink-1); }
  /* The heading of a card, with the export button at its right end. */
  .cardhead {
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 10px;
  }
  .exportbtn {
    font: inherit; font-size: 11px; color: var(--ink-2);
    background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 6px; padding: 1px 7px; cursor: pointer; flex: none;
  }
  .exportbtn:hover { background: var(--page); color: var(--ink-1); }
  .card .note { font-size: 11.5px; color: var(--muted); margin: 0 0 8px; }
  .legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 11.5px; color: var(--ink-2); margin: 0 0 6px; }
  .legend .swatch {
    display: inline-block; width: 14px; height: 3px; border-radius: 2px;
    margin-right: 6px; vertical-align: 3px;
  }
  .legend .swatch.band { height: 10px; border-radius: 2px; opacity: 0.35; }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
    gap: 16px;
  }
  .grid .card { margin-bottom: 0; }
  svg { display: block; }
  svg text { font: 10.5px system-ui, -apple-system, "Segoe UI", sans-serif; fill: var(--muted); }
  svg .endlabel { font-size: 11px; font-weight: 600; fill: var(--ink-2); }
  svg .branchlabel { font-size: 10.5px; font-weight: 600; fill: var(--ink-2); }
  svg .marklabel { font-size: 10.5px; font-weight: 600; fill: var(--ink-2); }
  svg .plot-hit {
    cursor: pointer;
    outline: none;
    /* A swipe over a chart must scroll the page, not select points. */
    touch-action: pan-y;
  }
  svg .plot-hit:focus-visible { outline: 2px solid var(--series-1); outline-offset: -2px; }
  #tooltip {
    position: fixed;
    pointer-events: none;
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 8px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.18);
    padding: 8px 11px;
    font-size: 12px;
    max-width: min(340px, calc(100vw - 24px));
    z-index: 10;
    display: none;
  }
  #tooltip .val { font-size: 14px; font-weight: 650; color: var(--ink-1); }
  #tooltip .val .key {
    display: inline-block; width: 14px; height: 3px; border-radius: 2px;
    background: var(--series-1); vertical-align: middle; margin-right: 6px;
  }
  #tooltip .meta { color: var(--ink-2); margin-top: 3px; }
  #tooltip .mono { font-family: ui-monospace, monospace; font-size: 11px; }
  #tooltip .sub { color: var(--muted); margin-top: 2px; }
  .empty { color: var(--ink-2); font-size: 13px; padding: 8px 0 16px; }
  details { margin-top: 24px; }
  summary { cursor: pointer; color: var(--ink-2); font-weight: 600; font-size: 13px; }
  .tablewrap { overflow-x: auto; margin-top: 12px; max-height: 70vh; }
  table { border-collapse: collapse; font-size: 12px; background: var(--surface-1); }
  th, td { padding: 5px 10px; border-bottom: 1px solid var(--grid); text-align: left; white-space: nowrap; }
  th { color: var(--ink-2); font-weight: 600; position: sticky; top: 0; background: var(--surface-1); }
  td.num { font-variant-numeric: tabular-nums; text-align: right; }
  td.mono a { font-family: ui-monospace, monospace; color: var(--series-1); text-decoration: none; }
  td.mono a:hover { text-decoration: underline; }
  td.muted { color: var(--muted); }
  td.where { color: var(--ink-2); }
  td.where.branch { color: var(--ink-1); font-weight: 600; }
  td.subject { max-width: 360px; overflow: hidden; text-overflow: ellipsis; }
  /* The export dialog: the figure as it will look, and its markup. */
  #exportbox {
    width: min(880px, calc(100vw - 32px));
    max-height: calc(100vh - 48px);
    /* A short window scrolls the box; it does not cut it off. */
    overflow: auto;
    padding: 0;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--surface-1);
    color: var(--ink-1);
    font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  #exportbox::backdrop { background: rgba(0,0,0,0.45); }
  #exportbox .inner { padding: 16px 18px 18px; }
  #exportbox h2 { font-size: 14px; font-weight: 650; margin: 0 0 4px; }
  #exportbox .note {
    font-size: 11.5px; color: var(--muted); margin: 0 0 10px; max-width: 76ch;
  }
  #exportbox .filters .note { margin: 0; }
  /* A wide figure scrolls inside the dialog; the dialog does not grow. */
  #export-preview { overflow-x: auto; margin: 12px 0; }
  #export-code {
    display: block; width: 100%; height: 150px; box-sizing: border-box;
    font-family: ui-monospace, monospace; font-size: 10.5px;
    background: var(--page); color: var(--ink-1);
    border: 1px solid var(--border); border-radius: 8px; padding: 8px;
    resize: vertical; white-space: pre;
  }
</style>
</head>
<body class="viz-root">
<div class="wrap">
  <h1>Clash benchmark history</h1>
  <p class="subtitle" id="subtitle">__SUBTITLE__</p>
  <noscript>
    <p class="empty">This page draws its graphs with JavaScript. The data is in
    the results/ directory of the clash-benchmarks repository.</p>
  </noscript>
  <div class="filterbar">
    <div class="filters">
      <label>Machine <select id="f-machine"></select></label>
      <span class="sep"></span>
      <button type="button" data-days="30">Last 30 days</button>
      <button type="button" data-days="90">Last 90 days</button>
      <button type="button" data-days="0" aria-pressed="true">All</button>
      <label>From <input type="date" id="f-from"></label>
      <label>To <input type="date" id="f-to"></label>
    </div>
    <div class="filters">
      <label>Branch <select id="f-ref"></select></label>
      <button type="button" id="f-pin" aria-pressed="false">Pin to commit</button>
      <span class="sep"></span>
      <label>Metric <select id="f-metric">
        <option value="time">Time</option>
        <option value="memory">Memory</option>
        <option value="alloc">Allocation</option>
        <option value="gc">MUT/GC split</option>
      </select></label>
    </div>
    <details class="commitbox" id="commitbox">
      <summary>Commit details <span class="head" id="commit-head"></span></summary>
      <div id="commitinfo"></div>
    </details>
  </div>
  <div id="headline"></div>
  <div class="grid" id="panels"></div>
  <div id="empty"></div>
  <details id="tablebox">
    <summary>Table view</summary>
    <div class="tablewrap" id="table"></div>
  </details>
</div>
<dialog id="exportbox" aria-labelledby="export-head">
  <div class="inner">
    <h2 id="export-head">Export <span id="export-name"></span></h2>
    <p class="note">One self-contained &lt;svg&gt; element, with the palette,
    the title and a link back to this view inside it. It needs nothing from
    this page, so it can go straight into a blog post. It shows the commits
    of the view as it stands: this machine, this branch, this metric and this
    date range.</p>
    <div class="filters">
      <label>Theme <select id="export-theme">
        <option value="auto">Light and dark, by the theme of the page</option>
        <option value="light">Light only</option>
        <option value="dark">Dark only</option>
      </select></label>
      <span class="sep"></span>
      <button type="button" id="export-copy">Copy HTML</button>
      <button type="button" id="export-save">Save SVG</button>
      <button type="button" id="export-close">Close</button>
      <span class="note" id="export-msg" role="status"></span>
    </div>
    <div id="export-preview"></div>
    <textarea id="export-code" readonly spellcheck="false"
      aria-label="Markup of the figure"></textarea>
  </div>
</dialog>
<div id="tooltip" role="status"></div>
<script>
const DATA = __DATA__;
const PALETTE = __PALETTE__;

const M = { top: 14, right: 56, bottom: 26, left: 64 };
// The right margin holds the label of the newest value, which is up to
// five digits wide.
const M_NARROW = { top: 14, right: 44, bottom: 26, left: 42 };
// Below this width a panel has no room for a y-axis label. Then the
// unit goes into the heading of the card. See drawPanel().
const NARROW = 420;
const SERIES_COLORS = ["var(--series-1)", "var(--series-2)"];
const BRANCH_COLOR = "var(--branch)";

// ---------------------------------------------------------------- helpers

function el(name, attrs) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const k in attrs) node.setAttribute(k, attrs[k]);
  return node;
}

function niceTicks(lo, hi, n) {
  const span = hi - lo || 1;
  const step0 = span / Math.max(1, n);
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const norm = step0 / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step)
    out.push(v);
  return { ticks: out, step };
}

function fmt(v, step) {
  if (v === 0) v = 0; // Math.ceil can give -0, which prints as "-0".
  const dec = Math.max(0, -Math.floor(Math.log10(step || 1)) + (step >= 1 ? 0 : 1));
  return v.toLocaleString("en-US", { maximumFractionDigits: Math.min(dec, 6) });
}

function fmtVal(v) {
  return v.toLocaleString("en-US", { maximumSignificantDigits: 4 });
}

// Pick one unit for a whole panel of byte values, from its largest value,
// and scale the values to it. Binary units, because peak_mb is MiB from
// the GHC runtime, so that conversion stays exact.
const BYTE_UNITS = [["TiB", 2 ** 40], ["GiB", 2 ** 30], ["MiB", 2 ** 20]];
function scaleBytes(values) {
  const max = Math.max(...values.filter(Boolean).flatMap(v => v.vs));
  const [unit, size] = BYTE_UNITS.find(([, s]) => max >= s)
    || BYTE_UNITS[BYTE_UNITS.length - 1];
  for (const v of values) if (v) v.vs = v.vs.map(b => b / size);
  return unit;
}

function commitUrl(sha, onBranch) {
  const c = DATA.commits[sha];
  const repo = onBranch ? VD.ref.repo : DATA.upstreamRepo;
  if (!onBranch && c.pr != null)
    return "https://github.com/" + DATA.upstreamRepo + "/pull/" + c.pr;
  return "https://github.com/" + repo + "/commit/" + sha;
}

// ------------------------------------------------------------ panel model

// Build the panels of one machine, one branch and one metric. A panel
// holds one value for each commit of the branch, or null where there is
// no result. The series of one panel share the y-axis, so metrics with
// different units get panels of their own.
function buildPanels(machineId, ref, metric) {
  const own = DATA.results[machineId] || {};
  const shas = ref.commits;
  const panels = [];

  // One value for each commit, read out of the wireDemo entry by pick().
  // A commit whose wireDemo leg did not run is a hole.
  const wireValues = pick => shas.map(sha => {
    const r = own[sha];
    if (!r || r.wire.status !== "ok") return null;
    return { vs: pick(r.wire), extra: r.wire.overlays || [] };
  });
  // The same for one normalization example. pick() reads the packed
  // array of the example: mean, stddev, allocation, mutator, gc.
  const normValues = (name, pick) => shas.map(sha => {
    const entry = own[sha] && own[sha].norm[name];
    return entry ? pick(entry) : null;
  });
  // The unit of a normalization time panel, over the values it shows.
  const msScale = values => {
    const max = Math.max(...values.filter(Boolean).flatMap(v => v.vs));
    return max < DATA.msLimit ? ["ms", 1000] : ["s", 1];
  };

  if (metric === "memory") {
    const values = wireValues(w => [w.max_live_bytes, w.peak_mb * 2 ** 20]);
    if (values.some(Boolean)) {
      panels.push({
        id: "wire_demo-mem",
        title: "wireDemo (bittide-hardware)",
        note: "Largest live heap, and most memory taken from the OS, "
          + "during one HDL generation of wireDemoTest",
        unit: scaleBytes(values),
        dim: "memory",
        series: ["live heap", "peak (OS)"],
        colors: SERIES_COLORS,
        values,
        headline: true,
        wire: true,
      });
    }
    // The examples have no live-heap numbers; see the Allocation metric.
    return panels;
  }

  if (metric === "alloc") {
    const values = wireValues(w => [w.alloc_bytes]);
    if (values.some(Boolean)) {
      panels.push({
        id: "wire_demo-alloc",
        title: "wireDemo (bittide-hardware)",
        note: "Total allocation of one HDL generation of wireDemoTest",
        unit: scaleBytes(values),
        dim: "allocation",
        series: ["allocated"],
        colors: [SERIES_COLORS[0]],
        values,
        headline: true,
        wire: true,
      });
    }
    for (const name of DATA.benchmarks) {
      const values = normValues(name, entry => ({ vs: [entry[2]] }));
      if (!values.some(Boolean)) continue;
      panels.push({
        id: "norm-alloc-" + name,
        title: name.split("/").pop(),
        note: name,
        unit: scaleBytes(values),
        dim: "allocation",
        series: ["allocated"],
        colors: [SERIES_COLORS[0]],
        values,
      });
    }
    return panels;
  }

  if (metric === "gc") {
    const values = wireValues(w => [w.mut_wall_s, w.gc_wall_s]);
    if (values.some(Boolean)) {
      panels.push({
        id: "wire_demo-gc",
        title: "wireDemo (bittide-hardware)",
        note: "Wall time of one HDL generation of wireDemoTest, split "
          + "into mutator and collector",
        unit: "s",
        dim: "time",
        series: ["mutator", "gc"],
        colors: SERIES_COLORS,
        values,
        headline: true,
        wire: true,
      });
    }
    for (const name of DATA.benchmarks) {
      const raw = normValues(name, entry => ({ vs: [entry[3], entry[4]] }));
      if (!raw.some(Boolean)) continue;
      const [unit, scale] = msScale(raw);
      panels.push({
        id: "norm-gc-" + name,
        title: name.split("/").pop(),
        note: name,
        unit,
        dim: "time",
        series: ["mutator", "gc"],
        colors: SERIES_COLORS,
        values: raw.map(v => v && { vs: v.vs.map(s => s * scale) }),
      });
    }
    return panels;
  }

  // The default metric: the compile times.
  const values = wireValues(w => [w.norm_s, w.total_s]);
  if (values.some(Boolean)) {
    panels.push({
      id: "wire_demo",
      title: "wireDemo (bittide-hardware)",
      note: "Clash times of one HDL generation of wireDemoTest",
      unit: "s",
      dim: "time",
      series: ["normalization", "total"],
      colors: SERIES_COLORS,
      values,
      headline: true,
      wire: true,
    });
  }
  for (const name of DATA.benchmarks) {
    const raw = normValues(name, entry => ({ vs: [entry[0]], sds: [entry[1]] }));
    if (!raw.some(Boolean)) continue;
    const [unit, scale] = msScale(raw);
    panels.push({
      id: "norm-" + name,
      title: name.split("/").pop(),
      note: name,
      unit,
      dim: "time",
      series: ["mean"],
      colors: [SERIES_COLORS[0]],
      values: raw.map(v => v && {
        vs: v.vs.map(s => s * scale),
        sds: v.sds.map(s => s * scale),
      }),
    });
  }
  return panels;
}

// ------------------------------------------------------------------ panel

const tooltip = document.getElementById("tooltip");

function hideTooltip() { tooltip.style.display = "none"; }

function showTooltip(px, py, panel, idx) {
  const sha = VD.commits[idx];
  const c = DATA.commits[sha];
  const val = panel.values[idx];
  const onBranch = isBranch(idx);
  tooltip.replaceChildren();
  for (let k = panel.series.length - 1; k >= 0; k--) {
    const row = document.createElement("div");
    row.className = "val";
    const key = document.createElement("span");
    key.className = "key";
    key.style.background = onBranch && panel.series.length === 1
      ? BRANCH_COLOR : panel.colors[k];
    row.appendChild(key);
    let text = fmtVal(val.vs[k]) + " " + panel.unit;
    if (val.sds) text += " ± " + fmtVal(val.sds[k]);
    if (panel.series.length > 1) text += " " + panel.series[k];
    row.appendChild(document.createTextNode(text));
    tooltip.appendChild(row);
  }
  const meta = document.createElement("div");
  meta.className = "meta";
  const mono = document.createElement("span");
  mono.className = "mono";
  mono.textContent = sha.slice(0, 9);
  meta.appendChild(mono);
  meta.appendChild(document.createTextNode(
    " · " + c.d + " · " + (!onBranch ? "master"
      : VD.ref.detached ? VD.ref.label : "branch " + VD.ref.label)));
  tooltip.appendChild(meta);
  const subj = document.createElement("div");
  subj.className = "sub";
  subj.textContent = c.s;
  tooltip.appendChild(subj);
  const result = (DATA.results[state.machine] || {})[sha];
  if (result && result.quick) {
    const note = document.createElement("div");
    note.className = "sub";
    note.textContent = "partial run: one file, no wireDemo";
    tooltip.appendChild(note);
  }
  if (val.extra && val.extra.length) {
    const ex = document.createElement("div");
    ex.className = "sub";
    ex.textContent = "overlays: " + val.extra.join(", ");
    tooltip.appendChild(ex);
  }
  const open = document.createElement("div");
  open.className = "sub";
  open.textContent = !onBranch && c.pr != null
    ? "click → PR #" + c.pr + " ↗" : "click → commit ↗";
  tooltip.appendChild(open);
  tooltip.style.display = "block";
  const r = tooltip.getBoundingClientRect();
  let x = px + 14, y = py - r.height - 10;
  if (x + r.width > innerWidth - 8) x = px - r.width - 14;
  if (y < 8) y = py + 14;
  tooltip.style.left = x + "px";
  tooltip.style.top = y + "px";
}

function isBranch(i) {
  return VD.branchPoint != null && i > VD.branchPoint;
}

// The legend of one panel: the series when there is more than one, and
// the branch when the view has one. The page draws these in HTML above a
// card, the exporter draws the same list inside the figure.
function legendItems(panel) {
  const items = [];
  if (panel.series.length > 1)
    panel.series.forEach((name, k) => items.push([panel.colors[k], name, false]));
  if (VD.branchPoint != null) {
    const name = VD.ref.detached ? VD.ref.label : "branch " + VD.ref.label;
    if (panel.series.length === 1)
      items.push([BRANCH_COLOR, "on " + name, false]);
    else if (VD.branchPoint > 0)
      // The band. A view that is a branch all the way across draws none;
      // see renderPanel().
      items.push([BRANCH_COLOR, "commits on " + name, true]);
  }
  return items;
}

// Split one run of points at the branch point. The point at the branch
// point belongs to both parts, so the line stays connected.
function colorParts(points) {
  const bp = VD.branchPoint;
  if (bp == null || points[points.length - 1][0] <= bp)
    return [{ branch: false, points }];
  if (points[0][0] > bp) return [{ branch: true, points }];
  const cut = points.findIndex(([i]) => i > bp);
  return [
    { branch: false, points: points.slice(0, cut) },
    { branch: true, points: points.slice(Math.max(0, cut - 1)) },
  ];
}

// Draw one panel. Return true when the panel is too narrow for a
// y-axis label.
//
// opts.width draws at a width of its own, instead of the width of the
// container: a figure for export has a size that the window does not
// decide. opts.static leaves out everything that only a reader of this
// page can use - the crosshair, the hit area and its handlers - and with
// it the dots that are there to be highlighted and nothing else.
function renderPanel(container, panel, height, opts) {
  container.replaceChildren();
  opts = opts || {};
  const W = opts.width || Math.max(280, container.clientWidth);
  const H = height;
  const m = W < NARROW ? M_NARROW : M;
  const tight = m === M_NARROW;
  const iw = W - m.left - m.right, ih = H - m.top - m.bottom;
  const n = VD.commits.length;
  const ns = panel.series.length;
  const pts = [];
  panel.values.forEach((val, i) => { if (val) pts.push([i, val]); });
  if (!pts.length) return tight;

  let lo = Infinity, hi = -Infinity;
  for (const [, val] of pts) {
    for (let s = 0; s < ns; s++) {
      const sd = val.sds ? val.sds[s] : 0;
      lo = Math.min(lo, val.vs[s] - sd);
      hi = Math.max(hi, val.vs[s] + sd);
    }
  }
  const pad = (hi - lo || Math.abs(hi) || 1) * 0.12;
  const positive = lo >= 0;
  lo -= pad; hi += pad;
  if (positive && lo < 0) lo = 0; // A measurement is never negative.

  const x = i => m.left + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
  const y = v => m.top + ih - ((v - lo) / (hi - lo)) * ih;

  const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}` });

  // The band behind the commits of the branch. It carries the branch,
  // together with the rule at the branch point and the legend.
  //
  // It is there to tell the commits of the branch from the commits of
  // master before it. A view that is a branch all the way across - a
  // release branch, or a detached commit whose branch point is not on
  // master any more - has nothing to tell apart, and a tint over the
  // whole plot only asks the reader to decode a colour. That view says
  // it in a word instead; see the label further down.
  const bp = VD.branchPoint;
  const allBranch = bp != null && bp <= 0;
  if (bp != null && bp > 0 && bp < n - 1) {
    svg.appendChild(el("rect", { x: x(bp), y: m.top,
      width: m.left + iw - x(bp), height: ih,
      fill: BRANCH_COLOR, opacity: 0.07 }));
  }

  const { ticks, step } = niceTicks(lo, hi, Math.max(3, Math.floor(ih / 45)));
  for (const t of ticks) {
    svg.appendChild(el("line", { x1: m.left, x2: m.left + iw, y1: y(t), y2: y(t),
      stroke: "var(--grid)", "stroke-width": 1 }));
    const lab = el("text", { x: m.left - 8, y: y(t) + 3.5, "text-anchor": "end",
      style: "font-variant-numeric: tabular-nums" });
    lab.textContent = fmt(t, step);
    svg.appendChild(lab);
  }
  svg.appendChild(el("line", { x1: m.left, x2: m.left + iw,
    y1: m.top + ih, y2: m.top + ih, stroke: "var(--axis)", "stroke-width": 1 }));

  if (!tight) {
    // The label turns about (12, cy), so its baseline runs up the figure
    // and its letters stand to the left of that line. Put the baseline a
    // line-height right of the pivot, and the letters land beside the
    // left edge rather than over it.
    const cy = m.top + ih / 2;
    const ylab = el("text", { x: 12, y: cy + 10, "text-anchor": "middle",
      transform: `rotate(-90 12 ${cy})` });
    ylab.textContent = panel.dim + " (" + panel.unit + ")";
    svg.appendChild(ylab);
  }

  const nx = Math.max(2, Math.floor(iw / 120));
  const seen = new Set();
  let prevDate = null;
  for (let k = 0; k < nx; k++) {
    const i = Math.round((k / (nx - 1)) * (n - 1));
    const date = DATA.commits[VD.commits[i]].d;
    if (seen.has(i) || date === prevDate) continue;
    seen.add(i);
    prevDate = date;
    const lab = el("text", { x: x(i), y: m.top + ih + 16, "text-anchor": "middle" });
    lab.textContent = date;
    svg.appendChild(lab);
  }

  // Runs of measured commits. A machine does not measure every commit,
  // so connect the line over a small hole. A big hole breaks the line:
  // there the graph must not suggest a trend that nobody measured.
  const gapLimit = Math.max(3, Math.round(n * 0.02));
  const runs = [];
  let cur = [];
  pts.forEach(([i, val], k) => {
    if (k > 0 && i - pts[k - 1][0] > gapLimit) { runs.push(cur); cur = []; }
    cur.push([i, val]);
  });
  runs.push(cur);

  for (let s = 0; s < ns; s++) {
    for (const run of runs) {
      for (const part of colorParts(run)) {
        const color = part.branch && ns === 1 ? BRANCH_COLOR : panel.colors[s];
        const p = part.points;
        if (p.length > 1 && p[0][1].sds) {
          const up = p.map(([i, v]) => `${x(i)},${y(v.vs[s] + v.sds[s])}`);
          const dn = p.slice().reverse().map(
            ([i, v]) => `${x(i)},${y(v.vs[s] - v.sds[s])}`);
          svg.appendChild(el("polygon", { points: up.concat(dn).join(" "),
            fill: color, opacity: 0.1 }));
        }
        if (p.length > 1) {
          svg.appendChild(el("polyline", {
            points: p.map(([i, v]) => `${x(i)},${y(v.vs[s])}`).join(" "),
            fill: "none", stroke: color, "stroke-width": 2,
            "stroke-linejoin": "round", "stroke-linecap": "round" }));
        }
      }
    }
  }

  // The rule at the branch point, with a label on the big panel.
  if (bp != null && bp >= 0 && bp < n - 1) {
    svg.appendChild(el("line", { x1: x(bp), x2: x(bp), y1: m.top, y2: m.top + ih,
      stroke: BRANCH_COLOR, "stroke-width": 1.5, "stroke-dasharray": "4 3" }));
    if (panel.headline) {
      // Keep the label inside the plot: put it left of the rule when the
      // branch point sits near the right edge.
      const late = x(bp) > m.left + iw * 0.6;
      const lab = el("text", { x: x(bp) + (late ? -6 : 6), y: m.top + 11,
        "text-anchor": late ? "end" : "start", class: "branchlabel" });
      lab.textContent = "branch point " + VD.commits[bp].slice(0, 9);
      svg.appendChild(lab);
    }
  }

  // The word that stands in for the band. It goes against the right
  // edge, opposite the branch point at the left.
  if (allBranch && panel.headline) {
    const lab = el("text", { x: m.left + iw, y: m.top + 11,
      "text-anchor": "end", class: "branchlabel", fill: BRANCH_COLOR });
    lab.textContent = VD.ref.detached ? VD.ref.label
      : "branch " + VD.ref.label;
    svg.appendChild(lab);
  }

  // The marked commits of this view: the releases of a release branch,
  // or, on master, the commits where the release branches left. Neither
  // moves, so both mark a point of the graph that a reader can come back
  // to and compare against. The label sits at the foot of the plot,
  // which is where the branch point label is not.
  const marks = VD.ref.marks || {};
  for (let i = 0; i < n; i++) {
    const mark = marks[VD.commits[i]];
    if (!mark) continue;
    svg.appendChild(el("line", { x1: x(i), x2: x(i), y1: m.top, y2: m.top + ih,
      stroke: "var(--axis)", "stroke-width": 1, "stroke-dasharray": "2 3" }));
    if (!panel.headline) continue;
    // Keep the label inside the plot: a release is often the newest
    // commit of the branch, hard against the right edge.
    const late = x(i) > m.left + iw * 0.6;
    const lab = el("text", { x: x(i) + (late ? -5 : 5), y: m.top + ih - 6,
      "text-anchor": late ? "end" : "start", class: "marklabel" });
    lab.textContent = mark;
    svg.appendChild(lab);
  }

  // A dot for each commit, while the commits are far enough apart to
  // tell one from the next. Closer than that they merge into a bar, so
  // the line carries the shape on its own and the reader gets a dot only
  // under the pointer. See "spare" below: a chain of a few thousand
  // commits used to make a hidden circle for every one of them, which is
  // tens of thousands of nodes that nobody ever sees.
  const dotSpacing = pts.length > 1 ? iw / (pts.length - 1) : iw;
  const drawDots = dotSpacing >= 7;
  const markers = new Map();
  if (drawDots) {
    for (const [i, val] of pts) {
      const dots = [];
      for (let s = 0; s < ns; s++) {
        const color = isBranch(i) && ns === 1 ? BRANCH_COLOR : panel.colors[s];
        const dot = el("circle", { cx: x(i), cy: y(val.vs[s]), r: 4,
          fill: color, stroke: "var(--surface-1)", "stroke-width": 2 });
        svg.appendChild(dot);
        dots.push(dot);
      }
      markers.set(i, dots);
    }
  }

  // Direct labels at the newest point, one for each series. Push them
  // apart when they overlap.
  const [li, lval] = pts[pts.length - 1];
  const labelYs = panel.series.map((_, s) => y(lval.vs[s]) + 4);
  const order = labelYs.map((_, s) => s).sort((a, b) => labelYs[a] - labelYs[b]);
  for (let k = 1; k < order.length; k++) {
    if (labelYs[order[k]] - labelYs[order[k - 1]] < 13)
      labelYs[order[k]] = labelYs[order[k - 1]] + 13;
  }
  for (let s = 0; s < ns; s++) {
    const end = el("text", { x: x(li) + 8, y: labelYs[s], class: "endlabel" });
    end.textContent = fmtVal(lval.vs[s]);
    svg.appendChild(end);
  }

  if (opts.static) {
    container.appendChild(svg);
    return tight;
  }

  const cross = el("line", { y1: m.top, y2: m.top + ih,
    stroke: "var(--axis)", "stroke-width": 1, visibility: "hidden" });
  svg.appendChild(cross);

  // The dot under the pointer, when the panel draws no dots of its own:
  // one circle for each series, moved to the commit that the reader is
  // on. One circle serves a chain of any length.
  const spare = drawDots ? null : panel.series.map(() => {
    const dot = el("circle", { r: 4, stroke: "var(--surface-1)",
      "stroke-width": 2, visibility: "hidden" });
    svg.appendChild(dot);
    return dot;
  });

  const hit = el("rect", { x: m.left, y: m.top, width: iw, height: ih,
    fill: "transparent", class: "plot-hit", tabindex: 0 });
  svg.appendChild(hit);

  let active = null;
  function setDots(i, r, show) {
    if (spare) {
      const val = panel.values[i];
      for (let s = 0; s < ns; s++) {
        const dot = spare[s];
        if (!show || !val) { dot.setAttribute("visibility", "hidden"); continue; }
        dot.setAttribute("cx", x(i));
        dot.setAttribute("cy", y(val.vs[s]));
        dot.setAttribute("r", r);
        dot.setAttribute("fill",
          isBranch(i) && ns === 1 ? BRANCH_COLOR : panel.colors[s]);
        dot.setAttribute("visibility", "visible");
      }
      return;
    }
    for (const dot of markers.get(i) || []) dot.setAttribute("r", r);
  }
  function highlight(i, clientX, clientY) {
    if (active != null) setDots(active, 4, false);
    active = i;
    setDots(i, 5.5, true);
    cross.setAttribute("x1", x(i));
    cross.setAttribute("x2", x(i));
    cross.setAttribute("visibility", "visible");
    showTooltip(clientX, clientY, panel, i);
  }
  function clear() {
    if (active != null) setDots(active, 4, false);
    active = null;
    cross.setAttribute("visibility", "hidden");
    hideTooltip();
  }
  function nearest(i0) {
    let best = null, bd = Infinity;
    for (const [i] of pts) {
      const d = Math.abs(i - i0);
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }
  function openCommit(i) {
    window.open(commitUrl(VD.commits[i], isBranch(i)), "_blank");
  }
  function at(clientX) {
    const rect = svg.getBoundingClientRect();
    return nearest((clientX - rect.left - m.left) / iw * (n - 1));
  }
  // A touch move is a scroll gesture, so follow the pointer only for a
  // mouse. On a touch screen a tap selects the nearest point instead.
  let fresh = false;
  hit.addEventListener("pointermove", e => {
    if (e.pointerType === "touch") return;
    const i = at(e.clientX);
    if (i != null) highlight(i, e.clientX, e.clientY);
  });
  hit.addEventListener("pointerdown", e => {
    if (e.pointerType !== "touch") return;
    const i = at(e.clientX);
    // The first tap shows the values. Only a tap on a point that is
    // already selected opens the commit: one tap must not take a reader
    // away from the page.
    fresh = i !== active;
    if (i != null) highlight(i, e.clientX, e.clientY);
  });
  hit.addEventListener("click", () => {
    if (fresh) { fresh = false; return; }
    if (active != null) openCommit(active);
  });
  // A touch pointer leaves as soon as the finger goes up. Keep the
  // tooltip then; it goes away with the next tap or on blur.
  hit.addEventListener("pointerleave", e => {
    if (e.pointerType !== "touch") clear();
  });
  hit.addEventListener("focus", () => {
    // A pointer click fires focus before click. Keep the point that the
    // pointer selected; go to the newest point only on keyboard focus,
    // where no point is selected yet.
    if (active != null) return;
    const i = pts[pts.length - 1][0];
    const rect = svg.getBoundingClientRect();
    highlight(i, rect.left + x(i), rect.top + y(panel.values[i].vs[0]));
  });
  hit.addEventListener("blur", clear);
  hit.addEventListener("keydown", e => {
    if (e.key === "Enter" && active != null) { openCommit(active); return; }
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const order = pts.map(([i]) => i);
    let k = order.indexOf(active == null ? order[order.length - 1] : active);
    k = Math.min(order.length - 1, Math.max(0, k + (e.key === "ArrowRight" ? 1 : -1)));
    const i = order[k];
    const rect = svg.getBoundingClientRect();
    highlight(i, rect.left + x(i), rect.top + y(panel.values[i].vs[0]));
  });

  container.appendChild(svg);
  return tight;
}

// ------------------------------------------------------------------ table

function renderTable() {
  const box = document.getElementById("table");
  box.replaceChildren();
  if (!VD.commits.length) return;
  const table = document.createElement("table");
  const head = table.insertRow();
  for (const name of ["Commit", "Date", "Where", "Subject"]) {
    const th = document.createElement("th");
    th.textContent = name;
    head.appendChild(th);
  }
  for (const panel of VD.panels) {
    const th = document.createElement("th");
    th.textContent = panel.title + " (" + panel.unit
      + (panel.series.length > 1 ? "; " + panel.series.join(" / ") : "") + ")";
    head.appendChild(th);
  }
  const body = table.createTBody();
  VD.commits.forEach((sha, i) => {
    const c = DATA.commits[sha];
    const onBranch = isBranch(i);
    const row = body.insertRow();
    const shaCell = row.insertCell();
    shaCell.className = "mono";
    const link = document.createElement("a");
    link.href = commitUrl(sha, onBranch);
    link.textContent = sha.slice(0, 9);
    shaCell.appendChild(link);
    row.insertCell().textContent = c.d;
    const where = row.insertCell();
    where.className = "where" + (onBranch ? " branch" : "");
    where.textContent = onBranch ? VD.ref.label : "master";
    const subject = row.insertCell();
    subject.className = "subject";
    subject.textContent = c.s;
    const result = (DATA.results[state.machine] || {})[sha];
    for (const panel of VD.panels) {
      const cell = row.insertCell();
      const val = panel.values[i];
      if (!val) {
        cell.className = "muted";
        cell.textContent = !result ? "—"
          : panel.wire ? (result.wire.reason || "skipped")
          : (result.normReason || "—");
        continue;
      }
      cell.className = "num";
      cell.textContent = val.vs.map((v, s) => fmtVal(v)
        + (val.sds ? " ± " + fmtVal(val.sds[s]) : "")).join(" / ");
    }
  });
  box.appendChild(table);
}

// --------------------------------------------------------- commit details

const commitHead = document.getElementById("commit-head");
const commitInfo = document.getElementById("commitinfo");

// One row of the definition list. The value is a string or a node.
function kv(list, label, value) {
  const dt = document.createElement("dt");
  dt.textContent = label;
  list.appendChild(dt);
  const dd = document.createElement("dd");
  if (typeof value === "string") dd.textContent = value;
  else dd.appendChild(value);
  list.appendChild(dd);
  return dd;
}

function anchor(href, text, mono) {
  const a = document.createElement("a");
  a.href = href;
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = text;
  if (mono) a.className = "mono";
  return a;
}

// The commit that the link names: the head of the branch, or the commit
// that the reader pinned. Nine characters of a sha say little on their
// own, so this shows what the commit is and where it sits, and hands the
// reader the commit itself, its pull request and its run.
function renderCommitDetails() {
  const sha = VD.head;
  const commit = sha ? DATA.commits[sha] : null;
  const onBranch = !!sha && VD.ref.branchPoint != null
    && VD.ref.commits.indexOf(sha) > VD.ref.branchPoint;
  commitHead.textContent = sha
    ? "· " + sha.slice(0, 9) + " · "
      + (onBranch ? VD.ref.label : "master")
    : "";
  commitInfo.replaceChildren();
  const list = document.createElement("dl");
  list.className = "kv";
  commitInfo.appendChild(list);
  if (!commit) {
    // A commit link to a commit that this page does not hold: the sha of
    // a branch that was pruned before the branch had a result.
    kv(list, "Commit", sha ? sha + " — not on this page" : "none");
    return;
  }
  const repo = onBranch ? VD.ref.repo : DATA.upstreamRepo;
  kv(list, "Commit",
     anchor("https://github.com/" + repo + "/commit/" + sha, sha, true));
  kv(list, "Date", commit.d);
  kv(list, "Where", onBranch
    ? (VD.ref.detached ? VD.ref.label : VD.ref.repo + " @ " + VD.ref.ref)
    : DATA.upstreamRepo + " @ master");
  kv(list, "Subject", commit.s);
  if (commit.pr != null)
    kv(list, "Pull request",
       anchor("https://github.com/" + DATA.upstreamRepo + "/pull/" + commit.pr,
              "#" + commit.pr + " ↗"));
  const machine = DATA.machines.find(m => m.id === state.machine) || {};
  const name = machine.label || state.machine;
  const result = (DATA.results[state.machine] || {})[sha];
  const cell = kv(list, "Result", !result ? "no run on " + name
    : (result.quick ? "partial run (one file, no wireDemo)" : "full run")
      + " on " + name);
  if (result && result.url) {
    cell.appendChild(document.createTextNode(" · "));
    cell.appendChild(anchor(result.url, "workflow run ↗"));
  }
}

// ----------------------------------------------------------------- export

// One panel as a figure to paste elsewhere: a single <svg> element that
// holds its own palette, title, legend and a link back to this view, and
// that asks nothing of the page around it.
//
// An inline <svg> in HTML puts its <style> in the host document, not in a
// scope of its own, so every rule of the figure names the class of its
// root element: a bare "text" rule would reach the whole blog post.
const EXPORT_CLASS = "clash-bench-figure";
const EXPORT_W = 760;
const EXPORT_FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif';

const exportBox = document.getElementById("exportbox");
const exportCode = document.getElementById("export-code");
const exportPreview = document.getElementById("export-preview");
const exportTheme = document.getElementById("export-theme");
const exportMsg = document.getElementById("export-msg");
// The panel in the dialog, and the theme that the reader chose last: the
// choice holds for the next figure as well.
let exportOf = null;
let exportMode = "auto";

// The address of this view, for the link in the figure. A page opened
// from a file has no address worth sharing; then the link goes to the
// published site, where the same query names the same view.
//
// The query comes from the state, not from the address bar: a page opened
// from a file cannot rewrite its address, so what is up there may be
// behind. See writeUrl().
function permalink() {
  const base = location.protocol === "http:" || location.protocol === "https:"
    ? location.origin + location.pathname : DATA.siteUrl;
  return base + "?" + viewQuery();
}

function permalinkLabel() {
  return permalink().replace(/^https?:\/\//, "").replace(/\?.*$/, "")
    .replace(/\/$/, "");
}

// The stylesheet of a figure: the palette, then the text styles that the
// figure needs. "light" and "dark" pin the palette, for a post that is
// one or the other; "auto" carries both.
//
// "auto" asks twice, the way palette_css() does for the page: the
// preference of the reader, and then data-theme, which a post with a
// theme switch of its own sets and which wins in both directions. A
// figure that only followed the operating system would stay light on a
// page the reader has just turned dark; one that only followed the
// attribute would stay light on a page that has no switch at all.
//
// Both rules look for the attribute on any ancestor, and not on the root
// element the way palette_css() does: this figure is a guest in a page
// whose shape it does not know, and a post may well put its theme on a
// wrapper rather than on <html>.
function exportCss(mode) {
  const root = "." + EXPORT_CLASS;
  const vars = (which, indent) => Object.keys(PALETTE[which])
    .map(k => indent + "--" + k + ": " + PALETTE[which][k] + ";").join("\n");
  let css = root + " {\n" + vars(mode === "dark" ? "dark" : "light", "  ")
    + "\n}\n";
  if (mode === "auto")
    css += "@media (prefers-color-scheme: dark) {\n  " + root
      + ':not([data-theme="light"] *) {\n'
      + vars("dark", "    ") + "\n  }\n}\n"
      + '[data-theme="dark"] ' + root + " {\n" + vars("dark", "  ")
      + "\n}\n";
  return css
    + root + " text { font: 10.5px " + EXPORT_FONT + "; fill: var(--muted); }\n"
    + root + " .title { font-size: 13px; font-weight: 650; fill: var(--ink-1); }\n"
    + root + " .sub { font-size: 10.5px; fill: var(--muted); }\n"
    + root + " .legend { font-size: 11px; fill: var(--ink-2); }\n"
    + root + " .endlabel { font-size: 11px; font-weight: 600; fill: var(--ink-2); }\n"
    + root + " .branchlabel { font-size: 10.5px; font-weight: 600; fill: var(--ink-2); }\n"
    + root + " .marklabel { font-size: 10.5px; font-weight: 600; fill: var(--ink-2); }\n"
    + root + " a text { text-decoration: underline; }\n";
}

// Build the figure of one panel. The plot itself comes from renderPanel,
// the same drawing that the page shows; this adds the header and the
// palette around it.
//
// The header is one line: the title of the panel, and the address of the
// view it came from. What is not in it - the note of the panel, the
// machine, the branch, the dates, the legend - belongs to the text of the
// post around the figure, where a writer says it in their own words, and
// the link carries all of it for a reader who wants the numbers.
function exportFigure(panel, mode) {
  const plotH = panel.headline ? 300 : 240;
  const holder = document.createElement("div");
  renderPanel(holder, panel, plotH, { width: EXPORT_W, static: true });
  const plot = holder.firstChild;
  if (!plot) return null;

  const pad = 16;
  const titleY = 30;
  const headH = 44;
  const H = headH + plotH;

  const svg = el("svg", {
    xmlns: "http://www.w3.org/2000/svg", class: EXPORT_CLASS,
    width: EXPORT_W, height: H, viewBox: "0 0 " + EXPORT_W + " " + H,
    // The attributes give a size to a reader that has no CSS; the style
    // lets the figure shrink into a narrow column.
    style: "width: 100%; max-width: " + EXPORT_W + "px; height: auto;",
  });
  const style = el("style", {});
  style.textContent = "\n" + exportCss(mode);
  svg.appendChild(style);
  // A square frame: a figure sits in the column of a post, among
  // paragraphs whose corners are square too.
  svg.appendChild(el("rect", { x: 0.5, y: 0.5, width: EXPORT_W - 1,
    height: H - 1, rx: 0, fill: "var(--surface-1)", stroke: "var(--border)" }));
  const title = el("text", { x: pad, y: titleY, class: "title" });
  title.textContent = panel.title;
  svg.appendChild(title);

  const link = el("a", { href: permalink(), target: "_blank" });
  const linkText = el("text", { x: EXPORT_W - pad, y: titleY, class: "sub",
    "text-anchor": "end" });
  linkText.textContent = permalinkLabel();
  link.appendChild(linkText);
  svg.appendChild(link);

  const g = el("g", { transform: "translate(0 " + headH + ")" });
  g.append(...plot.childNodes);
  svg.appendChild(g);
  return svg;
}

// The markup of a figure, one element to a line: a reader who opens the
// box sees what they are about to paste. The break goes between tags
// only, and never inside the text of a label.
//
// The coordinates come from a division and carry all the digits of it.
// Two decimals are a hundredth of a pixel, which is far below what a
// screen can show, and they take a third off the size of the markup.
function exportMarkup(svg) {
  return new XMLSerializer().serializeToString(svg)
    .replace(/\d+\.\d{3,}/g, n => String(Number(Number(n).toFixed(2))))
    .replace(/></g, ">\n<") + "\n";
}

function fillExport() {
  const svg = exportOf && exportFigure(exportOf, exportMode);
  const markup = svg ? exportMarkup(svg) : "";
  exportCode.value = markup;
  // The preview is the markup itself, parsed again: what the reader sees
  // is what the box hands them.
  exportPreview.innerHTML = markup;
  exportMsg.textContent = "";
}

function openExport(panel) {
  exportOf = panel;
  document.getElementById("export-name").textContent = panel.title;
  exportTheme.value = exportMode;
  fillExport();
  exportBox.showModal();
}

exportTheme.addEventListener("change", () => {
  exportMode = exportTheme.value;
  fillExport();
});
document.getElementById("export-copy").addEventListener("click", async () => {
  exportCode.focus();
  exportCode.select();
  let copied = false;
  try {
    await navigator.clipboard.writeText(exportCode.value);
    copied = true;
  } catch (err) {
    // No clipboard permission, or a page that is not a secure context.
    try { copied = document.execCommand("copy"); } catch (err2) { copied = false; }
  }
  exportMsg.textContent = copied ? "Copied."
    : "The markup is selected; press Ctrl+C or Cmd+C.";
});
// The name of a saved figure: what it shows, where it ran, on which
// branch, and the day of its newest commit. A directory of figures then
// says what each one is without opening any of them.
function exportFilename() {
  const slug = text => String(text).toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  const last = DATA.commits[VD.commits[VD.commits.length - 1]].d;
  return ["clash-bench", slug(exportOf.id), slug(state.machine),
          slug(VD.ref.label), last].filter(Boolean).join("-") + ".svg";
}
document.getElementById("export-save").addEventListener("click", () => {
  if (!exportCode.value) return;
  // The markup is a whole SVG document as well as an element to paste:
  // it names its own namespace and carries its own size, so a file of it
  // opens on its own.
  const blob = new Blob([exportCode.value], { type: "image/svg+xml" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = exportFilename();
  // In the document, and out of it again: a browser that wants the anchor
  // to be in the page before it follows a download gets its way.
  document.body.appendChild(link);
  link.click();
  link.remove();
  // The download reads the blob after the click, so let the URL live long
  // enough for it, rather than revoking it here.
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  exportMsg.textContent = "Saved " + link.download + ".";
});
document.getElementById("export-close").addEventListener("click", () => {
  exportBox.close();
});
// A click on the backdrop is a click on the dialog itself.
exportBox.addEventListener("click", e => {
  if (e.target === exportBox) exportBox.close();
});
exportBox.addEventListener("close", () => {
  exportOf = null;
  exportPreview.replaceChildren();
  exportCode.value = "";
});

// ----------------------------------------------------------------- render

// The panels that are on screen now, for redraw().
let DRAWN = [];

// The table is a row for each commit with a cell for each panel, and its
// box starts closed. On a chain of a few thousand commits that is tens
// of thousands of cells that nobody asked to see, so build them when the
// reader opens the box, and on a redraw only while it is open.
const tableBox = document.getElementById("tablebox");
let tableDrawn = false;
tableBox.addEventListener("toggle", () => {
  if (tableBox.open && !tableDrawn) {
    renderTable();
    tableDrawn = true;
  }
});

function drawPanel(entry) {
  const tight = renderPanel(entry.holder, entry.panel, entry.height);
  entry.heading.textContent =
    entry.panel.title + (tight ? " (" + entry.panel.unit + ")" : "");
}

function renderAll() {
  DRAWN = [];
  // The panels below are new objects. A dialog that is open holds one of
  // the old ones, of a view that is no longer on screen.
  if (exportBox.open) exportBox.close();
  const headline = document.getElementById("headline");
  const grid = document.getElementById("panels");
  const empty = document.getElementById("empty");
  headline.replaceChildren();
  grid.replaceChildren();
  empty.replaceChildren();
  document.getElementById("table").replaceChildren();
  tableDrawn = false;

  const machine = DATA.machines.find(m => m.id === state.machine) || {};
  const results = VD.commits.filter(
    sha => (DATA.results[state.machine] || {})[sha]).length;
  document.getElementById("subtitle").textContent =
    `${results} of ${VD.commits.length} commits measured · ${machine.label || state.machine}`
    + (machine.cpu ? ` · ${machine.cpu}` : "")
    + ` · ${VD.ref.label}` + (VD.ref.pr != null ? ` (#${VD.ref.pr})` : "")
    + ` · rendered ${DATA.generated}`;

  tableBox.style.display = VD.panels.length ? "" : "none";
  if (!VD.panels.length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = `No results for ${machine.label || state.machine} on `
      + `${VD.ref.label} in this date range.`;
    empty.appendChild(p);
    return;
  }

  for (const panel of VD.panels) {
    const card = document.createElement("div");
    card.className = "card";
    const head = document.createElement("div");
    head.className = "cardhead";
    const h = document.createElement("h2");
    head.appendChild(h);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "exportbtn";
    button.textContent = "Export";
    button.title = "Copy this graph as HTML";
    button.addEventListener("click", () => openExport(panel));
    head.appendChild(button);
    card.appendChild(head);
    if (panel.note) {
      const note = document.createElement("p");
      note.className = "note";
      note.textContent = panel.note;
      card.appendChild(note);
    }
    const items = legendItems(panel);
    if (items.length) {
      const leg = document.createElement("div");
      leg.className = "legend";
      for (const [color, name, band] of items) {
        const item = document.createElement("span");
        const sw = document.createElement("span");
        sw.className = band ? "swatch band" : "swatch";
        sw.style.background = color;
        item.appendChild(sw);
        item.appendChild(document.createTextNode(name));
        leg.appendChild(item);
      }
      card.appendChild(leg);
    }
    const holder = document.createElement("div");
    card.appendChild(holder);
    const entry = { panel, holder, heading: h,
                    height: panel.headline ? 280 : 190 };
    (panel.headline ? headline : grid).appendChild(card);
    drawPanel(entry);
    DRAWN.push(entry);
  }
  if (tableBox.open) {
    renderTable();
    tableDrawn = true;
  }
}

// ------------------------------------------------------------------ state

const machineSelect = document.getElementById("f-machine");
const refSelect = document.getElementById("f-ref");
const pinButton = document.getElementById("f-pin");
const metricSelect = document.getElementById("f-metric");
const fromInput = document.getElementById("f-from");
const toInput = document.getElementById("f-to");

// The selection is either a branch of the selector or one commit. A
// pinned commit keeps its sha here even when a branch carries it: the sha
// is what makes the link permanent, so writeUrl() must not replace it
// with the name of the branch that carries it today.
const state = { machine: DATA.machines[0].id,
                sel: { type: "branch", key: "master" }, metric: "time",
                from: null, to: null };
// "head" is the commit that the link names: the pinned commit, or the
// newest commit of the branch. It comes from the whole branch and not
// from the date range, so that narrowing the range leaves the link alone.
let VD = { commits: [], panels: [], ref: DATA.refs[0], branchPoint: null,
           head: null };

const MASTER = DATA.refs[0];
const MASTER_INDEX = new Map(MASTER.commits.map((sha, i) => [sha, i]));

for (const m of DATA.machines) {
  const option = document.createElement("option");
  option.value = m.id;
  option.textContent = m.label + " (" + m.results + " results)";
  machineSelect.appendChild(option);
}
for (const r of DATA.refs) {
  const option = document.createElement("option");
  option.value = r.key;
  option.textContent = r.key === "master" ? "master"
    : (r.pr != null ? "#" + r.pr + " " : "") + r.repo + " @ " + r.ref;
  refSelect.appendChild(option);
}

// Grow a sha prefix from the URL to the full sha, when there is exactly
// one commit that matches.
function findCommit(prefix) {
  if (!prefix || DATA.commits[prefix]) return prefix || null;
  if (prefix.length < 7) return null;
  let found = null;
  for (const sha in DATA.commits) {
    if (sha.startsWith(prefix)) {
      if (found) return null;
      found = sha;
    }
  }
  return found;
}

// The view of one commit that no branch of the selector carries: the
// commit and its first-parent ancestors, joined to master where the
// parents reach it. The parents come from the branch snapshots and from
// the results themselves, so this works after the branch is pruned.
function detachedRef(sha) {
  const chain = [];
  const seen = new Set();
  let cur = sha;
  while (cur && DATA.commits[cur] && !MASTER_INDEX.has(cur) && !seen.has(cur)) {
    seen.add(cur);
    chain.push(cur);
    cur = DATA.commits[cur].p;
  }
  chain.reverse();
  let head = [], branchPoint = -1;
  if (cur && MASTER_INDEX.has(cur)) {
    head = MASTER.commits.slice(0, MASTER_INDEX.get(cur) + 1);
    branchPoint = head.length - 1;
  }
  return {
    key: "detached", detached: true,
    label: "detached -- " + sha.slice(0, 9),
    repo: DATA.upstreamRepo, ref: null, pr: null,
    commits: head.concat(chain),
    branchPoint: branchPoint,
  };
}

// Find the branch that carries one commit: a branch of the selector where
// the commit sits past the branch point, else master, else nothing - then
// the commit gets a detached view.
function resolveView(rawSha) {
  const sha = findCommit(rawSha);
  if (sha) {
    for (const ref of DATA.refs) {
      if (ref.branchPoint != null && ref.commits.indexOf(sha) > ref.branchPoint)
        return ref;
    }
    if (MASTER_INDEX.has(sha)) return MASTER;
  }
  return detachedRef(sha || rawSha);
}

// A branch of the URL: the key of the selector, "owner/repo@name", or the
// name of the branch on its own when only one repository has a branch of
// that name. A link written by hand says ?branch=my-work.
function findRef(name) {
  if (!name) return null;
  const key = DATA.refs.find(r => r.key === name);
  if (key) return key;
  const named = DATA.refs.filter(r => r.ref === name);
  return named.length === 1 ? named[0] : null;
}

// The same, over the branches that are out of the selector: the ones
// whose pull request is closed. They keep the newest commit that their
// snapshot saw.
function findPruned(name) {
  if (!name) return null;
  const key = DATA.pruned.find(p => p.key === name);
  if (key) return key;
  const named = DATA.pruned.filter(p => p.ref === name);
  return named.length === 1 ? named[0] : null;
}

// The view comes from the query string: ?machine=<id> with &branch=<key>,
// or &commit=<sha> once the reader pins one, plus &metric=<m>.
//
// Older links put the same names in the fragment, and always named a
// commit, as "head". Those still work: a link in an issue or a message
// must not rot.
function readUrl() {
  const search = new URLSearchParams(location.search);
  const hash = new URLSearchParams(location.hash.slice(1));
  const get = name => search.get(name) || hash.get(name);
  const machine = get("machine");
  if (machine && DATA.machines.some(m => m.id === machine)) state.machine = machine;
  const commit = get("commit") || get("head");
  const name = get("branch") || get("ref");
  const ref = findRef(name);
  // A branch that is out of the selector: its pull request closed, or the
  // branch went away and took its snapshot with it. As long as the page
  // still holds the snapshot, the link lands on the newest commit that
  // the snapshot saw, as a detached view of the branch. That is worth
  // more than a silent jump to master.
  const gone = ref ? null : findPruned(name);
  // A link that names nothing means the default view, not "keep what is
  // there": the URL names the whole view. So does a missing metric.
  state.sel = commit ? { type: "commit", sha: commit.toLowerCase() }
    : gone ? { type: "commit", sha: gone.head }
    : { type: "branch", key: ref ? ref.key : "master" };
  const metric = get("metric");
  state.metric = ["memory", "alloc", "gc"].includes(metric) ? metric : "time";
}

// The query of this view, as it goes into the address bar and into the
// link of an exported figure.
function viewQuery() {
  const params = new URLSearchParams();
  params.set("machine", state.machine);
  if (state.sel.type === "commit") params.set("commit", state.sel.sha);
  else params.set("branch", state.sel.key);
  if (state.metric !== "time") params.set("metric", state.metric);
  // A query may hold "/" and "@" as they are, and the key of a branch is
  // full of both: owner/repo@name reads better than its escaped form.
  return params.toString().replace(/%2F/g, "/").replace(/%40/g, "@");
}

function writeUrl() {
  try {
    history.replaceState(null, "", location.pathname + "?" + viewQuery());
  } catch (err) {
    // A page opened from a file has an opaque origin, and a browser
    // refuses to rewrite the address of such a page. Only the address bar
    // stays behind; the page itself works, and permalink() reads the
    // state rather than the address.
  }
}

// Show the view in the branch selector. A detached view is not a branch,
// so it gets an option of its own for as long as it is on screen.
function syncRefSelect(ref) {
  let detachedOption = document.getElementById("detached-option");
  if (!ref.detached) {
    if (detachedOption) detachedOption.remove();
    refSelect.value = ref.key;
    return;
  }
  if (!detachedOption) {
    detachedOption = document.createElement("option");
    detachedOption.id = "detached-option";
    detachedOption.value = "detached";
    refSelect.appendChild(detachedOption);
  }
  detachedOption.textContent = ref.label;
  refSelect.value = "detached";
}

// The button that turns a branch link into a commit link, and back. A
// commit link that no branch carries has nothing to go back to, so there
// the button is out of use.
function syncPin() {
  const pinned = state.sel.type === "commit";
  const stuck = pinned && VD.ref.detached;
  pinButton.setAttribute("aria-pressed", pinned ? "true" : "false");
  pinButton.textContent = pinned ? "Pinned to commit" : "Pin to commit";
  pinButton.disabled = stuck;
  pinButton.title = stuck
    ? "No branch of the selector carries this commit, so the link names the "
      + "commit"
    : pinned
    ? "The link names this commit. Press again, or \"y\", to name the branch"
    : "Name this commit in the link instead of the branch, so that the link "
      + "keeps this view after the branch moves (shortcut: \"y\")";
}

function togglePin() {
  if (state.sel.type === "commit") {
    if (VD.ref.detached) return;
    state.sel = { type: "branch", key: VD.ref.key };
  } else {
    if (!VD.head) return;
    state.sel = { type: "commit", sha: VD.head };
  }
  apply();
}

// Rebuild the view from the state: pick the branch, build the panels,
// then cut everything to the date range.
function apply() {
  const ref = state.sel.type === "commit" ? resolveView(state.sel.sha)
    : DATA.refs.find(r => r.key === state.sel.key) || DATA.refs[0];
  const head = state.sel.type === "commit"
    ? (findCommit(state.sel.sha) || state.sel.sha)
    : ref.commits[ref.commits.length - 1] || null;
  const panels = buildPanels(state.machine, ref, state.metric);
  const dates = ref.commits.map(sha => DATA.commits[sha].d);
  const first = dates[0] || "", last = dates[dates.length - 1] || "";

  fromInput.min = toInput.min = first;
  fromInput.max = toInput.max = last;
  if (!state.from || state.from < first || state.from > last) state.from = first;
  if (!state.to || state.to > last || state.to < first) state.to = last;
  fromInput.value = state.from;
  toInput.value = state.to;

  let s = null, e = null;
  dates.forEach((d, i) => {
    if (d >= state.from && d <= state.to) {
      if (s == null) s = i;
      e = i;
    }
  });
  if (s == null) {
    VD = { commits: [], panels: [], ref, branchPoint: null, head };
  } else {
    let bp = ref.branchPoint;
    if (bp != null) bp = Math.max(-1, bp - s);
    VD = {
      commits: ref.commits.slice(s, e + 1),
      panels: panels
        .map(p => Object.assign({}, p, { values: p.values.slice(s, e + 1) }))
        .filter(p => p.values.some(Boolean)),
      ref,
      branchPoint: bp,
      head,
    };
  }
  machineSelect.value = state.machine;
  metricSelect.value = state.metric;
  syncRefSelect(ref);
  syncPin();
  writeUrl();
  renderCommitDetails();
  renderAll();
}

machineSelect.addEventListener("change", () => {
  state.machine = machineSelect.value;
  apply();
});
refSelect.addEventListener("change", () => {
  if (refSelect.value === "detached") return;
  // Picking a branch names the branch again, whatever was pinned before.
  state.sel = { type: "branch", key: refSelect.value };
  // The branches cover different date ranges. Start from the whole range.
  state.from = state.to = null;
  markPreset(document.querySelector('.filters button[data-days="0"]'));
  apply();
});
pinButton.addEventListener("click", togglePin);
// "y" pins and unpins, the key that GitHub uses for the same move. Not
// while a field or the export dialog has the key.
addEventListener("keydown", e => {
  if (e.key !== "y" && e.key !== "Y") return;
  if (e.metaKey || e.ctrlKey || e.altKey || exportBox.open) return;
  const tag = e.target && e.target.tagName;
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
  e.preventDefault();
  togglePin();
});
metricSelect.addEventListener("change", () => {
  state.metric = metricSelect.value;
  // Another metric of the same commits: the date range stays.
  apply();
});

// The date presets, and only those: the other buttons of a filter row
// carry a state of their own.
function markPreset(button) {
  for (const b of document.querySelectorAll(".filters button[data-days]"))
    b.setAttribute("aria-pressed", b === button ? "true" : "false");
}
for (const b of document.querySelectorAll(".filters button[data-days]")) {
  b.addEventListener("click", () => {
    // The presets count back from the newest commit, not from today.
    const days = Number(b.dataset.days);
    const chain = VD.ref.commits;
    state.to = null;
    if (!days || !chain.length) {
      state.from = null;
    } else {
      const last = DATA.commits[chain[chain.length - 1]].d;
      const d = new Date(last + "T00:00:00Z");
      d.setUTCDate(d.getUTCDate() - days);
      state.from = d.toISOString().slice(0, 10);
    }
    markPreset(b);
    apply();
  });
}
for (const input of [fromInput, toInput]) {
  input.addEventListener("change", () => {
    state.from = fromInput.value || null;
    state.to = toInput.value || null;
    markPreset(null);
    apply();
  });
}
// The address can change under the page: the back button after a link
// within the page, or a fragment that a reader edits by hand.
addEventListener("popstate", () => { readUrl(); apply(); });
addEventListener("hashchange", () => { readUrl(); apply(); });

function redraw() {
  DRAWN.forEach(drawPanel);
}

// A plot gets the width of its card at the moment that it is drawn. Two
// things change that width later: a new selection, and the scrollbar that
// appears when the page becomes longer than the window. Watch the width
// of the grid and draw the plots again when it changes.
//
// Watch the element, not the window: a phone fires "resize" while it
// scrolls, because the address bar comes and goes, and only the height
// changes there. Redrawing the plots in place also keeps the height of
// the page, so the reader stays where they are. A rebuild of the page
// would send them back to the top.
let observedWidth = 0;
let redrawTimer = null;
new ResizeObserver(entries => {
  const width = entries[0].contentRect.width;
  if (Math.abs(width - observedWidth) < 1) return;
  observedWidth = width;
  clearTimeout(redrawTimer);
  redrawTimer = setTimeout(redraw, 120);
}).observe(document.getElementById("panels"));

readUrl();
apply();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())

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

The page holds all data. It gives the reader two selectors:

- the machine. Numbers from different machines are not comparable, so the
  page shows one machine at a time.
- the branch. The default is master. For another branch, the x-axis is
  master up to the branch point, then the commits of the branch. The
  commits of the branch have their own colour.

Master comes from the clone, not from the results: this way the graph also
shows the commits that have no result yet, as holes. A branch comes from
its snapshot in branches/, because a branch does not stay where it is.
See bench/result_schema.py.
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
    return path


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


def load_branches(root):
    """Read the branch snapshots."""
    snapshots = []
    for path in sorted((root / "branches").glob("**/*.json")):
        snapshot = json.loads(path.read_text())
        problems = validate_branch(snapshot)
        if problems:
            for problem in problems:
                print(f"{path}: {problem}", file=sys.stderr)
            sys.exit("render.py: bad branch snapshot")
        snapshots.append(snapshot)
    return snapshots


def master_chain(repo, ref, known):
    """Return the first-parent commits of master, oldest first.

    The chain starts at the oldest commit that has a result: older
    commits say nothing. It ends at the head of master, so the graph also
    shows the newest commits that have no result yet.
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
    args = parser.parse_args()

    results = load_results(args.data)
    machines = load_machines(args.data, results)
    snapshots = load_branches(args.data)

    known = {sha for own in results.values() for sha in own}
    master = master_chain(clash_clone(args), args.clash_ref, known)
    master_index = {c["sha"]: i for i, c in enumerate(master)}

    # One metadata record for each commit, for the tooltips and the table.
    commits = {}

    def add_commit(commit):
        commits.setdefault(commit["sha"], {
            "s": commit["subject"],
            "d": commit["date"],
            "pr": pr_number(commit["subject"]),
        })

    for commit in master:
        add_commit(commit)

    refs = [{
        "key": "master",
        "label": "master",
        "repo": UPSTREAM_REPO,
        "ref": "master",
        "commits": [c["sha"] for c in master],
        "branchPoint": None,
    }]

    for snapshot in snapshots:
        for commit in snapshot["commits"]:
            add_commit(commit)
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
            "commits": head + [c["sha"] for c in snapshot["commits"]],
            "branchPoint": branch_point,
        })

    # The measurements, in a small form: one entry for each machine and
    # commit that has a result.
    benchmarks = set()
    packed = {}
    for machine, own in results.items():
        for sha, result in own.items():
            benchmarks.update(result["normalization"])
            wd = result["wire_demo"]
            entry = {
                "norm": {name: [v["mean_s"], v["stddev_s"]]
                         for name, v in result["normalization"].items()},
                "wire": {"status": wd["status"]},
                "quick": result["run"]["quick"],
                "url": result["run"]["workflow_run_url"],
            }
            if wd["status"] == "ok" and wd["runs"]:
                run = wd["runs"][0]
                entry["wire"].update({
                    "norm_s": run["normalization_s"],
                    "netlist_s": run["netlist_s"],
                    "total_s": run["total_s"],
                    "overlays": wd["overlays"],
                })
            else:
                entry["wire"]["reason"] = wd["skip_reason"]
            packed.setdefault(machine, {})[sha] = entry

    data = {
        "generated": now("minutes"),
        "upstreamRepo": UPSTREAM_REPO,
        "msLimit": MS_LIMIT,
        "machines": machines,
        "commits": commits,
        "refs": refs,
        "results": packed,
        "benchmarks": sorted(benchmarks),
    }

    subtitle = (f"{sum(len(o) for o in results.values())} results · "
                f"{len(machines)} machine(s) · {len(refs) - 1} branch(es) · "
                f"master {master[0]['sha'][:9]}..{master[-1]['sha'][:9]}")
    page = (TEMPLATE
            .replace("__DATA__", json.dumps(data, separators=(",", ":")))
            .replace("__SUBTITLE__", html.escape(subtitle)))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page)
    print(f"render.py: wrote {args.out} ({len(page) // 1024} KiB): {subtitle}")
    return 0


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clash benchmark history</title>
<style>
  /* Colours: categorical slots 1 to 3 of the reference palette. Slot 3
     is the branch accent. Both modes are selected, not flipped. */
  .viz-root {
    color-scheme: light;
    --surface-1: #fcfcfb;
    --page: #f9f9f7;
    --ink-1: #0b0b0b;
    --ink-2: #52514e;
    --muted: #898781;
    --grid: #e1e0d9;
    --axis: #c3c2b7;
    --series-1: #2a78d6;
    --series-2: #eb6834;
    --branch: #1baf7a;
    --border: rgba(11,11,11,0.10);
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) .viz-root {
      color-scheme: dark;
      --surface-1: #1a1a19;
      --page: #0d0d0d;
      --ink-1: #ffffff;
      --ink-2: #c3c2b7;
      --muted: #898781;
      --grid: #2c2c2a;
      --axis: #383835;
      --series-1: #3987e5;
      --series-2: #d95926;
      --branch: #199e70;
      --border: rgba(255,255,255,0.10);
    }
  }
  :root[data-theme="dark"] .viz-root {
    color-scheme: dark;
    --surface-1: #1a1a19;
    --page: #0d0d0d;
    --ink-1: #ffffff;
    --ink-2: #c3c2b7;
    --muted: #898781;
    --grid: #2c2c2a;
    --axis: #383835;
    --series-1: #3987e5;
    --series-2: #d95926;
    --branch: #199e70;
    --border: rgba(255,255,255,0.10);
  }
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
  .filters {
    display: flex; flex-wrap: wrap; gap: 10px 14px; align-items: center;
    margin: 16px 0; font-size: 12.5px; color: var(--ink-2);
  }
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
  .filters label { display: flex; gap: 6px; align-items: center; }
  .filters .sep { width: 1px; height: 20px; background: var(--border); }
  .card h2 { font-size: 13px; font-weight: 600; margin: 0 0 2px; color: var(--ink-1); }
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
  svg .plot-hit { cursor: pointer; outline: none; }
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
    max-width: 340px;
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
  <div class="filters">
    <label>Machine <select id="f-machine"></select></label>
    <label>Branch <select id="f-ref"></select></label>
    <span class="sep"></span>
    <button type="button" data-days="30">Last 30 days</button>
    <button type="button" data-days="90">Last 90 days</button>
    <button type="button" data-days="0" aria-pressed="true">All</button>
    <label>From <input type="date" id="f-from"></label>
    <label>To <input type="date" id="f-to"></label>
  </div>
  <div id="headline"></div>
  <div class="grid" id="panels"></div>
  <div id="empty"></div>
  <details id="tablebox">
    <summary>Table view</summary>
    <div class="tablewrap" id="table"></div>
  </details>
</div>
<div id="tooltip" role="status"></div>
<script>
const DATA = __DATA__;

const M = { top: 14, right: 56, bottom: 26, left: 64 };
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

function commitUrl(sha, onBranch) {
  const c = DATA.commits[sha];
  const repo = onBranch ? VD.ref.repo : DATA.upstreamRepo;
  if (!onBranch && c.pr != null)
    return "https://github.com/" + DATA.upstreamRepo + "/pull/" + c.pr;
  return "https://github.com/" + repo + "/commit/" + sha;
}

// ------------------------------------------------------------ panel model

// Build the panels of one machine and one branch. A panel holds one value
// for each commit of the branch, or null where there is no result.
function buildPanels(machineId, ref) {
  const own = DATA.results[machineId] || {};
  const shas = ref.commits;
  const panels = [];

  const wireValues = shas.map(sha => {
    const r = own[sha];
    if (!r || r.wire.status !== "ok") return null;
    return { vs: [r.wire.norm_s, r.wire.total_s], extra: r.wire.overlays || [] };
  });
  if (wireValues.some(Boolean)) {
    panels.push({
      id: "wire_demo",
      title: "wireDemo (bittide-hardware)",
      note: "Clash times of one HDL generation of wireDemoTest",
      unit: "s",
      series: ["normalization", "total"],
      colors: SERIES_COLORS,
      values: wireValues,
      headline: true,
    });
  }

  for (const name of DATA.benchmarks) {
    const means = shas
      .map(sha => own[sha] && own[sha].norm[name])
      .filter(Boolean)
      .map(entry => entry[0]);
    if (!means.length) continue;
    const useMs = Math.max(...means) < DATA.msLimit;
    const scale = useMs ? 1000 : 1;
    panels.push({
      id: "norm-" + name,
      title: name.split("/").pop(),
      note: name,
      unit: useMs ? "ms" : "s",
      series: ["mean"],
      colors: [SERIES_COLORS[0]],
      values: shas.map(sha => {
        const entry = own[sha] && own[sha].norm[name];
        if (!entry) return null;
        return { vs: [entry[0] * scale], sds: [entry[1] * scale] };
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
    " · " + c.d + " · " + (onBranch ? "branch " + VD.ref.label : "master")));
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

function renderPanel(container, panel, height) {
  container.replaceChildren();
  const W = Math.max(280, container.clientWidth);
  const H = height;
  const iw = W - M.left - M.right, ih = H - M.top - M.bottom;
  const n = VD.commits.length;
  const ns = panel.series.length;
  const pts = [];
  panel.values.forEach((val, i) => { if (val) pts.push([i, val]); });
  if (!pts.length) return;

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
  if (positive && lo < 0) lo = 0; // A time is never negative.

  const x = i => M.left + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
  const y = v => M.top + ih - ((v - lo) / (hi - lo)) * ih;

  const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}` });

  // The band behind the commits of the branch. It carries the branch,
  // together with the rule at the branch point and the legend.
  const bp = VD.branchPoint;
  if (bp != null && bp < n - 1) {
    const x0 = bp < 0 ? M.left : x(bp);
    svg.appendChild(el("rect", { x: x0, y: M.top, width: M.left + iw - x0,
      height: ih, fill: BRANCH_COLOR, opacity: 0.07 }));
  }

  const { ticks, step } = niceTicks(lo, hi, Math.max(3, Math.floor(ih / 45)));
  for (const t of ticks) {
    svg.appendChild(el("line", { x1: M.left, x2: M.left + iw, y1: y(t), y2: y(t),
      stroke: "var(--grid)", "stroke-width": 1 }));
    const lab = el("text", { x: M.left - 8, y: y(t) + 3.5, "text-anchor": "end",
      style: "font-variant-numeric: tabular-nums" });
    lab.textContent = fmt(t, step);
    svg.appendChild(lab);
  }
  svg.appendChild(el("line", { x1: M.left, x2: M.left + iw,
    y1: M.top + ih, y2: M.top + ih, stroke: "var(--axis)", "stroke-width": 1 }));

  const cy = M.top + ih / 2;
  const ylab = el("text", { x: 12, y: cy, "text-anchor": "middle",
    transform: `rotate(-90 12 ${cy})` });
  ylab.textContent = "time (" + panel.unit + ")";
  svg.appendChild(ylab);

  const nx = Math.max(2, Math.floor(iw / 120));
  const seen = new Set();
  let prevDate = null;
  for (let k = 0; k < nx; k++) {
    const i = Math.round((k / (nx - 1)) * (n - 1));
    const date = DATA.commits[VD.commits[i]].d;
    if (seen.has(i) || date === prevDate) continue;
    seen.add(i);
    prevDate = date;
    const lab = el("text", { x: x(i), y: M.top + ih + 16, "text-anchor": "middle" });
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
    svg.appendChild(el("line", { x1: x(bp), x2: x(bp), y1: M.top, y2: M.top + ih,
      stroke: BRANCH_COLOR, "stroke-width": 1.5, "stroke-dasharray": "4 3" }));
    if (panel.headline) {
      // Keep the label inside the plot: put it left of the rule when the
      // branch point sits near the right edge.
      const late = x(bp) > M.left + iw * 0.6;
      const lab = el("text", { x: x(bp) + (late ? -6 : 6), y: M.top + 11,
        "text-anchor": late ? "end" : "start", class: "branchlabel" });
      lab.textContent = "branch point " + VD.commits[bp].slice(0, 9);
      svg.appendChild(lab);
    }
  }

  const dotSpacing = pts.length > 1 ? iw / (pts.length - 1) : iw;
  const drawDots = dotSpacing >= 7;
  const markers = new Map();
  for (const [i, val] of pts) {
    const dots = [];
    for (let s = 0; s < ns; s++) {
      const color = isBranch(i) && ns === 1 ? BRANCH_COLOR : panel.colors[s];
      const dot = el("circle", { cx: x(i), cy: y(val.vs[s]), r: 4,
        fill: color, stroke: "var(--surface-1)", "stroke-width": 2 });
      if (!drawDots) dot.setAttribute("visibility", "hidden");
      svg.appendChild(dot);
      dots.push(dot);
    }
    markers.set(i, dots);
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

  const cross = el("line", { y1: M.top, y2: M.top + ih,
    stroke: "var(--axis)", "stroke-width": 1, visibility: "hidden" });
  svg.appendChild(cross);

  const hit = el("rect", { x: M.left, y: M.top, width: iw, height: ih,
    fill: "transparent", class: "plot-hit", tabindex: 0 });
  svg.appendChild(hit);

  let active = null;
  function setDots(i, r, forceShow) {
    for (const dot of markers.get(i) || []) {
      dot.setAttribute("r", r);
      if (forceShow) dot.setAttribute("visibility", "visible");
      else if (!drawDots) dot.setAttribute("visibility", "hidden");
    }
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
  hit.addEventListener("pointermove", e => {
    const rect = svg.getBoundingClientRect();
    const fx = (e.clientX - rect.left - M.left) / iw * (n - 1);
    const i = nearest(fx);
    if (i != null) highlight(i, e.clientX, e.clientY);
  });
  hit.addEventListener("click", () => { if (active != null) openCommit(active); });
  hit.addEventListener("pointerleave", clear);
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
          : panel.id === "wire_demo" ? (result.wire.reason || "skipped") : "—";
        continue;
      }
      cell.className = "num";
      cell.textContent = val.vs.map((v, s) => fmtVal(v)
        + (val.sds ? " ± " + fmtVal(val.sds[s]) : "")).join(" / ");
    }
  });
  box.appendChild(table);
}

// ----------------------------------------------------------------- render

function renderAll() {
  const headline = document.getElementById("headline");
  const grid = document.getElementById("panels");
  const empty = document.getElementById("empty");
  headline.replaceChildren();
  grid.replaceChildren();
  empty.replaceChildren();

  const machine = DATA.machines.find(m => m.id === state.machine) || {};
  const results = VD.commits.filter(
    sha => (DATA.results[state.machine] || {})[sha]).length;
  document.getElementById("subtitle").textContent =
    `${results} of ${VD.commits.length} commits measured · ${machine.label || state.machine}`
    + (machine.cpu ? ` · ${machine.cpu}` : "")
    + ` · ${VD.ref.label} · rendered ${DATA.generated}`;

  document.getElementById("tablebox").style.display =
    VD.panels.length ? "" : "none";
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
    const h = document.createElement("h2");
    h.textContent = panel.title;
    card.appendChild(h);
    if (panel.note) {
      const note = document.createElement("p");
      note.className = "note";
      note.textContent = panel.note;
      card.appendChild(note);
    }
    const items = [];
    if (panel.series.length > 1)
      panel.series.forEach((name, k) => items.push([panel.colors[k], name, false]));
    if (VD.branchPoint != null) {
      if (panel.series.length === 1)
        items.push([BRANCH_COLOR, "on branch " + VD.ref.label, false]);
      else
        items.push([BRANCH_COLOR, "commits on branch " + VD.ref.label, true]);
    }
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
    if (panel.headline) {
      headline.appendChild(card);
      renderPanel(holder, panel, 280);
    } else {
      grid.appendChild(card);
      renderPanel(holder, panel, 190);
    }
  }
  renderTable();
}

// ------------------------------------------------------------------ state

const machineSelect = document.getElementById("f-machine");
const refSelect = document.getElementById("f-ref");
const fromInput = document.getElementById("f-from");
const toInput = document.getElementById("f-to");

const state = { machine: DATA.machines[0].id, ref: "master", from: null, to: null };
let VD = { commits: [], panels: [], ref: DATA.refs[0], branchPoint: null };

for (const m of DATA.machines) {
  const option = document.createElement("option");
  option.value = m.id;
  option.textContent = m.label + " (" + m.results + " results)";
  machineSelect.appendChild(option);
}
for (const r of DATA.refs) {
  const option = document.createElement("option");
  option.value = r.key;
  option.textContent = r.key === "master" ? "master" : r.repo + " @ " + r.ref;
  refSelect.appendChild(option);
}

function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  const machine = params.get("machine");
  const ref = params.get("ref");
  if (machine && DATA.machines.some(m => m.id === machine)) state.machine = machine;
  if (ref && DATA.refs.some(r => r.key === ref)) state.ref = ref;
}

function writeHash() {
  const params = new URLSearchParams();
  params.set("machine", state.machine);
  params.set("ref", state.ref);
  history.replaceState(null, "", "#" + params.toString());
}

// Rebuild the view from the state: pick the branch, build the panels,
// then cut everything to the date range.
function apply() {
  const ref = DATA.refs.find(r => r.key === state.ref) || DATA.refs[0];
  const panels = buildPanels(state.machine, ref);
  const dates = ref.commits.map(sha => DATA.commits[sha].d);
  const first = dates[0], last = dates[dates.length - 1];

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
    VD = { commits: [], panels: [], ref, branchPoint: null };
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
    };
  }
  machineSelect.value = state.machine;
  refSelect.value = state.ref;
  writeHash();
  renderAll();
}

machineSelect.addEventListener("change", () => {
  state.machine = machineSelect.value;
  apply();
});
refSelect.addEventListener("change", () => {
  state.ref = refSelect.value;
  // The branches cover different date ranges. Start from the whole range.
  state.from = state.to = null;
  markPreset(document.querySelector('.filters button[data-days="0"]'));
  apply();
});

function markPreset(button) {
  for (const b of document.querySelectorAll(".filters button"))
    b.setAttribute("aria-pressed", b === button ? "true" : "false");
}
for (const b of document.querySelectorAll(".filters button")) {
  b.addEventListener("click", () => {
    // The presets count back from the newest commit, not from today.
    const days = Number(b.dataset.days);
    state.to = null;
    if (!days) {
      state.from = null;
    } else {
      const ref = DATA.refs.find(r => r.key === state.ref) || DATA.refs[0];
      const last = DATA.commits[ref.commits[ref.commits.length - 1]].d;
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
addEventListener("hashchange", () => { readHash(); apply(); });

let resizeTimer = null;
addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(renderAll, 150);
});

readHash();
apply();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())

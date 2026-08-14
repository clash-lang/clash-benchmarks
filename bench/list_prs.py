#!/usr/bin/env python3
"""Write the open pull requests of clash-compiler to a JSON file.

Usage:
  list_prs.py [--repo owner/name] [--out PATH] [--max-pages N]

  --repo REPO     repository to ask about (default clash-lang/clash-compiler)
  --out PATH      file to write (default: standard output)
  --max-pages N   pages of 100 to read at most (default 5)

The bot needs two things from GitHub that a clone cannot give:

- which open pull requests carry the label that asks for a benchmark.
  bench/pr_catchup.py turns those into work.
- which branch snapshots still belong to an open pull request. The site
  shows those, and only those. See bench/prune_branches.py.

Both come from the same call, so one script makes the list and both
consumers read the file:

    {
      "repo": "clash-lang/clash-compiler",
      "fetched": "2026-08-14T07:02:41+00:00",
      "prs": [
        {
          "number": 3345,
          "title": "Make normalization faster",
          "draft": false,
          "labels": ["performance"],
          "head_repo": "someone/clash-compiler",
          "head_ref": "perf/faster-strings",
          "head_sha": "<40 hex>",
          "base_ref": "master"
        }
      ]
    }

A draft pull request is in the list: a draft that says it makes Clash
faster is exactly the thing to measure.

A pull request whose fork is gone has no head repository. The script
leaves it out: there is nothing left to benchmark, and nothing that a
branch snapshot could still describe.

The container has no requests module, so this uses urllib. GITHUB_TOKEN
authenticates the call when it is set. Without a token the call still
works on a public repository, with a much smaller rate limit.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from result_schema import now  # noqa: E402

API = "https://api.github.com"
PER_PAGE = 100
TIMEOUT = 30


def get(url, token):
    """Read one page of the API. Any failure ends the script."""
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "clash-benchmarks",
    })
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        remaining = error.headers.get("X-RateLimit-Remaining")
        note = " (rate limit is used up)" if remaining == "0" else ""
        sys.exit(f"list_prs.py: {url} gave HTTP {error.code}{note}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        sys.exit(f"list_prs.py: {url} failed: {error}")


def load(path):
    """Read back a file that this script wrote."""
    with open(path) as f:
        data = json.load(f)
    for key in ("repo", "prs"):
        if not isinstance(data, dict) or key not in data:
            sys.exit(f"{path}: not a pull request list from list_prs.py: "
                     f"there is no {key!r}")
    return data


def with_label(data, label):
    """Return the pull requests that carry one label, lowest number first."""
    wanted = label.lower()
    return [pr for pr in data["prs"]
            if any(name.lower() == wanted for name in pr["labels"])]


def by_head(data):
    """Map (head repo, head ref) to the lowest open pull request number."""
    heads = {}
    for pr in sorted(data["prs"], key=lambda pr: pr["number"]):
        heads.setdefault((pr["head_repo"], pr["head_ref"]), pr["number"])
    return heads


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", default="clash-lang/clash-compiler")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--max-pages", type=int, default=5)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    prs = []
    for page in range(1, args.max_pages + 1):
        batch = get(
            f"{API}/repos/{args.repo}/pulls"
            f"?state=open&per_page={PER_PAGE}&page={page}",
            token,
        )
        if not isinstance(batch, list):
            sys.exit(f"list_prs.py: unexpected answer for page {page}")
        for pr in batch:
            head = pr.get("head") or {}
            head_repo = (head.get("repo") or {}).get("full_name")
            if not head_repo:
                print(f"list_prs.py: #{pr['number']} has no head repository "
                      f"any more, leaving it out", file=sys.stderr)
                continue
            prs.append({
                "number": pr["number"],
                "title": pr.get("title") or "",
                "draft": bool(pr.get("draft")),
                "labels": sorted(label["name"] for label in pr.get("labels", [])),
                "head_repo": head_repo,
                "head_ref": head.get("ref") or "",
                "head_sha": head.get("sha") or "",
                "base_ref": (pr.get("base") or {}).get("ref") or "",
            })
        if len(batch) < PER_PAGE:
            break
    else:
        print(f"list_prs.py: stopped after {args.max_pages} pages; there may be "
              f"more open pull requests (--max-pages)", file=sys.stderr)

    prs.sort(key=lambda pr: pr["number"])
    data = {"repo": args.repo, "fetched": now(), "prs": prs}

    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(f"list_prs.py: wrote {args.out}: {len(prs)} open pull requests "
              f"of {args.repo}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

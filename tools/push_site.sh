#!/usr/bin/env bash
# Put the rendered site on the pages branch.
#
# Usage: push_site.sh <site-dir>
#
# Run this script in a clone of clash-benchmarks that has an authenticated
# origin remote. GitHub Pages serves the pages branch of this repository
# from its root, so the branch holds only the site: this script replaces
# the whole content of the branch with <site-dir>.
#
# The file .nojekyll goes with it. Without that file, GitHub Pages sends
# the site through Jekyll, which drops files that start with a dot or an
# underscore.
#
# If the push is rejected, the script fetches the branch again and
# retries, with three attempts maximum.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <site-dir>" >&2
  exit 1
fi

site=$(realpath "$1")
if [[ ! -f "${site}/index.html" ]]; then
  echo "push_site.sh: ${site}/index.html is absent; render the site first" >&2
  exit 1
fi

wt=$(mktemp -d)
cleanup() {
  git worktree remove --force "${wt}" 2>/dev/null || true
  rm -rf "${wt}"
}
trap cleanup EXIT

for attempt in 1 2 3; do
  git fetch origin pages
  git worktree remove --force "${wt}" 2>/dev/null || true
  git worktree add --detach "${wt}" FETCH_HEAD

  # Remove the old site, but keep the git administration of the worktree.
  find "${wt}" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
  cp -r "${site}/." "${wt}/"
  touch "${wt}/.nojekyll"

  git -C "${wt}" add -A
  if git -C "${wt}" diff --cached --quiet; then
    echo "push_site.sh: the site is already up to date"
    exit 0
  fi

  git -C "${wt}" \
    -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -q -m "Render the benchmark site"

  if git -C "${wt}" push origin HEAD:refs/heads/pages; then
    echo "push_site.sh: pushed the site to the pages branch"
    exit 0
  fi

  echo "push_site.sh: push rejected, retrying (attempt ${attempt}/3)" >&2
  sleep "${attempt}"
done

echo "push_site.sh: giving up after 3 attempts" >&2
exit 1

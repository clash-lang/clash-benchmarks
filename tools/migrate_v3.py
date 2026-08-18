#!/usr/bin/env python3
"""Rewrap schema v3 results as schema v4, in place.

Schema v4 gives the normalization leg the same status envelope as the
wireDemo leg, so that a leg the commit under test breaks can be stored
as skipped. All v3 results were complete, so the measurements move into
"benchmarks" unchanged:

    "normalization": {...}   becomes   "normalization": {
                                         "status": "ok",
                                         "skip_reason": null,
                                         "benchmarks": {...}
                                       }

Usage:
  migrate_v3.py [<repo-root>]

The script is repeatable: a file that is already v4 stays as it is.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))

from result_schema import SCHEMA_VERSION, validate_result  # noqa: E402


def main():
    root = (Path(sys.argv[1]) if len(sys.argv) > 1
            else Path(__file__).resolve().parent.parent)
    changed = kept = problems = 0
    for path in sorted((root / "results").glob("*/*/*.json")):
        data = json.loads(path.read_text())
        version = data.get("schema_version")
        if version == SCHEMA_VERSION:
            kept += 1
            continue
        if version != 3:
            sys.exit(f"migrate_v3.py: {path} has schema version {version!r}; "
                     "this script only knows v3")
        data["schema_version"] = SCHEMA_VERSION
        data["normalization"] = {
            "status": "ok",
            "skip_reason": None,
            "benchmarks": data["normalization"],
        }
        found = validate_result(data)
        for problem in found:
            print(f"{path}: {problem}", file=sys.stderr)
        problems += len(found)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        changed += 1
    print(f"migrate_v3.py: rewrapped {changed} results, "
          f"{kept} were already v4, {problems} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

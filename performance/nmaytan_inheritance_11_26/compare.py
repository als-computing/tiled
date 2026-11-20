"""Print a Markdown table comparing bench.py result files.

Usage: compare.py BASELINE.json OTHER.json [OTHER.json ...]

One row per scenario: the median time of each run, the ratio of each other
run to the baseline, the number of SQL statements, and the number of results.
A result count is marked "*" when its fingerprint differs from the
baseline's: the versions found different results. That is expected for
scenarios where inheritance makes more nodes visible (see README.md) and a
bug anywhere else.
"""

import json
import sys


def main(paths):
    runs = [json.load(open(path)) for path in paths]
    base = runs[0]
    names = list(base["scenarios"])
    for run in runs[1:]:
        names += [name for name in run["scenarios"] if name not in names]

    header = ["scenario"] + [run["label"] for run in runs]
    header += [f"{run['label']} / {base['label']}" for run in runs[1:]]
    header += ["statements", "results"]
    print("| " + " | ".join(header) + " |")
    print("|---|" + "---:|" * (2 * len(runs) - 1) + "---|---|")
    for group in ["read", "write"]:
        for name in names:
            records = [run["scenarios"].get(name) for run in runs]
            if next(r for r in records if r is not None)["group"] != group:
                continue
            cells = [name] + [format_time(r) for r in records]
            for r in records[1:]:
                cells.append(ratio(records[0], r))
            cells.append(" / ".join(stat(r, "statements") for r in records))
            cells.append(" / ".join(results(records[0], r) for r in records))
            print("| " + " | ".join(cells) + " |")


def ok(record):
    return record is not None and not record.get("timeout")


def format_time(record):
    if record is None:
        return ""
    if record.get("timeout"):
        return "timeout"
    ms = record["median_ms"]
    if ms >= 1000:
        return f"{ms / 1000:.2f} s"
    if ms >= 10:
        return f"{ms:.0f} ms"
    return f"{ms:.1f} ms"


def ratio(base, other):
    if not (ok(base) and ok(other)):
        return ""
    return f"{other['median_ms'] / base['median_ms']:.2f}×"


def stat(record, key):
    return str(record[key]) if ok(record) else "-"


def results(base, record):
    if not ok(record):
        return "-"
    shown = "404" if record["results"] == -1 else str(record["results"])
    if ok(base) and record["fingerprint"] != base["fingerprint"]:
        shown += "*"
    return shown


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1:])

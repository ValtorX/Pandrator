"""Summarize live-replay result files (story 2.15, fork-only).

    uv run --no-project --python 3.11 python live_replay_report.py <client.jsonl> [--api <api.jsonl>]
        [--bucket 300] [--labels probe_plan_status_summary,probe_auth_status]

Prints, per time bucket, the median and max client latency of each label and the
server's in-flight count and CPU seconds from the launcher's stats lines.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime


def load(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("client")
    parser.add_argument("--api")
    parser.add_argument("--bucket", type=float, default=300.0)
    parser.add_argument("--labels", default="")
    options = parser.parse_args()
    rows = load(options.client)
    start = datetime.fromisoformat(rows[0]["t"])
    wanted = set(filter(None, options.labels.split(",")))
    buckets: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    failures: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        label = row.get("label")
        if not label or (wanted and label not in wanted) or "seconds" not in row:
            continue
        index = int((datetime.fromisoformat(row["t"]) - start).total_seconds() // options.bucket)
        buckets[index][label].append(row["seconds"])
        if row.get("error") or (row.get("status") and int(row["status"]) >= 400):
            failures[index][label] += 1
    stats: dict[int, list[dict]] = defaultdict(list)
    if options.api:
        for row in load(options.api):
            if row.get("kind") != "stats":
                continue
            index = int((datetime.fromisoformat(row["t"]) - start).total_seconds() // options.bucket)
            if index >= 0:
                stats[index].append(row)
    for index in sorted(buckets):
        line = [f"[{index * options.bucket / 60:6.1f} min]"]
        server = stats.get(index)
        if server:
            line.append(
                f"inflight<= {max(len(s['inflight']) for s in server)} "
                f"oldest<= {max((s['inflight'][0]['age'] for s in server if s['inflight']), default=0):.0f}s "
                f"cpu+{server[-1]['cpu_s'] - server[0]['cpu_s']:.0f}s threads {server[-1]['threads']}"
            )
        print(" ".join(line))
        for label in sorted(buckets[index]):
            values = buckets[index][label]
            fail = failures[index].get(label, 0)
            print(f"    {label:32s} n={len(values):3d} med={statistics.median(values):7.2f} "
                  f"max={max(values):7.2f}" + (f" failed={fail}" if fail else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

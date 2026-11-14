"""Sample a process's threads, CPU, RSS and sockets without touching it.

    uv run --no-project --python 3.11 --with psutil python proc_sample.py <pid> [label]

Prints one JSON line: wall time (UTC), RSS, thread count, per-thread CPU seconds,
CPU percent over one second, and the process's TCP sockets by state. Reads only
operating-system counters; never connects to the process or pauses it.
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime

import psutil


def sample(pid: int, label: str = "") -> dict:
    process = psutil.Process(pid)
    before = process.cpu_times()
    time.sleep(1.0)
    after = process.cpu_times()
    connections = process.net_connections(kind="tcp")
    listen_ports = {c.laddr.port for c in connections if c.status == psutil.CONN_LISTEN}
    served = [c for c in connections if c.laddr and c.laddr.port in listen_ports and c.raddr]
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "label": label,
        "pid": pid,
        "rss_mb": process.memory_info().rss // 2**20,
        "threads": process.num_threads(),
        "cpu_percent_1s": round((after.user + after.system - before.user - before.system) * 100, 1),
        "cpu_total_s": round(after.user + after.system, 1),
        "served_sockets": dict(Counter(c.status for c in served)),
        "busy_threads": sorted(
            (
                {"id": t.id, "cpu_s": round(t.user_time + t.system_time, 1)}
                for t in process.threads()
                if t.user_time + t.system_time > 5
            ),
            key=lambda item: -item["cpu_s"],
        ),
    }


if __name__ == "__main__":
    print(json.dumps(sample(int(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "")), flush=True)

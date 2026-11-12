"""Time the large-project read paths, admission and the opening check.

Starts the checkout's server on a copied or seeded data root, calls the same
endpoints with the same parameters as the MCP tools, and times generation
admission and the run's opening speech plan and voice check from job events.
The run is held at its "Loading speech provider settings" event and cancelled
there, so no TTS engine is needed.

    <uv env> python _bmad-output/scripts/large_project_timing.py --root <data root>
    <uv env> python _bmad-output/scripts/large_project_timing.py --root <data root> --profile

Results hold timings, counts, statuses and function or SQL names only: no
paths, titles or text. The root must not be the owner's live data folder.
"""

from __future__ import annotations

import argparse
import contextlib
import cProfile
import json
import logging
import os
import pstats
import re
import socket
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

CHECK_START = "Checking speech plan and voice references"
CHECK_END = "Loading speech provider settings"
OWNER_PORTS = (8097, 8098)
READY_PREFIX = "PERF-READY "
LOGS_PREFIX = "PERF-LOGS "
AUDIT_SPOOL_MARKER = "Audit writer busy"
DATABASE_LOCKED_MARKER = "database is locked"
INFLIGHT_PATH = "/__perf/inflight"
HARNESS_JOB_KINDS = ("generation.admit", "generation.run")
TERMINAL_JOB_STATES = {"succeeded", "failed", "canceled", "cancelled", "dead"}
SLOW_LIMIT_SECONDS = 15.0
SEGMENT_FIELDS = "id,ordinal,status"
_PATHLIKE = re.compile(r"([A-Za-z]:[\\/]|\\\\|/)[^\s'\"]*")


class RootRefused(RuntimeError):
    """The data root is unsafe or unsuitable to serve."""


# ---------------------------------------------------------------- data root


def live_lock_pid(root: Path) -> int | None:
    """Return the PID holding the root's instance lock, if that process is alive."""
    lock_path = root / "pandrator.instance.lock"
    if not lock_path.is_file():
        return None
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        lock = {}
    pid = int(lock.get("pid") or 0) if isinstance(lock, dict) else 0
    if pid <= 0:
        return None
    import psutil

    return pid if psutil.pid_exists(pid) else None


def inside_install(root: Path) -> bool:
    """True for a Manager install's data folder: a sibling app/ with current*.json pointers."""
    app = root.resolve().parent / "app"
    return app.is_dir() and any(app.glob("current*.json"))


def active_job_count(database: Path) -> int:
    """Queued or running jobs, read without writing (the worker would run them)."""
    with contextlib.closing(_connect_readonly(database)) as connection:
        return int(connection.execute(
            "SELECT count(*) FROM jobs WHERE status NOT IN ('succeeded', 'failed', 'canceled')"
        ).fetchone()[0])


def check_root(root: Path) -> None:
    if inside_install(root):
        raise RootRefused("The data root belongs to a Pandrator install; time a copy outside it.")
    if not (root / "pandrator.sqlite3").is_file():
        raise RootRefused("The data root has no pandrator.sqlite3.")
    if not (root / "migration-web-v1.json").is_file():
        # Without the marker, startup treats the database as an unfinished
        # legacy import and moves it aside for a fresh one.
        raise RootRefused("The data root has no migration-web-v1.json; copy it with the database.")
    pid = live_lock_pid(root)
    if pid is not None:
        raise RootRefused(
            f"The data root's instance lock is held by live PID {pid}; "
            "time a copy, never a running install."
        )
    active = active_job_count(root / "pandrator.sqlite3")
    if active:
        raise RootRefused(
            f"The data root has {active} queued or running jobs; the harness worker "
            "would run them. Use a copy without pending work."
        )


def owner_app_listening() -> bool | None:
    """Passively check whether anything listens on the owner's ports."""
    try:
        import psutil

        return any(
            conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port in OWNER_PORTS
            for conn in psutil.net_connections(kind="tcp")
        )
    except Exception:  # noqa: BLE001 - access to the connection table varies
        return None


def free_port() -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        if port not in OWNER_PORTS:
            return port


@dataclass
class ProjectChoice:
    session_id: str
    segments: int
    revisions: int
    takes: int
    active_revision_id: str
    non_active_revision_id: str | None
    first_segment_ids: list[str] = field(default_factory=list)


def _connect_readonly(database: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)


def select_projects(
    database: Path,
    *,
    large_target: int,
    small_target: int,
    large_session: str | None = None,
    small_session: str | None = None,
) -> tuple[ProjectChoice, ProjectChoice]:
    """Pick the plans whose active revision is closest to each target size."""
    with contextlib.closing(_connect_readonly(database)) as connection:
        rows = connection.execute(
            """
            SELECT p.session_id, p.active_revision_id, count(s.id)
            FROM generation_plans p
            JOIN sessions se ON se.id = p.session_id
            JOIN generation_segments s ON s.plan_revision_id = p.active_revision_id
            WHERE se.trashed_at IS NULL
            GROUP BY p.id
            """
        ).fetchall()
        if not rows:
            raise RootRefused("The data root has no speech plans to time.")

        def choose(target: int, explicit: str | None, exclude: str | None) -> tuple[str, str, int]:
            if explicit:
                for row in rows:
                    if row[0] == explicit:
                        return row
                raise RootRefused("The requested session has no active speech plan.")
            candidates = [row for row in rows if row[0] != exclude] or rows
            return min(candidates, key=lambda row: (abs(row[2] - target), row[0]))

        large = choose(large_target, large_session, None)
        small = choose(small_target, small_session, large[0])

        def describe(row: tuple[str, str, int]) -> ProjectChoice:
            session_id, active_id, count = row
            revisions = connection.execute(
                """
                SELECT r.id FROM generation_plan_revisions r
                JOIN generation_plans p ON p.id = r.plan_id
                WHERE p.session_id = ? ORDER BY r.revision_number DESC
                """,
                (session_id,),
            ).fetchall()
            non_active = next((rid for (rid,) in revisions if rid != active_id), None)
            takes = connection.execute(
                """
                SELECT count(*) FROM audio_takes t
                JOIN generation_segments s ON s.id = t.generation_segment_id
                WHERE s.plan_revision_id = ?
                """,
                (active_id,),
            ).fetchone()[0]
            first = [
                rid
                for (rid,) in connection.execute(
                    """
                    SELECT id FROM generation_segments
                    WHERE plan_revision_id = ? AND removed = 0
                    ORDER BY ordinal LIMIT 15
                    """,
                    (active_id,),
                )
            ]
            return ProjectChoice(
                session_id, count, len(revisions), int(takes), active_id, non_active, first
            )

        return describe(large), describe(small)


# ---------------------------------------------------------------- server side


class HarnessStop(RuntimeError):
    """Stops a generation run at the provider-settings line."""


def hold_at_provider_load(
    handler: Callable[..., Any],
    *,
    hold_seconds: float,
    on_reached: Callable[[dict[str, Any]], None] | None = None,
) -> Callable[..., Any]:
    """Wrap the generation.run handler so it stops before any provider work.

    At the provider-settings progress event the run waits for its cancel
    event (set by the API cancel the timing client sends) and then stops.
    """

    def wrapped(payload: dict[str, Any], progress: Callable[..., Any], cancel_event: threading.Event):
        def guarded(value: float, detail: str | None = None) -> None:
            progress(value, detail)
            if detail == CHECK_END:
                if on_reached is not None:
                    on_reached(payload)
                cancel_event.wait(hold_seconds)
                raise HarnessStop("Stopped at the provider-settings line by the timing harness.")

        return handler(payload, guarded, cancel_event)

    return wrapped


def build_services(root: Path, *, hold_seconds: float, cancel_in_process: bool):
    """Create the app, a bearer token and a worker for one data root."""
    from unittest.mock import patch

    gpu_probe = patch(
        "pandrator.web.capabilities.probe_gpu",
        return_value={"available": False, "devices": [], "guidance": "Timing harness."},
    )
    gpu_probe.start()
    from pandrator.web.api import create_app
    from pandrator.web.auth import AuthService
    from pandrator.web.jobs import JobQueue, Worker, noop_handler
    from pandrator.web.workflow_handlers import WorkflowHandlers

    app = create_app(data_root=root, testing=False)
    app.config["SESSION_COOKIE_NAME"] = "pandrator_perf_session"
    services = app.extensions["pandrator"]
    database = services["database"]
    _, token = AuthService(database).create_api_token("large-project-timing")
    queue = JobQueue(database)
    # Only the two jobs the harness starts: any other job a copied database
    # holds (LLM work, exports, provider calls) must never run for real.
    workflow = WorkflowHandlers(database, services["paths"], jobs=queue).handlers()
    handlers = {"noop": noop_handler, **{kind: workflow[kind] for kind in HARNESS_JOB_KINDS}}

    def request_cancel(payload: dict[str, Any]) -> None:
        if cancel_in_process:
            queue.request_cancel(str(payload.get("_job_id") or ""))

    handlers["generation.run"] = hold_at_provider_load(
        handlers["generation.run"], hold_seconds=hold_seconds, on_reached=request_cancel
    )
    worker = Worker(queue, f"perf-{uuid.uuid4().hex[:8]}", handlers)
    return app, token, worker, gpu_probe


class InflightCounter:
    """WSGI wrapper counting requests the app is still working on.

    A client that gives up does not stop the server's work, so the harness
    asks this count, not the client, when the server is idle again.
    """

    def __init__(self, app: Callable[..., Any]):
        self.app = app
        self.count = 0
        self._lock = threading.Lock()

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        if environ.get("PATH_INFO") == INFLIGHT_PATH:
            body = json.dumps({"inflight": self.count}).encode()
            start_response("200 OK", [("Content-Type", "application/json"),
                                      ("Content-Length", str(len(body)))])
            return [body]
        with self._lock:
            self.count += 1
        try:
            result = self.app(environ, start_response)
            try:
                return list(result)
            finally:
                close = getattr(result, "close", None)
                if close is not None:
                    close()
        finally:
            with self._lock:
                self.count -= 1


class ServerLogCounter(logging.Handler):
    """Counts the two contention symptoms in the server's log without keeping any text."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.audit_spools = 0
        self.database_locked = 0
        self._counts_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                text += " " + str(record.exc_info[1])
        except Exception:
            return
        with self._counts_lock:
            if AUDIT_SPOOL_MARKER in text:
                self.audit_spools += 1
            if DATABASE_LOCKED_MARKER in text.lower():
                self.database_locked += 1

    def line(self) -> str:
        with self._counts_lock:
            return LOGS_PREFIX + json.dumps(
                {"audit_spools": self.audit_spools, "database_locked": self.database_locked}
            )


def parse_log_counts(line: str) -> dict[str, int] | None:
    """Read back a child's LOGS line; anything else is not one."""
    if not line.startswith(LOGS_PREFIX):
        return None
    try:
        data = json.loads(line[len(LOGS_PREFIX):])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    counts = {key: data.get(key) for key in ("audit_spools", "database_locked")}
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in counts.values()):
        return None
    return {key: int(value) for key, value in counts.items()}  # type: ignore[arg-type]


def serve_child(root: Path, port: int, hold_seconds: float) -> int:
    """Child process: serve the root and run one worker until stdin closes."""
    try:
        from pandrator.web.api_server import create_api_server
    except ImportError:
        # A checkout from before the abandoned-reads story (upstream main, a
        # baseline) has no shared factory; serve it the way it serves itself.
        from waitress import create_server

        def create_api_server(application, socket_map, **adjustments):
            return create_server(application, map=socket_map, **adjustments)

    app, token, worker, _ = build_services(root, hold_seconds=hold_seconds, cancel_in_process=False)
    worker_thread = threading.Thread(
        target=worker.run_forever, kwargs={"poll_interval": 0.2}, daemon=True
    )
    worker_thread.start()
    # A handler on the root logger switches off logging's last-resort console output
    # and makes later basicConfig calls no-ops, so configure the console first.
    logging.basicConfig()
    log_counter = ServerLogCounter()
    logging.getLogger().addHandler(log_counter)
    server = create_api_server(
        InflightCounter(app), {}, host="127.0.0.1", port=port, threads=12,
        connection_limit=512, channel_timeout=120, cleanup_interval=5,
    )

    def watch_parent() -> None:
        sys.stdin.read()
        print(log_counter.line(), flush=True)
        worker.stop()
        worker_thread.join(timeout=5)
        with contextlib.suppress(Exception):
            app.extensions["pandrator"]["database"].dispose()
        os._exit(0)

    threading.Thread(target=watch_parent, daemon=True).start()
    print(READY_PREFIX + json.dumps({"port": port, "token": token}), flush=True)
    server.run()
    return 0


# ---------------------------------------------------------------- client side


@dataclass
class Sample:
    seconds: float
    status: int | None
    timed_out: bool = False
    error: str | None = None
    bytes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "seconds": round(self.seconds, 3), "status": self.status,
            "timed_out": self.timed_out, "error": self.error, "bytes": self.bytes,
        }


class Client:
    def __init__(self, base_url: str, token: str, timeout: float):
        import requests

        self._requests = requests
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.timeout = timeout

    def call(
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None, timeout: float | None = None,
        idempotent: bool = False,
    ) -> tuple[Sample, Any]:
        headers = dict(self.headers)
        if idempotent:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        started = time.perf_counter()
        try:
            response = self._requests.request(
                method, self.base_url + path, params=params, json=body, headers=headers,
                timeout=(10, timeout or self.timeout),
            )
        except self._requests.Timeout:
            return Sample(time.perf_counter() - started, None, timed_out=True), None
        except self._requests.RequestException as error:
            return Sample(time.perf_counter() - started, None, error=type(error).__name__), None
        elapsed = time.perf_counter() - started
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return Sample(elapsed, response.status_code, bytes=len(response.content)), payload


def inflight(client: Client) -> int | None:
    sample, payload = client.call("GET", INFLIGHT_PATH, timeout=30)
    if sample.status != 200 or not isinstance(payload, dict):
        return None
    return int(payload.get("inflight", 0))


def wal_bytes(database: Path) -> int:
    """Size of the database's write-ahead log by stat alone; a missing file is 0."""
    try:
        return Path(f"{database}-wal").stat().st_size
    except FileNotFoundError:
        return 0


class WalSampler:
    """Stats the write-ahead log on a thread. It never opens or writes the database."""

    def __init__(self, database: Path, interval: float = 1.0):
        self.database = database
        self.interval = interval
        self.start_bytes = wal_bytes(database)
        self.peak_bytes = self.start_bytes
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.sample()

    def sample(self) -> int:
        size = wal_bytes(self.database)
        self.peak_bytes = max(self.peak_bytes, size)
        return size

    def start(self) -> WalSampler:
        self._thread.start()
        return self

    def stop(self) -> int:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join()
        return self.sample()


def wait_for_idle(client: Client, timeout: float, *, own: int = 1) -> float | None:
    """Seconds until abandoned requests finish; None if they outlast timeout."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        count = inflight(client)
        if count is not None and count <= own - 1:
            return round(time.monotonic() - started, 1)
        time.sleep(1.0)
    return None


@dataclass(frozen=True)
class Operation:
    name: str
    method: str
    path: str
    params: dict[str, Any] | None = None


def read_matrix(project: ProjectChoice) -> list[Operation]:
    """Read requests for one project. The MCP status and history tools send the summary
    variants plus `revision_active`; the full `plan_status` and `revisions` stay for reference."""
    session = f"/api/v1/sessions/{project.session_id}"
    listing = {"cursor": 0, "limit": 50, "audio_version_side": "after"}
    operations = [
        Operation("auth_status", "GET", "/api/v1/auth/status"),
        Operation("segments_default", "GET", f"{session}/generation-segments", listing),
        Operation(
            "segments_compact", "GET", f"{session}/generation-segments",
            {**listing, "view": "compact", "take_history": "selected"},
        ),
        Operation(
            "segments_fields", "GET", f"{session}/generation-segments",
            {**listing, "fields": SEGMENT_FIELDS},
        ),
        Operation(
            "segments_limit100", "GET", f"{session}/generation-segments", {**listing, "limit": 100}
        ),
    ]
    if project.non_active_revision_id:
        operations.append(Operation(
            "segments_non_active", "GET", f"{session}/generation-segments",
            {**listing, "plan_revision_id": project.non_active_revision_id},
        ))
    operations += [
        Operation("plan_status", "GET", f"{session}/generation-plan/status"),
        Operation("plan_status_summary", "GET", f"{session}/generation-plan/status", {"summary": "true"}),
        Operation("revisions", "GET", f"{session}/generation-plan/revisions", {"limit": 50}),
        Operation(
            "revisions_summary", "GET", f"{session}/generation-plan/revisions",
            {"limit": 50, "summary": "true"},
        ),
    ]
    operations.append(Operation(
        "revision_active", "GET",
        f"{session}/generation-plan/revisions/{project.active_revision_id}",
    ))
    if project.non_active_revision_id:
        operations.append(Operation(
            "revision_non_active", "GET",
            f"{session}/generation-plan/revisions/{project.non_active_revision_id}",
        ))
    return operations


def summarize(samples: list[Sample]) -> dict[str, Any]:
    completed = [sample.seconds for sample in samples if not sample.timed_out and sample.error is None]
    worst = max((sample.seconds for sample in samples), default=None)
    return {
        "runs": len(samples),
        "median_seconds": round(statistics.median(completed), 3) if completed else None,
        "max_seconds": round(worst, 3) if worst is not None else None,
        "timeouts": sum(sample.timed_out for sample in samples),
        "errors": sum(sample.error is not None for sample in samples),
        "statuses": sorted({sample.status for sample in samples if sample.status is not None}),
        "verdict": verdict(samples),
        "samples": [sample.as_dict() for sample in samples],
    }


def verdict(samples: list[Sample]) -> str:
    """Slow when any run timed out, failed to connect, or took over 15 s."""
    if not samples:
        return "not_run"
    if any(sample.timed_out or sample.error for sample in samples):
        return "slow"
    return "slow" if max(sample.seconds for sample in samples) > SLOW_LIMIT_SECONDS else "fast"


def selected(only: set[str] | None, project: str, name: str) -> bool:
    """``--only`` names an operation, a project ("small") or one project's operation."""
    return not only or bool({name, project, f"{project}.{name}"} & only)


def run_matrix(
    client: Client, operations: list[Operation], runs: int, phase: str,
    only: set[str] | None, log: Callable[[str], None], *, repeat_limit: float | None = None,
    drain: Callable[[], float | None] | None = None,
    after_row: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Time each operation; one that is slower than repeat_limit runs once.

    A timed-out request keeps running on the server, so it is not repeated,
    and with ``drain`` the next measurement waits until the server is idle.
    """
    results = []
    project = phase.split("_", 1)[0]
    for operation in operations:
        if not selected(only, project, operation.name):
            continue
        samples = []
        drained: float | None = None
        for _ in range(runs):
            sample, _payload = client.call(operation.method, operation.path, params=operation.params)
            samples.append(sample)
            log(f"  {phase:<17} {operation.name:<22} {format_sample(sample)}")
            if sample.timed_out and drain is not None:
                drained = drain()
                log(f"  {phase:<17} {operation.name:<22} server busy {drained}s more after the timeout")
            if sample.timed_out or (repeat_limit is not None and sample.seconds > repeat_limit):
                break
        row = {"phase": phase, "operation": operation.name, **summarize(samples)}
        if drain is not None and any(sample.timed_out for sample in samples):
            row["busy_after_timeout_seconds"] = drained
        results.append(row)
        if after_row is not None:
            after_row(row)
    return results


def format_sample(sample: Sample) -> str:
    if sample.timed_out:
        return f"timed out after {sample.seconds:.1f}s"
    if sample.error:
        return f"{sample.error} after {sample.seconds:.1f}s"
    return f"{sample.status} in {sample.seconds:.2f}s"


LIGHT_TIMEOUT_SECONDS = 30.0


def read_burst(
    client: Client, operation: Operation, *, seconds: float, workers: int, abandon_after: float,
    light_operation: Operation | None = None, light_workers: int = 0, light_pace: float = 0.0,
) -> dict[str, Any]:
    """Heavy reads whose clients give up like the MCP's 15 s timeout.

    Light workers loop a cheap read beside them for the same window; they show the
    per-request write contention that heavy listings alone do not.
    """
    deadline = time.monotonic() + seconds
    lock = threading.Lock()
    samples: list[Sample] = []
    light_samples: list[Sample] = []

    def light_loop() -> None:
        assert light_operation is not None
        while time.monotonic() < deadline:
            sample, _ = client.call(
                light_operation.method, light_operation.path, params=light_operation.params,
                timeout=LIGHT_TIMEOUT_SECONDS,
            )
            with lock:
                light_samples.append(sample)
            if light_pace > 0:
                time.sleep(light_pace)

    def loop() -> None:
        while time.monotonic() < deadline:
            sample, _ = client.call(
                operation.method, operation.path, params=operation.params, timeout=abandon_after
            )
            with lock:
                samples.append(sample)

    threads = [threading.Thread(target=loop, daemon=True) for _ in range(workers)]
    if light_operation is not None:
        threads += [threading.Thread(target=light_loop, daemon=True) for _ in range(light_workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(seconds + max(abandon_after, LIGHT_TIMEOUT_SECONDS) + 30)
    result: dict[str, Any] = {
        "operation": operation.name, "seconds": seconds, "workers": workers,
        "abandon_after_seconds": abandon_after, "requests": len(samples),
        "abandoned": sum(sample.timed_out for sample in samples),
        "completed": sum(sample.status is not None for sample in samples),
        "errors": sum(sample.error is not None for sample in samples),
    }
    if light_operation is not None and light_workers > 0:
        summary = summarize(light_samples)
        result["light"] = {
            "operation": light_operation.name, "workers": light_workers,
            "pace_seconds": light_pace, "timeout_seconds": LIGHT_TIMEOUT_SECONDS,
            "requests": summary["runs"], "statuses": summary["statuses"],
            "median_seconds": summary["median_seconds"], "max_seconds": summary["max_seconds"],
            "timeouts": summary["timeouts"], "errors": summary["errors"],
        }
    return result


def burst_operation(operations: list[Operation], name: str) -> Operation:
    """The read matrix's operation a burst loops, by name."""
    for operation in operations:
        if operation.name == name:
            return operation
    raise SystemExit(
        f"--burst-operation {name!r} is not a read operation; choose one of "
        + ", ".join(operation.name for operation in operations)
    )


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def seconds_between(start: str | None, end: str | None) -> float | None:
    first, last = parse_timestamp(start), parse_timestamp(end)
    if first is None or last is None:
        return None
    return round((last - first).total_seconds(), 3)


def check_window(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Find the opening check's start and end in a job's event timeline."""
    start = end = None
    for event in events:
        if event.get("event_type") != "job.progress":
            continue
        detail = (event.get("payload_json") or {}).get("detail")
        if detail == CHECK_START and start is None:
            start = event.get("created_at")
        elif detail == CHECK_END and end is None:
            end = event.get("created_at")
    return {
        "check_started": start is not None,
        "check_finished": end is not None,
        "check_seconds": seconds_between(start, end),
    }


def job_timings(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": job.get("status"),
        "queue_wait_seconds": seconds_between(job.get("created_at"), job.get("started_at")),
        "duration_seconds": seconds_between(job.get("started_at"), job.get("finished_at")),
        "error_code": job.get("error_code"),
        "error": scrub(job.get("error_message")),
    }


def scrub(message: Any) -> str | None:
    """Keep an error's wording but drop anything shaped like a path."""
    if not message:
        return None
    return _PATHLIKE.sub("<path>", str(message))[:300]


def wait_for_job(
    client: Client, job_id: str, *, timeout: float, on_poll: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sample, job = client.call("GET", f"/api/v1/jobs/{job_id}", timeout=30)
        if isinstance(job, dict) and sample.status == 200:
            if on_poll is not None:
                on_poll(job)
            if job.get("status") in TERMINAL_JOB_STATES:
                return job
        time.sleep(1.0)
    return None


class ReadSampler:
    """Sample reads, each on its own interval, while a job runs.

    A sampled read that is itself CPU-heavy competes with the job for the
    server's interpreter, so heavy reads get a long interval or none.
    """

    def __init__(self, client: Client, plan: list[tuple[Operation, float]]):
        self.client = client
        self.plan = [(operation, interval) for operation, interval in plan if interval > 0]
        self.samples: dict[str, list[Sample]] = {operation.name: [] for operation, _ in self.plan}
        self._stop = threading.Event()
        self._threads = [threading.Thread(target=self._run, args=item, daemon=True) for item in self.plan]

    def _run(self, operation: Operation, interval: float) -> None:
        while not self._stop.is_set():
            sample, _ = self.client.call(operation.method, operation.path, params=operation.params)
            self.samples[operation.name].append(sample)
            self._stop.wait(interval)

    def __enter__(self) -> ReadSampler:
        for thread in self._threads:
            thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(self.client.timeout + 15)

    def summary(self) -> dict[str, Any]:
        return {
            name: {key: value for key, value in summarize(samples).items() if key != "samples"}
            for name, samples in self.samples.items()
        }


def run_admission(
    client: Client, large: ProjectChoice, small: ProjectChoice, size: int, *,
    job_timeout: float, sample_interval: float, log: Callable[[str], None],
    listing_interval: float = 0.0,
) -> dict[str, Any]:
    session = f"/api/v1/sessions/{large.session_id}"
    segment_ids = large.first_segment_ids[:size]
    result: dict[str, Any] = {"segments": len(segment_ids), "operation": "regenerate"}
    reads = {operation.name: operation for operation in read_matrix(small)}
    sampling = [(reads["auth_status"], sample_interval), (reads["segments_default"], listing_interval)]
    result["sampling"] = {"auth_status_every_seconds": sample_interval,
                          "small_listing_every_seconds": listing_interval}
    with ReadSampler(client, sampling) as sampler:
        posted, body = client.call(
            "POST", f"{session}/generation-admissions",
            body={"operation": "regenerate", "segment_ids": segment_ids}, idempotent=True,
        )
        result["admit_request"] = posted.as_dict()
        admission_id = body.get("job_id") if isinstance(body, dict) else None
        if posted.status != 202 or not admission_id:
            result["error"] = scrub((body or {}).get("error", {}).get("message")
                                    if isinstance(body, dict) else None)
            log(f"  admission {size:>2}: request {format_sample(posted)}")
            result["reads_during"] = sampler.summary()
            return result
        admission = wait_for_job(client, admission_id, timeout=job_timeout)
        if admission is None:
            result["admission"] = {"status": "timed_out"}
            log(f"  admission {size:>2}: no terminal state within {job_timeout:.0f}s")
            result["reads_during"] = sampler.summary()
            return result
        result["admission"] = job_timings(admission)
        log(f"  admission {size:>2}: {admission.get('status')} in "
            f"{result['admission']['duration_seconds']}s "
            f"(queued {result['admission']['queue_wait_seconds']}s)")
        child_id = (admission.get("result_json") or {}).get("job_id")
        if admission.get("status") != "succeeded" or not child_id:
            result["reads_during"] = sampler.summary()
            return result

        cancel: dict[str, Any] = {}

        def cancel_at_provider_line(job: dict[str, Any]) -> None:
            if cancel or job.get("progress_detail") != CHECK_END:
                return
            sample, _ = client.call("POST", f"/api/v1/jobs/{child_id}/cancel", timeout=60)
            cancel.update(sample.as_dict())

        child = wait_for_job(client, child_id, timeout=job_timeout, on_poll=cancel_at_provider_line)
        _, logs = client.call("GET", f"/api/v1/jobs/{child_id}/logs", timeout=60)
        events = (logs or {}).get("items") or [] if isinstance(logs, dict) else []
        result["run"] = {**(job_timings(child) if child else {"status": "timed_out"}),
                         **check_window(events), "cancel_request": cancel or None}
        log(f"  check     {size:>2}: {result['run'].get('check_seconds')}s, "
            f"run {result['run'].get('status')} (queued {result['run'].get('queue_wait_seconds')}s)")
    result["reads_during"] = sampler.summary()
    return result


def start_server(root: Path, port: int, hold_seconds: float, startup_timeout: float):
    command = [sys.executable, str(Path(__file__).resolve()), "--serve", "--root", str(root),
               "--port", str(port), "--hold-seconds", str(hold_seconds)]
    child = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
    )
    ready: dict[str, Any] = {}
    started = threading.Event()
    log_counts: dict[str, int] = {}

    def read_output() -> None:
        assert child.stdout is not None
        for line in child.stdout:
            if line.startswith(READY_PREFIX):
                ready.update(json.loads(line[len(READY_PREFIX):]))
                started.set()
            elif (counts := parse_log_counts(line)) is not None:
                log_counts.update(counts)
        started.set()  # a child that exits before READY must not wait out the timeout

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    started.wait(startup_timeout)
    child.log_counts = log_counts  # type: ignore[attr-defined]
    child.output_reader = reader  # type: ignore[attr-defined]
    if not ready:
        stop_server(child)
        raise RuntimeError("The timing server did not start.")
    return child, ready


def stop_server(child: subprocess.Popen) -> dict[str, int]:
    """Stop the child and return the contention counts it printed on the way out."""
    with contextlib.suppress(Exception):
        assert child.stdin is not None
        child.stdin.close()
    try:
        child.wait(30)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(10)
    reader = getattr(child, "output_reader", None)
    if reader is not None:
        reader.join(5)
    return dict(getattr(child, "log_counts", {}))


def git_revision() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True,
            cwd=Path(__file__).resolve().parents[2],
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def describe_project(project: ProjectChoice) -> dict[str, Any]:
    return {"segments": project.segments, "revisions": project.revisions, "takes": project.takes}


# ---------------------------------------------------------------- profile mode


def normalize_sql(statement: str) -> str:
    text = " ".join(statement.split())
    text = re.sub(r"\((?:\s*\?\s*,)+\s*\?\s*\)", "(?, ...)", text)
    text = re.sub(r"\(__\[POSTCOMPILE_[^\]]+\]\)", "(?, ...)", text)
    return text[:240]


class SqlLog:
    """Total time and count per normalized statement."""

    def __init__(self, engine):
        from sqlalchemy import event

        self._event = event
        self.engine = engine
        self.totals: dict[str, list[float]] = {}
        self._local = threading.local()
        event.listen(engine, "before_cursor_execute", self._before)
        event.listen(engine, "after_cursor_execute", self._after)

    def _before(self, conn, cursor, statement, parameters, context, executemany):
        self._local.started = time.perf_counter()

    def _after(self, conn, cursor, statement, parameters, context, executemany):
        started = getattr(self._local, "started", None)
        if started is None:
            return
        entry = self.totals.setdefault(normalize_sql(statement), [0.0, 0])
        entry[0] += time.perf_counter() - started
        entry[1] += 1

    def reset(self) -> None:
        self.totals = {}

    def top(self, count: int) -> list[dict[str, Any]]:
        ranked = sorted(self.totals.items(), key=lambda item: item[1][0], reverse=True)[:count]
        return [{"statement": sql, "seconds": round(total, 3), "count": int(calls)}
                for sql, (total, calls) in ranked]

    def summary(self) -> dict[str, Any]:
        return {
            "statements": int(sum(calls for _, calls in self.totals.values())),
            "seconds": round(sum(total for total, _ in self.totals.values()), 3),
        }

    def close(self) -> None:
        self._event.remove(self.engine, "before_cursor_execute", self._before)
        self._event.remove(self.engine, "after_cursor_execute", self._after)


def function_name(filename: str, line: int, name: str) -> str:
    """A module-qualified name, never a filesystem path."""
    normalized = filename.replace("\\", "/")
    for marker in ("/site-packages/", "/Lib/", "/lib/python3.11/"):
        if marker in normalized:
            normalized = normalized.split(marker, 1)[1]
            break
    else:
        anchor = normalized.rfind("/pandrator")
        normalized = normalized[anchor + 1:] if anchor >= 0 else normalized.rsplit("/", 1)[-1]
    module = normalized.removesuffix(".py").replace("/", ".")
    return f"{module}:{line}({name})" if line else f"{module}({name})"


def top_functions(profile: cProfile.Profile, count: int) -> dict[str, list[dict[str, Any]]]:
    stats = pstats.Stats(profile)
    table = stats.stats  # type: ignore[attr-defined]
    # The largest share of each function's time spent in a single callee.
    widest_callee: dict[Any, float] = {}
    for _callee, (_cc, _nc, _tt, _ct, callers) in table.items():
        for caller, edge in callers.items():
            widest_callee[caller] = max(widest_callee.get(caller, 0.0), edge[3])
    rows = []
    for key, (_cc, calls, tottime, cumtime, _callers) in table.items():
        filename, line, name = key
        # A pass-through frame hands nearly all its time to one callee; the
        # callee is the informative row.
        passthrough = cumtime > 0 and widest_callee.get(key, 0.0) >= 0.98 * cumtime
        rows.append((function_name(filename, line, name), calls, tottime, cumtime, filename, passthrough))

    def ranked(key: int, own_code_only: bool) -> list[dict[str, Any]]:
        selected = [row for row in rows
                    if not own_code_only or (
                        not row[5]
                        and "pandrator" in row[4].replace("\\", "/").split("/site-packages/")[0])]
        selected.sort(key=lambda row: row[key], reverse=True)
        return [{"function": row[0], "calls": row[1], "tottime": round(row[2], 3),
                 "cumtime": round(row[3], 3)} for row in selected[:count]]

    return {"by_cumulative": ranked(3, True), "by_own_time": ranked(2, False)}


def profile_call(
    label: str, call: Callable[[], Any], sql: SqlLog, top: int, log: Callable[[str], None],
) -> tuple[dict[str, Any], Any]:
    sql.reset()
    profile = cProfile.Profile()
    started = time.perf_counter()
    profile.enable()
    try:
        value = call()
    finally:
        profile.disable()
    elapsed = time.perf_counter() - started
    entry = {"operation": label, "profiled_seconds": round(elapsed, 3), "sql": sql.summary(),
             "top_sql": sql.top(top), **top_functions(profile, top)}
    log(f"\n== {label}: {elapsed:.2f}s under cProfile, {entry['sql']['statements']} statements "
        f"({entry['sql']['seconds']}s in SQL)")
    for row in entry["by_cumulative"][:top]:
        log(f"   {row['cumtime']:>9.2f}s cum {row['tottime']:>8.2f}s own {row['calls']:>9} x {row['function']}")
    for row in entry["by_own_time"][:max(3, top // 2)]:
        log(f"   {row['tottime']:>9.2f}s own {row['cumtime']:>8.2f}s cum {row['calls']:>9} x {row['function']}")
    for row in entry["top_sql"][:top]:
        log(f"   {row['seconds']:>9.2f}s sql {row['count']:>9} x {row['statement'][:150]}")
    return entry, value


def run_profile(args: argparse.Namespace, large: ProjectChoice, small: ProjectChoice,
                log: Callable[[str], None], entries: list[dict[str, Any]],
                save: Callable[[], None]) -> None:
    """Profile the selected operations, saving the results after each one."""
    app, token, worker, gpu_probe = build_services(
        args.root, hold_seconds=0.0, cancel_in_process=True
    )
    database = app.extensions["pandrator"]["database"]
    client = app.test_client()
    headers = {"Authorization": f"Bearer {token}"}
    sql = SqlLog(database.engine)
    only = set(args.only.split(",")) if args.only else None
    try:
        for project_name, project in (("large", large), ("small", small)):
            for operation in read_matrix(project):
                if not selected(only, project_name, operation.name):
                    continue

                def call(operation=operation):
                    response = client.open(operation.path, method=operation.method,
                                           query_string=operation.params, headers=headers)
                    return response.status_code

                entry, status = profile_call(f"{project_name}.{operation.name}", call, sql, args.top, log)
                entries.append({**entry, "status": status})
                save()
        for size in admission_sizes(args):
            if only and "admission" not in only:
                continue
            response = client.post(
                f"/api/v1/sessions/{large.session_id}/generation-admissions",
                json={"operation": "regenerate", "segment_ids": large.first_segment_ids[:size]},
                headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            )
            if response.status_code != 202:
                entries.append({"operation": f"admission_{size}", "status": response.status_code})
                save()
                continue
            entry, _ = profile_call(f"admission_{size}.admit", worker.run_once, sql, args.top, log)
            entries.append(entry)
            save()
            entry, _ = profile_call(f"admission_{size}.opening_check", worker.run_once, sql, args.top, log)
            entries.append(entry)
            save()
    finally:
        sql.close()
        gpu_probe.stop()
        database.dispose()


# ---------------------------------------------------------------- main


def admission_sizes(args: argparse.Namespace) -> list[int]:
    return [int(value) for value in str(args.admission_sizes).split(",") if value.strip()]


def print_table(results: dict[str, Any], log: Callable[[str], None]) -> None:
    log("\n" + "phase              operation               median      max  verdict  statuses")
    for row in results.get("reads", []):
        median = "-" if row["median_seconds"] is None else f"{row['median_seconds']:.2f}s"
        worst = "-" if row["max_seconds"] is None else f"{row['max_seconds']:.2f}s"
        extra = f" timeouts={row['timeouts']}" if row["timeouts"] else ""
        log(f"{row['phase']:<18} {row['operation']:<22} {median:>8} {worst:>8}  {row['verdict']:<7}  "
            f"{row['statuses']}{extra}")
    if burst := results.get("burst"):
        log(f"\nburst: {burst['requests']} requests over {burst['seconds']:.0f}s by {burst['workers']} "
            f"clients, {burst['abandoned']} abandoned at {burst['abandon_after_seconds']:.0f}s; "
            f"{burst.get('inflight_after')} still running after it, server busy "
            f"{burst.get('busy_after_small_reads_seconds')}s more after the after-load reads")
        if wal := burst.get("wal"):
            log("burst WAL: " + ", ".join(
                f"{name.removesuffix('_bytes')} {wal[name] / 1048576:.1f} MiB"
                for name in ("start_bytes", "peak_bytes", "burst_end_bytes",
                             "after_load_reads_bytes", "settled_bytes") if name in wal))
        if light := burst.get("light"):
            log(f"burst light reads: {light['requests']} {light['operation']} requests by "
                f"{light['workers']} clients (pace {light['pace_seconds']}s), statuses "
                f"{light['statuses']}, median {light['median_seconds']}s, "
                f"max {light['max_seconds']}s, timeouts {light['timeouts']}, "
                f"errors {light['errors']}")
    if "server_logs" in results:
        if (server_logs := results["server_logs"]) is None:
            log("server log, whole run: counts not collected")
        else:
            log(f"server log, whole run: audit spools {server_logs['audit_spools']}, "
                f"database-locked records {server_logs['database_locked']}")
    for item in results.get("admissions", []):
        admission = item.get("admission") or {}
        run = item.get("run") or {}
        log(f"admission {item['segments']:>2} segments: admit {admission.get('duration_seconds')}s "
            f"(queued {admission.get('queue_wait_seconds')}s, {admission.get('status')}), "
            f"check {run.get('check_seconds')}s (run {run.get('status')})")
        for name, reads in (item.get("reads_during") or {}).items():
            log(f"   reads during, {name}: {reads['runs']} samples, median {reads['median_seconds']}s, "
                f"max {reads['max_seconds']}s, timeouts {reads['timeouts']}")


def default_output(root: Path, label: str, profile: bool) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    kind = "profile" if profile else "timing"
    return root.parent / "results" / f"{label}-{kind}-{stamp}.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True, help="Copied or seeded data root.")
    parser.add_argument("--label", help="Dataset label for the results file (default: root folder name).")
    parser.add_argument("--out", type=Path, help="Results JSON path (default: <root>/../results/).")
    parser.add_argument("--runs", type=int, default=3, help="Runs per read operation.")
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-request timeout in seconds.")
    parser.add_argument("--repeat-limit", type=float, default=60.0,
                        help="An operation slower than this many seconds runs once.")
    parser.add_argument("--job-timeout", type=float, default=1800.0, help="Per-job wait in seconds.")
    parser.add_argument("--load-seconds", type=float, default=60.0, help="Read-load burst length.")
    parser.add_argument("--load-workers", type=int, default=4, help="Concurrent burst clients.")
    parser.add_argument("--abandon-after", type=float, default=15.0, help="Burst client timeout.")
    parser.add_argument("--burst-operation", default="segments_default",
                        help="Read the burst clients loop on the large project: any read_matrix "
                             "operation name (default: segments_default).")
    parser.add_argument("--light-workers", type=int, default=0,
                        help="Clients that loop the small project's auth_status beside the burst.")
    parser.add_argument("--light-pace", type=float, default=0.0,
                        help="Seconds between one light client's requests.")
    parser.add_argument("--admission-sizes", default="1,15", help="Comma list of segment counts.")
    parser.add_argument("--sample-interval", type=float, default=2.0,
                        help="Seconds between auth-status samples during jobs.")
    parser.add_argument("--listing-interval", type=float, default=0.0,
                        help="Seconds between small-listing samples during jobs (0: none). The "
                             "listing is CPU-heavy and slows the job it is sampled against.")
    parser.add_argument("--skip-reads", action="store_true", help="Skip both read matrices.")
    parser.add_argument("--large-segments", type=int, default=5000, help="Target size of the large plan.")
    parser.add_argument("--small-segments", type=int, default=189, help="Target size of the small plan.")
    parser.add_argument("--large-session", help="Session id of the large project.")
    parser.add_argument("--small-session", help="Session id of the small project.")
    parser.add_argument(
        "--only",
        help="Comma list of what to run: operation names (both projects), 'small' or 'large' "
             "(a whole project), 'small.<operation>' or 'large.<operation>', 'admission', and "
             "'burst' (the read-load burst and the after-load small reads).")
    parser.add_argument("--skip-burst", action="store_true", help="Skip the burst and the after-load reads.")
    parser.add_argument("--skip-admission", action="store_true", help="Skip the admission scenarios.")
    parser.add_argument("--skip-large-reads", action="store_true",
                        help="Skip the large project's read matrix (rerunning small-project rows).")
    parser.add_argument("--profile", action="store_true", help="Profile each operation in-process.")
    parser.add_argument("--top", type=int, default=15, help="Rows per profile listing.")
    parser.add_argument("--port", type=int, help="Server port (default: a free one, never 8097/8098).")
    parser.add_argument("--hold-seconds", type=float, default=120.0, help=argparse.SUPPRESS)
    parser.add_argument("--startup-timeout", type=float, default=900.0, help=argparse.SUPPRESS)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.root = args.root.expanduser().resolve()
    if args.port in OWNER_PORTS:
        parser.error("Ports 8097 and 8098 belong to the owner's install.")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.serve:
        return serve_child(args.root, args.port, args.hold_seconds)

    def log(message: str) -> None:
        print(message, flush=True)

    try:
        check_root(args.root)
        large, small = select_projects(
            args.root / "pandrator.sqlite3", large_target=args.large_segments,
            small_target=args.small_segments, large_session=args.large_session,
            small_session=args.small_session,
        )
    except RootRefused as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    # Rejected here, not when the burst starts after server startup and every earlier phase.
    # A run without the burst ignores the option, which may name a root-specific read.
    selected = set(args.only.split(",")) if args.only else None
    runs_burst = not args.skip_burst and (not selected or "burst" in selected)
    heavy = burst_operation(read_matrix(large), args.burst_operation) if runs_burst else None
    label = args.label or args.root.name
    results: dict[str, Any] = {
        "schema_version": 1,
        "label": label,
        "mode": "profile" if args.profile else "timing",
        "started_at": datetime.now(UTC).isoformat(),
        "checkout": git_revision(),
        "owner_app_listening": owner_app_listening(),
        "runs": args.runs,
        "timeout_seconds": args.timeout,
        "repeat_limit_seconds": args.repeat_limit,
        "slow_limit_seconds": SLOW_LIMIT_SECONDS,
        "projects": {"large": describe_project(large), "small": describe_project(small)},
    }
    log(f"{label}: large plan {large.segments} segments / {large.revisions} revisions / "
        f"{large.takes} takes; small plan {small.segments} segments; "
        f"owner app listening: {results['owner_app_listening']}")
    output = args.out or default_output(args.root, label, args.profile)
    output.parent.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        # Rewritten after every completed operation or phase, so a stopped
        # multi-hour run keeps what it measured; finished_at marks completion.
        output.write_text(json.dumps(results, indent=2), encoding="utf-8")

    save()
    if args.profile:
        results["profile"] = []
        run_profile(args, large, small, log, results["profile"], save)
    else:
        port = args.port or free_port()
        started = time.perf_counter()
        child, ready = start_server(args.root, port, args.hold_seconds, args.startup_timeout)
        results["server_startup_seconds"] = round(time.perf_counter() - started, 3)
        reads: list[dict[str, Any]] = []
        results["reads"] = reads

        def record(row: dict[str, Any]) -> None:
            reads.append(row)
            save()

        try:
            client = Client(f"http://127.0.0.1:{ready['port']}", ready["token"], args.timeout)
            only = set(args.only.split(",")) if args.only else None
            # Order matters: abandoned requests keep the server busy, so the
            # burst, which abandons on purpose, runs last.
            repeat = args.repeat_limit

            def drain() -> float | None:
                return wait_for_idle(client, args.job_timeout)

            if not args.skip_reads:
                run_matrix(client, read_matrix(small), args.runs, "small", only, log,
                           repeat_limit=repeat, drain=drain, after_row=record)
            if not args.skip_large_reads and not args.skip_reads:
                run_matrix(client, read_matrix(large), args.runs, "large", only, log,
                           repeat_limit=repeat, drain=drain, after_row=record)
            if not args.skip_admission and (not only or "admission" in only):
                results["admissions"] = []
                for size in admission_sizes(args):
                    results["admissions"].append(run_admission(
                        client, large, small, size, job_timeout=args.job_timeout,
                        sample_interval=args.sample_interval, log=log,
                        listing_interval=args.listing_interval))
                    save()
            if heavy is not None:
                log(f"  read-load burst: {args.burst_operation}, {args.load_seconds:.0f}s x "
                    f"{args.load_workers} clients")
                # The sampler runs until the settled size is taken, so the peak
                # covers the after-load reads and the idle wait too.
                wal = WalSampler(args.root / "pandrator.sqlite3").start()
                try:
                    results["burst"] = read_burst(
                        client, heavy, seconds=args.load_seconds, workers=args.load_workers,
                        abandon_after=args.abandon_after,
                        light_operation=next(
                            op for op in read_matrix(small) if op.name == "auth_status"),
                        light_workers=args.light_workers, light_pace=args.light_pace,
                    )
                    wal_figures = {"start_bytes": wal.start_bytes,
                                   "burst_end_bytes": wal.sample()}
                    wal_figures["peak_bytes"] = wal.peak_bytes
                    results["burst"]["wal"] = wal_figures
                    results["burst"]["inflight_after"] = inflight(client)
                    save()
                    # Measured while the abandoned reads still run, as a client
                    # would meet the server after a burst of timeouts. With
                    # --only, the burst names the phase; the small reads follow
                    # the usual selection, all of them when only "burst" is given.
                    after_only = (None if only is None or only <= {"burst", "admission"}
                                  else only)
                    run_matrix(client, read_matrix(small), args.runs, "small_after_load",
                               after_only, log, repeat_limit=repeat, after_row=record)
                    wal_figures["after_load_reads_bytes"] = wal.sample()
                    busy = wait_for_idle(client, args.job_timeout)
                    results["burst"]["busy_after_small_reads_seconds"] = busy
                    # Each request's audit write is what restarts and truncates the log.
                    wal_figures["settle_statuses"] = [
                        client.call("GET", "/api/v1/auth/status", timeout=30)[0].status
                        for _ in range(3)]
                    wal_figures["settled_bytes"] = wal.stop()
                    wal_figures["peak_bytes"] = wal.peak_bytes
                finally:
                    wal.stop()
                log(f"  server busy {busy}s more after the after-load reads")
        finally:
            # None, not a missing key, when the child printed no counts, so a run
            # without them is not mistaken for an older run; saved here so a
            # stopped run keeps them too.
            results["server_logs"] = stop_server(child) or None
            save()
        print_table(results, log)
    results["finished_at"] = datetime.now(UTC).isoformat()
    save()
    log(f"\nResults written to {output.name} in the results folder beside the data root.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

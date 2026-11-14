"""Run ``python -m pandrator ...`` as the install does, with replay instrumentation.

    <uv env> python live_replay_launch.py --log <file.jsonl> -- --data-dir <root> serve ...
    <uv env> python live_replay_launch.py --log <file.jsonl> -- --data-dir <root> worker ...

Fork-only harness for the live-slowdown replay (story 2.15). It calls the real CLI
entry point unchanged and adds, in this process only:

- an outbound-connection guard: loopback only, and never the owner's ports (the
  install's TTS server on 8060, the app on 8097/8098), so a copied project's
  settings cannot reach the owner's services or the network;
- one JSON line per finished HTTP request (start, method, masked route, status,
  seconds, whether the client had already left);
- one JSON line per writer hold (BEGIN IMMEDIATE acquired to commit or rollback)
  and per implicit write transaction over a threshold, with the wait for the
  lock and the application frames that opened it;
- one JSON line per pool checkout that waited over a threshold;
- a stats line every few seconds: threads, in-flight requests with their ages,
  pool status, connections checked out, RSS.

It records ids masked, never tokens, bodies, query strings or paths on disk.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BLOCKED_PORTS = {8060, 8097, 8098}
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32}")


class Log:
    def __init__(self, path: Path) -> None:
        self._file = open(path, "a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()

    def write(self, kind: str, **fields: Any) -> None:
        record = {"t": datetime.now(UTC).isoformat(timespec="milliseconds"), "kind": kind, **fields}
        line = json.dumps(record, default=str)
        with self._lock:
            self._file.write(line + "\n")


def mask(path: str) -> str:
    return _ID.sub("<id>", path)


def guard_outbound() -> None:
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def allowed(address: Any) -> bool:
        if isinstance(address, tuple) and len(address) >= 2:
            host, port = str(address[0]), int(address[1])
            return host in LOOPBACK and port not in BLOCKED_PORTS
        return True

    def connect(self, address):  # noqa: ANN001
        if not allowed(address):
            raise ConnectionRefusedError("Blocked by the replay harness.")
        return original_connect(self, address)

    def connect_ex(self, address):  # noqa: ANN001
        if not allowed(address):
            return 10061
        return original_connect_ex(self, address)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]


def app_frames(limit: int = 5) -> str:
    frames = []
    for frame in traceback.extract_stack()[:-2]:
        name = frame.filename.replace("\\", "/")
        if "/pandrator/" in name and "/site-packages/" not in name and not name.endswith(
            ("/web/database.py",)
        ):
            frames.append(f"{Path(name).stem}:{frame.lineno}({frame.name})")
    return " < ".join(reversed(frames[-limit:]))


class Instrument:
    def __init__(self, log: Log, hold_threshold: float, wait_threshold: float) -> None:
        self.log = log
        self.hold_threshold = hold_threshold
        self.wait_threshold = wait_threshold
        self.local = threading.local()
        self.engines: list[Any] = []
        self.inflight: dict[int, tuple[str, str, float]] = {}
        self.inflight_lock = threading.Lock()
        self.counts = {"holds": 0, "requests": 0, "checkout_waits": 0}

    # ------------------------------------------------------------ database
    def attach(self, engine: Any) -> None:
        from sqlalchemy import event

        self.engines.append(engine)
        event.listen(engine, "before_cursor_execute", self._before)
        event.listen(engine, "after_cursor_execute", self._after)
        event.listen(engine, "commit", self._end)
        event.listen(engine, "rollback", self._end)
        raw_connection = engine.raw_connection

        def timed_raw_connection(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return raw_connection(*args, **kwargs)
            finally:
                waited = time.perf_counter() - started
                if waited >= self.wait_threshold:
                    self.counts["checkout_waits"] += 1
                    self.log.write("checkout_wait", seconds=round(waited, 3),
                                   pool=engine.pool.status(), where=app_frames())

        engine.raw_connection = timed_raw_connection

    def _state(self) -> dict[int, dict[str, Any]]:
        state = getattr(self.local, "state", None)
        if state is None:
            state = self.local.state = {}
        return state

    def _before(self, conn, _cursor, statement, *_args) -> None:
        self.local.statements = getattr(self.local, "statements", 0) + 1
        head = statement.lstrip()[:16].upper()
        state = self._state()
        if head.startswith("BEGIN IMMEDIATE"):
            state[id(conn)] = {"immediate": True, "asked": time.perf_counter(), "where": app_frames()}
            return
        entry = state.get(id(conn))
        if entry is None and head.startswith(("INSERT", "UPDATE", "DELETE")):
            state[id(conn)] = {"immediate": False, "asked": None,
                               "started": time.perf_counter(), "where": app_frames(),
                               "first": statement.strip()[:60]}

    def _after(self, conn, _cursor, statement, *_args) -> None:
        entry = self._state().get(id(conn))
        if entry and entry.get("immediate") and "started" not in entry and statement.lstrip()[
            :15
        ].upper().startswith("BEGIN IMMEDIATE"):
            entry["started"] = time.perf_counter()

    def _end(self, conn, *_args) -> None:
        entry = self._state().pop(id(conn), None)
        if not entry or "started" not in entry:
            return
        held = time.perf_counter() - entry["started"]
        waited = entry["started"] - entry["asked"] if entry.get("asked") else None
        self.counts["holds"] += 1
        if held >= self.hold_threshold or (waited or 0) >= self.wait_threshold:
            self.log.write(
                "hold", immediate=entry["immediate"], held=round(held, 3),
                waited=None if waited is None else round(waited, 3),
                thread=threading.current_thread().name, where=entry["where"],
                first=entry.get("first"),
            )

    # ------------------------------------------------------------ requests
    def wrap(self, app: Any) -> Any:
        wsgi_app = app.wsgi_app

        def timed(environ, start_response):  # noqa: ANN001
            started = time.perf_counter()
            began = datetime.now(UTC).isoformat(timespec="milliseconds")
            method = environ.get("REQUEST_METHOD", "")
            path = mask(environ.get("PATH_INFO", ""))
            status: list[str] = []

            def recording_start(code, headers, exc_info=None):  # noqa: ANN001
                status.append(code.split(" ", 1)[0])
                return start_response(code, headers, exc_info)

            key = threading.get_ident()
            self.local.statements = 0
            self.local.recast_cache = {}
            cpu_started = time.thread_time()
            with self.inflight_lock:
                self.inflight[key] = (method, path, started)
            try:
                return wsgi_app(environ, recording_start)
            finally:
                with self.inflight_lock:
                    self.inflight.pop(key, None)
                probe = environ.get("waitress.client_disconnected")
                try:
                    gone = bool(probe()) if callable(probe) else None
                except Exception:  # noqa: BLE001
                    gone = None
                self.counts["requests"] += 1
                self.log.write(
                    "request", began=began, method=method, path=path,
                    query=bool(environ.get("QUERY_STRING")),
                    summary="summary=true" in environ.get("QUERY_STRING", ""),
                    status=status[0] if status else None,
                    seconds=round(time.perf_counter() - started, 3), client_gone=gone,
                    thread_cpu=round(time.thread_time() - cpu_started, 3),
                    statements=getattr(self.local, "statements", 0),
                    thread=threading.current_thread().name,
                )

        app.wsgi_app = timed
        return app

    # ------------------------------------------------------------ stats
    def stats_loop(self, interval: float) -> None:
        try:
            import psutil

            process = psutil.Process()
        except Exception:  # noqa: BLE001
            process = None
        while True:
            time.sleep(interval)
            now = time.perf_counter()
            with self.inflight_lock:
                inflight = sorted(
                    ({"method": m, "path": p, "age": round(now - s, 1)} for m, p, s in self.inflight.values()),
                    key=lambda item: -item["age"],
                )
            pools = []
            for engine in self.engines:
                pool = engine.pool
                pools.append({
                    "checkedout": pool.checkedout() if hasattr(pool, "checkedout") else None,
                    "status": pool.status(),
                })
            self.log.write(
                "stats", threads=threading.active_count(),
                rss_mb=(process.memory_info().rss // 2**20) if process else None,
                cpu_s=(round(sum(process.cpu_times()[:2]), 1)) if process else None,
                inflight=inflight, pools=pools, counts=dict(self.counts),
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("--hold-threshold", type=float, default=0.25)
    parser.add_argument("--wait-threshold", type=float, default=0.25)
    parser.add_argument("--stats-interval", type=float, default=5.0)
    parser.add_argument("--patch-recast-cache", action="store_true",
                        help="Experiment: give generation_recast's apply_resolved_binding call a "
                             "per-request service-config cache, as every other call site has.")
    parser.add_argument("--switch-interval", type=float, default=None,
                        help="Override sys.setswitchinterval (diagnostic only).")
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    argv = [item for item in options.rest if item != "--"]

    guard_outbound()
    if options.switch_interval is not None:
        sys.setswitchinterval(options.switch_interval)
    log = Log(Path(options.log))
    instrument = Instrument(log, options.hold_threshold, options.wait_threshold)

    from pandrator.web import database as database_module

    original_init = database_module.Database.__init__

    def instrumented_init(self, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        instrument.attach(self.engine)

    database_module.Database.__init__ = instrumented_init  # type: ignore[method-assign]

    if options.patch_recast_cache:
        from pandrator.web import generation_recast

        original_apply = generation_recast.apply_resolved_binding

        def cached_apply(*args: Any, **kwargs: Any) -> Any:
            if kwargs.get("_service_config_cache") is None:
                cache = getattr(instrument.local, "recast_cache", None)
                if cache is None:
                    cache = instrument.local.recast_cache = {}
                kwargs["_service_config_cache"] = cache
            return original_apply(*args, **kwargs)

        generation_recast.apply_resolved_binding = cached_apply  # type: ignore[assignment]

    from pandrator.web import cli

    original_create_app = cli.create_app

    def instrumented_create_app(*args: Any, **kwargs: Any) -> Any:
        return instrument.wrap(original_create_app(*args, **kwargs))

    cli.create_app = instrumented_create_app  # type: ignore[assignment]
    threading.Thread(target=instrument.stats_loop, args=(options.stats_interval,),
                     name="replay-stats", daemon=True).start()
    log.write("start", pid=os.getpid(), command=argv[2:3] if len(argv) > 2 else argv,
              switch_interval=sys.getswitchinterval(), patch_recast_cache=options.patch_recast_cache)
    return int(cli.main(argv) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

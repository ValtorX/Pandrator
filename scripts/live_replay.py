"""Live-slowdown replay driver (story 2.15, fork-only).

    <uv env> python live_replay.py prepare --root <copy> --token-file <file> --active <revision id>
    <uv env> python live_replay.py drive --base http://127.0.0.1:<port> --token-file <file>
        --out <results.jsonl> --minutes 45 [--scenario ...]

``prepare`` points the copy's plan at the given revision and mints a bearer for
the copy into a token file outside the data root; the token is never printed.
``drive`` runs the UI's reads, its stage actions and MCP calls through the real
``pandrator_mcp`` client (15 s limit) against a server started with
``live_replay_launch.py``, and records every client-side latency as JSON lines.
Results hold timings, statuses and masked routes only.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import large_project_timing as timing  # noqa: E402

SID = "188d6a65-a31c-4f0f-8b04-f5aac8732f8c"


# ---------------------------------------------------------------- prepare


def prepare(root: Path, token_file: Path, active: str) -> None:
    timing.check_root(root)
    with sqlite3.connect(root / "pandrator.sqlite3") as raw:
        plan_id, = raw.execute("select id from generation_plans where session_id=?", (SID,)).fetchone()
        number = raw.execute(
            "select revision_number from generation_plan_revisions where id=? and plan_id=?",
            (active, plan_id),
        ).fetchone()
        if number is None:
            raise SystemExit("The revision does not belong to the session's plan.")
        raw.execute("update generation_plans set active_revision_id=? where id=?", (active, plan_id))
        raw.commit()
    from pandrator.web.auth import AuthService
    from pandrator.web.database import Database

    database = Database(root / "pandrator.sqlite3")
    try:
        _, token = AuthService(database).create_api_token("live-replay")
    finally:
        database.dispose()
    token_file.write_text(token, encoding="utf-8")
    print(json.dumps({"active_revision_number": number[0], "token_file_written": True}))


# ---------------------------------------------------------------- recording


class Recorder:
    def __init__(self, path: Path) -> None:
        self._file = open(path, "a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()
        self.started = time.perf_counter()

    def write(self, **fields: Any) -> None:
        fields = {"t": datetime.now(UTC).isoformat(timespec="milliseconds"),
                  "elapsed": round(time.perf_counter() - self.started, 1), **fields}
        with self._lock:
            self._file.write(json.dumps(fields, default=str) + "\n")


class Http:
    """Bearer HTTP client standing in for the browser's fetches."""

    def __init__(self, base: str, token: str, recorder: Recorder) -> None:
        import requests

        self.requests = requests
        self.base = base.rstrip("/")
        self.session = requests.Session()
        self.session.trust_env = False
        self.headers = {"Authorization": f"Bearer {token}"}
        self.recorder = recorder

    def call(self, label: str, method: str, path: str, *, timeout: float = 120.0,
             body: Any = None, headers: dict[str, str] | None = None,
             params: dict[str, Any] | None = None) -> tuple[int | None, Any]:
        merged = {**self.headers, **(headers or {})}
        if method != "GET" and "Idempotency-Key" not in merged:
            merged["Idempotency-Key"] = str(uuid.uuid4())
        started = time.perf_counter()
        status: int | None = None
        payload: Any = None
        error = None
        try:
            # A fresh connection per call, like a browser fetch aborted on timeout.
            response = self.requests.request(
                method, self.base + path, json=body, headers=merged, params=params,
                timeout=(10, timeout),
            )
            status = response.status_code
            try:
                payload = response.json()
            except ValueError:
                payload = None
        except self.requests.Timeout:
            error = "timeout"
        except self.requests.RequestException as caught:
            error = type(caught).__name__
        self.recorder.write(kind="client", label=label, method=method,
                            seconds=round(time.perf_counter() - started, 3), status=status, error=error)
        return status, payload


# ---------------------------------------------------------------- MCP client


def mcp_client(base: str, token: str):
    from pandrator_mcp.clients.application import ApplicationClient
    from pandrator_mcp.credentials import CredentialReference, CredentialResolver, EnvironmentCredentialBackend
    from pandrator_mcp.network_policy import NetworkPolicy, TargetMode
    from pandrator_mcp.targets import TargetProfile, TargetRegistry

    variable = "PANDRATOR_REPLAY_APPLICATION_TOKEN"
    environment = {variable: token}
    profile = TargetProfile(
        name="replay",
        mode=TargetMode.PRIVATE_NETWORK,
        application_origin=base,
        allowed_private_cidrs=("127.0.0.0/8",),
        allow_insecure_private_network=True,
        requested_application_scopes=("app.read", "app.write"),
        application_credential=CredentialReference(
            backend="environment", reference=variable, audience="application",
        ),
    )
    registry = TargetRegistry(
        [profile], network_policy=NetworkPolicy(lambda _host, _port: ("127.0.0.1",)),
    )
    return ApplicationClient(
        registry.bind("replay"),
        CredentialResolver((EnvironmentCredentialBackend(environment=environment),)),
        timeout_seconds=15.0,
    )


def timed_mcp(recorder: Recorder, label: str, call: Callable[[], Any]) -> Any:
    started = time.perf_counter()
    error = None
    result = None
    try:
        result = call()
    except Exception as caught:  # noqa: BLE001 - recorded, never raised
        error = f"{type(caught).__name__}:{getattr(caught, 'code', '')}"
    recorder.write(kind="mcp", label=label, seconds=round(time.perf_counter() - started, 3), error=error)
    return result


# ---------------------------------------------------------------- workload


def session_path(suffix: str) -> str:
    return f"/api/v1/sessions/{SID}{suffix}"


UI_BURST = [
    ("workflow", "GET", "/workflow", None),
    ("sources_status", "GET", "/sources/status", None),
    ("settings_output", "GET", "/settings/output", None),
    ("settings_text", "GET", "/settings/text", None),
    ("voice_setup", "GET", "/voice-setup", None),
    ("audiobook_setup", "GET", "/audiobook-setup", None),
    ("outcome_plan", "GET", "/outcome-plan", None),
    ("generation_summary", "GET", "/generation/summary", None),
    ("plan_status_summary", "GET", "/generation-plan/status", {"summary": "true"}),
]


class Workload:
    def __init__(self, http: Http, mcp: Any, recorder: Recorder, stop: threading.Event) -> None:
        self.http = http
        self.mcp = mcp
        self.recorder = recorder
        self.stop = stop

    def every(self, interval: float, action: Callable[[], None], jitter: float = 0.1) -> threading.Thread:
        def loop() -> None:
            while not self.stop.is_set():
                started = time.perf_counter()
                try:
                    action()
                except Exception as caught:  # noqa: BLE001
                    self.recorder.write(kind="loop_error", error=type(caught).__name__)
                wait = interval - (time.perf_counter() - started)
                self.stop.wait(max(0.2, wait + random.uniform(-jitter, jitter) * interval))

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        return thread

    # Fixed probes, tracked over time.
    def probe(self) -> None:
        self.http.call("probe_plan_status_summary", "GET", session_path("/generation-plan/status"),
                       params={"summary": "true"}, timeout=120)
        self.http.call("probe_auth_status", "GET", "/api/v1/auth/status", timeout=120)
        self.http.call("probe_generation_summary", "GET", session_path("/generation/summary"), timeout=120)

    def ui_burst(self) -> None:
        threads = []
        for label, method, suffix, params in UI_BURST:
            thread = threading.Thread(
                target=self.http.call, args=(f"ui_{label}", method, session_path(suffix)),
                kwargs={"params": params, "timeout": 120}, daemon=True,
            )
            thread.start()
            threads.append(thread)
        self.http.call("ui_jobs", "GET", "/api/v1/jobs", timeout=120)
        for thread in threads:
            thread.join()

    def events_stream(self) -> None:
        """Keep one /events stream open, as every open tab does."""
        import requests

        while not self.stop.is_set():
            try:
                with requests.get(self.http.base + "/api/v1/events", headers=self.http.headers,
                                  stream=True, timeout=(10, 60)) as response:
                    self.recorder.write(kind="client", label="events_open", status=response.status_code)
                    for _ in response.iter_lines():
                        if self.stop.is_set():
                            break
            except Exception as caught:  # noqa: BLE001
                self.recorder.write(kind="client", label="events_error", error=type(caught).__name__)
                self.stop.wait(2)

    def selection_save(self) -> None:
        status, workflow = self.http.call("selection_read", "GET", session_path("/workflow"))
        selection = None
        try:
            for stage in (workflow or {}).get("stages", []):
                if stage.get("key") == "prepare_text":
                    selection = stage
        except AttributeError:
            selection = None
        with sqlite3.connect(f"file:{self.db}?mode=ro", uri=True) as raw:
            row = raw.execute(
                "select artifact_id, revision from session_stage_selections where session_id=? and stage_key='prepare_text'",
                (SID,),
            ).fetchone()
            prepared = [r[0] for r in raw.execute(
                "select id from artifacts where session_id=? and role='prepared_text' and state!='deleted' order by created_at",
                (SID,))]
        if not row or not prepared:
            return
        target = random.choice([a for a in prepared if a != row[0]] or prepared)
        self.http.call("selection_save", "PUT", session_path("/stages/prepare_text/selection"),
                       body={"artifact_id": target}, headers={"If-Match": f'"{row[1]}"'})

    db: str = ""

    def impact(self) -> None:
        self.http.call("impact_prepare_text", "GET", session_path("/stages/prepare_text/impact"))

    def run_prepare_text(self) -> None:
        self.http.call("run_prepare_text", "POST", session_path("/stages/prepare_text/run"), body={})

    # MCP calls through the real client and its 15 s limit.
    def mcp_reads(self) -> None:
        mcp = self.mcp
        timed_mcp(self.recorder, "mcp_plan_status_full", lambda: mcp.get_speech_plan_status(SID))
        timed_mcp(self.recorder, "mcp_plan_status_summary",
                  lambda: mcp.get_speech_plan_status(SID, summary=True))
        timed_mcp(self.recorder, "mcp_list_segments", lambda: mcp.list_generation_segments(SID, limit=50))
        timed_mcp(self.recorder, "mcp_list_revisions", lambda: mcp.list_speech_plan_revisions(SID))

    def mcp_regenerate(self) -> None:
        with sqlite3.connect(f"file:{self.db}?mode=ro", uri=True) as raw:
            active, = raw.execute(
                "select active_revision_id from generation_plans where session_id=?", (SID,)).fetchone()
            ids = [r[0] for r in raw.execute(
                "select id from generation_segments where plan_revision_id=? and removed=0 "
                "order by ordinal limit 2 offset ?", (active, random.randrange(0, 4000)))]
        timed_mcp(self.recorder, "mcp_regenerate_segments", lambda: self.mcp.start_generation_run(
            SID, segment_ids=ids, operation="regenerate", admission=True,
            idempotency_key=str(uuid.uuid4())))

    def mcp_recast_preview(self) -> None:
        timed_mcp(self.recorder, "mcp_recast_preview",
                  lambda: self.mcp.generation_recast_request("preview", {"session_id": SID}))


def drive(options: argparse.Namespace) -> None:
    token = Path(options.token_file).read_text(encoding="utf-8").strip()
    recorder = Recorder(Path(options.out))
    http = Http(options.base, token, recorder)
    mcp = mcp_client(options.base, token) if options.mcp else None
    stop = threading.Event()
    work = Workload(http, mcp, recorder, stop)
    work.db = Path(options.db).as_posix()
    recorder.write(kind="start", scenario=options.scenario, minutes=options.minutes)
    plan = set(options.scenario.split(","))
    threading.Thread(target=work.events_stream, daemon=True).start()
    work.every(options.probe_interval, work.probe, jitter=0.0)
    if "ui" in plan:
        work.every(10.0, work.ui_burst)
    if "selection" in plan:
        work.every(300.0, work.selection_save, jitter=0.2)
    if "impact" in plan:
        work.every(120.0, work.impact, jitter=0.2)
    if "prepare" in plan:
        work.every(600.0, work.run_prepare_text, jitter=0.1)
    if "mcp" in plan and mcp is not None:
        work.every(options.mcp_interval, work.mcp_reads, jitter=0.2)
    if "regenerate" in plan and mcp is not None:
        work.every(options.regenerate_interval, work.mcp_regenerate, jitter=0.2)
    if "recast" in plan and mcp is not None:
        work.every(options.recast_interval, work.mcp_recast_preview, jitter=0.1)
    deadline = time.monotonic() + options.minutes * 60
    while time.monotonic() < deadline:
        time.sleep(5)
    stop.set()
    recorder.write(kind="stop")


def convoy(options: argparse.Namespace) -> None:
    """Probe latency with one recast preview in flight, then with none."""
    token = Path(options.token_file).read_text(encoding="utf-8").strip()
    recorder = Recorder(Path(options.out))
    http = Http(options.base, token, recorder)
    work = Workload(http, None, recorder, threading.Event())
    work.db = Path(options.db).as_posix()
    recorder.write(kind="start", scenario="convoy", label=options.label)

    def probes(phase: str) -> None:
        for _ in range(options.rounds):
            http.call(f"{phase}_plan_status_summary", "GET", session_path("/generation-plan/status"),
                      params={"summary": "true"})
            http.call(f"{phase}_generation_summary", "GET", session_path("/generation/summary"))
            http.call(f"{phase}_workflow", "GET", session_path("/workflow"))
        work.selection_save()

    probes("idle_before")
    recast = threading.Thread(target=http.call, args=("recast_preview", "POST",
                              session_path("/generation-recast/preview")),
                              kwargs={"body": {}, "timeout": 3600}, daemon=True)
    recast.start()
    time.sleep(10)
    probes("one_recast")
    recast.join()
    probes("idle_after")


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--root", required=True)
    p.add_argument("--token-file", required=True)
    p.add_argument("--active", required=True)
    d = commands.add_parser("drive")
    d.add_argument("--base", required=True)
    d.add_argument("--token-file", required=True)
    d.add_argument("--db", required=True)
    d.add_argument("--out", required=True)
    d.add_argument("--minutes", type=float, default=45)
    d.add_argument("--scenario", default="ui,selection,impact,mcp")
    d.add_argument("--probe-interval", type=float, default=10.0)
    d.add_argument("--mcp-interval", type=float, default=60.0)
    d.add_argument("--recast-interval", type=float, default=120.0)
    d.add_argument("--regenerate-interval", type=float, default=180.0)
    d.add_argument("--mcp", action=argparse.BooleanOptionalAction, default=True)
    c = commands.add_parser("convoy")
    c.add_argument("--base", required=True)
    c.add_argument("--token-file", required=True)
    c.add_argument("--db", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--label", default="")
    c.add_argument("--rounds", type=int, default=4)
    options = parser.parse_args()
    if options.command == "convoy":
        convoy(options)
        return 0
    if options.command == "prepare":
        prepare(Path(options.root), Path(options.token_file), options.active)
    else:
        drive(options)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Small-N tests for large_project_seed.py and large_project_timing.py."""

from __future__ import annotations

import http.server
import json
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import admission_writer_holds as holds_tool  # noqa: E402
import large_project_seed as seed_tool  # noqa: E402
import large_project_timing as timing  # noqa: E402

TINY_SHAPE = {
    "large": {"segments": 12, "regenerated_segments": 2, "optimized_segments": 1, "chapter_every": 5},
    "small": {"segments": 5, "regenerated_segments": 1, "optimized_segments": 0, "chapter_every": 0,
              "draft_revision": 1.2, "draft_removed": 1},
    "voices": 4,
    "characters": 2,
    "small_characters": 1,
    "filler_segments": 3,
}


@pytest.fixture(scope="module")
def seeded_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("perf") / "synthetic"
    assert seed_tool.main(["--root", str(root), "--shape", json.dumps(TINY_SHAPE)]) == 0
    return root


def test_seed_matches_the_requested_shape(seeded_root: Path) -> None:
    shape = seed_tool.measure(seeded_root)
    large, small, filler = shape["plans"]
    assert large["casting_enabled"] and small["casting_enabled"] and not filler["casting_enabled"]
    # Narrator, two categories and the characters each hold a cast reference.
    assert large["speech_references"] == 3 + 2 and small["speech_references"] == 3 + 1
    assert [revision["segments"] for revision in filler["revisions"]] == [3]
    assert filler["revisions"][0]["takes"] == 1
    sizes = [revision["segments"] for revision in large["revisions"]]
    # 0.947 x 12 rounds to 11; the active revision restores the last one.
    assert sizes == [11, 12, 12]
    assert [revision["active"] for revision in large["revisions"]] == [False, False, True]
    first, second, active = large["revisions"]
    assert first["takes"] == first["active_takes"] == 11
    assert second["active_takes"] == 12 and second["takes"] > 12
    # Restore clones every take, then two segments gain a newer active take.
    assert active["takes"] == second["takes"] + 2
    assert active["active_takes"] == 12 and active["completed"] == 12
    assert active["optimized"] == 1
    # The draft between the last earlier revision and the restore has no audio.
    assert [revision["segments"] for revision in small["revisions"]] == [5, 5, 6, 5]
    draft = small["revisions"][2]
    assert (draft["active"], draft["removed"], draft["takes"]) == (False, 1, 0)
    assert small["revisions"][3]["active"] and small["revisions"][3]["active_takes"] == 5
    assert small["revisions"][0]["takes"] == 30
    assert shape["totals"]["voices"] == 4
    assert shape["totals"]["voice_samples"] == 4


def test_seeded_takes_carry_the_apps_current_identity(seeded_root: Path) -> None:
    large, _small = timing.select_projects(
        seeded_root / "pandrator.sqlite3", large_target=12, small_target=5
    )
    app, token, _worker, gpu_probe = timing.build_services(
        seeded_root, hold_seconds=0, cancel_in_process=True
    )
    try:
        response = app.test_client().get(
            f"/api/v1/sessions/{large.session_id}/generation-segments",
            query_string={"limit": 20}, headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        items = response.get_json()["items"]
        assert len(items) == 12
        assert {item["audio_reuse_reason"] for item in items} == {"reusable"}
        # Only the harness's own jobs run: a copied database's other work never does.
        assert set(_worker.handlers) == {"noop", "generation.admit", "generation.run"}
    finally:
        gpu_probe.stop()
        app.extensions["pandrator"]["database"].dispose()


def test_seed_refuses_a_non_empty_root(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("user file", encoding="utf-8")
    assert seed_tool.main(["--root", str(tmp_path)]) == 2
    assert [path.name for path in tmp_path.iterdir()] == ["keep.txt"]


def test_shape_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        seed_tool.Shape.from_overrides({"large": {"segmnets": 5}})
    with pytest.raises(ValueError):
        seed_tool.Shape.from_overrides({"colour": 1})


def test_timing_refuses_a_live_lock_and_a_missing_marker(tmp_path: Path, seeded_root: Path) -> None:
    root = tmp_path / "copy"
    root.mkdir()
    (root / "pandrator.sqlite3").write_bytes((seeded_root / "pandrator.sqlite3").read_bytes())
    with pytest.raises(timing.RootRefused, match="migration-web-v1.json"):
        timing.check_root(root)
    (root / "migration-web-v1.json").write_text("{}", encoding="utf-8")
    (root / "pandrator.instance.lock").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    with pytest.raises(timing.RootRefused, match="live PID"):
        timing.check_root(root)
    assert timing.main(["--root", str(root)]) == 2
    (root / "pandrator.instance.lock").write_text(json.dumps({"pid": 0}), encoding="utf-8")
    timing.check_root(root)


def _copy_root(seeded_root: Path, root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "pandrator.sqlite3").write_bytes((seeded_root / "pandrator.sqlite3").read_bytes())
    (root / "migration-web-v1.json").write_text("{}", encoding="utf-8")
    return root


def test_both_scripts_refuse_a_root_inside_an_install(tmp_path: Path, seeded_root: Path) -> None:
    install = tmp_path / "install"
    (install / "app").mkdir(parents=True)
    (install / "app" / "current.json").write_text("{}", encoding="utf-8")
    data = _copy_root(seeded_root, install / "data")
    with pytest.raises(timing.RootRefused, match="install"):
        timing.check_root(data)
    assert timing.main(["--root", str(data)]) == 2
    # --force does not override it, and nothing is created.
    assert seed_tool.main(["--root", str(install / "fresh"), "--force"]) == 2
    assert not (install / "fresh").exists()
    # An app/ folder without version pointers is not an install.
    (install / "app" / "current.json").unlink()
    timing.check_root(data)


def test_timing_refuses_a_root_with_pending_jobs(tmp_path: Path, seeded_root: Path) -> None:
    import sqlite3

    root = _copy_root(seeded_root, tmp_path / "copy")
    with sqlite3.connect(root / "pandrator.sqlite3") as connection:
        (workspace_id,) = connection.execute("SELECT id FROM workspaces LIMIT 1").fetchone()
        connection.execute(
            "INSERT INTO jobs (id, kind, workspace_id, status, payload_json, resource_keys_json, "
            "progress, lease_generation, attempts, max_attempts, created_at, updated_at) VALUES "
            "('queued-1', 'export.create', ?, 'queued', '{}', '[]', 0, 0, 0, 1, "
            "'2026-11-10 00:00:00', '2026-11-10 00:00:00')",
            (workspace_id,),
        )
    with pytest.raises(timing.RootRefused, match="1 queued or running"):
        timing.check_root(root)
    with sqlite3.connect(root / "pandrator.sqlite3") as connection:
        connection.execute("UPDATE jobs SET status = 'succeeded'")
    timing.check_root(root)


def test_only_selects_projects_operations_and_the_burst(seeded_root: Path, tmp_path: Path) -> None:
    assert timing.selected(None, "small", "plan_status")
    assert timing.selected({"plan_status"}, "large", "plan_status")
    assert timing.selected({"small"}, "small", "revisions")
    assert not timing.selected({"small"}, "large", "revisions")
    assert timing.selected({"large.revisions"}, "large", "revisions")
    assert not timing.selected({"large.revisions"}, "small", "revisions")
    common = ["--root", str(seeded_root), "--runs", "1", "--skip-admission", "--label", "tiny",
              "--large-segments", "12", "--small-segments", "5"]
    without = tmp_path / "without.json"
    assert timing.main([*common, "--only", "auth_status", "--out", str(without)]) == 0
    result = json.loads(without.read_text(encoding="utf-8"))
    assert "burst" not in result
    assert {(row["phase"], row["operation"]) for row in result["reads"]} == {
        ("small", "auth_status"), ("large", "auth_status")}
    with_burst = tmp_path / "with.json"
    assert timing.main([*common, "--only", "auth_status,burst", "--load-seconds", "1",
                        "--light-workers", "1", "--light-pace", "0.1",
                        "--out", str(with_burst)]) == 0
    result = json.loads(with_burst.read_text(encoding="utf-8"))
    assert result["burst"]["requests"] >= 1
    assert set(result["burst"]["wal"]) == {
        "start_bytes", "peak_bytes", "burst_end_bytes", "after_load_reads_bytes",
        "settled_bytes", "settle_statuses"}
    assert result["burst"]["wal"]["settle_statuses"] == [200, 200, 200]
    light = result["burst"]["light"]
    assert light["operation"] == "auth_status" and light["workers"] == 1
    assert light["requests"] >= 1 and light["statuses"] == [200]
    # The child's log counts come back through stop_server for the whole run.
    assert set(result["server_logs"]) == {"audit_spools", "database_locked"}
    assert all(type(value) is int for value in result["server_logs"].values())
    assert ("small_after_load", "auth_status") in {
        (row["phase"], row["operation"]) for row in result["reads"]}
    assert result["burst"]["operation"] == "segments_default"
    chosen = tmp_path / "chosen.json"
    assert timing.main([*common, "--only", "auth_status,burst", "--load-seconds", "1",
                        "--burst-operation", "plan_status", "--out", str(chosen)]) == 0
    assert json.loads(chosen.read_text(encoding="utf-8"))["burst"]["operation"] == "plan_status"


def test_unknown_burst_operation_is_rejected_before_the_server_starts(
    seeded_root: Path, tmp_path: Path, monkeypatch,
) -> None:
    def started(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the server must not start for an unknown burst operation")

    monkeypatch.setattr(timing, "start_server", started)
    with pytest.raises(SystemExit, match="nope"):
        timing.main(["--root", str(seeded_root), "--runs", "1", "--label", "tiny",
                     "--large-segments", "12", "--small-segments", "5",
                     "--burst-operation", "nope", "--out", str(tmp_path / "x.json")])


def test_burst_operation_must_be_a_read_matrix_name() -> None:
    operations = [timing.Operation("auth_status", "GET", "/a"),
                  timing.Operation("plan_status", "GET", "/p")]
    assert timing.burst_operation(operations, "plan_status").path == "/p"
    with pytest.raises(SystemExit, match="auth_status, plan_status"):
        timing.burst_operation(operations, "nope")


def test_a_stopped_run_keeps_what_it_measured(seeded_root: Path, tmp_path: Path, monkeypatch) -> None:
    def stop(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(timing, "run_admission", stop)
    output = tmp_path / "partial.json"
    with pytest.raises(KeyboardInterrupt):
        timing.main(["--root", str(seeded_root), "--runs", "1", "--skip-burst", "--label", "tiny",
                     "--only", "auth_status,admission", "--large-segments", "12",
                     "--small-segments", "5", "--out", str(output)])
    partial = json.loads(output.read_text(encoding="utf-8"))
    assert "finished_at" not in partial
    assert [row["operation"] for row in partial["reads"]] == ["auth_status", "auth_status"]
    assert set(partial["server_logs"]) == {"audit_spools", "database_locked"}


def test_timing_never_uses_the_owner_ports() -> None:
    with pytest.raises(SystemExit):
        timing.parse_args(["--root", ".", "--port", "8098"])
    assert timing.free_port() not in timing.OWNER_PORTS


def test_check_window_reads_the_progress_events() -> None:
    events = [
        {"event_type": "job.claimed", "payload_json": {}, "created_at": "2026-11-10T10:00:00+00:00"},
        {"event_type": "job.progress", "payload_json": {"detail": timing.CHECK_START},
         "created_at": "2026-11-10T10:00:01.500000+00:00"},
        {"event_type": "job.progress", "payload_json": {"progress": 0.0},
         "created_at": "2026-11-10T10:00:30+00:00"},
        {"event_type": "job.progress", "payload_json": {"detail": timing.CHECK_END},
         "created_at": "2026-11-10T10:06:01.750000+00:00"},
    ]
    assert timing.check_window(events) == {
        "check_started": True, "check_finished": True, "check_seconds": 360.25,
    }
    assert timing.check_window(events[:2]) == {
        "check_started": True, "check_finished": False, "check_seconds": None,
    }


def test_job_timings_and_scrubbed_errors() -> None:
    job = {
        "status": "failed", "created_at": "2026-11-10T10:00:00", "started_at": "2026-11-10T10:00:02Z",
        "finished_at": "2026-11-10T10:11:02+00:00", "error_code": "ValueError",
        "error_message": r"Could not read C:\Users\someone\data\voices\a.wav or /home/x/y.wav today",
    }
    timings = timing.job_timings(job)
    assert timings["queue_wait_seconds"] == 2.0
    assert timings["duration_seconds"] == 660.0
    assert "someone" not in timings["error"] and "/home" not in timings["error"]
    assert timings["error"].startswith("Could not read <path>")


class _FailedAdmissionClient:
    """Answers the admission POST, then reports the admission job as failed."""

    timeout = 5.0

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def call(self, method: str, path: str, **_options: object) -> tuple[timing.Sample, object]:
        self.calls.append((method, path))
        if method == "POST":
            return timing.Sample(0.1, 202), {"job_id": "admit-1"}
        return timing.Sample(0.1, 200), {
            "status": "failed", "created_at": "2026-11-10T10:00:00Z",
            "started_at": "2026-11-10T10:00:01Z", "finished_at": "2026-11-10T10:00:04Z",
            "error_message": r"Voice missing at C:\Users\someone\voices\a.wav",
        }


def test_a_failed_admission_is_recorded_and_the_run_continues() -> None:
    project = timing.ProjectChoice("s-1", 12, 3, 20, "r-3", "r-2", ["g-1", "g-2"])
    client = _FailedAdmissionClient()
    results = [
        timing.run_admission(client, project, project, size, job_timeout=5, sample_interval=0,
                             log=lambda _message: None)
        for size in (1, 2)
    ]
    # Both sizes ran: a failed admission does not stop the run.
    assert [result["segments"] for result in results] == [1, 2]
    for result in results:
        assert result["admission"]["status"] == "failed"
        assert result["admission"]["duration_seconds"] == 3.0
        assert result["admission"]["error"].startswith("Voice missing at <path>")
        assert "run" not in result
    assert not any("/cancel" in path for _method, path in client.calls)


class _SlowHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path.startswith("/slow"):
            time.sleep(2.0)
        body = b'{"ok": true}'
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, *_args: object) -> None:
        pass


def test_a_timed_out_request_is_recorded_and_the_run_continues() -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = timing.Client(f"http://127.0.0.1:{server.server_address[1]}", "token", timeout=0.5)
        operations = [
            timing.Operation("slow", "GET", "/slow"),
            timing.Operation("fast", "GET", "/fast"),
        ]
        rows = timing.run_matrix(client, operations, 2, "large", None, lambda _message: None)
        # Without a timeout, an operation over the repeat limit also runs once.
        patient = timing.Client(client.base_url, "token", timeout=10)
        limited = timing.run_matrix(patient, operations[:1], 3, "large", None, lambda _message: None,
                                    repeat_limit=1.0)
    finally:
        server.shutdown()
    slow, fast = rows
    # A timed-out request is not repeated: it is still running on the server.
    assert slow["runs"] == 1 and slow["timeouts"] == 1
    assert slow["verdict"] == "slow" and slow["median_seconds"] is None
    assert fast["runs"] == 2 and fast["timeouts"] == 0 and fast["statuses"] == [200]
    assert fast["verdict"] == "fast"
    assert limited[0]["runs"] == 1 and limited[0]["statuses"] == [200]


def test_verdict_uses_the_fifteen_second_limit() -> None:
    assert timing.verdict([timing.Sample(14.9, 200)]) == "fast"
    assert timing.verdict([timing.Sample(1.0, 200), timing.Sample(15.1, 200)]) == "slow"
    assert timing.verdict([timing.Sample(1.0, None, timed_out=True)]) == "slow"
    assert timing.verdict([]) == "not_run"


def test_normalize_sql_collapses_parameter_lists() -> None:
    statement = "SELECT a\n  FROM t WHERE id IN (?, ?, ?,\n ?) AND b = ?"
    assert timing.normalize_sql(statement) == "SELECT a FROM t WHERE id IN (?, ...) AND b = ?"


def test_function_names_never_carry_paths() -> None:
    name = timing.function_name(r"C:\Users\someone\src\pandrator\web\jobs.py", 12, "run_once")
    assert name == "pandrator.web.jobs:12(run_once)"
    assert timing.function_name("~", 0, "<built-in method time.sleep>") == "~(<built-in method time.sleep>)"


def test_timing_run_writes_the_result_schema(seeded_root: Path, tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    code = timing.main([
        "--root", str(seeded_root), "--runs", "1", "--skip-burst", "--admission-sizes", "1",
        "--large-segments", "12", "--small-segments", "5", "--out", str(output), "--label", "tiny",
        "--listing-interval", "1", "--ui-interval", "1",
    ])
    assert code == 0
    text = output.read_text(encoding="utf-8")
    result = json.loads(text)
    assert result["schema_version"] == 1 and result["mode"] == "timing"
    assert result["projects"]["large"] == {"segments": 12, "revisions": 3, "takes": result["projects"]["large"]["takes"]}
    operations = {(row["phase"], row["operation"]) for row in result["reads"]}
    for name in ("auth_status", "segments_default", "segments_compact", "segments_fields",
                 "segments_limit100", "segments_non_active", "plan_status", "plan_status_summary",
                 "revisions", "revisions_summary", "revision_active", "revision_non_active"):
        assert ("large", name) in operations and ("small", name) in operations
    assert all(row["statuses"] == [200] for row in result["reads"])
    (admission,) = result["admissions"]
    assert admission["admission"]["status"] == "succeeded"
    assert admission["admission"]["duration_seconds"] is not None
    assert admission["run"]["check_finished"] is True
    assert admission["run"]["check_seconds"] is not None
    assert admission["run"]["status"] in {"canceled", "cancelled"}
    assert set(admission["reads_during"]) == {
        "auth_status", "segments_default", "large_ui_runs_compact", "large_plan_status_summary",
        "large_ui_generation_summary", "large_segments_default",
    }
    assert admission["sampling"] == {"auth_status_every_seconds": 2.0,
                                     "small_listing_every_seconds": 1.0,
                                     "large_ui_reads_every_seconds": 1.0}
    # Timings, counts and names only: no seeded text, titles or paths.
    for forbidden in ("Synthetic large book", "Chapter", str(seeded_root), seeded_root.name, "Users"):
        assert forbidden not in text


def test_inflight_counter_counts_requests_and_answers_its_own_path() -> None:
    seen: list[int] = []

    def app(_environ, start_response):
        seen.append(counter.count)
        start_response("200 OK", [])
        return [b"done"]

    counter = timing.InflightCounter(app)
    assert counter({"PATH_INFO": "/api/v1/auth/status"}, lambda *_args: None) == [b"done"]
    assert seen == [1] and counter.count == 0
    (body,) = counter({"PATH_INFO": timing.INFLIGHT_PATH}, lambda *_args: None)
    assert json.loads(body) == {"inflight": 0}


def test_server_log_counter_counts_spools_and_locked_records_and_round_trips() -> None:
    counter = timing.ServerLogCounter()
    logger = logging.getLogger("perf-counter-test")
    logger.propagate = False
    logger.addHandler(counter)
    try:
        logger.warning("Audit writer busy; event saved to pending audit spool")
        logger.warning("Audit writer busy; event saved to pending audit spool")
        logger.error("request failed: (sqlite3.OperationalError) database is locked")
        try:
            raise RuntimeError("Database is locked")
        except RuntimeError:
            logger.exception("write failed")
        logger.warning("unrelated")
    finally:
        logger.removeHandler(counter)
    assert (counter.audit_spools, counter.database_locked) == (2, 2)
    assert timing.parse_log_counts(counter.line() + "\n") == {
        "audit_spools": 2, "database_locked": 2}


def test_log_counts_ignore_lines_that_are_not_counts() -> None:
    assert timing.parse_log_counts("PERF-READY {}") is None
    assert timing.parse_log_counts(timing.LOGS_PREFIX + "not json") is None
    assert timing.parse_log_counts(timing.LOGS_PREFIX + "[1]") is None
    assert timing.parse_log_counts(timing.LOGS_PREFIX + '{"audit_spools": "1"}') is None
    assert timing.parse_log_counts(timing.LOGS_PREFIX + '{"audit_spools": 0, "database_locked": 0}') == {
        "audit_spools": 0, "database_locked": 0}


def test_burst_adds_light_reads_beside_the_heavy_ones() -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = timing.Client(f"http://127.0.0.1:{server.server_address[1]}", "token", timeout=5)
        heavy = timing.Operation("heavy", "GET", "/heavy")
        light = timing.Operation("auth_status", "GET", "/light")
        alone = timing.read_burst(client, heavy, seconds=0.3, workers=1, abandon_after=5)
        mixed = timing.read_burst(
            client, heavy, seconds=0.3, workers=1, abandon_after=5,
            light_operation=light, light_workers=3, light_pace=0.05)
    finally:
        server.shutdown()
    assert "light" not in alone
    assert mixed["requests"] >= 1 and mixed["workers"] == 1
    summary = mixed["light"]
    assert summary["operation"] == "auth_status" and summary["workers"] == 3
    assert summary["pace_seconds"] == 0.05 and summary["timeout_seconds"] == 30.0
    assert summary["requests"] >= 3 and summary["statuses"] == [200]
    assert summary["timeouts"] == 0 and summary["errors"] == 0
    assert summary["median_seconds"] is not None and summary["max_seconds"] is not None
    lines: list[str] = []
    timing.print_table({"burst": {**mixed, "inflight_after": 0}}, lines.append)
    assert any("burst light reads" in line and "3 clients" in line for line in lines)


def test_table_says_when_server_log_counts_were_not_collected() -> None:
    lines: list[str] = []
    timing.print_table({"server_logs": None}, lines.append)
    assert any("counts not collected" in line for line in lines)
    lines.clear()
    timing.print_table({"server_logs": {"audit_spools": 3, "database_locked": 0}}, lines.append)
    assert any("audit spools 3" in line for line in lines)


def test_wal_sampler_tracks_the_peak_and_a_missing_file_counts_as_zero(tmp_path: Path) -> None:
    database = tmp_path / "pandrator.sqlite3"
    wal = tmp_path / "pandrator.sqlite3-wal"
    assert timing.wal_bytes(database) == 0
    sampler = timing.WalSampler(database, interval=0.01).start()
    try:
        wal.write_bytes(b"x" * 100)
        deadline = time.monotonic() + 10
        while sampler.peak_bytes < 100 and time.monotonic() < deadline:
            time.sleep(0.01)
        wal.write_bytes(b"x" * 10)
    finally:
        final = sampler.stop()
    assert sampler.start_bytes == 0 and sampler.peak_bytes == 100 and final == 10
    wal.unlink()
    assert timing.wal_bytes(database) == 0
    assert not database.exists()


def test_table_prints_one_wal_line() -> None:
    lines: list[str] = []
    wal = {"start_bytes": 0, "peak_bytes": 2097152, "burst_end_bytes": 2097152,
           "after_load_reads_bytes": 2097152, "settled_bytes": 1048576}
    burst = {"requests": 1, "seconds": 1.0, "workers": 1, "abandoned": 0,
             "abandon_after_seconds": 15.0, "wal": wal}
    timing.print_table({"burst": burst}, lines.append)
    printed = [line for line in lines if line.startswith("burst WAL")]
    assert len(printed) == 1 and "settled 1.0 MiB" in printed[0] and "peak 2.0 MiB" in printed[0]


def test_writer_holds_run_reports_the_commit_transaction(seeded_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "copy"
    shutil.copytree(seeded_root, root)
    output = tmp_path / "holds.json"
    code = holds_tool.main([
        "--root", str(root), "--runs", "2", "--sizes", "1", "--large-segments", "12",
        "--small-segments", "5", "--out", str(output), "--label", "tiny",
    ])
    assert code == 0
    text = output.read_text(encoding="utf-8")
    result = json.loads(text)
    admissions = [item for item in result["runs"] if item["phase"] == "admission"]
    assert [item["run"] for item in admissions] == [1, 2]
    for item in admissions:
        commit = item["admission_commit"]
        assert commit["hold_seconds"] > 0 and "start_in_session (total)" in commit["pieces"]
        assert commit["fallback"] is False and item["claimed"] is True
        assert item["job_status"] == "succeeded"
        assert item["writer_holds"] >= 2
    (summary,) = result["summary"]
    assert summary["segments"] == 1 and summary["runs"] == 2 and summary["excluded_runs"] == 0
    assert summary["commit_hold_median_seconds"] is not None
    # Timings, counts and function names only: no seeded text, titles or paths.
    for forbidden in ("Synthetic large book", "Chapter", str(seeded_root), seeded_root.name, "Users"):
        assert forbidden not in text


def test_writer_hold_summary_takes_the_median_per_size() -> None:
    def run(size: int, seconds: float) -> dict:
        return {"segments": size, "phase": "admission", "holds_over_250ms": [],
                "admission_commit": {"hold_seconds": seconds}}

    runs = [run(1, 0.8), run(1, 0.6), run(1, 0.7), run(15, 0.9), run(15, 0.8), run(15, 0.7)]
    one, fifteen = holds_tool.summarize(runs, [1, 15])
    assert one["commit_hold_median_seconds"] == 0.7 and one["commit_hold_within_target"]
    assert one["excluded_runs"] == 0
    assert fifteen["commit_hold_median_seconds"] == 0.8 and not fifteen["commit_hold_within_target"]


def test_writer_hold_summary_excludes_uncertain_and_failed_runs() -> None:
    def run(seconds: float, **extra) -> dict:
        commit = {"hold_seconds": seconds, "fallback": extra.pop("fallback", False)}
        return {"segments": 1, "phase": "admission", "holds_over_250ms": [],
                "admission_commit": commit, "claimed": True, "job_status": "succeeded", **extra}

    runs = [run(0.5), run(0.6), run(0.1, fallback=True), run(0.1, job_status="failed"),
            run(0.1, claimed=False, job_status=None), run(0.1, admission_commit=None)]
    (summary,) = holds_tool.summarize(runs, [1])
    assert summary["runs"] == 6 and summary["excluded_runs"] == 4
    assert summary["commit_hold_seconds"] == [0.5, 0.6]
    assert summary["commit_hold_median_seconds"] == 0.55 and summary["commit_hold_within_target"]
    # Nothing certain left: no verdict, never a pass.
    (empty,) = holds_tool.summarize([run(0.1, fallback=True)], [1])
    assert empty["commit_hold_median_seconds"] is None and not empty["commit_hold_within_target"]
    assert holds_tool.describe([{"seconds": 0.4, "thread": "Main", "where": "x", "pieces": {},
                                 "in_admission": True, "admission_commit": False}]
                               )["admission_commit"]["fallback"] is True


def test_writer_hold_ignores_other_connections_on_the_same_thread(tmp_path: Path) -> None:
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'holds.sqlite3'}")
    recorder = holds_tool.HoldRecorder(engine)
    try:
        with engine.connect() as writer:
            writer.exec_driver_sql("BEGIN IMMEDIATE")
            # The credentials cache reloads through its own session inside a hold.
            with engine.connect() as other:
                other.exec_driver_sql("SELECT 1")
                other.commit()
            recorder.piece("after the other commit", 0.01)
            writer.commit()
    finally:
        recorder.close()
        engine.dispose()
    (hold,) = recorder.take()
    assert hold["pieces"] == {"after the other commit": [0.01, 1]}


@pytest.mark.parametrize("sizes", ["", "0,1", "1,x"])
def test_writer_holds_rejects_bad_sizes(sizes: str, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        holds_tool.parse_args(["--root", str(tmp_path), "--sizes", sizes])


def test_writer_holds_refuses_sizes_beyond_the_plan(seeded_root: Path, tmp_path: Path) -> None:
    code = holds_tool.main([
        "--root", str(seeded_root), "--sizes", "1,99", "--large-segments", "12",
        "--small-segments", "5", "--out", str(tmp_path / "holds.json"),
    ])
    assert code == 2 and not (tmp_path / "holds.json").exists()


def _revision_database(path: Path, settings: dict, artifact_path: str | None) -> Path:
    import sqlite3

    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE generation_plan_revisions (id TEXT, settings_json TEXT)")
        connection.execute("CREATE TABLE artifacts (id TEXT, relative_path TEXT)")
        connection.execute("INSERT INTO generation_plan_revisions VALUES ('rev', ?)",
                           (json.dumps(settings),))
        if artifact_path is not None:
            connection.execute("INSERT INTO artifacts VALUES ('art', ?)", (artifact_path,))
    return path


def test_reviewable_route_wires_the_active_revision_and_its_source(tmp_path: Path) -> None:
    database = _revision_database(tmp_path / "a.sqlite3", {"_source_artifact_id": "art"}, "x/Source.JSON")
    assert holds_tool.reviewable_source(database, "rev") == "art"
    assert holds_tool.reviewable_payload("sess", "rev", "art") == {
        "session_id": "sess", "source_artifact_id": "art",
        "speech_plan_revision_id": "rev", "settings": {},
    }
    # With the stage's resolution, its flattened settings come along and the revision's source stays.
    staged = holds_tool.reviewable_payload(
        "sess", "rev", "art", {"settings": {"service": "x"}, "source_artifact_id": "other"})
    assert staged["settings"] == {"service": "x"} and staged["source_artifact_id"] == "art"


@pytest.mark.parametrize("settings,artifact", [
    ({}, "x/source.json"),
    ({"_source_artifact_id": "art"}, None),
    ({"_source_artifact_id": "art"}, "x/source.txt"),
])
def test_reviewable_route_refuses_a_revision_without_a_usable_source(
    tmp_path: Path, settings: dict, artifact: str | None,
) -> None:
    database = _revision_database(tmp_path / "a.sqlite3", settings, artifact)
    with pytest.raises(timing.RootRefused):
        holds_tool.reviewable_source(database, "rev")


def test_reviewable_route_summary_takes_the_median_longest_hold() -> None:
    def run(longest: float, contains: bool = False) -> dict:
        return {"longest_hold_seconds": longest,
                "holds_over_250ms": [{"contains_check": contains}] if contains else []}

    summary = holds_tool.summarize_route([run(0.4), run(0.5), run(2.0, True)])
    assert summary["longest_hold_median_seconds"] == 0.5 and summary["longest_hold_within_target"]
    assert summary["holds_containing_check"] == 1
    described = holds_tool.describe_route([
        {"seconds": 0.3, "thread": "MainThread", "where": "x", "pieces": {"plan_audio_identities": [0.2, 1]}},
        {"seconds": 0.1, "thread": "MainThread", "where": "y", "pieces": {}},
    ])
    assert described["longest_hold_seconds"] == 0.3
    assert [hold["contains_check"] for hold in described["holds_over_250ms"]] == [True]


def test_reviewable_route_refuses_a_plan_without_a_source_artifact(
    seeded_root: Path, tmp_path: Path,
) -> None:
    code = holds_tool.main([
        "--root", str(seeded_root), "--route", "reviewable", "--large-segments", "12",
        "--small-segments", "5", "--out", str(tmp_path / "holds.json"),
    ])
    assert code == 2 and not (tmp_path / "holds.json").exists()

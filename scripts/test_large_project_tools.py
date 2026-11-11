"""Small-N tests for large_project_seed.py and large_project_timing.py."""

from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
                        "--out", str(with_burst)]) == 0
    result = json.loads(with_burst.read_text(encoding="utf-8"))
    assert result["burst"]["requests"] >= 1
    assert ("small_after_load", "auth_status") in {
        (row["phase"], row["operation"]) for row in result["reads"]}


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
        "--listing-interval", "1",
    ])
    assert code == 0
    text = output.read_text(encoding="utf-8")
    result = json.loads(text)
    assert result["schema_version"] == 1 and result["mode"] == "timing"
    assert result["projects"]["large"] == {"segments": 12, "revisions": 3, "takes": result["projects"]["large"]["takes"]}
    operations = {(row["phase"], row["operation"]) for row in result["reads"]}
    for name in ("auth_status", "segments_default", "segments_compact", "segments_fields",
                 "segments_limit100", "segments_non_active", "plan_status", "plan_status_summary",
                 "revisions", "revisions_summary", "revision_non_active"):
        assert ("large", name) in operations and ("small", name) in operations
    assert all(row["statuses"] == [200] for row in result["reads"])
    (admission,) = result["admissions"]
    assert admission["admission"]["status"] == "succeeded"
    assert admission["admission"]["duration_seconds"] is not None
    assert admission["run"]["check_finished"] is True
    assert admission["run"]["check_seconds"] is not None
    assert admission["run"]["status"] in {"canceled", "cancelled"}
    assert set(admission["reads_during"]) == {"auth_status", "segments_default"}
    assert admission["sampling"] == {"auth_status_every_seconds": 2.0, "small_listing_every_seconds": 1.0}
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

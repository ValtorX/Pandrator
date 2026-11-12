"""Time every SQLite writer hold during generation admission and its opening check.

Runs an admission of each size through `Worker.run_once` in-process (no server,
no profiler), then the run's opening check, and times each `BEGIN IMMEDIATE` to
its commit or rollback. A hold over 250 ms makes any other request that audits
in the window wait out its busy timeout and spool, so those are listed with the
calling functions. The admission's commit transaction is the one that runs
`start_in_session`; the time spent in its pieces is recorded beside its hold.

    <uv env> python _bmad-output/scripts/admission_writer_holds.py --root <copied data root> --runs 3

Results hold timings, counts and function names only: no paths, titles or text.
The root must be a copy outside any install. The JSON lands beside the root in
`results/`, like the timing script's.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

SLOW_HOLD_SECONDS = 0.25
TARGET_MEDIAN_SECONDS = 0.75
COMMIT_MARKER = "start_in_session (total)"
ADMISSION_FUNCTION = "run_generation_admission"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import large_project_timing as timing  # noqa: E402


def caller_frames() -> tuple[str, bool]:
    """The application functions that opened the transaction, innermost first.

    Returns the innermost four for display, and whether the admission job is anywhere on
    the stack: a deeper call path must not stop its transactions counting as admission's.
    """
    frames = []
    for frame in traceback.extract_stack()[:-3]:
        name = frame.filename.replace("\\", "/")
        if ("pandrator" in name and "/site-packages/" not in name
                and "large_project_timing" not in name and "admission_writer_holds" not in name
                and not name.endswith("/database.py")):
            frames.append(f"{Path(name).stem}:{frame.lineno}({frame.name})")
    in_admission = any(f"({ADMISSION_FUNCTION})" in frame for frame in frames)
    return " < ".join(reversed(frames[-4:])), in_admission


class HoldRecorder:
    """Times each writer transaction on the engine and the pieces inside admission's commit."""

    def __init__(self, engine: Any):
        from sqlalchemy import event

        self.engine = engine
        self._local = threading.local()
        self._lock = threading.Lock()
        self.holds: list[dict[str, Any]] = []
        self._undo: list[Callable[[], None]] = []
        event.listen(engine, "before_cursor_execute", self._begin)
        event.listen(engine, "after_cursor_execute", self._acquired)
        event.listen(engine, "commit", self._end)
        event.listen(engine, "rollback", self._end)
        self._undo.append(lambda: event.remove(engine, "before_cursor_execute", self._begin))
        self._undo.append(lambda: event.remove(engine, "after_cursor_execute", self._acquired))
        self._undo.append(lambda: event.remove(engine, "commit", self._end))
        self._undo.append(lambda: event.remove(engine, "rollback", self._end))

    # A hold belongs to the connection that began it. Other connections on the same
    # thread commit inside it (the credentials cache reloads through its own session
    # while a job is enqueued), and must not end it.

    def _begin(self, conn, _cursor, statement, _parameters, _context, _many) -> None:
        if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
            self._local.opening = id(conn)
            self._local.where, self._local.in_admission = caller_frames()
            self._local.pieces = {}

    def _acquired(self, conn, _cursor, statement, _parameters, _context, _many) -> None:
        # The hold starts once BEGIN IMMEDIATE returns: before that, the transaction
        # is waiting for another writer, not blocking anyone.
        if getattr(self._local, "opening", None) == id(conn) and statement.strip().upper(
        ).startswith("BEGIN IMMEDIATE"):
            self._local.opening = None
            self._local.conn = id(conn)
            self._local.started = time.perf_counter()

    def _end(self, conn, *_args) -> None:
        started = getattr(self._local, "started", None)
        if started is None or id(conn) != self._local.conn:
            return
        pieces = self._local.pieces
        record = {
            "seconds": time.perf_counter() - started,
            "thread": threading.current_thread().name.split("-")[0],
            "where": self._local.where,
            "in_admission": self._local.in_admission,
            "admission_commit": self._local.in_admission and COMMIT_MARKER in pieces,
            "pieces": pieces,
        }
        self._local.started = None
        self._local.conn = None
        with self._lock:
            self.holds.append(record)

    def piece(self, label: str, seconds: float) -> None:
        pieces = getattr(self._local, "pieces", None)
        if getattr(self._local, "started", None) is not None and pieces is not None:
            entry = pieces.setdefault(label, [0.0, 0])
            entry[0] += seconds
            entry[1] += 1

    def instrument(self, owner: Any, name: str, label: str) -> None:
        """Wrap a function or method to time its calls inside a writer transaction."""
        original = owner.__dict__.get(name) if isinstance(owner, type) else getattr(owner, name)
        function = original.__func__ if isinstance(original, (staticmethod, classmethod)) else original

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                self.piece(label, time.perf_counter() - started)

        replacement: Any = wrapper
        if isinstance(original, staticmethod):
            replacement = staticmethod(wrapper)
        elif isinstance(original, classmethod):
            replacement = classmethod(wrapper)
        setattr(owner, name, replacement)
        self._undo.append(lambda: setattr(owner, name, original))

    def take(self) -> list[dict[str, Any]]:
        with self._lock:
            taken, self.holds = self.holds, []
        return taken

    def close(self) -> None:
        for undo in reversed(self._undo):
            undo()
        self._undo.clear()


def instrument_admission(recorder: HoldRecorder) -> None:
    from sqlalchemy.orm import Session

    import pandrator.web.generation_admission as admission
    import pandrator.web.generation_edit_audio as edit_audio
    import pandrator.web.generation_start_preparation as preparation
    import pandrator.web.workspace as workspace
    from pandrator.web.generation_speech_preparation import GenerationSpeechPreparation

    recorder.instrument(edit_audio, "inherit_edit_copy_audio", "inherit_edit_copy_audio")
    recorder.instrument(edit_audio, "capture_selection_guards", "capture_selection_guards")
    recorder.instrument(preparation, "snapshot_guard", "snapshot_guard (start_in_session)")
    recorder.instrument(admission, "snapshot_guard", "snapshot_guard (_same_snapshot)")
    recorder.instrument(admission, "_settings_revision_guard", "_settings_revision_guard")
    recorder.instrument(workspace, "deepcopy", "workspace.deepcopy")
    recorder.instrument(workspace, "stable_hash", "workspace.stable_hash")
    recorder.instrument(workspace, "generation_resource_keys", "generation_resource_keys")
    recorder.instrument(workspace, "interrupt_generation", "interrupt_generation")
    recorder.instrument(workspace, "_settings_source_receipt", "_settings_source_receipt")
    recorder.instrument(workspace.GenerationService, "_start_snapshot", "_start_snapshot")
    recorder.instrument(workspace.GenerationService, "_start_source", "_start_source")
    recorder.instrument(GenerationSpeechPreparation, "publish", "speech preparation publish")
    recorder.instrument(workspace.GenerationService, "start_in_session", COMMIT_MARKER)
    recorder.instrument(Session, "flush", "session.flush")
    recorder.instrument(Session, "commit", "session.commit")


def describe(holds: list[dict[str, Any]]) -> dict[str, Any]:
    commit = [item for item in holds if item["admission_commit"]]
    fallback = not commit
    if fallback:
        # Keep the longest hold the admission job opened for inspection. It is marked and
        # never counts toward the verdict: it may not be the commit transaction at all.
        commit = sorted((item for item in holds if item["in_admission"]),
                        key=lambda item: -item["seconds"])[:1]
    slow = sorted((item for item in holds if item["seconds"] > SLOW_HOLD_SECONDS),
                  key=lambda item: -item["seconds"])
    return {
        "writer_holds": len(holds),
        "held_seconds": round(sum(item["seconds"] for item in holds), 3),
        "holds_over_250ms": [
            {"seconds": round(item["seconds"], 3), "thread": item["thread"], "where": item["where"]}
            for item in slow
        ],
        "admission_commit": ({
            "hold_seconds": round(commit[0]["seconds"], 3),
            "fallback": fallback,
            "pieces": {
                label: {"seconds": round(seconds, 3), "calls": calls}
                for label, (seconds, calls) in sorted(
                    commit[0]["pieces"].items(), key=lambda item: -item[1][0])
            },
        } if commit else None),
    }


def counts_toward_verdict(entry: dict[str, Any]) -> bool:
    """A run counts only when its admission job ran to success and its commit hold is certain."""
    commit = entry["admission_commit"]
    return (bool(commit) and not commit.get("fallback")
            and entry.get("claimed", True) is not False
            and entry.get("job_status", "succeeded") == "succeeded")


def summarize(runs: list[dict[str, Any]], sizes: list[int]) -> list[dict[str, Any]]:
    summary = []
    for size in sizes:
        admissions = [item for item in runs if item["segments"] == size and item["phase"] == "admission"]
        counted = [item for item in admissions if counts_toward_verdict(item)]
        holds = [item["admission_commit"]["hold_seconds"] for item in counted]
        summary.append({
            "segments": size,
            "runs": len(admissions),
            "excluded_runs": len(admissions) - len(counted),
            "commit_hold_seconds": holds,
            "commit_hold_median_seconds": round(statistics.median(holds), 3) if holds else None,
            "commit_hold_within_target": bool(holds) and statistics.median(holds) <= TARGET_MEDIAN_SECONDS,
            "holds_over_250ms_per_run": [len(item["holds_over_250ms"]) for item in admissions],
        })
    return summary


def job_status(database: Any, job_id: str | None) -> str | None:
    from pandrator.web.models import Job

    if not job_id:
        return None
    with database.session() as session:
        job = session.get(Job, job_id)
        return job.status if job else None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True, help="Copied data root.")
    parser.add_argument("--label", help="Dataset label for the results file (default: root folder name).")
    parser.add_argument("--out", type=Path, help="Results JSON path (default: <root>/../results/).")
    parser.add_argument("--sizes", default="1,15", help="Comma list of segment counts.")
    parser.add_argument("--runs", type=int, default=1, help="Repeats of each size on the same root.")
    parser.add_argument("--large-segments", type=int, default=5000, help="Target size of the large plan.")
    parser.add_argument("--small-segments", type=int, default=189, help="Target size of the small plan.")
    parser.add_argument("--large-session", help="Session id of the large project.")
    args = parser.parse_args(argv)
    args.root = args.root.expanduser().resolve()
    if args.runs < 1:
        parser.error("--runs must be at least 1.")
    try:
        args.sizes = [int(value) for value in args.sizes.split(",") if value.strip()]
    except ValueError:
        parser.error("--sizes must be a comma list of whole numbers.")
    if not args.sizes or min(args.sizes) < 1:
        parser.error("--sizes must list at least one segment count, each at least 1.")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        timing.check_root(args.root)
        large, _small = timing.select_projects(
            args.root / "pandrator.sqlite3", large_target=args.large_segments,
            small_target=args.small_segments, large_session=args.large_session,
        )
    except timing.RootRefused as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    sizes = args.sizes
    if max(sizes) > len(large.first_segment_ids):
        # A larger size would admit only the ids there are, under a misleading label.
        print(f"Refused: --sizes goes up to {max(sizes)}, but the large plan offers "
              f"{len(large.first_segment_ids)} segments to admit.", file=sys.stderr)
        return 2
    label = args.label or args.root.name
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.out or args.root.parent / "results" / f"{label}-writer-holds-{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {
        "schema_version": 1, "label": label, "mode": "writer-holds",
        "started_at": datetime.now(UTC).isoformat(), "checkout": timing.git_revision(),
        "slow_hold_seconds": SLOW_HOLD_SECONDS, "target_median_seconds": TARGET_MEDIAN_SECONDS,
        "projects": {"large": timing.describe_project(large)}, "runs": [],
    }

    def save() -> None:
        output.write_text(json.dumps(results, indent=2), encoding="utf-8")

    app, token, worker, gpu_probe = timing.build_services(args.root, hold_seconds=0.0,
                                                           cancel_in_process=True)
    database = app.extensions["pandrator"]["database"]
    recorder = HoldRecorder(database.engine)
    instrument_admission(recorder)
    client = app.test_client()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        for size in sizes:
            for run in range(1, args.runs + 1):
                response = client.post(
                    f"/api/v1/sessions/{large.session_id}/generation-admissions",
                    json={"operation": "regenerate", "segment_ids": large.first_segment_ids[:size]},
                    headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
                )
                if response.status_code != 202:
                    print(f"Admission of {size} was refused: HTTP {response.status_code}",
                          file=sys.stderr)
                    results["error"] = f"admission of {size} answered HTTP {response.status_code}"
                    save()
                    return 1
                admission_id = (response.get_json() or {}).get("job_id")
                for phase in ("admission", "check"):
                    recorder.take()
                    started = time.perf_counter()
                    claimed = worker.run_once()
                    elapsed = time.perf_counter() - started
                    entry = {"segments": size, "run": run, "phase": phase,
                             "seconds": round(elapsed, 3), "claimed": bool(claimed),
                             **describe(recorder.take())}
                    if phase == "admission":
                        entry["job_status"] = job_status(database, admission_id)
                    results["runs"].append(entry)
                    commit = entry["admission_commit"]
                    print(f"{size:>2} segments, run {run}, {phase}: {elapsed:.2f}s, "
                          f"{entry['writer_holds']} writer holds, "
                          f"{len(entry['holds_over_250ms'])} over 250 ms"
                          + (f", commit transaction {commit['hold_seconds']}s"
                             + (" (FALLBACK, not counted)" if commit["fallback"] else "")
                             if commit else "")
                          + (f", job {entry['job_status']}" if phase == "admission" else "")
                          + ("" if claimed else ", NO JOB CLAIMED"),
                          flush=True)
                    save()
        results["summary"] = summarize(results["runs"], sizes)
        results["finished_at"] = datetime.now(UTC).isoformat()
        save()
        for item in results["summary"]:
            print(f"{item['segments']:>2} segments: commit hold median "
                  f"{item['commit_hold_median_seconds']}s over {item['runs']} runs "
                  f"({item['excluded_runs']} excluded) "
                  f"(target {TARGET_MEDIAN_SECONDS}s: "
                  f"{'met' if item['commit_hold_within_target'] else 'NOT met'})")
        print(f"Results: {output.name}")
    finally:
        recorder.close()
        gpu_probe.stop()
        database.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Time every SQLite writer hold during generation admission and its opening check.

Runs an admission of each size through `Worker.run_once` in-process (no server,
no profiler), then the run's opening check, and times each `BEGIN IMMEDIATE` to
its commit or rollback. A hold over 250 ms makes any other request that audits
in the window wait out its busy timeout and spool, so those are listed with the
calling functions. The admission's commit transaction is the one that runs
`start_in_session`; the time spent in its pieces is recorded beside its hold.

    <uv env> python _bmad-output/scripts/admission_writer_holds.py --root <copied data root> --runs 3

`--route reviewable` instead calls the workflow's reviewable generation start on the
large project's active revision (the route behind the `generate_audio` stage), with the
run itself stubbed out, with the payload, resolved snapshot and settings hash the stage
resolves (as the workflow's `generate_audio` stage does), and times that route's writer holds and whether any of them
contains the speech freeze or the identity pass.

`--mode missing-only --missing <n>` first deactivates the active takes of the large
plan's first n segments on the copy (so they are missing), then admits missing-only
generation instead of a regenerate: the commit transaction then copies every other
segment's preserved take into the run.

`--route selection` times the stage selection save (`PUT .../stages/prepare_text/selection`,
switching between the large project's two newest prepared texts) and `--route plan-store`
times storing the active revision's records as a new plan revision inside one write
transaction, the way Prepare text stores its plan (rows prepared before the writer when
the checkout can). Both report each run's longest writer hold.

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
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

SLOW_HOLD_SECONDS = 0.25
TARGET_MEDIAN_SECONDS = 0.75
COMMIT_MARKER = "start_in_session (total)"
ROUTE_TARGETS = {"selection": 0.25, "plan-store": 0.75}
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
    import pandrator.web.generation_selection as selection
    from pandrator.web.jobs import JobQueue
    from pandrator.web.workspace_settings import WorkspaceSettingsService

    recorder.instrument(selection, "collect_row_state", "collect_row_state")
    recorder.instrument(workspace.GenerationService, "_run_payload", "_run_payload")
    recorder.instrument(JobQueue, "enqueue_in_session", "enqueue_in_session")
    recorder.instrument(WorkspaceSettingsService, "resolve_in_session", "settings.resolve_in_session")
    if hasattr(workspace, "_clone_preserved_takes"):
        recorder.instrument(workspace, "_clone_preserved_takes", "_clone_preserved_takes")
    recorder.instrument(Session, "flush", "session.flush")
    recorder.instrument(Session, "commit", "session.commit")


ROUTE_CHECK_PIECES = ("freeze_speech_snapshot", "plan_audio_identities")
REVIEWABLE_SUFFIXES = (".json", ".srt")


def instrument_reviewable(recorder: HoldRecorder) -> None:
    import pandrator.web.generation_audio_identity as identity
    import pandrator.web.generation_start_preparation as preparation
    import pandrator.web.speech_plan_workspace as speech

    recorder.instrument(speech, "freeze_speech_snapshot", "freeze_speech_snapshot")
    recorder.instrument(identity, "plan_audio_identities", "plan_audio_identities")
    recorder.instrument(preparation, "snapshot_guard", "snapshot_guard")


def reviewable_source(database: Path, revision_id: str) -> str:
    """The source artifact id the active revision names, or a refusal."""
    import contextlib

    with contextlib.closing(timing._connect_readonly(database)) as connection:
        row = connection.execute(
            "SELECT settings_json FROM generation_plan_revisions WHERE id = ?", (revision_id,)
        ).fetchone()
        settings = json.loads(row[0]) if row and row[0] else {}
        artifact_id = str((settings or {}).get("_source_artifact_id") or "")
        if not artifact_id:
            raise timing.RootRefused("The active revision names no source artifact.")
        found = connection.execute(
            "SELECT relative_path FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
    if not found or Path(str(found[0] or "")).suffix.lower() not in REVIEWABLE_SUFFIXES:
        raise timing.RootRefused("The revision's source artifact is not a .json or .srt file.")
    return artifact_id


def ensure_source_file(paths: Any, database: Any, artifact_id: str) -> bool:
    """Give a copied root's JSON source a stand-in file when the copy left the sessions out.

    The route resolves the source file but never reads a JSON source while it reuses the
    active revision, so an empty list stands in. Returns whether one was written.
    """
    from pandrator.web.models import Artifact

    with database.snapshot_session() as session:
        relative = session.get(Artifact, artifact_id).relative_path
    target = paths.managed_path(relative)
    if target.is_file() or target.suffix.lower() != ".json":
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("[]", encoding="utf-8")
    return True


def reviewable_payload(session_id: str, revision_id: str, source_artifact_id: str,
                       stage: dict[str, Any] | None = None) -> dict[str, Any]:
    """The `generate_audio` stage's handler payload, with the revision's own source.

    `stage` is the stage's resolved payload (`settings`, `resolved_settings_snapshot`,
    `settings_hash`); without it the settings are empty.
    """
    stage = stage or {}
    return {"session_id": session_id, "source_artifact_id": source_artifact_id,
            "speech_plan_revision_id": revision_id, "settings": dict(stage.get("settings") or {})}


def resolve_stage_payload(database: Any, session_id: str) -> dict[str, Any]:
    """What the workflow resolves for `generate_audio` with no Run Now values."""
    from pandrator.web.jobs import JobQueue
    from pandrator.web.workflows import WorkflowService

    return WorkflowService(database, JobQueue(database)).resolve_stage(
        session_id, "generate_audio", {}, continuation=False).payload


def describe_route(holds: list[dict[str, Any]]) -> dict[str, Any]:
    """The route's holds: every one over 250 ms with its frames, and whether it held the check."""
    slow = sorted((item for item in holds if item["seconds"] > SLOW_HOLD_SECONDS),
                  key=lambda item: -item["seconds"])
    return {
        "writer_holds": len(holds),
        "longest_hold_seconds": round(max((item["seconds"] for item in holds), default=0.0), 3),
        "held_seconds": round(sum(item["seconds"] for item in holds), 3),
        "holds_over_250ms": [{
            "seconds": round(item["seconds"], 3), "thread": item["thread"], "where": item["where"],
            "contains_check": any(label in item["pieces"] for label in ROUTE_CHECK_PIECES),
            "pieces": {label: {"seconds": round(seconds, 3), "calls": calls}
                       for label, (seconds, calls) in sorted(item["pieces"].items(),
                                                             key=lambda pair: -pair[1][0])},
        } for item in slow],
    }


def summarize_route(runs: list[dict[str, Any]]) -> dict[str, Any]:
    longest = [item["longest_hold_seconds"] for item in runs]
    median = statistics.median(longest) if longest else None
    return {
        "runs": len(runs),
        "longest_hold_seconds": longest,
        "longest_hold_median_seconds": round(median, 3) if median is not None else None,
        "longest_hold_within_target": median is not None and median <= TARGET_MEDIAN_SECONDS,
        "holds_containing_check": sum(
            1 for item in runs for hold in item["holds_over_250ms"] if hold["contains_check"]),
    }


def run_reviewable(args: argparse.Namespace, large: Any, output: Path) -> int:
    from unittest import mock

    from sqlalchemy import delete, select

    from pandrator.web.accounts.execution_actor import HostActor, execution_actor_scope
    from pandrator.web.models import GenerationRun

    try:
        source_id = reviewable_source(args.root / "pandrator.sqlite3", large.active_revision_id)
    except timing.RootRefused as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    label = args.label or args.root.name
    results: dict[str, Any] = {
        "schema_version": 1, "label": label, "mode": "writer-holds-reviewable",
        "started_at": datetime.now(UTC).isoformat(), "checkout": timing.git_revision(),
        "slow_hold_seconds": SLOW_HOLD_SECONDS, "target_median_seconds": TARGET_MEDIAN_SECONDS,
        "projects": {"large": timing.describe_project(large)}, "runs": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    app, _token, _worker, gpu_probe = timing.build_services(args.root, hold_seconds=0.0,
                                                            cancel_in_process=True)
    services = app.extensions["pandrator"]
    database = services["database"]
    # A fresh handler set, like the worker's, with only the run itself stubbed.
    from pandrator.web.workflow_handlers import WorkflowHandlers

    handlers = WorkflowHandlers(database, services["paths"])
    with execution_actor_scope(HostActor("pandrator-cli")):
        results["source_stand_in"] = ensure_source_file(services["paths"], database, source_id)
    recorder = HoldRecorder(database.engine)
    instrument_reviewable(recorder)
    with execution_actor_scope(HostActor("pandrator-cli")):
        stage = resolve_stage_payload(database, large.session_id)
    payload = reviewable_payload(large.session_id, large.active_revision_id, source_id, stage)
    snapshot = stage.get("resolved_settings_snapshot")
    settings_hash = str(stage.get("settings_hash") or "") or None
    results["stage_resolved"] = True
    results["stage_source_is_revision_source"] = stage.get("source_artifact_id") == source_id
    try:
        for run in range(1, args.runs + 1):
            with database.snapshot_session() as session:
                existing = set(session.scalars(select(GenerationRun.id).where(
                    GenerationRun.session_id == large.session_id)))
            recorder.take()
            started = time.perf_counter()
            error = None
            try:
                with execution_actor_scope(HostActor("pandrator-cli")), \
                        mock.patch.object(handlers, "run_generation", return_value={}):
                    handlers._run_reviewable_generation(
                        dict(payload), lambda *_a, **_k: None, threading.Event(),
                        resolved_snapshot=deepcopy(snapshot), settings_hash=settings_hash)
            except Exception as failure:  # recorded by type only: messages may name data
                error = type(failure).__name__
            elapsed = time.perf_counter() - started
            entry = {"run": run, "seconds": round(elapsed, 3), "error": error,
                     **describe_route(recorder.take())}
            with database.immediate_session() as session:
                created = [item for item in session.scalars(select(GenerationRun.id).where(
                    GenerationRun.session_id == large.session_id)) if item not in existing]
                if created:
                    session.execute(delete(GenerationRun).where(GenerationRun.id.in_(created)))
            results["runs"].append(entry)
            print(f"route run {run}: {elapsed:.2f}s, {entry['writer_holds']} writer holds, "
                  f"longest {entry['longest_hold_seconds']}s, "
                  f"{len(entry['holds_over_250ms'])} over 250 ms"
                  + (f", FAILED ({error})" if error else ""), flush=True)
            output.write_text(json.dumps(results, indent=2), encoding="utf-8")
            if error:
                return 1
        results["summary"] = summarize_route(results["runs"])
        results["finished_at"] = datetime.now(UTC).isoformat()
        output.write_text(json.dumps(results, indent=2), encoding="utf-8")
        summary = results["summary"]
        print(f"route: longest hold median {summary['longest_hold_median_seconds']}s "
              f"(target {TARGET_MEDIAN_SECONDS}s: "
              f"{'met' if summary['longest_hold_within_target'] else 'NOT met'}), "
              f"{summary['holds_containing_check']} slow holds contain the freeze or identity pass")
        print(f"Results: {output.name}")
    finally:
        recorder.close()
        gpu_probe.stop()
        database.dispose()
    return 0


def deactivate_first_segments(database_path: Path, revision_id: str, count: int) -> int:
    """Make the first ``count`` segments of a revision missing: deactivate their active takes."""
    import sqlite3

    raw = sqlite3.connect(database_path)
    try:
        ids = [row[0] for row in raw.execute(
            "select id from generation_segments where plan_revision_id=? and removed=0 "
            "order by ordinal limit ?", (revision_id, count))]
        marks = ",".join("?" * len(ids))
        changed = raw.execute(
            f"update audio_takes set is_active=0 where is_active=1 and generation_segment_id in ({marks})",
            ids).rowcount
        raw.commit()
        return changed
    finally:
        raw.close()


def plan_records(database: Any, revision_id: str) -> list[dict[str, Any]]:
    """The active revision's segments as the records Prepare text would store."""
    from sqlalchemy import select

    from pandrator.web.models import GenerationSegment

    records = []
    with database.snapshot_session() as session:
        for segment in session.scalars(select(GenerationSegment).where(
                GenerationSegment.plan_revision_id == revision_id,
                GenerationSegment.removed.is_(False)).order_by(GenerationSegment.ordinal)):
            plan = dict(segment.speech_plan_json or {})
            records.append({
                "text": segment.text, "tts_optimized_sentence": segment.optimized_text or "",
                "speech_plan": plan, "speech_xml": plan.get("speech_xml"),
                "paragraph": "yes" if segment.paragraph_break_after else "no",
                "speaker": segment.speaker, "language": segment.language,
                "source_segment_ids": list(segment.source_segment_ids_json or []),
            })
    return records


def run_route(args: argparse.Namespace, large: Any, output: Path) -> int:
    """Time the selection save or the plan store's writer hold, ``args.runs`` times."""
    import sqlite3

    from pandrator.web.jobs import JobQueue
    from pandrator.web.workflow_handlers import WorkflowHandlers

    output.parent.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {
        "schema_version": 1, "label": args.label or args.root.name, "mode": f"{args.route}-holds",
        "started_at": datetime.now(UTC).isoformat(), "checkout": timing.git_revision(),
        "target_median_seconds": ROUTE_TARGETS[args.route],
        "projects": {"large": timing.describe_project(large)}, "runs": [],
    }

    def save() -> None:
        output.write_text(json.dumps(results, indent=2), encoding="utf-8")

    app, token, _worker, gpu_probe = timing.build_services(args.root, hold_seconds=0.0,
                                                            cancel_in_process=True)
    services = app.extensions["pandrator"]
    database = services["database"]
    recorder = HoldRecorder(database.engine)
    client = app.test_client()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if args.route == "selection":
            raw = sqlite3.connect(f"file:{args.root / 'pandrator.sqlite3'}?mode=ro", uri=True)
            prepared = [row[0] for row in raw.execute(
                "select id from artifacts where session_id=? and role='prepared_text' "
                "and state!='deleted' order by created_at", (large.session_id,))]
            raw.close()
            if len(prepared) < 2:
                print("Refused: the large project needs two prepared texts to switch between.",
                      file=sys.stderr)
                return 2
        else:
            handlers = WorkflowHandlers(database, services["paths"], jobs=JobQueue(database))
            records = plan_records(database, large.active_revision_id)
            results["records"] = len(records)
            results["records_with_markup"] = sum(1 for item in records if item.get("speech_xml"))
        for run in range(1, args.runs + 1):
            recorder.take()
            started = time.perf_counter()
            if args.route == "selection":
                current = client.get(
                    f"/api/v1/sessions/{large.session_id}/stages/prepare_text/artifacts",
                    headers=headers).get_json() or {}
                selected = current.get("selected_artifact_id")
                target = next(item for item in reversed(prepared) if item != selected)
                started = time.perf_counter()
                response = client.put(
                    f"/api/v1/sessions/{large.session_id}/stages/prepare_text/selection",
                    json={"artifact_id": target},
                    headers={**headers, "If-Match": f'"{current.get("revision", 0)}"',
                             "Idempotency-Key": str(uuid.uuid4())})
                status = response.status_code
                holds = [item for item in recorder.take()
                         if "workflow_stage_selection" in item["where"]]
            else:
                prepare = getattr(handlers, "_prepare_generation_plan_rows", None)
                prepared_rows = (prepare(large.session_id, records, settings={})
                                 if prepare is not None else None)
                recorder.take()
                extra = {"prepared_rows": prepared_rows} if prepared_rows is not None else {}
                with database.immediate_session() as unit:
                    handlers._store_generation_plan(
                        large.session_id, records, settings={},
                        source_artifact_id=f"holds-{uuid.uuid4()}", db_session=unit,
                        force_new=True, **extra)
                status = 200
                holds = recorder.take()
            elapsed = time.perf_counter() - started
            longest = max((item["seconds"] for item in holds), default=0.0)
            entry = {"run": run, "status": status, "seconds": round(elapsed, 3),
                     "writer_holds": len(holds), "longest_hold_seconds": round(longest, 3)}
            results["runs"].append(entry)
            print(f"{args.route} run {run}: HTTP {status}, {elapsed:.2f}s, "
                  f"{len(holds)} writer holds, longest {longest:.3f}s", flush=True)
            save()
        longest = [item["longest_hold_seconds"] for item in results["runs"]]
        median = statistics.median(longest)
        results["summary"] = {
            "longest_hold_median_seconds": round(median, 3),
            "within_target": median <= ROUTE_TARGETS[args.route],
        }
        results["finished_at"] = datetime.now(UTC).isoformat()
        save()
        print(f"{args.route}: longest hold median {median:.3f}s "
              f"(target {ROUTE_TARGETS[args.route]}s: "
              f"{'met' if median <= ROUTE_TARGETS[args.route] else 'NOT met'})")
        print(f"Results: {output.name}")
    finally:
        recorder.close()
        gpu_probe.stop()
        database.dispose()
    return 0


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
    parser.add_argument("--route", choices=("admission", "reviewable", "selection", "plan-store"),
                        default="admission",
                        help="Which route to time (default: generation admission).")
    parser.add_argument("--mode", choices=("regenerate", "missing-only"), default="regenerate",
                        help="Admission kind (default: regenerate the first --sizes segments).")
    parser.add_argument("--missing", type=int, default=8,
                        help="With --mode missing-only: segments made missing before the admissions.")
    args = parser.parse_args(argv)
    if args.mode == "missing-only":
        if args.route != "admission":
            parser.error("--mode missing-only applies to the admission route only.")
        if args.missing < 1:
            parser.error("--missing must be at least 1.")
        args.sizes = str(args.missing)
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
    if args.route == "reviewable":
        label = args.label or args.root.name
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return run_reviewable(args, large, args.out or args.root.parent / "results"
                              / f"{label}-reviewable-holds-{stamp}.json")
    if args.route in ROUTE_TARGETS:
        label = args.label or args.root.name
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return run_route(args, large, args.out or args.root.parent / "results"
                         / f"{label}-{args.route}-holds-{stamp}.json")
    missing_only = args.mode == "missing-only"
    if missing_only:
        made = deactivate_first_segments(args.root / "pandrator.sqlite3",
                                         large.active_revision_id, args.missing)
        print(f"Made {args.missing} segments missing ({made} active takes deactivated).", flush=True)
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
        "admission": "missing-only" if missing_only else "regenerate",
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
                    json=({"missing_only": True} if missing_only else
                          {"operation": "regenerate", "segment_ids": large.first_segment_ids[:size]}),
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

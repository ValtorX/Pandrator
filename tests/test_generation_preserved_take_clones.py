"""Missing-only and stale-only runs copy their preserved takes after the commit.

The start's write transaction used to deactivate every preserved take and insert
the run's copies itself: on a 5,167-segment book with 5,159 preserved takes that
kept the writer for about 0.45 s of the commit's 1.2 s. The run is now committed
first, and ``copy_preserved_takes`` makes the copies in short write
transactions of at most ``_CLONE_CHUNK`` segments, from the start's preserved
map or, in the ``generation.run`` job, from a map derived from the run's
snapshot. These tests compare the result with the old per-take loop, kept below
as the reference, and count statements, rows and write transactions, never
time.
"""

from __future__ import annotations

import threading
import unittest
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import event, select

from pandrator.web import workspace
from pandrator.web.generation_audio_identity import take_reuse_reason
from pandrator.web.models import Artifact, AudioTake, GenerationRun, GenerationSegment
from pandrator.web.tts_providers import TtsCapabilities
from pandrator.web.workspace import RevisionConflict, copy_preserved_takes
from tests import test_generation_missing_only as missing_tests


def reference_clone(
    session, *, run_id, plan_revision_id, preserved_items, missing_only, identities,
) -> None:
    """The per-take loop ``start_in_session`` ran before the bulk copy."""
    for segment_id, take_id in preserved_items.items():
        source_take = session.get(AudioTake, take_id)
        segment = session.get(GenerationSegment, segment_id)
        artifact = (
            session.get(Artifact, source_take.artifact_id)
            if source_take and source_take.artifact_id else None
        )
        if missing_only:
            if (source_take is None or segment is None or artifact is None
                    or segment.plan_revision_id != plan_revision_id
                    or segment.status != "completed"
                    or source_take.generation_segment_id != segment_id
                    or source_take.status != "completed" or not source_take.is_active
                    or artifact.state == "deleted"):
                raise RevisionConflict("A preserved take changed.")
        elif (source_take is None or segment is None or artifact is None
                or segment.plan_revision_id != plan_revision_id
                or segment.status != "completed"
                or source_take.generation_segment_id != segment_id
                or source_take.status != "completed" or not source_take.is_active
                or take_reuse_reason(segment, source_take, artifact, identities[segment_id])
                != "reusable"):
            raise RevisionConflict("A reusable take changed.")
        source_take.is_active = False
        session.add(AudioTake(
            generation_segment_id=segment_id,
            generation_run_id=run_id,
            artifact_id=source_take.artifact_id,
            parent_take_id=source_take.id,
            kind=source_take.kind,
            status="completed",
            settings_hash=source_take.settings_hash,
            duration_ms=source_take.duration_ms,
            is_active=True,
        ))
    session.flush()


class Writers:
    """Statements by write transaction (``BEGIN IMMEDIATE`` to its commit)."""

    def __init__(self) -> None:
        self.items: list[str] = []
        self.transactions: list[list[tuple[str, int]]] = []
        self._open: dict[int, list[tuple[str, int]]] = {}

    def statement(self, connection, statement, parameters, many) -> None:
        self.items.append(statement)
        key = id(connection.connection.dbapi_connection)
        if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
            self._open[key] = []
            self.transactions.append(self._open[key])
        elif key in self._open:
            self._open[key].append((statement, len(parameters) if many else 1))

    def end(self, connection) -> None:
        self._open.pop(id(connection.connection.dbapi_connection), None)

    @staticmethod
    def _is_write(statement: str, verb: str) -> bool:
        return (statement.lstrip().upper().startswith(verb)
                and "audio_takes" in statement.split("(")[0])

    def writes(self, verb: str) -> int:
        return sum(1 for text in self.items if self._is_write(text, verb))

    def per_take_reads(self) -> int:
        return sum(
            1 for text in self.items
            if text.lstrip().upper().startswith("SELECT")
            and "FROM audio_takes" in text
            and "WHERE audio_takes.id = ?" in text
        )

    def take_writes(self, transaction: list[tuple[str, int]]) -> tuple[int, int]:
        """(UPDATE statements, inserted rows) on ``audio_takes`` in one transaction."""
        return (
            sum(1 for text, _rows in transaction if self._is_write(text, "UPDATE")),
            sum(rows for text, rows in transaction if self._is_write(text, "INSERT")),
        )

    def copy_transactions(self) -> list[tuple[int, int]]:
        return [counts for counts in map(self.take_writes, self.transactions) if any(counts)]


class PreservedTakeCloneTests(unittest.TestCase):
    setUp = missing_tests.GenerationMissingOnlyTests.setUp
    _override = staticmethod(missing_tests.GenerationMissingOnlyTests._override)
    _create_case = missing_tests.GenerationMissingOnlyTests._create_case
    _resolved = missing_tests.GenerationMissingOnlyTests._resolved
    _wave_path = missing_tests.GenerationMissingOnlyTests._wave_path
    _seed_completed = missing_tests.GenerationMissingOnlyTests._seed_completed

    @contextmanager
    def _writers(self):
        recorded = Writers()

        def record(connection, _cursor, statement, parameters, _context, many):
            recorded.statement(connection, statement, parameters, many)

        def end(connection):
            recorded.end(connection)

        engine = self.database.engine
        event.listen(engine, "before_cursor_execute", record)
        event.listen(engine, "commit", end)
        event.listen(engine, "rollback", end)
        try:
            yield recorded
        finally:
            event.remove(engine, "before_cursor_execute", record)
            event.remove(engine, "commit", end)
            event.remove(engine, "rollback", end)

    def _case(self, preserved: int, missing: int) -> tuple[str, list[str]]:
        session_id, _revision, ids = self._create_case(
            texts=[f"Block number {index}." for index in range(preserved + missing)],
        )
        self._seed_completed(session_id, ids[:preserved], self._override())
        return session_id, ids

    def _takes(self, session_id: str, ids: list[str]) -> list[tuple[Any, ...]]:
        """Every take of the case, by segment position, without generated ids."""
        position = {segment_id: index for index, segment_id in enumerate(ids)}
        with self.database.session() as session:
            takes = list(session.scalars(
                select(AudioTake).where(AudioTake.generation_segment_id.in_(ids))
            ))
            by_id = {take.id: take for take in takes}
            return sorted(
                (
                    position[take.generation_segment_id],
                    take.generation_run_id is not None,
                    take.parent_take_id is not None
                    and by_id[take.parent_take_id].generation_run_id is None,
                    take.artifact_id,
                    take.kind,
                    take.status,
                    take.settings_hash,
                    take.duration_ms,
                    take.is_active,
                    take.revision,
                )
                for take in takes
            )

    def _vary_sources(self, ids: list[str]) -> None:
        """Give each preserved take its own kind, settings hash and duration."""
        position = {segment_id: index for index, segment_id in enumerate(ids)}
        with self.database.session() as session:
            for take in session.scalars(
                select(AudioTake).where(AudioTake.generation_segment_id.in_(ids))
            ):
                index = position[take.generation_segment_id]
                take.kind = "rvc" if index % 2 else "tts"
                take.settings_hash = f"settings-{index}"
                take.duration_ms = 1000 + index

    def _start(self, session_id: str, **mode: bool) -> dict[str, Any]:
        return self.generation.start(session_id, run_override=self._override(), **mode)

    def _start_without_copy(
        self, session_id: str, **mode: bool,
    ) -> tuple[str, dict[str, str], Writers]:
        """Start as a crash right after the commit would: the run, no copies.

        Returns the run, the preserved map the start held and the statements
        of the start itself.
        """
        captured: dict[str, Any] = {}

        def capture(_database, prepared, result):
            captured["items"] = {
                **dict(prepared.get("reusable_take_ids") or {}),
                **dict(prepared.get("preserved_take_ids") or {}),
            }
            captured["run_id"] = result["id"]

        with (
            patch.object(workspace, "copy_preserved_takes_after_start", capture),
            self._writers() as writers,
        ):
            result = self._start(session_id, **mode)
        self.assertEqual(result["id"], captured["run_id"])
        return result["id"], captured["items"], writers

    def _run_owned(self, run_id: str) -> list[AudioTake]:
        with self.database.session() as session:
            return list(session.scalars(
                select(AudioTake).where(AudioTake.generation_run_id == run_id)
            ))

    def _derived(self, run_id: str) -> dict[str, str]:
        with self.database.session() as session:
            context = workspace._preserved_copy_context(session, run_id, None, derive=True)
            assert context is not None
            revision_id, _missing_only, _identities, request_ids, guards = context
            return workspace._derive_preserved_items(
                session, run_id, revision_id, request_ids, guards,
            )

    def _run_job(self, run_id: str, calls: list[str] | None = None) -> dict[str, Any]:
        """Run the run's ``generation.run`` job in process with a silent provider."""
        from pydub import AudioSegment

        with self.database.session() as session:
            job_id = session.get(GenerationRun, run_id).job_id
        payload = missing_tests.GenerationMissingOnlyTests._job_payload(self, job_id)
        capabilities = TtsCapabilities(batch_synthesis=False, streaming_batch=False)

        def synthesize(text, _settings, **_kwargs):
            if calls is not None:
                calls.append(text)
            return AudioSegment.silent(duration=20)

        with (
            patch.object(self.handlers.tts_providers, "synthesize", side_effect=synthesize),
            patch.object(
                self.handlers.tts_providers, "synthesis_capabilities", return_value=capabilities,
            ),
        ):
            return self.handlers.run_generation(
                {
                    "generation_run_id": run_id,
                    "segment_ids": payload["segment_ids"],
                    "operation": payload["operation"],
                },
                lambda *_: None,
                threading.Event(),
            )

    def _active(self, ids: list[str]) -> dict[str, list[str]]:
        """Each segment's active take ids."""
        active: dict[str, list[str]] = {segment_id: [] for segment_id in ids}
        with self.database.session() as session:
            for take in session.scalars(
                select(AudioTake).where(
                    AudioTake.generation_segment_id.in_(ids), AudioTake.is_active.is_(True),
                )
            ):
                active[take.generation_segment_id].append(take.id)
        return {segment_id: sorted(values) for segment_id, values in active.items()}

    def _reference_start(self, session_id: str, **mode: bool) -> None:
        run_id, items, _writers = self._start_without_copy(session_id, **mode)
        with self.database.session() as session:
            run = session.get(GenerationRun, run_id)
            reference_clone(
                session, run_id=run_id, plan_revision_id=run.plan_revision_id,
                preserved_items=items, missing_only=bool(mode.get("missing_only")),
                identities=run.settings_snapshot_json.get("generation_audio_identities") or {},
            )

    def _compare_with_reference(self, **mode: bool) -> Writers:
        reference_id, reference_ids = self._case(preserved=5, missing=2)
        session_id, ids = self._case(preserved=5, missing=2)
        # Distinct source values, the same in both cases, so the copies must take
        # them over rather than match by accident.
        self._vary_sources(reference_ids)
        self._vary_sources(ids)
        self._reference_start(reference_id, **mode)
        with self._writers() as writers:
            self._start(session_id, **mode)
        expected = self._takes(reference_id, reference_ids)
        actual = self._takes(session_id, ids)
        # Artifacts differ per case; compare everything else, then the artifact map.
        self.assertEqual(
            [row[:3] + row[4:] for row in expected], [row[:3] + row[4:] for row in actual],
        )
        self.assertEqual(10, len(actual))  # 5 sources and 5 active copies
        with self.database.session() as session:
            for segment_id in ids[:5]:
                takes = list(session.scalars(
                    select(AudioTake).where(AudioTake.generation_segment_id == segment_id)
                ))
                source = next(take for take in takes if take.generation_run_id is None)
                copy = next(take for take in takes if take.generation_run_id is not None)
                self.assertEqual(
                    (source.artifact_id, source.id, False, True),
                    (copy.artifact_id, copy.parent_take_id, source.is_active, copy.is_active),
                )
        return writers

    # --- the commit makes no copy; the copies after it equal the old loop ---

    def test_the_start_commit_makes_no_copy(self):
        for mode in ({"missing_only": True}, {"stale_only": True}):
            with self.subTest(**mode):
                session_id, _ids = self._case(preserved=4, missing=1)
                run_id, items, writers = self._start_without_copy(session_id, **mode)
                self.assertEqual(4, len(items))
                self.assertEqual((0, 0), (writers.writes("UPDATE"), writers.writes("INSERT")))
                self.assertEqual([], self._run_owned(run_id))
                self.assertEqual(4, copy_preserved_takes(self.database, run_id, items))
                self.assertEqual(4, len(self._run_owned(run_id)))

    def test_missing_only_copies_equal_the_per_take_loop(self):
        writers = self._compare_with_reference(missing_only=True)
        # The start's commit, then one copy transaction: one UPDATE, five rows.
        self.assertEqual([(1, 5)], writers.copy_transactions())

    def test_stale_only_copies_equal_the_per_take_loop(self):
        self._compare_with_reference(stale_only=True)

    def test_copies_spanning_several_chunks_equal_the_per_take_loop(self):
        # Five preserved takes in chunks of two: three copy transactions, each
        # with one deactivation UPDATE and at most two copies.
        with patch.object(workspace, "_CLONE_CHUNK", 2):
            for mode in ({"missing_only": True}, {"stale_only": True}):
                with self.subTest(**mode):
                    writers = self._compare_with_reference(**mode)
                    self.assertEqual([(1, 2), (1, 2), (1, 1)], writers.copy_transactions())

    def test_each_copy_transaction_covers_at_most_one_chunk(self):
        session_id, _ids = self._case(preserved=7, missing=1)
        run_id, items, _writers = self._start_without_copy(session_id, missing_only=True)
        with patch.object(workspace, "_CLONE_CHUNK", 3), self._writers() as writers:
            self.assertEqual(7, copy_preserved_takes(self.database, run_id, items))
        self.assertEqual([(1, 3), (1, 3), (1, 1)], writers.copy_transactions())
        self.assertEqual(3, len(writers.transactions))

    def test_copies_below_the_chunk_size_cost_one_update_and_one_insert(self):
        counts = []
        for preserved in (3, 9):
            session_id, ids = self._case(preserved=preserved, missing=1)
            # Copies whose None columns differ still go in one batch.
            with self.database.session() as session:
                for take in session.scalars(
                    select(AudioTake).where(AudioTake.generation_segment_id.in_(ids[::2]))
                ):
                    take.settings_hash = "seeded-hash"
                    take.duration_ms = None
            with self._writers() as writers:
                self._start(session_id, missing_only=True)
            counts.append((
                writers.per_take_reads(),
                writers.writes("UPDATE"),
                writers.writes("INSERT"),
            ))
        # No per-take point read. Below ``_CLONE_CHUNK`` (500) takes, one UPDATE
        # deactivates the sources and one INSERT executemany makes the copies;
        # the same for 3 preserved takes as for 9. The start's own INSERTs are
        # into other tables.
        self.assertEqual([(0, 1, 1), (0, 1, 1)], counts)

    def test_no_copy_transaction_when_nothing_is_preserved(self):
        session_id, _ids = self._case(preserved=0, missing=3)
        with self._writers() as writers:
            self._start(session_id, missing_only=True)
        self.assertEqual((0, 0), (writers.writes("UPDATE"), writers.writes("INSERT")))
        self.assertEqual(1, len(writers.transactions))  # the start's commit only

    # --- idempotency ---

    def test_a_second_copy_issues_no_write(self):
        session_id, _ids = self._case(preserved=4, missing=1)
        run_id, items, _writers = self._start_without_copy(session_id, missing_only=True)
        self.assertEqual(4, copy_preserved_takes(self.database, run_id, items))
        for preserved_items in (items, None):
            with self._writers() as writers:
                self.assertEqual(0, copy_preserved_takes(self.database, run_id, preserved_items))
            self.assertEqual([], writers.transactions)
            self.assertEqual((0, 0), (writers.writes("UPDATE"), writers.writes("INSERT")))
        self.assertEqual(4, len(self._run_owned(run_id)))

    def test_admission_and_worker_copying_one_run_make_each_copy_once(self):
        session_id, ids = self._case(preserved=5, missing=1)
        run_id, items, _writers = self._start_without_copy(session_id, missing_only=True)
        original = self.database.immediate_session
        raced: list[int] = []
        racing: list[bool] = []

        @contextmanager
        def worker_first(*args, **kwargs):
            # Both copiers have read that the chunk needs copying; the worker's
            # write transactions commit before the admission's first begins.
            if not racing:
                racing.append(True)
                raced.append(copy_preserved_takes(self.database, run_id, None))
            with original(*args, **kwargs) as session:
                yield session

        with (
            patch.object(workspace, "_CLONE_CHUNK", 2),
            patch.object(self.database, "immediate_session", worker_first),
        ):
            admission = copy_preserved_takes(self.database, run_id, items)
        self.assertEqual([5], raced)
        self.assertEqual(0, admission)
        owned = self._run_owned(run_id)
        self.assertEqual(sorted(ids[:5]), sorted(take.generation_segment_id for take in owned))

    def test_concurrent_copiers_make_each_copy_once(self):
        session_id, ids = self._case(preserved=6, missing=1)
        run_id, items, _writers = self._start_without_copy(session_id, missing_only=True)
        barrier = threading.Barrier(2, timeout=30)
        results: list[int] = []
        errors: list[BaseException] = []

        def copier(preserved_items):
            try:
                barrier.wait()
                results.append(copy_preserved_takes(self.database, run_id, preserved_items))
            except BaseException as error:  # pragma: no cover - reported below
                errors.append(error)

        with patch.object(workspace, "_CLONE_CHUNK", 2):
            threads = [threading.Thread(target=copier, args=(value,)) for value in (items, None)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
        self.assertEqual([], [thread.name for thread in threads if thread.is_alive()])
        self.assertEqual([], errors)
        self.assertEqual(6, sum(results))
        owned = self._run_owned(run_id)
        self.assertEqual(sorted(ids[:6]), sorted(take.generation_segment_id for take in owned))

    # --- the derived map equals the prepared one ---

    def test_the_derived_map_equals_the_prepared_one(self):
        for mode in ({"missing_only": True}, {"stale_only": True}):
            with self.subTest(**mode):
                session_id, _ids = self._case(preserved=5, missing=2)
                run_id, items, _writers = self._start_without_copy(session_id, **mode)
                self.assertEqual(5, len(items))
                self.assertEqual(items, self._derived(run_id))

    def test_the_derived_map_leaves_out_a_removed_segment(self):
        session_id, ids = self._case(preserved=4, missing=1)
        with self.database.session() as session:
            session.get(GenerationSegment, ids[1]).removed = True
        run_id, items, _writers = self._start_without_copy(session_id, missing_only=True)
        self.assertNotIn(ids[1], items)
        self.assertEqual(3, len(items))
        self.assertEqual(items, self._derived(run_id))

    def test_the_derived_map_survives_a_guard_naming_an_uncopyable_active_take(self):
        # Beside each completed active take, a newer active take that is failed
        # (no artifact) or whose artifact is deleted. The start preserves the
        # completed one; the guard keeps whichever active row came last, so it
        # can name the other. Force that, then derive.
        session_id, ids = self._case(preserved=3, missing=1)
        deleted = self.services["artifacts"].register(
            self._wave_path("deleted"), kind="audio", role="generation_take",
            session_id=session_id, metadata={},
        )
        with self.database.session() as session:
            gone = session.get(Artifact, deleted.id)
            gone.state = "deleted"
            # As both deletion paths record it: deleted before the start.
            gone.metadata_json = {"deleted_at": "2000-01-01T00:00:00+00:00"}
            session.add(AudioTake(
                generation_segment_id=ids[0], status="failed", is_active=True,
            ))
            session.add(AudioTake(
                generation_segment_id=ids[1], artifact_id=deleted.id,
                status="completed", is_active=True,
            ))
        run_id, items, _writers = self._start_without_copy(session_id, missing_only=True)
        self.assertEqual(3, len(items))
        self.assertEqual(items, self._derived(run_id))
        with self.database.session() as session:
            run = session.get(GenerationRun, run_id)
            snapshot = dict(run.settings_snapshot_json)
            guards = dict(snapshot["generation_selection_guards"])
            guards[ids[0]] = {"artifact_id": None, "revision": 0}
            guards[ids[1]] = {"artifact_id": deleted.id, "revision": 0}
            snapshot["generation_selection_guards"] = guards
            run.settings_snapshot_json = snapshot
        self.assertEqual(items, self._derived(run_id))
        self.assertEqual(3, copy_preserved_takes(self.database, run_id, None))
        self.assertEqual(
            sorted(items.values()),
            sorted(take.parent_take_id for take in self._run_owned(run_id)),
        )

    def test_a_resume_never_copies_a_take_made_after_the_run(self):
        # The run's copy skipped a segment whose guarded audio was deleted; a
        # later run then regenerated it. The job derives again on every
        # attempt, so a resume must leave that later take alone rather than
        # copy and deactivate it.
        session_id, ids = self._case(preserved=3, missing=1)
        run_id, items, _writers = self._start_without_copy(session_id, missing_only=True)
        later_artifact = self.services["artifacts"].register(
            self._wave_path("later"), kind="audio", role="generation_take",
            session_id=session_id, metadata={},
        )
        with self.database.session() as session:
            source = session.get(AudioTake, items[ids[0]])
            session.get(Artifact, source.artifact_id).state = "deleted"
            source.is_active = False
            later = AudioTake(
                generation_segment_id=ids[0], artifact_id=later_artifact.id,
                status="completed", is_active=True,
            )
            session.add(later)
            session.flush()
            later_id = later.id
        self.assertNotIn(ids[0], self._derived(run_id))
        self.assertEqual(2, copy_preserved_takes(self.database, run_id, None))
        self.assertEqual(
            sorted(ids[1:3]), sorted(take.generation_segment_id for take in self._run_owned(run_id)),
        )
        self.assertEqual([later_id], self._active(ids[:1])[ids[0]])

    def test_the_derived_map_never_stands_in_for_a_take_that_lost_its_audio(self):
        # Two active completed takes: an older one, then the one the start
        # preserves. The preserved take's audio is deleted before the job
        # runs. The prepared map skips that segment; the derived one must not
        # copy the older take in its place, which is other audio.
        session_id, _revision, ids = self._create_case(
            texts=[f"Block number {index}." for index in range(4)],
        )
        older_artifact = self.services["artifacts"].register(
            self._wave_path("older"), kind="audio", role="generation_take",
            session_id=session_id, metadata={},
        )
        with self.database.session() as session:
            older = AudioTake(
                generation_segment_id=ids[0], artifact_id=older_artifact.id,
                status="completed", is_active=True, duration_ms=100,
            )
            session.add(older)
            session.flush()
            older_id = older.id
        self._seed_completed(session_id, ids[:3], self._override())
        run_id, items, _writers = self._start_without_copy(session_id, missing_only=True)
        self.assertNotEqual(older_id, items[ids[0]])
        with self.database.session() as session:
            artifact = session.get(Artifact, session.get(AudioTake, items[ids[0]]).artifact_id)
            artifact.state = "deleted"
            artifact.metadata_json = {
                **dict(artifact.metadata_json or {}),
                "deleted_at": workspace.utcnow().isoformat(),
            }
        with self.assertLogs("pandrator.web.workspace", level="WARNING") as logs:
            derived = self._derived(run_id)
        self.assertIn(f"{run_id} left 1 preserved segments uncopied", "\n".join(logs.output))
        self.assertEqual({key: items[key] for key in ids[1:3]}, derived)
        self.assertEqual(2, copy_preserved_takes(self.database, run_id, None))
        self.assertEqual(0, copy_preserved_takes(self.database, run_id, None))
        self.assertIn(older_id, self._active(ids[:1])[ids[0]])

    def test_the_derived_map_prefers_the_guarded_take_over_its_earlier_copy(self):
        # A second missing-only run preserves the first run's copies; their
        # sources share the artifact, and neither may stand in for the copy.
        session_id, ids = self._case(preserved=3, missing=2)
        first = self._start(session_id, missing_only=True)
        first_copies = {take.id for take in self._run_owned(first["id"])}
        run_id, items, _writers = self._start_without_copy(session_id, missing_only=True)
        self.assertEqual(3, len(items))
        self.assertEqual(first_copies, set(items.values()))
        self.assertEqual(items, self._derived(run_id))

    # --- a take changed between the commit and the copy ---

    def _select_other_take(self, session_id: str, segment_id: str) -> str:
        """Add a second completed take on ``segment_id`` and select it, as a user would."""
        artifact = self.services["artifacts"].register(
            self._wave_path(f"{segment_id}-other"), kind="audio", role="generation_take",
            session_id=session_id, metadata={},
        )
        with self.database.session() as session:
            other = AudioTake(
                generation_segment_id=segment_id, artifact_id=artifact.id,
                status="completed", is_active=False, duration_ms=50,
            )
            session.add(other)
            session.flush()
            other_id = other.id
            revision = session.get(GenerationSegment, segment_id).revision
        self.generation.select_take(segment_id, other_id, revision)
        return other_id

    def test_a_take_selected_after_the_commit_stays_selected(self):
        for derive in (False, True):
            with self.subTest(derive=derive):
                session_id, ids = self._case(preserved=3, missing=1)
                run_id, items, _writers = self._start_without_copy(session_id, missing_only=True)
                selected = self._select_other_take(session_id, ids[0])
                copied = copy_preserved_takes(self.database, run_id, None if derive else items)
                self.assertEqual(3, copied)
                with self.database.session() as session:
                    takes = {
                        take.id: take for take in session.scalars(
                            select(AudioTake).where(AudioTake.generation_segment_id == ids[0])
                        )
                    }
                copy = next(take for take in takes.values() if take.generation_run_id == run_id)
                source = takes[items[ids[0]]]
                self.assertEqual(
                    (source.id, source.artifact_id, False, 1),
                    (copy.parent_take_id, copy.artifact_id, copy.is_active, copy.revision),
                )
                self.assertFalse(source.is_active)
                self.assertTrue(takes[selected].is_active)
                self.assertEqual(
                    [selected], [take.id for take in takes.values() if take.is_active],
                )
                # The other preserved segments got active copies as usual.
                for segment_id in ids[1:3]:
                    with self.database.session() as session:
                        active = session.scalars(
                            select(AudioTake).where(
                                AudioTake.generation_segment_id == segment_id,
                                AudioTake.is_active.is_(True),
                            )
                        ).one()
                    self.assertEqual(run_id, active.generation_run_id)

    def test_a_segment_whose_take_failed_its_check_is_not_copied(self):
        session_id, ids = self._case(preserved=4, missing=1)
        run_id, items, _writers = self._start_without_copy(session_id, missing_only=True)
        with self.database.session() as session:
            deleted = session.get(AudioTake, items[ids[0]])
            session.get(Artifact, deleted.artifact_id).state = "deleted"
            session.get(AudioTake, items[ids[1]]).status = "failed"
            session.get(GenerationSegment, ids[2]).status = "ready"
        with self.assertLogs("pandrator.web.workspace", level="WARNING") as logs:
            self.assertEqual(1, copy_preserved_takes(self.database, run_id, items))
        self.assertIn(f"{run_id} left 3 preserved segments uncopied", "\n".join(logs.output))
        self.assertEqual([ids[3]], [take.generation_segment_id for take in self._run_owned(run_id)])
        with self.database.session() as session:
            # A skipped source keeps its activity.
            self.assertTrue(session.get(AudioTake, items[ids[0]]).is_active)

    def test_a_stale_only_take_no_longer_reusable_is_not_copied(self):
        session_id, ids = self._case(preserved=3, missing=1)
        run_id, items, _writers = self._start_without_copy(session_id, stale_only=True)
        with self.database.session() as session:
            artifact = session.get(Artifact, session.get(AudioTake, items[ids[0]]).artifact_id)
            metadata = dict(artifact.metadata_json or {})
            identity = dict(metadata[missing_tests.IDENTITY_KEY])
            metadata[missing_tests.IDENTITY_KEY] = {**identity, "settings_hash": "not-this"}
            artifact.metadata_json = metadata
        self.assertEqual(2, copy_preserved_takes(self.database, run_id, items))
        self.assertEqual(
            sorted(ids[1:3]), sorted(take.generation_segment_id for take in self._run_owned(run_id)),
        )

    def test_a_take_changed_before_the_commit_still_conflicts(self):
        session_id, ids = self._case(preserved=3, missing=1)
        prepared = self.generation.prepare_start(
            session_id, run_override=self._override(), missing_only=True,
        )
        try:
            with self.database.session() as session:
                session.get(AudioTake, prepared["preserved_take_ids"][ids[0]]).is_active = False
            with self.assertRaises(RevisionConflict), self.database.immediate_session() as session:
                self.generation.start_in_session(session, session_id, prepared=prepared)
        finally:
            self.generation.cleanup_prepared_start(prepared)

    def test_a_run_that_preserves_nothing_is_never_copied_for(self):
        session_id, ids = self._case(preserved=2, missing=1)
        result = self._start(session_id)
        with self._writers() as writers:
            self.assertEqual(0, copy_preserved_takes(self.database, result["id"], None))
        self.assertEqual([], writers.transactions)

    # --- a crash between the commit and the copy ---

    def test_the_run_completes_every_copy_a_crash_left_unmade(self):
        reference_id, reference_ids = self._case(preserved=4, missing=2)
        self._reference_start(reference_id, missing_only=True)
        session_id, ids = self._case(preserved=4, missing=2)
        run_id, items, _writers = self._start_without_copy(session_id, missing_only=True)
        # Part of the copies were made before the crash.
        first = dict(list(items.items())[:1])
        self.assertEqual(1, copy_preserved_takes(self.database, run_id, first))
        calls: list[str] = []
        with self._writers() as writers:
            finished = self._run_job(run_id, calls)
        self.assertEqual("completed", finished["status"])
        self.assertEqual(["Block number 4.", "Block number 5."], calls)
        # The job made the three remaining copies in one transaction.
        self.assertIn((1, 3), writers.copy_transactions())
        owned = self._run_owned(run_id)
        preserved = [take for take in owned if take.generation_segment_id in items]
        self.assertEqual(
            sorted(items.values()), sorted(take.parent_take_id for take in preserved),
        )
        # The preserved rows equal the reference's; generated rows differ by artifact.
        expected = [row for row in self._takes(reference_id, reference_ids) if row[0] < 4]
        actual = [row for row in self._takes(session_id, ids) if row[0] < 4]
        self.assertEqual(
            [row[:3] + row[4:] for row in expected], [row[:3] + row[4:] for row in actual],
        )

    def test_a_failed_copy_after_the_commit_leaves_the_start_to_its_job(self):
        session_id, ids = self._case(preserved=3, missing=1)
        with (
            patch.object(workspace, "copy_preserved_takes", side_effect=RuntimeError("copy")),
            self.assertLogs("pandrator.web.workspace", level="ERROR") as logs,
        ):
            result = self._start(session_id, missing_only=True)
        self.assertEqual("queued", result["status"])
        self.assertIn(result["id"], "\n".join(logs.output))
        self.assertEqual([], self._run_owned(result["id"]))
        self.assertEqual("completed", self._run_job(result["id"])["status"])
        preserved = [
            take for take in self._run_owned(result["id"]) if take.generation_segment_id in ids[:3]
        ]
        self.assertEqual(3, len(preserved))

    # --- the route and the admission copy right after their commit ---

    def test_the_start_route_copies_after_its_commit(self):
        session_id, ids = self._case(preserved=3, missing=1)
        response = self.client.post(
            f"/api/v1/sessions/{session_id}/generation-runs",
            json={"run_override": self._override(), "missing_only": True},
            headers={**self.headers, "Idempotency-Key": "preserved-copy-route-0001"},
        )
        self.assertEqual(202, response.status_code, response.get_json())
        owned = self._run_owned(response.get_json()["id"])
        self.assertEqual(sorted(ids[:3]), sorted(take.generation_segment_id for take in owned))
        self.assertTrue(all(take.is_active for take in owned))

    def test_the_admission_copies_after_its_commit(self):
        from pandrator.web.job_execution import current_job_lease

        session_id, ids = self._case(preserved=3, missing=1)
        response = self.client.post(
            f"/api/v1/sessions/{session_id}/generation-admissions",
            json={"run_override": self._override(), "missing_only": True},
            headers={**self.headers, "Idempotency-Key": "preserved-copy-admission-0001"},
        )
        self.assertEqual(202, response.status_code, response.get_json())
        job = self.services["jobs"].claim("preserved-copy-worker")
        self.assertEqual(response.get_json()["id"], job.id)
        token = current_job_lease.set({
            "job_id": job.id, "owner_id": job.lease_owner,
            "lease_generation": job.lease_generation,
        })
        try:
            marker = self.handlers.run_generation_admission(
                job.payload_json, lambda *_: None, threading.Event(),
            )
        finally:
            current_job_lease.reset(token)
        owned = self._run_owned(marker["generation_run_id"])
        self.assertEqual(sorted(ids[:3]), sorted(take.generation_segment_id for take in owned))
        self.assertTrue(all(take.is_active for take in owned))

    # --- a run that inherits the flags from its source run ---

    def test_a_regenerate_after_a_missing_or_stale_only_run_copies_nothing(self):
        for flag in ("missing_only", "stale_only"):
            for explicit_source in (True, False):
                with self.subTest(flag=flag, explicit_source=explicit_source):
                    session_id, ids = self._case(preserved=3, missing=1)
                    source = self._start(session_id, **{flag: True})
                    self.assertEqual("completed", self._run_job(source["id"])["status"])
                    targeted = self.generation.start(
                        session_id,
                        run_override=self._override(),
                        segment_ids=ids[:1],
                        operation="regenerate",
                        **({"generation_run_id": source["id"]} if explicit_source else {}),
                    )
                    with self.database.session() as session:
                        snapshot = session.get(GenerationRun, targeted["id"]).settings_snapshot_json
                    # What this guards against: the regenerate merged its source
                    # run's snapshot, flag included.
                    self.assertIs(True, snapshot.get(flag))
                    untouched = self._active(ids[1:])
                    with self._writers() as writers:
                        self.assertEqual(
                            0, copy_preserved_takes(self.database, targeted["id"], None),
                        )
                    self.assertEqual([], writers.transactions)
                    self._run_job(targeted["id"])
                    self.assertEqual(untouched, self._active(ids[1:]))
                    self.assertEqual(
                        [], [
                            take.generation_segment_id
                            for take in self._run_owned(targeted["id"])
                            if take.generation_segment_id != ids[0]
                        ],
                    )


@pytest.fixture
def case():
    from tests.test_web_generation_regeneration import GenerationRegenerationTests

    value = GenerationRegenerationTests()
    value.setUp()
    try:
        yield value
    finally:
        value.tearDown()


def test_the_derived_map_equals_the_prepared_one_on_an_edit_copy(case):
    from tests.test_generation_edit_audio import edit, execute, page

    original = case._start()
    execute(case, original)
    edited = edit(case, 0, "Edited one")
    current = page(case)
    with case.database.session() as session:
        assert session.get(GenerationSegment, edited["id"]).plan_revision_id == current[
            "plan_revision_id"
        ] != session.get(GenerationRun, original["id"]).plan_revision_id
    captured: dict[str, Any] = {}

    def capture(_database, prepared, result):
        captured["items"] = dict(prepared.get("preserved_take_ids") or {})
        captured["run_id"] = result["id"]

    with patch.object(workspace, "copy_preserved_takes_after_start", capture):
        case._start(missing_only=True)
    items = captured["items"]
    assert set(items) == {row["id"] for row in current["items"]} - {edited["id"]}
    with case.database.session() as session:
        context = workspace._preserved_copy_context(
            session, captured["run_id"], None, derive=True,
        )
        assert context is not None
        revision_id, _missing_only, _identities, request_ids, guards = context
        assert request_ids == [edited["id"]]
        derived = workspace._derive_preserved_items(
            session, captured["run_id"], revision_id, request_ids, guards,
        )
    assert derived == items
    assert copy_preserved_takes(case.database, captured["run_id"], None) == len(items)

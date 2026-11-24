from __future__ import annotations

import threading
import uuid
import wave
from collections import OrderedDict
from copy import deepcopy
from typing import get_args

import pytest
from sqlalchemy import event, select

from pandrator.web import models as m
from pandrator.web import speech_boundaries
from pandrator.web.assembly_silences import render_part_silences
from pandrator.web.generation_controls import save_generation_controls
from pandrator.web.generation_review import PROJECTABLE_SEGMENT_FIELDS
from pandrator.web.global_settings import GlobalSettingsService
from pandrator.web.projects.artifact_identity import resolve_artifact_ownership
from pandrator.web.speech_boundaries import (
    BoundaryBlock,
    _revision_blocks,
    assembly_pause,
    freeze_boundaries,
    resolve_run_boundaries,
)
from pandrator.web.workspace import (
    OUTPUT_ASSEMBLY_ALGORITHM_VERSION,
    expected_output_assembly_snapshot,
    find_matching_output_assembly,
    output_assembly_matches,
    output_assembly_settings_hash,
)
from pandrator_mcp.schemas.generation_fields import GenerationSegmentField
from pandrator_mcp.tools.generation import _segment_projection
from tests.test_performance_plans import adopt, create, edit

pytest_plugins = ("tests.test_performance_plans",)


def _add_run(case, snapshot: dict, *, sequence_number: int) -> str:
    with case["services"]["database"].session() as session:
        run = m.GenerationRun(
            session_id=case["session_id"],
            plan_revision_id=case["revision_id"],
            sequence_number=sequence_number,
            status="completed",
            settings_snapshot_json=snapshot,
        )
        session.add(run)
        session.flush()
        return run.id


def _set_segment(
    case,
    segment_id: str,
    *,
    silence_after_ms: int,
    speech_plan: dict | None = None,
    voice: str | None = None,
    text: str | None = None,
) -> None:
    with case["services"]["database"].session() as session:
        row = session.get(m.GenerationSegment, segment_id)
        row.silence_after_ms = silence_after_ms
        if speech_plan is not None:
            row.speech_plan_json = speech_plan
        if voice is not None:
            row.voice = voice
        if text is not None:
            row.text = text


def _global_audio_defaults(case, **changes) -> None:
    settings = GlobalSettingsService(case["services"]["database"], case["services"]["paths"])
    current = settings.defaults("audio")
    settings.replace(
        "defaults.audio",
        {**current["value"], **changes},
        "*" if current["revision"] == 0 else str(current["revision"]),
    )


def _read_pause(case, **options) -> dict:
    return case["services"]["generation"].list_segments(
        case["session_id"],
        fields=["assembly_silence_after_ms", "silence_after_ms"],
        take_history="selected",
        **options,
    )


def test_selected_run_takes_pause_lengths_from_current_settings(case):
    """A run keeps its boundary facts; the lengths come from the current settings."""
    services = case["services"]
    sid = case["segment_ids"][0]
    _set_segment(
        case,
        sid,
        silence_after_ms=700,
        speech_plan={"pause_kind": "sentence"},
    )
    frozen, _ = services["workspace_settings"].resolve(
        case["session_id"], sections=["tts", "audio"]
    )
    frozen["audio"]["sentence_silence_ms"] = 250
    frozen["speech_boundaries"] = {
        sid: {"boundary_after": None, "silence_after_ms": 250, "text_hash": None}
    }
    frozen_run_id = _add_run(case, frozen, sequence_number=900)

    legacy_snapshot = dict(frozen)
    legacy_snapshot.pop("speech_boundaries")
    legacy_run_id = _add_run(case, legacy_snapshot, sequence_number=901)
    _global_audio_defaults(case, sentence_silence_ms=900)

    pinned = _read_pause(
        case,
        generation_run_id=frozen_run_id,
        segment_ids=[sid],
    )["items"][0]
    legacy = _read_pause(
        case,
        generation_run_id=legacy_run_id,
        segment_ids=[sid],
    )["items"][0]
    # Both runs froze 250 (one in its boundaries, one in its audio); the block's
    # sentence pause is now 900, and that is what assembling either run gives.
    assert pinned["assembly_silence_after_ms"] == 900
    assert legacy["assembly_silence_after_ms"] == 900
    assert pinned["silence_after_ms"] == legacy["silence_after_ms"] == 700

    with services["database"].session() as session:
        assert session.get(m.GenerationSegment, sid).silence_after_ms == 700
        assert session.get(m.GenerationRun, legacy_run_id).settings_snapshot_json == legacy_snapshot
        assert session.get(m.GenerationRun, frozen_run_id).settings_snapshot_json == frozen
    sid_zero, sid_dialogue = case["segment_ids"][:2]
    _set_segment(
        case,
        sid_zero,
        silence_after_ms=700,
        speech_plan={"pause_kind": "paragraph", "silence_override_ms": 0},
    )
    text = "A line of dialogue."
    _set_segment(
        case,
        sid_dialogue,
        silence_after_ms=700,
        text=text,
        speech_plan={
            "speech_xml": f'<segment id="{sid_dialogue}"><dialogue>{text}</dialogue></segment>'
        },
    )
    _global_audio_defaults(case, sentence_silence_ms=250, paragraph_silence_ms=700)

    items = services["generation"].list_segments(
        case["session_id"], segment_ids=[sid_zero, sid_dialogue]
    )["items"]
    pauses = {item["id"]: item["assembly_silence_after_ms"] for item in items}
    assert pauses == {sid_zero: 0, sid_dialogue: 250}


def test_legacy_selected_markup_without_frozen_characters_uses_stored_pause(case):
    from pandrator.web.generation_controls import save_generation_controls

    sid = case["segment_ids"][0]
    text = "Hello there."
    markup = (
        f'<segment id="{sid}"><dialogue><speaker ref="legacy-speaker">'
        f"{text}</speaker></dialogue></segment>"
    )
    _set_segment(
        case,
        sid,
        silence_after_ms=700,
        text=text,
        speech_plan={"speech_xml": markup},
    )
    with case["services"]["database"].session() as session:
        save_generation_controls(
            session,
            case["session_id"],
            expected_revision=0,
            characters=[{"id": "legacy-speaker", "display_name": "Legacy speaker"}],
        )

    legacy_snapshot = {
        "audio": {"sentence_silence_ms": 250, "paragraph_silence_ms": 700},
        "performance_snapshot": {"annotations": {sid: {"annotation": {"_speech_xml": markup}}}},
    }
    run_id = _add_run(case, legacy_snapshot, sequence_number=902)
    item = _read_pause(
        case,
        generation_run_id=run_id,
        segment_ids=[sid],
    )["items"][0]

    assert item["assembly_silence_after_ms"] == 700
    assert item["silence_after_ms"] == 700
    with case["services"]["database"].session() as session:
        assert session.get(m.GenerationRun, run_id).settings_snapshot_json == legacy_snapshot
        assert session.get(m.GenerationSegment, sid).silence_after_ms == 700


def test_page_terminal_pause_uses_next_voice_floor_and_is_not_suppressed(case):
    sid_first, sid_second, sid_last = case["segment_ids"]
    _set_segment(case, sid_first, silence_after_ms=0, voice="Kore")
    _set_segment(case, sid_second, silence_after_ms=0, voice="Puck")
    _set_segment(
        case,
        sid_last,
        silence_after_ms=700,
        speech_plan={"silence_override_ms": 700},
    )
    _global_audio_defaults(case, voice_change_silence_ms=300)

    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = " ".join(statement.lower().split())
        if "from generation_segments" in normalized and "count(" not in normalized:
            statements.append(normalized)

    event.listen(case["services"]["database"].engine, "before_cursor_execute", capture)
    try:
        first_page = _read_pause(case, cursor=0, limit=1)["items"]
        final_page = _read_pause(case, cursor=2, limit=1)["items"]
    finally:
        event.remove(case["services"]["database"].engine, "before_cursor_execute", capture)

    assert first_page[0]["id"] == sid_first
    assert first_page[0]["assembly_silence_after_ms"] == 300
    assert final_page[0]["id"] == sid_last
    assert final_page[0]["assembly_silence_after_ms"] == 700
    assert statements and all(" limit " in sql for sql in statements), statements
    with case["services"]["database"].session() as session:
        session.get(m.SessionRecord, case["session_id"]).workflow_kind = "voiceover"
    _set_segment(
        case,
        sid_last,
        silence_after_ms=700,
        speech_plan={"pause_kind": "paragraph"},
    )

    metadata = {
        "render_parts": [
            {
                "silence_policy": "assembly-v1",
                "start_frame": 0,
                "end_frame": 1600,
                "sample_rate_hz": 16000,
                "silence_before_ms": 0,
            },
            {
                "silence_policy": "assembly-v1",
                "start_frame": 1600,
                "end_frame": 3200,
                "sample_rate_hz": 16000,
                "silence_before_ms": 100,
            },
        ]
    }
    with case["services"]["database"].session() as session:
        ownership = resolve_artifact_ownership(
            session, session_id=case["session_id"]
        )
        artifact = m.Artifact(
            workspace_id=ownership.workspace_id,
            project_id=ownership.project_id,
            session_id=case["session_id"],
            kind="audio",
            role="generation_take",
            relative_path=f"test/{uuid.uuid4().hex}.wav",
            metadata_json=metadata,
        )
        session.add(artifact)
        session.flush()
        session.add(
            m.AudioTake(
                generation_segment_id=sid_last,
                artifact_id=artifact.id,
                status="completed",
                is_active=True,
                duration_ms=100,
            )
        )

    item = case["services"]["generation"].list_segments(
        case["session_id"],
        segment_ids=[sid_last],
        fields=["assembly_silence_after_ms", "silence_after_ms", "takes"],
    )["items"][0]
    assert item["silence_after_ms"] == 700
    assert item["assembly_silence_after_ms"] == 0
    assert item["takes"][0]["assembly_silences"] == []

    with case["services"]["database"].session() as session:
        session.get(m.SessionRecord, case["session_id"]).workflow_kind = "audiobook"
        session.get(m.GenerationSegment, sid_last).node_kind = "subtitle_cue"
    subtitle_item = case["services"]["generation"].list_segments(
        case["session_id"],
        segment_ids=[sid_last],
        fields=["assembly_silence_after_ms", "silence_after_ms", "takes"],
    )["items"][0]
    assert subtitle_item["assembly_silence_after_ms"] == 0
    assert subtitle_item["takes"][0]["assembly_silences"] == []


def test_marked_take_silences_are_safe_and_legacy_parts_add_no_silence(case):
    sid = case["segment_ids"][0]
    marked_parts = [
        {
            "index": 0,
            "silence_policy": "assembly-v1",
            "start_frame": 0,
            "end_frame": 1600,
            "sample_rate_hz": 16000,
            "duration_ms": 100,
            "silence_before_ms": 0,
        },
        {
            "index": 1,
            "silence_policy": "assembly-v1",
            "start_frame": 1600,
            "end_frame": 3200,
            "sample_rate_hz": 16000,
            "duration_ms": 100,
            "silence_before_ms": 100,
        },
    ]
    legacy_parts = [
        {key: value for key, value in part.items() if key != "silence_policy"}
        for part in marked_parts
    ]
    insertions = render_part_silences(
        {"render_parts": marked_parts}, {"in_segment_voice_change_silence_ms": 100}
    )
    assert [(item.offset_frames, item.sample_rate_hz, item.duration_ms) for item in insertions] == [
        (1600, 16000, 100)
    ]
    assert (
        render_part_silences(
            {"render_parts": legacy_parts}, {"in_segment_voice_change_silence_ms": 100}
        )
        == ()
    )

    frozen, _ = case["services"]["workspace_settings"].resolve(
        case["session_id"], sections=["tts", "audio"]
    )
    frozen["audio"]["in_segment_voice_change_silence_ms"] = 100
    run_id = _add_run(case, frozen, sequence_number=900)
    _global_audio_defaults(case, in_segment_voice_change_silence_ms=250)

    for index, parts in enumerate((marked_parts, legacy_parts)):
        with case["services"]["database"].session() as session:
            ownership = resolve_artifact_ownership(
                session, session_id=case["session_id"]
            )
            artifact = m.Artifact(
                workspace_id=ownership.workspace_id,
                project_id=ownership.project_id,
                session_id=case["session_id"],
                kind="audio",
                role="generation_take",
                relative_path=f"test/{uuid.uuid4().hex}.wav",
                metadata_json={"render_parts": parts},
            )
            session.add(artifact)
            session.flush()
            take = m.AudioTake(
                generation_segment_id=sid,
                artifact_id=artifact.id,
                status="completed",
                is_active=index == 0,
                duration_ms=200,
            )
            session.add(take)

    payload = case["services"]["generation"].list_segments(
        case["session_id"],
        generation_run_id=run_id,
        segment_ids=[sid],
        fields=["takes"],
    )
    takes = payload["items"][0]["takes"]
    marked_take = next(take for take in takes if take["assembly_silences"])
    legacy_take = next(take for take in takes if not take["assembly_silences"])
    assert marked_take["assembly_silences"] == [{"at_ms": 100, "duration_ms": 100}]
    assert legacy_take["assembly_silences"] == []

    safe = _segment_projection(payload["items"][0])
    safe_marked_take = next(take for take in safe["takes"] if take["assembly_silences"])
    assert safe_marked_take["assembly_silences"] == marked_take["assembly_silences"]
    assert "render_parts" not in safe_marked_take
    assert "relative_path" not in safe_marked_take


def _mark_chapter_marker(case, segment_id: str) -> None:
    with case["services"]["database"].session() as session:
        session.get(m.GenerationSegment, segment_id).node_kind = "chapter_marker"
    for sid in case["segment_ids"]:
        _set_segment(case, sid, silence_after_ms=700, speech_plan={})


def _pauses(case, **options) -> list[int]:
    items = _read_pause(case, **options)["items"]
    return [item["assembly_silence_after_ms"] for item in items]


def test_playlist_and_run_show_chapter_pauses_at_a_marker(case):
    _mark_chapter_marker(case, case["segment_ids"][1])

    # No run chosen: the resolved settings carry the built-in defaults.
    assert _pauses(case) == [3000, 1750, 700]
    # A page that holds only the block before the marker still sees its successor.
    assert _pauses(case, cursor=0, limit=1) == [3000]
    assert _pauses(case, cursor=1, limit=1) == [1750]

    # The run-start freeze records the same pauses, and the playlist with that run shows them.
    run = case["services"]["generation"].start(case["session_id"])
    with case["services"]["database"].session() as session:
        frozen = session.get(m.GenerationRun, run["id"]).settings_snapshot_json
    assert [
        frozen["speech_boundaries"][sid]["silence_after_ms"] for sid in case["segment_ids"]
    ] == [3000, 1750, 700]
    assert _pauses(case, generation_run_id=run["id"]) == [3000, 1750, 700]


def test_settings_patch_changes_the_next_runs_chapter_pause(case):
    _mark_chapter_marker(case, case["segment_ids"][1])
    base = f"/api/v1/sessions/{case['session_id']}/settings/audio"
    rejected = case["client"].patch(
        base,
        json={"value": {"chapter_silence_ms": -1}},
        headers={**case["headers"], "If-Match": '"0"', "Idempotency-Key": "chapter-bad"},
    )
    assert rejected.status_code == 422
    assert "chapter_silence_ms" in rejected.get_json()["error"]["message"]

    patched = case["client"].patch(
        base,
        json={"value": {"chapter_silence_ms": 4000}},
        headers={**case["headers"], "If-Match": '"0"', "Idempotency-Key": "chapter-ok"},
    )
    assert patched.status_code == 200, patched.get_json()
    assert _pauses(case) == [4000, 1750, 700]

    run = case["services"]["generation"].start(case["session_id"])
    with case["services"]["database"].session() as session:
        frozen = session.get(m.GenerationRun, run["id"]).settings_snapshot_json
    assert frozen["speech_boundaries"][case["segment_ids"][0]]["silence_after_ms"] == 4000
    assert frozen["speech_boundaries"][case["segment_ids"][1]]["silence_after_ms"] == 1750


def test_run_frozen_before_chapter_settings_gets_the_current_chapter_pauses(case):
    _mark_chapter_marker(case, case["segment_ids"][1])
    old = {"audio": {"sentence_silence_ms": 250, "paragraph_silence_ms": 700}}
    run_id = _add_run(case, old, sequence_number=903)
    assert _pauses(case, generation_run_id=run_id) == [3000, 1750, 700]
    assert _pauses(case) == [3000, 1750, 700]


def test_mcp_projection_field_enum_matches():
    assert "assembly_silence_after_ms" in PROJECTABLE_SEGMENT_FIELDS
    assert PROJECTABLE_SEGMENT_FIELDS == set(get_args(GenerationSegmentField))


# Story 3 of the chapter pauses epic: a run keeps its boundary facts, and the
# pause lengths come from the current settings wherever the run is assembled.

BOOK = [
    {"text": "The opening paragraph.", "paragraph": "yes"},
    {"text": "The end of the first chapter.", "paragraph": "yes"},
    {"text": "Chapter Two", "node_kind": "chapter_marker", "paragraph": "yes"},
    {"text": "The second chapter begins.", "paragraph": "yes"},
]


def _book(case, segments=BOOK) -> dict:
    """The case with a four-block revision: paragraph, end of chapter, marker, paragraph."""
    plan = case["services"]["generation"].create_plan(
        case["session_id"], source_revision_id=None, settings={}, segments=segments
    )
    revision = plan["active_revision_id"]
    with case["services"]["database"].session() as session:
        ids = list(
            session.scalars(
                select(m.GenerationSegment.id)
                .where(m.GenerationSegment.plan_revision_id == revision)
                .order_by(m.GenerationSegment.ordinal)
            )
        )
    return {**case, "revision_id": revision, "segment_ids": ids}


def _update_audio(case, **changes) -> None:
    workspace = case["services"]["workspace_settings"]
    current = workspace.get(case["session_id"], "audio")
    workspace.update(case["session_id"], "audio", current["revision"], {**current["override"], **changes})


def _resolve(case, run_snapshot: dict, audio: dict, segments=None, run_id=None) -> dict:
    with case["services"]["database"].session() as session:
        rows = None
        if segments is not None:
            rows = [session.get(m.GenerationSegment, sid) for sid in segments]
        return resolve_run_boundaries(
            session, case["revision_id"], run_snapshot, audio, segments=rows, run_id=run_id
        )


def _complete_with_takes(case, run_id: str) -> None:
    """Give every block a completed take of 100 ms owned by the run, and complete it."""
    services = case["services"]
    with services["database"].session() as session:
        record = session.get(m.SessionRecord, case["session_id"])
        folder = services["paths"].sessions / record.storage_key
    folder.mkdir(parents=True, exist_ok=True)
    for index, sid in enumerate(case["segment_ids"]):
        path = folder / f"take-{index}.wav"
        with wave.open(str(path), "wb") as writer:
            writer.setframerate(16000)
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.writeframes(b"\x00\x00" * 1600)
        artifact = services["artifacts"].register(
            path, kind="audio", role="generation_take", session_id=case["session_id"]
        )
        with services["database"].session() as session:
            session.get(m.GenerationSegment, sid).status = "completed"
            session.add(
                m.AudioTake(
                    generation_segment_id=sid,
                    generation_run_id=run_id,
                    artifact_id=artifact.id,
                    kind="tts",
                    status="completed",
                    duration_ms=100,
                    is_active=True,
                )
            )
    with services["database"].session() as session:
        session.get(m.GenerationRun, run_id).status = "completed"


def _assemble(case, run_id: str) -> tuple[str, list[int]]:
    services = case["services"]
    queued = services["generation"].create_assembly(case["session_id"], generation_run_id=run_id)
    services["workflow_handlers"].assemble_generation_output(
        {"output_assembly_id": queued["id"]}, lambda *_args: None, threading.Event()
    )
    with services["database"].session() as session:
        assembly = session.get(m.OutputAssembly, queued["id"])
        assert assembly.status == "completed", assembly.settings_json
        takes = assembly.settings_json["takes"]
    return queued["id"], [item["silence_after_ms"] for item in takes]


def _current_assembly(case, run_id: str) -> str | None:
    """The run's assembly the export freshness check would reuse, if any."""
    resolved, _ = case["services"]["workspace_settings"].resolve(case["session_id"])
    with case["services"]["database"].session() as session:
        match, _snapshot, _hash = find_matching_output_assembly(
            session,
            session_id=case["session_id"],
            run=session.get(m.GenerationRun, run_id),
            resolved_settings_snapshot=resolved,
        )
        return match.id if match is not None else None


def _production_state(case) -> dict:
    """Takes, take reuse, speaker plans, runs and jobs, for a before/after comparison."""
    services = case["services"]
    with services["database"].session() as session:
        takes = sorted(
            (take.id, take.generation_segment_id, take.status, take.is_active, take.generation_run_id)
            for take in session.scalars(select(m.AudioTake))
        )
        plans = sorted(
            (plan.id, plan.plan_revision_id, plan.status, plan.version, repr(plan.manual_annotations_json))
            for plan in session.scalars(select(m.PerformancePlan))
        )
        runs = sorted(
            (run.id, run.status, repr(run.settings_snapshot_json))
            for run in session.scalars(select(m.GenerationRun))
        )
        jobs = sorted((job.id, job.kind) for job in session.scalars(select(m.Job)))
    reuse = [
        (item["id"], item["audio_reuse_reason"], item["has_reusable_take"])
        for item in services["generation"].list_segments(
            case["session_id"], fields=["audio_reuse_reason", "has_reusable_take"]
        )["items"]
    ]
    return {"takes": takes, "plans": plans, "runs": runs, "jobs": jobs, "reuse": reuse}


def test_pause_change_reaches_the_reassembly_of_the_same_run(case):
    book = _book(case)
    services = book["services"]
    services["workspace_settings"].update(book["session_id"], "output", 0, {"format": "wav"})
    adopt(book, edit(book, create(book)))
    run_id = services["generation"].start(book["session_id"])["id"]
    _complete_with_takes(book, run_id)

    first_id, first = _assemble(book, run_id)
    assert first == [700, 3000, 1750, 0]
    assert _current_assembly(book, run_id) == first_id
    assert _pauses(book, generation_run_id=run_id) == [700, 3000, 1750, 700]
    before = _production_state(book)
    assert len(before["takes"]) == 4 and any(plan[2] == "adopted" for plan in before["plans"])

    _update_audio(book, paragraph_silence_ms=1400, chapter_silence_ms=4000)

    # The old assembly reads as out of date; the playlist with the run already
    # shows what re-assembling it gives.
    assert _current_assembly(book, run_id) is None
    assert _pauses(book, generation_run_id=run_id) == [1400, 4000, 1750, 1400]
    second_id, second = _assemble(book, run_id)
    assert second == [1400, 4000, 1750, 0]
    assert _current_assembly(book, run_id) == second_id

    after = _production_state(book)
    assert after["takes"] == before["takes"]
    assert after["plans"] == before["plans"]
    assert after["runs"] == before["runs"]
    assert after["reuse"] == before["reuse"]
    new_jobs = sorted(kind for job in after["jobs"] if job not in before["jobs"] for kind in [job[1]])
    assert new_jobs == ["generation.assemble"], new_jobs


# Story 4 of the chapter pauses epic: scene breaks and scene headings.

SCENE_BOOK = [
    {"text": "The end of the scene.", "paragraph": "yes"},
    {"text": "Three days later", "node_kind": "heading", "paragraph": "yes"},
    {"text": "The next scene begins.", "paragraph": "yes"},
    {"text": "It goes on.", "paragraph": "yes"},
]


def _scene_book(case) -> dict:
    """Scene end, heading that opens the next scene, then ordinary blocks, all stored at 700."""
    book = _book(case, segments=SCENE_BOOK)
    with book["services"]["database"].session() as session:
        first = session.get(m.GenerationSegment, book["segment_ids"][0])
        first.speech_plan_json = {
            **(first.speech_plan_json or {}),
            "speech_xml": f'<segment id="{first.id}" boundary_after="scene">{first.text}</segment>',
        }
        for sid in book["segment_ids"]:
            session.get(m.GenerationSegment, sid).silence_after_ms = 700
    return book


def test_playlist_and_run_show_scene_and_heading_pauses(case):
    book = _scene_book(case)
    # No run chosen: the built-in defaults, 2000 after the scene end and 1000 after the heading.
    assert _pauses(book) == [2000, 1000, 700, 700]
    # A page holding only the heading still shows its pause.
    assert _pauses(book, cursor=1, limit=1) == [1000]

    run = book["services"]["generation"].start(book["session_id"])
    with book["services"]["database"].session() as session:
        frozen = session.get(m.GenerationRun, run["id"]).settings_snapshot_json
    assert [
        frozen["speech_boundaries"][sid]["silence_after_ms"] for sid in book["segment_ids"]
    ] == [2000, 1000, 700, 700]
    assert frozen["speech_boundaries"][book["segment_ids"][0]]["boundary_after"] == "scene"
    assert _pauses(book, generation_run_id=run["id"]) == [2000, 1000, 700, 700]


def test_settings_patch_changes_the_next_runs_scene_pauses(case):
    book = _scene_book(case)
    base = f"/api/v1/sessions/{book['session_id']}/settings/audio"
    rejected = book["client"].patch(
        base,
        json={"value": {"scene_silence_ms": -1}},
        headers={**book["headers"], "If-Match": '"0"', "Idempotency-Key": "scene-bad"},
    )
    assert rejected.status_code == 422
    assert "scene_silence_ms" in rejected.get_json()["error"]["message"]

    patched = book["client"].patch(
        base,
        json={"value": {"scene_silence_ms": 2600, "scene_heading_silence_ms": 1200}},
        headers={**book["headers"], "If-Match": '"0"', "Idempotency-Key": "scene-ok"},
    )
    assert patched.status_code == 200, patched.get_json()
    run = book["services"]["generation"].start(book["session_id"])
    with book["services"]["database"].session() as session:
        frozen = session.get(m.GenerationRun, run["id"]).settings_snapshot_json
    assert [
        frozen["speech_boundaries"][sid]["silence_after_ms"] for sid in book["segment_ids"]
    ] == [2600, 1200, 700, 700]


def test_run_frozen_before_scene_settings_gets_the_current_scene_pauses(case):
    book = _scene_book(case)
    old = {"audio": {"sentence_silence_ms": 250, "paragraph_silence_ms": 700}}
    run_id = _add_run(book, old, sequence_number=904)
    # A legacy run froze no boundaries; each block's own markup and kind give the pause.
    assert _pauses(book, generation_run_id=run_id) == [2000, 1000, 700, 700]


def test_scene_pause_change_reaches_the_reassembly_of_the_same_run(case):
    book = _scene_book(case)
    services = book["services"]
    services["workspace_settings"].update(book["session_id"], "output", 0, {"format": "wav"})
    run_id = services["generation"].start(book["session_id"])["id"]
    _complete_with_takes(book, run_id)

    first_id, first = _assemble(book, run_id)
    assert first == [2000, 1000, 700, 0]
    assert _current_assembly(book, run_id) == first_id
    before = _production_state(book)
    assert len(before["takes"]) == 4

    _update_audio(book, scene_silence_ms=2600)
    assert _current_assembly(book, run_id) is None
    assert _pauses(book, generation_run_id=run_id) == [2600, 1000, 700, 700]
    second_id, second = _assemble(book, run_id)
    assert second == [2600, 1000, 700, 0]
    assert _current_assembly(book, run_id) == second_id

    _update_audio(book, scene_heading_silence_ms=1500)
    assert _current_assembly(book, run_id) is None
    assert _assemble(book, run_id)[1] == [2600, 1500, 700, 0]

    after = _production_state(book)
    assert after["takes"] == before["takes"]
    assert after["plans"] == before["plans"]
    assert after["runs"] == before["runs"]
    assert after["reuse"] == before["reuse"]
    new_jobs = sorted(job[1] for job in after["jobs"] if job not in before["jobs"])
    assert new_jobs == ["generation.assemble"] * 2, new_jobs


def test_unchanged_settings_resolve_to_the_pauses_the_run_froze(case):
    book = _book(case)
    with book["services"]["database"].session() as session:
        rows = [session.get(m.GenerationSegment, sid) for sid in book["segment_ids"]]
        rows[0].voice = "one"
        rows[1].voice = "two"
        rows[3].speech_plan_json = {**rows[3].speech_plan_json, "silence_override_ms": 4321}
        text = rows[1].text
        rows[1].speech_plan_json = {
            **rows[1].speech_plan_json,
            "speech_xml": f'<segment id="{rows[1].id}" boundary_after="chapter">{text}</segment>',
        }
    resolved, _ = book["services"]["workspace_settings"].resolve(
        book["session_id"], sections=["tts", "audio"]
    )
    for gap in (0, 1500):
        snapshot = {
            "audio": {**resolved["audio"], "voice_change_silence_ms": gap},
            "tts": {"performance_enabled": False},
        }
        with book["services"]["database"].session() as session:
            freeze_boundaries(session, book["revision_id"], snapshot)
        frozen = snapshot["speech_boundaries"]
        again = _resolve(book, snapshot, snapshot["audio"])
        assert again == frozen
        assert [frozen[sid]["silence_after_ms"] for sid in book["segment_ids"]] == [
            max(700, gap), 3000, 1750, 4321
        ]
        # The freeze records the end voices only when it resolves them.
        assert all(("voices" in entry) is bool(gap) for entry in frozen.values())


def test_voice_change_minimum_raised_after_the_run_started(case):
    book = _book(case, segments=[{"text": f"Block {n}.", "paragraph": "yes"} for n in range(3)])
    with book["services"]["database"].session() as session:
        for sid, voice in zip(book["segment_ids"], ["one", "two", "two"], strict=True):
            session.get(m.GenerationSegment, sid).voice = voice
    resolved, _ = book["services"]["workspace_settings"].resolve(
        book["session_id"], sections=["tts", "audio"]
    )
    snapshot = {
        "audio": {**resolved["audio"], "voice_change_silence_ms": 0},
        "tts": {"performance_enabled": False},
    }
    with book["services"]["database"].session() as session:
        freeze_boundaries(session, book["revision_id"], snapshot)
    assert not any("voices" in entry for entry in snapshot["speech_boundaries"].values())
    run_id = _add_run(book, snapshot, sequence_number=910)
    assert _pauses(book, generation_run_id=run_id) == [700, 700, 700]

    _update_audio(book, voice_change_silence_ms=1500)
    assert _pauses(book, generation_run_id=run_id) == [1500, 700, 700]
    current, _ = book["services"]["workspace_settings"].resolve(book["session_id"], sections=["audio"])
    resolved_now = _resolve(book, snapshot, current["audio"])
    assert [resolved_now[sid]["silence_after_ms"] for sid in book["segment_ids"]] == [1500, 700, 700]


def test_frozen_voices_need_no_cast_rendering(case, monkeypatch):
    book = _book(case, segments=[{"text": f"Block {n}.", "paragraph": "yes"} for n in range(3)])
    with book["services"]["database"].session() as session:
        for sid, voice in zip(book["segment_ids"], ["one", "two", "two"], strict=True):
            session.get(m.GenerationSegment, sid).voice = voice
    snapshot = {
        "audio": {"paragraph_silence_ms": 700, "voice_change_silence_ms": 900},
        "tts": {"performance_enabled": False},
    }
    with book["services"]["database"].session() as session:
        freeze_boundaries(session, book["revision_id"], snapshot)

    def no_rendering(*_args, **_kwargs):
        pytest.fail("frozen voices should be reused, not rendered again")

    monkeypatch.setattr("pandrator.web.speech_boundaries._block_voices", no_rendering)
    resolved = _resolve(book, snapshot, {"paragraph_silence_ms": 1400, "voice_change_silence_ms": 1500})
    assert [resolved[sid]["silence_after_ms"] for sid in book["segment_ids"]] == [1500, 1400, 1400]


def test_manual_pause_survives_any_setting_change(case):
    book = _book(case)
    with book["services"]["database"].session() as session:
        for sid in book["segment_ids"][:2]:
            row = session.get(m.GenerationSegment, sid)
            row.speech_plan_json = {**row.speech_plan_json, "silence_override_ms": 400}
            row.voice = "one" if sid == book["segment_ids"][0] else "two"
    snapshot = {"audio": {"paragraph_silence_ms": 700}, "tts": {"performance_enabled": False}}
    with book["services"]["database"].session() as session:
        freeze_boundaries(session, book["revision_id"], snapshot)
    run_id = _add_run(book, snapshot, sequence_number=911)
    _update_audio(
        book, paragraph_silence_ms=1400, chapter_silence_ms=5000, voice_change_silence_ms=2000
    )
    assert _pauses(book, generation_run_id=run_id)[:2] == [400, 400]


def test_limited_run_gets_chapter_and_current_pauses_outside_it(case):
    book = _book(
        case,
        segments=[
            {"text": "Opening.", "paragraph": "yes"},
            {"text": "Middle.", "paragraph": "yes"},
            {"text": "End of chapter.", "paragraph": "yes"},
            {"text": "Chapter Two", "node_kind": "chapter_marker", "paragraph": "yes"},
            {"text": "It began.", "paragraph": "yes"},
            {"text": "It went on.", "paragraph": "yes"},
        ],
    )
    snapshot = {"audio": {"paragraph_silence_ms": 700}, "tts": {"performance_enabled": False}}
    with book["services"]["database"].session() as session:
        limited = [session.get(m.GenerationSegment, sid) for sid in book["segment_ids"][:2]]
        freeze_boundaries(session, book["revision_id"], snapshot, segments=limited)
    assert set(snapshot["speech_boundaries"]) == set(book["segment_ids"][:2])
    run_id = _add_run(book, snapshot, sequence_number=912)
    assert _pauses(book, generation_run_id=run_id) == [700, 700, 3000, 1750, 700, 700]
    _update_audio(book, paragraph_silence_ms=900)
    assert _pauses(book, generation_run_id=run_id) == [900, 900, 3000, 1750, 900, 900]
    # A playlist page that holds only the block before the marker still sees it.
    assert _pauses(book, generation_run_id=run_id, cursor=2, limit=1) == [3000]


def test_unresolvable_markup_outside_the_run_is_read_markup_free(case):
    book = _book(case, segments=[{"text": f"Block {n}.", "paragraph": "yes"} for n in range(3)])
    sid_in, sid_out, _last = book["segment_ids"]
    snapshot = {
        "audio": {"paragraph_silence_ms": 700},
        "tts": {"performance_enabled": False},
        "performance_snapshot": {"character_dictionary": []},
    }
    with book["services"]["database"].session() as session:
        freeze_boundaries(
            session, book["revision_id"], snapshot,
            segments=[session.get(m.GenerationSegment, sid_in)],
        )
        row = session.get(m.GenerationSegment, sid_out)
        row.speech_plan_json = {
            **row.speech_plan_json,
            "speech_xml": (
                f'<segment id="{sid_out}" boundary_after="continuation"><dialogue>'
                f'<speaker ref="removed-speaker">{row.text}</speaker></dialogue></segment>'
            ),
        }
    resolved = _resolve(book, snapshot, {"paragraph_silence_ms": 1400})
    # Markup-free: no boundary, its paragraph pause_kind applies.
    assert resolved[sid_out] == {
        "text_hash": None, "boundary_after": None, "silence_after_ms": 1400
    }
    run_id = _add_run(book, snapshot, sequence_number=913)
    _update_audio(book, paragraph_silence_ms=1400)
    assert _pauses(book, generation_run_id=run_id) == [1400, 1400, 1400]


def test_text_changed_after_the_run_started_still_refuses_assembly(case):
    book = _book(case, segments=[{"text": f"Block {n}.", "paragraph": "yes"} for n in range(2)])
    sid = book["segment_ids"][0]
    with book["services"]["database"].session() as session:
        row = session.get(m.GenerationSegment, sid)
        row.speech_plan_json = {
            **row.speech_plan_json,
            "speech_xml": f'<segment id="{sid}" boundary_after="paragraph">{row.text}</segment>',
        }
    snapshot = {"audio": {"paragraph_silence_ms": 700}, "tts": {"performance_enabled": False}}
    with book["services"]["database"].session() as session:
        freeze_boundaries(session, book["revision_id"], snapshot)
    with book["services"]["database"].session() as session:
        session.get(m.GenerationSegment, sid).text = "Changed after the run started."
    resolved = _resolve(book, snapshot, {"paragraph_silence_ms": 1400, "voice_change_silence_ms": 900})
    assert resolved[sid]["text_hash"] == snapshot["speech_boundaries"][sid]["text_hash"]
    with book["services"]["database"].session() as session:
        row = session.get(m.GenerationSegment, sid)
        with pytest.raises(ValueError, match="Speech text changed"):
            assembly_pause(row, {"speech_boundaries": resolved})


def test_resolution_reads_only_the_needed_columns_and_the_successor_kinds(case):
    """Two statements: one row query over the columns a pause needs (no whole entity),
    and story 2's bounded successor query; none per row."""
    book = _book(case)
    snapshot = {"audio": {"paragraph_silence_ms": 700}, "tts": {"performance_enabled": False}}
    with book["services"]["database"].session() as session:
        freeze_boundaries(session, book["revision_id"], snapshot)
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = " ".join(statement.lower().split())
        if "from generation_segments" in normalized:
            statements.append(normalized)

    engine = book["services"]["database"].engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        resolved = _resolve(book, snapshot, {"paragraph_silence_ms": 900, "chapter_silence_ms": 3000})
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert [resolved[sid]["silence_after_ms"] for sid in book["segment_ids"]] == [900, 3000, 900, 900]
    assert len(statements) == 2, statements
    assert "json_extract" in statements[0] and "optimization_status" not in statements[0]


def test_non_audiobook_sessions_keep_the_frozen_boundaries(case):
    frozen = {case["segment_ids"][0]: {"text_hash": None, "boundary_after": None, "silence_after_ms": 5}}
    with case["services"]["database"].session() as session:
        session.get(m.SessionRecord, case["session_id"]).workflow_kind = "voiceover"
    assert _resolve(case, {"speech_boundaries": frozen}, {"paragraph_silence_ms": 1400}) == frozen


def _stored_assembly(boundaries: dict) -> m.OutputAssembly:
    resolved = {"audio": {}, "output": {}, "assembly_algorithm": OUTPUT_ASSEMBLY_ALGORITHM_VERSION}
    return m.OutputAssembly(
        status="completed",
        artifact_id="artifact",
        settings_json={
            "plan_revision_id": "revision",
            "resolved": {**resolved, "speech_boundaries": boundaries},
        },
        settings_hash=output_assembly_settings_hash(resolved),
    )


def test_freshness_compares_boundary_facts_and_ignores_recorded_voices():
    old = {"a": {"text_hash": "h", "boundary_after": "paragraph", "silence_after_ms": 700}}
    expected_snapshot = {"audio": {}, "output": {}}
    expected_hash = output_assembly_settings_hash(expected_snapshot)

    def matches(stored: dict, expected: dict) -> bool:
        return output_assembly_matches(
            _stored_assembly(stored),
            expected_snapshot={**expected_snapshot, "speech_boundaries": expected},
            expected_settings_hash=expected_hash,
            expected_plan_revision_id="revision",
        )

    with_voices = {"a": {**old["a"], "voices": ["v1", "v2"]}}
    assert matches(old, with_voices)  # stored before voices were recorded
    assert matches(with_voices, old)
    for key, value in (("silence_after_ms", 1400), ("boundary_after", "chapter"), ("text_hash", "x")):
        assert not matches(old, {"a": {**old["a"], key: value}})
    assert not matches(old, {**with_voices, "b": old["a"]})
    # Entries are dicts wherever the app writes them; a stored entry of another shape
    # (a hand-edited or damaged settings_json) matches only an equal expected entry.
    assert matches({"a": 700}, {"a": 700})
    assert not matches({"a": 700}, {"a": 1400})
    assert not matches({"a": 700}, old)


CAST_CHARACTERS = [
    {"id": "c-one", "display_name": "One"},
    {"id": "c-two", "display_name": "Two"},
]
CAST_VOICES = {"characters": {"c-one": {"voice": "one"}, "c-two": {"voice": "two"}}}


def _cast_book(case) -> dict:
    """Casting on and an adopted speaker plan, no run: blocks spoken by c-one, c-two, c-two."""
    book = _book(case, segments=[{"text": f"Line {n}.", "paragraph": "yes"} for n in range(3)])
    services = book["services"]
    with services["database"].session() as session:
        save_generation_controls(
            session, book["session_id"], expected_revision=0,
            characters=CAST_CHARACTERS, cast=CAST_VOICES,
        )
        for sid, speaker in zip(book["segment_ids"], ["c-one", "c-two", "c-two"], strict=True):
            row = session.get(m.GenerationSegment, sid)
            row.speech_plan_json = {
                **row.speech_plan_json,
                "speech_xml": (
                    f'<segment id="{sid}"><dialogue><speaker ref="{speaker}">{row.text}'
                    "</speaker></dialogue></segment>"
                ),
            }
    tts = services["workspace_settings"].get(book["session_id"], "tts")
    services["workspace_settings"].update(
        book["session_id"], "tts", tts["revision"], {**tts["override"], "casting_enabled": True}
    )
    adopt(book, edit(book, create(book)))
    return book


def _cast_run(case) -> tuple[dict, str]:
    """A real run on ``_cast_book``, frozen with the voice-change minimum at 0 (so no
    voices recorded)."""
    book = _cast_book(case)
    services = book["services"]
    run_id = services["generation"].start(book["session_id"])["id"]
    with services["database"].session() as session:
        snapshot = session.get(m.GenerationRun, run_id).settings_snapshot_json
    assert snapshot["generation_control_snapshot"] and snapshot["performance_snapshot"]
    assert not any("voices" in entry for entry in snapshot["speech_boundaries"].values())
    return book, run_id


def _expected_pauses(book, run_id: str) -> list[int]:
    resolved, _ = book["services"]["workspace_settings"].resolve(book["session_id"])
    with book["services"]["database"].session() as session:
        expected = expected_output_assembly_snapshot(
            session, session.get(m.GenerationRun, run_id), resolved
        )
    return [expected["speech_boundaries"][sid]["silence_after_ms"] for sid in book["segment_ids"]]


def test_raised_minimum_applies_at_a_cast_speaker_change_of_a_frozen_run(case):
    book, run_id = _cast_run(case)
    # Each block ends a dialogue turn: the sentence pause.
    assert _pauses(book, generation_run_id=run_id) == [250, 250, 250]
    _update_audio(book, voice_change_silence_ms=1500)
    # The voices come from the run's frozen cast: c-one to c-two changes, c-two to c-two does not.
    assert _pauses(book, generation_run_id=run_id) == [1500, 250, 250]
    assert _expected_pauses(book, run_id) == [1500, 250, 250]


def test_no_run_playlist_and_assembly_freeze_the_adopted_plan_alike(case, monkeypatch):
    """The playlist and a run-less assembly share ``freeze_current_boundaries``: with
    casting on, both freeze the adopted speaker plan and give the same pauses."""
    from pandrator.web import performance_plans

    book = _cast_book(case)
    _update_audio(book, voice_change_silence_ms=1500)
    calls: list[str] = []
    real_freeze = performance_plans.freeze_performance_snapshot

    def counted(session, revision_id, snapshot):
        calls.append(revision_id)
        return real_freeze(session, revision_id, snapshot)

    monkeypatch.setattr(performance_plans, "freeze_performance_snapshot", counted)

    def assembled() -> list[int]:
        queued = book["services"]["generation"].create_assembly(book["session_id"])
        with book["services"]["database"].session() as session:
            record = session.get(m.OutputAssembly, queued["id"])
            boundaries = record.settings_json["resolved"]["speech_boundaries"]
        return [boundaries[sid]["silence_after_ms"] for sid in book["segment_ids"]]

    # With no run, no cast is frozen, so markup speakers render as plain narration and
    # the voice-change minimum does not apply (baseline 5.1, R3; story 3 left it).
    assert _pauses(book) == [250, 250, 250]
    assert len(calls) == 1
    assert assembled() == [250, 250, 250]
    assert len(calls) == 2
    tts = book["services"]["workspace_settings"].get(book["session_id"], "tts")
    book["services"]["workspace_settings"].update(
        book["session_id"], "tts", tts["revision"],
        {**tts["override"], "casting_enabled": False, "performance_enabled": False},
    )
    assert _pauses(book) == [250, 250, 250]
    assert assembled() == [250, 250, 250]
    assert len(calls) == 2


def test_a_block_whose_voices_cannot_be_rendered_gets_no_minimum(case):
    book, run_id = _cast_run(case)
    with book["services"]["database"].session() as session:
        run = session.get(m.GenerationRun, run_id)
        snapshot = deepcopy(run.settings_snapshot_json)
        # The frozen cast no longer holds the second block, so its voices cannot be rendered.
        del snapshot["generation_control_snapshot"]["segments"][book["segment_ids"][1]]
        run.settings_snapshot_json = snapshot
    _update_audio(book, voice_change_silence_ms=1500)
    assert _pauses(book, generation_run_id=run_id) == [250, 250, 250]
    assert _expected_pauses(book, run_id) == [250, 250, 250]


def _stale_markup(case, sid: str) -> None:
    """Markup whose text no longer matches the block's: not a speaker error."""
    with case["services"]["database"].session() as session:
        row = session.get(m.GenerationSegment, sid)
        row.speech_plan_json = {
            **row.speech_plan_json,
            "speech_xml": f'<segment id="{sid}" boundary_after="continuation">Other words.</segment>',
        }


def test_markup_errors_outside_a_limited_run_leave_the_block_markup_free(case):
    book = _book(case, segments=[{"text": f"Block {n}.", "paragraph": "yes"} for n in range(3)])
    sid_in, sid_stale, _last = book["segment_ids"]
    snapshot = {"audio": {"paragraph_silence_ms": 700}, "tts": {"performance_enabled": False}}
    with book["services"]["database"].session() as session:
        freeze_boundaries(
            session, book["revision_id"], snapshot,
            segments=[session.get(m.GenerationSegment, sid_in)],
        )
    _stale_markup(book, sid_stale)
    resolved = _resolve(book, snapshot, {"paragraph_silence_ms": 1400})
    assert resolved[sid_stale] == {"text_hash": None, "boundary_after": None, "silence_after_ms": 1400}
    run_id = _add_run(book, snapshot, sequence_number=914)
    _update_audio(book, paragraph_silence_ms=1400)
    assert _pauses(book, generation_run_id=run_id) == [1400, 1400, 1400]


def test_a_legacy_run_still_refuses_markup_that_no_longer_fits_its_block(case):
    book = _book(case, segments=[{"text": f"Block {n}.", "paragraph": "yes"} for n in range(3)])
    _stale_markup(book, book["segment_ids"][1])
    legacy = {"audio": {"paragraph_silence_ms": 700}, "tts": {"performance_enabled": False}}
    with pytest.raises(ValueError) as refused:
        _resolve(book, legacy, {"paragraph_silence_ms": 1400})
    assert "speaker" not in str(refused.value).lower()
    run_id = _add_run(book, legacy, sequence_number=915)
    with pytest.raises(ValueError):
        book["services"]["generation"].create_assembly(book["session_id"], generation_run_id=run_id)


def test_column_read_and_row_read_give_the_same_blocks(case):
    book = _book(case)
    plans = [
        {"pause_kind": "clause", "silence_override_ms": 0},
        {},
        {"pause_kind": "sentence", "silence_override_ms": None},
        {"pause_kind": "paragraph", "silence_override_ms": 4321},
    ]
    with book["services"]["database"].session() as session:
        rows = [session.get(m.GenerationSegment, sid) for sid in book["segment_ids"]]
        for row, plan in zip(rows, plans, strict=True):
            row.speech_plan_json = plan
        rows[0].speech_plan_json = {
            **plans[0],
            "speech_xml": f'<segment id="{rows[0].id}" boundary_after="paragraph">{rows[0].text}</segment>',
        }
        rows[1].optimized_text = "Spoken differently."
        rows[1].voice, rows[1].language = "Puck", "en"
        rows[2].silence_after_ms = 123
        session.flush()
        from_rows = [BoundaryBlock.from_row(row) for row in rows]
        from_columns = _revision_blocks(session, book["revision_id"])
    assert from_columns == from_rows
    assert [block.override for block in from_columns] == [0, None, None, 4321]
    assert [block.pause_kind for block in from_columns] == ["clause", None, "sentence", "paragraph"]
    assert from_columns[0].speech_xml and from_columns[1].speech_xml is None
    assert from_columns[1].text == "Spoken differently."


def test_assembling_a_legacy_run_gives_the_current_pause_lengths(case):
    book = _book(case)
    services = book["services"]
    services["workspace_settings"].update(book["session_id"], "output", 0, {"format": "wav"})
    legacy = {"audio": {"sentence_silence_ms": 250, "paragraph_silence_ms": 700}}
    run_id = _add_run(book, legacy, sequence_number=916)
    _complete_with_takes(book, run_id)
    _update_audio(book, paragraph_silence_ms=1400, chapter_silence_ms=4000)
    _assembly_id, manifest = _assemble(book, run_id)
    assert manifest == [1400, 4000, 1750, 0]


def _voiced_book(case) -> tuple[dict, dict]:
    """Three blocks voiced one, two, two, frozen with the minimum at 0 (no voices recorded)."""
    book = _book(case, segments=[{"text": f"Block {n}.", "paragraph": "yes"} for n in range(3)])
    with book["services"]["database"].session() as session:
        for sid, voice in zip(book["segment_ids"], ["one", "two", "two"], strict=True):
            session.get(m.GenerationSegment, sid).voice = voice
    snapshot = {
        "audio": {"paragraph_silence_ms": 700, "voice_change_silence_ms": 0},
        "tts": {"performance_enabled": False},
    }
    with book["services"]["database"].session() as session:
        freeze_boundaries(session, book["revision_id"], snapshot)
    assert not any("voices" in entry for entry in snapshot["speech_boundaries"].values())
    return book, snapshot


MINIMUM_ON = {"paragraph_silence_ms": 700, "voice_change_silence_ms": 1500}


def _count_renders(monkeypatch, fail_for: str | None = None) -> list[str]:
    calls: list[str] = []
    original = speech_boundaries._block_voices

    def counting(snapshot, block, review_ids, cache):
        calls.append(block.id)
        if block.id == fail_for:
            raise ValueError("Spoken text changed after the cast was frozen.")
        return original(snapshot, block, review_ids, cache)

    monkeypatch.setattr(speech_boundaries, "_block_voices", counting)
    monkeypatch.setattr(speech_boundaries, "_voice_cache", OrderedDict())
    return calls


def _silences(book, resolved: dict) -> list[int]:
    return [resolved[sid]["silence_after_ms"] for sid in book["segment_ids"]]


def test_rendered_voices_are_kept_per_run(case, monkeypatch):
    book, snapshot = _voiced_book(case)
    calls = _count_renders(monkeypatch)
    first = _resolve(book, snapshot, MINIMUM_ON, run_id="run-a")
    assert _silences(book, first) == [1500, 700, 700] and len(calls) == 3
    # The same run again renders nothing and gives the same result.
    assert _resolve(book, snapshot, MINIMUM_ON, run_id="run-a") == first and len(calls) == 3
    # Another run renders its own.
    assert _resolve(book, snapshot, MINIMUM_ON, run_id="run-b") == first and len(calls) == 6
    # Without a run id nothing is kept.
    _resolve(book, snapshot, MINIMUM_ON)
    _resolve(book, snapshot, MINIMUM_ON)
    assert len(calls) == 12
    # The run snapshot is not written to.
    assert not any("voices" in entry for entry in snapshot["speech_boundaries"].values())
    # A changed voice override is a new key.
    with book["services"]["database"].session() as session:
        session.get(m.GenerationSegment, book["segment_ids"][1]).voice = "one"
    assert _silences(book, _resolve(book, snapshot, MINIMUM_ON, run_id="run-a")) == [700, 1500, 700]
    assert len(calls) == 13


def test_a_block_that_cannot_be_rendered_is_not_retried(case, monkeypatch):
    book, snapshot = _voiced_book(case)
    calls = _count_renders(monkeypatch, fail_for=book["segment_ids"][1])
    for _attempt in range(2):
        resolved = _resolve(book, snapshot, MINIMUM_ON, run_id="run-c")
        # The second block's voices are unknown: no minimum on either side of it.
        assert _silences(book, resolved) == [700, 700, 700]
    assert calls.count(book["segment_ids"][1]) == 1 and len(calls) == 3


def test_the_voice_cache_is_bounded(case, monkeypatch):
    book, snapshot = _voiced_book(case)
    calls = _count_renders(monkeypatch)
    monkeypatch.setattr(speech_boundaries, "_VOICE_CACHE_LIMIT", 2)
    _resolve(book, snapshot, MINIMUM_ON, run_id="run-d")
    assert len(calls) == 3 and len(speech_boundaries._voice_cache) == 2
    # The least recently used block was evicted, so the next check renders it again.
    assert book["segment_ids"][0] not in {key[1] for key in speech_boundaries._voice_cache}
    _resolve(book, snapshot, MINIMUM_ON, run_id="run-d")
    assert len(calls) > 3 and len(speech_boundaries._voice_cache) == 2

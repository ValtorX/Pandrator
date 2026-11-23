"""Exact pause overrides, frozen casting and bounded fake-audio acceptance."""

from copy import deepcopy

import pytest
from pydub import AudioSegment
from sqlalchemy import select

from pandrator.web import models as m
from pandrator.web.generation_audio_identity import AudioIdentityContext
from pandrator.web.generation_cast_runtime import freeze_cast_snapshot, segment_render_parts
from pandrator.web.generation_rendering import build_render_parts, execute_render_parts
from pandrator.web.global_settings import GlobalSettingsService
from pandrator.web.settings_policy import BUILTIN_DEFAULTS, validate_audio_pause_settings
from pandrator.web.speech_boundaries import assembly_pause, freeze_boundaries
from pandrator.web.workflow_handlers import _default_silence_after_ms
from tests.test_audiobook_resegmentation import rows, topology
from tests.test_generation_rendering import CONTROLS
from tests.test_performance_plans import case as case

PAUSE_KEYS = (
    "sentence_silence_ms",
    "paragraph_silence_ms",
    "clause_silence_ms",
    "voice_change_silence_ms",
    "in_segment_voice_change_silence_ms",
    "chapter_silence_ms",
    "chapter_title_silence_ms",
    "scene_silence_ms",
    "scene_heading_silence_ms",
)


@pytest.mark.parametrize("key", PAUSE_KEYS)
@pytest.mark.parametrize("value", [True, False, "100", 1.5, -1, 10001, None])
def test_pause_validation_rejects_non_integer_or_out_of_range(key, value):
    with pytest.raises(ValueError, match="integer from 0 to 10000"):
        validate_audio_pause_settings({key: value, "unrelated": "kept"})


@pytest.mark.parametrize("value", [0, 10000])
def test_pause_validation_accepts_bounds_and_preserves_unrelated(value):
    settings = {key: value for key in PAUSE_KEYS} | {"unrelated": "kept"}
    validate_audio_pause_settings(settings)
    assert settings["unrelated"] == "kept"


@pytest.mark.parametrize("value", [True, False, "100", 1.5, -1, 5001, None])
def test_minimum_block_gap_rejects_non_integer_or_out_of_range(value):
    with pytest.raises(ValueError, match="synchronization_min_block_gap_ms.*0 to 5000"):
        validate_audio_pause_settings({"synchronization_min_block_gap_ms": value})


@pytest.mark.parametrize("value", [0, 100, 5000])
def test_minimum_block_gap_accepts_bounds_and_preserves_other_settings(value):
    settings = {"synchronization_min_block_gap_ms": value, "unrelated": "kept"}
    validate_audio_pause_settings(settings)
    assert settings == {"synchronization_min_block_gap_ms": value, "unrelated": "kept"}


@pytest.mark.parametrize("value", [0, 1, "true", None, 1.5])
def test_trim_silence_rejects_non_boolean_values(value):
    with pytest.raises(ValueError, match="synchronization_trim_silence.*boolean"):
        validate_audio_pause_settings({"synchronization_trim_silence": value})


@pytest.mark.parametrize("value", [True, False])
def test_trim_silence_defaults_on_and_accepts_both_booleans(case, value):
    assert BUILTIN_DEFAULTS["audio"]["synchronization_trim_silence"] is True
    settings = {"synchronization_trim_silence": value, "unrelated": "kept"}
    validate_audio_pause_settings(settings)
    assert settings["unrelated"] == "kept"

    workspace = case["services"]["workspace_settings"]
    session_id = case["session_id"]
    workspace.update(session_id, "audio", 0, {"synchronization_trim_silence": value})
    resolved, _ = workspace.resolve(session_id, sections=["audio"])
    assert resolved["audio"]["synchronization_trim_silence"] is value


def test_minimum_block_gap_default_validation_and_explicit_zero_round_trip(case):
    services = case["services"]
    workspace = services["workspace_settings"]
    session_id = case["session_id"]
    resolved, _ = workspace.resolve(session_id, sections=["audio"])
    assert resolved["audio"]["synchronization_min_block_gap_ms"] == 100
    with pytest.raises(ValueError, match="synchronization_min_block_gap_ms"):
        workspace.update(session_id, "audio", 0, {"synchronization_min_block_gap_ms": True})
    workspace.update(session_id, "audio", 0, {"synchronization_min_block_gap_ms": 0})
    resolved, _ = workspace.resolve(session_id, sections=["audio"])
    assert resolved["audio"]["synchronization_min_block_gap_ms"] == 0

    globals_service = GlobalSettingsService(services["database"], services["paths"])
    current = globals_service.defaults("audio")
    revision = "*" if current["revision"] == 0 else str(current["revision"])
    with pytest.raises(ValueError, match="synchronization_min_block_gap_ms"):
        globals_service.replace(
            "defaults.audio", {"synchronization_min_block_gap_ms": 5001}, revision
        )
    assert globals_service.defaults("audio") == current
    globals_service.replace("defaults.audio", {"synchronization_min_block_gap_ms": 0}, revision)
    assert globals_service.defaults("audio")["value"]["synchronization_min_block_gap_ms"] == 0


def test_pause_validation_runs_on_session_and_global_saves(case):
    services = case["services"]
    resolved, _ = services["workspace_settings"].resolve(case["session_id"], sections=["audio"])
    assert resolved["audio"]["in_segment_voice_change_silence_ms"] == 100
    assert resolved["audio"]["voice_change_silence_ms"] == 0
    with pytest.raises(ValueError, match="integer"):
        services["workspace_settings"].update(
            case["session_id"], "audio", 0, {"in_segment_voice_change_silence_ms": True}
        )
    globals_service = GlobalSettingsService(services["database"], services["paths"])
    current = globals_service.defaults("audio")
    with pytest.raises(ValueError, match="integer"):
        globals_service.replace(
            "defaults.audio",
            {"in_segment_voice_change_silence_ms": "40"},
            "*" if current["revision"] == 0 else str(current["revision"]),
        )
    assert globals_service.defaults("audio") == current


def test_default_clause_sentence_paragraph_explicit_zero_and_legacy_fallback():
    settings = {"sentence_silence_ms": 300, "paragraph_silence_ms": 900, "clause_silence_ms": 40}
    assert _default_silence_after_ms({"sentence_continues_after": True}, settings) == 40
    assert _default_silence_after_ms({}, settings) == 300
    assert _default_silence_after_ms({"paragraph": "yes"}, settings) == 900
    assert _default_silence_after_ms({"paragraph": "yes", "silence_after_ms": 0}, settings) == 0
    assert (
        _default_silence_after_ms({"sentence_continues_after": True}, {"sentence_silence_ms": 300})
        == 100
    )
    assert _default_silence_after_ms({}, settings, is_subtitle=True) == 0
    assert BUILTIN_DEFAULTS["audio"]["clause_silence_ms"] == 83
    assert BUILTIN_DEFAULTS["audio"]["voice_change_silence_ms"] == 0
    assert BUILTIN_DEFAULTS["audio"]["in_segment_voice_change_silence_ms"] == 100


@pytest.mark.parametrize("boundary", ["continuation", "paragraph", "dialogue_turn"])
@pytest.mark.parametrize("pause", [0, 123])
def test_manual_pause_survives_xml_and_performance_boundary_and_freezes(case, boundary, pause):
    with case["services"]["database"].session() as session:
        row = session.get(m.GenerationSegment, case["segment_ids"][0])
        case["services"]["generation"]._apply_segment_changes(
            session, row, {"silence_after_ms": pause}
        )
        xml = f'<segment id="{row.id}" boundary_after="{boundary}">{row.text}</segment>'
        row.speech_plan_json = {**row.speech_plan_json, "speech_xml": xml}
        snapshot = {
            "audio": {"sentence_silence_ms": 250, "paragraph_silence_ms": 700},
            "performance_snapshot": {"annotations": {row.id: {"annotation": {"_speech_xml": xml}}}},
        }
        freeze_boundaries(session, case["revision_id"], snapshot)
        assert assembly_pause(row, snapshot) == pause
        snapshot["audio"]["paragraph_silence_ms"] = 900
        row.speech_plan_json = {}
        row.silence_after_ms = 999
        assert assembly_pause(row, snapshot) == pause


def test_freeze_boundaries_accepts_bounded_ordered_segment_rows(case):
    with case["services"]["database"].session() as session:
        segments = list(
            session.scalars(
                select(m.GenerationSegment)
                .where(
                    m.GenerationSegment.plan_revision_id == case["revision_id"],
                    m.GenerationSegment.removed.is_(False),
                )
                .order_by(m.GenerationSegment.ordinal)
            )
        )
        snapshot = {"audio": {}}
        freeze_boundaries(session, case["revision_id"], snapshot, segments=segments[:2])
    assert set(snapshot["speech_boundaries"]) == {segment.id for segment in segments[:2]}


def test_frozen_empty_character_dictionary_does_not_read_current_controls(case, monkeypatch):
    def fail_if_read(*_args, **_kwargs):
        pytest.fail("frozen character dictionary should avoid current control lookup")

    monkeypatch.setattr(
        "pandrator.web.speech_boundaries.get_generation_controls", fail_if_read
    )
    with case["services"]["database"].session() as session:
        snapshot = {
            "audio": {},
            "performance_snapshot": {"character_dictionary": {}},
        }
        freeze_boundaries(session, case["revision_id"], snapshot)
    assert set(snapshot["speech_boundaries"]) == set(case["segment_ids"])


def test_source_explicit_pause_and_clause_classification_are_stored(case):
    plan = case["services"]["generation"].create_plan(
        case["session_id"],
        source_revision_id=None,
        settings={"clause_silence_ms": 41},
        segments=[
            {"text": "A clause,", "sentence_continues_after": True},
            {"text": "End.", "silence_after_ms": 0, "paragraph": "yes"},
        ],
    )
    segments = rows(case, plan["active_revision_id"])
    assert segments[0].speech_plan_json["pause_kind"] == "clause"
    assert segments[1].speech_plan_json["silence_override_ms"] == 0
    with case["services"]["database"].session() as session:
        snapshot = {"audio": {"clause_silence_ms": 42}}
        freeze_boundaries(session, plan["active_revision_id"], snapshot)
        assert assembly_pause(segments[0], snapshot) == 42
        assert assembly_pause(segments[1], snapshot) == 0


def _store_markup_plan(case, settings, segments):
    return case["services"]["workflow_handlers"]._store_generation_plan(
        case["session_id"], segments, settings=settings
    )


def test_tag_only_dialogue_turn_overrides_legacy_paragraph_on_create_and_freeze(case):
    revision_id, _segment_ids = _store_markup_plan(
        case,
        {"sentence_silence_ms": 250, "paragraph_silence_ms": 700},
        [
            {
                "text": "Hello.",
                "paragraph": "yes",
                "speech_xml": '<segment id="source-guid"><dialogue>Hello.</dialogue></segment>',
            }
        ],
    )
    segment = rows(case, revision_id)[0]
    assert segment.silence_after_ms == 250
    assert segment.paragraph_break_after is False
    assert segment.speech_plan_json["pause_kind"] == "sentence"

    with case["services"]["database"].session() as session:
        snapshot = {
            "audio": {"sentence_silence_ms": 250, "paragraph_silence_ms": 700}
        }
        freeze_boundaries(session, revision_id, snapshot)
    assert snapshot["speech_boundaries"][segment.id]["boundary_after"] == "dialogue_turn"
    assert assembly_pause(segment, snapshot) == 250


def test_explicit_paragraph_and_continuation_boundaries_beat_legacy_paragraph(case):
    revision_id, _segment_ids = _store_markup_plan(
        case,
        {"sentence_silence_ms": 250, "paragraph_silence_ms": 700},
        [
            {
                "text": "Paragraph.",
                "paragraph": "yes",
                "speech_xml": (
                    '<segment id="source-guid" boundary_after="paragraph">'
                    "Paragraph.</segment>"
                ),
            },
            {
                "text": "Continue,",
                "paragraph": "yes",
                "speech_xml": (
                    '<segment id="source-guid" boundary_after="continuation">'
                    "Continue,</segment>"
                ),
            },
        ],
    )
    segments = rows(case, revision_id)
    assert [segment.silence_after_ms for segment in segments] == [700, 0]
    assert segments[0].paragraph_break_after is True
    assert segments[0].speech_plan_json["pause_kind"] == "paragraph"
    assert segments[1].paragraph_break_after is False
    assert segments[1].speech_plan_json["pause_kind"] == "clause"

    with case["services"]["database"].session() as session:
        snapshot = {
            "audio": {"sentence_silence_ms": 250, "paragraph_silence_ms": 700}
        }
        freeze_boundaries(session, revision_id, snapshot)
    assert [assembly_pause(segment, snapshot) for segment in segments] == [700, 0]


def test_explicit_silence_override_wins_and_old_frozen_snapshot_stays_fixed(case):
    revision_id, _segment_ids = _store_markup_plan(
        case,
        {"sentence_silence_ms": 250, "paragraph_silence_ms": 700},
        [
            {
                "text": "Hello.",
                "paragraph": "yes",
                "silence_after_ms": 37,
                "speech_xml": (
                    '<segment id="source-guid" boundary_after="paragraph">'
                    "Hello.</segment>"
                ),
            },
            {
                "text": "Dialogue.",
                "paragraph": "yes",
                "speech_xml": (
                    '<segment id="source-guid"><dialogue>Dialogue.</dialogue></segment>'
                ),
            },
        ],
    )
    segments = rows(case, revision_id)
    assert segments[0].silence_after_ms == 37
    assert segments[0].speech_plan_json["silence_override_ms"] == 37
    assert segments[1].silence_after_ms == 250
    with case["services"]["database"].session() as session:
        old_snapshot = {
            "audio": {"sentence_silence_ms": 250, "paragraph_silence_ms": 700}
        }
        freeze_boundaries(session, revision_id, old_snapshot)
        new_snapshot = {
            "audio": {"sentence_silence_ms": 900, "paragraph_silence_ms": 1200}
        }
        freeze_boundaries(session, revision_id, new_snapshot)
    assert [assembly_pause(segment, old_snapshot) for segment in segments] == [37, 250]
    assert [assembly_pause(segment, new_snapshot) for segment in segments] == [37, 900]
    assert old_snapshot["speech_boundaries"][segments[0].id]["silence_after_ms"] == 37
    assert old_snapshot["speech_boundaries"][segments[1].id]["silence_after_ms"] == 250


def test_manual_pause_survives_text_reset_split_and_merge(case):
    generation = case["services"]["generation"]
    with case["services"]["database"].session() as session:
        row = session.get(m.GenerationSegment, case["segment_ids"][0])
        generation._apply_segment_changes(
            session, row, {"text": "A long sentence.", "silence_after_ms": 0}
        )
        assert row.speech_plan_json["silence_override_ms"] == 0
    response = topology(
        case, case["revision_id"], action="split", segment_id=case["segment_ids"][0], cursor=6
    )
    assert response.status_code == 201, response.get_json()
    revision = response.get_json()["plan_revision_id"]
    split_rows = rows(case, revision)
    assert "silence_override_ms" not in split_rows[0].speech_plan_json
    assert split_rows[1].speech_plan_json["silence_override_ms"] == 0
    response = topology(
        case,
        revision,
        action="merge",
        left_segment_id=split_rows[0].id,
        right_segment_id=split_rows[1].id,
    )
    assert response.status_code == 201, response.get_json()
    merged = rows(case, response.get_json()["plan_revision_id"])[0]
    assert merged.speech_plan_json["silence_override_ms"] == 0


def test_voice_change_floor_uses_actual_voice_and_explicit_pause_wins(case):
    with case["services"]["database"].session() as session:
        segments = list(
            session.scalars(
                select(m.GenerationSegment)
                .where(m.GenerationSegment.plan_revision_id == case["revision_id"])
                .order_by(m.GenerationSegment.ordinal)
            )
        )
        for row, voice in zip(segments, ["one", "two", "two"], strict=True):
            row.voice = voice
            row.speaker = "different-label" if voice == "two" else "label"
            row.silence_after_ms = 100
            row.speech_plan_json = {}
        snapshot = {
            "audio": {"voice_change_silence_ms": 200},
            "tts": {"performance_enabled": False},
        }
        freeze_boundaries(session, case["revision_id"], snapshot)
        assert [assembly_pause(row, snapshot) for row in segments] == [200, 100, 100]
        old = deepcopy(snapshot)
        segments[0].speech_plan_json = {"silence_override_ms": 0}
        segments[1].voice = "three"
        snapshot["audio"]["voice_change_silence_ms"] = 500
        freeze_boundaries(session, case["revision_id"], snapshot)
        assert assembly_pause(segments[0], snapshot) == 0
        assert assembly_pause(segments[0], old) == 200
        segments[0].speech_plan_json = {"silence_override_ms": 700}
        freeze_boundaries(session, case["revision_id"], snapshot)
        assert assembly_pause(segments[0], snapshot) == 700
        segments[0].speech_plan_json = {
            "speech_xml": f'<segment id="{segments[0].id}" boundary_after="paragraph">{segments[0].text}</segment>'
        }
        snapshot["audio"]["paragraph_silence_ms"] = 700
        freeze_boundaries(session, case["revision_id"], snapshot)
        assert assembly_pause(segments[0], snapshot) == 700


CHAPTER_AUDIO = {
    "sentence_silence_ms": 250,
    "paragraph_silence_ms": 700,
    "clause_silence_ms": 83,
    "chapter_silence_ms": 3000,
    "chapter_title_silence_ms": 1750,
}


def _chapter_rows(session, case, kinds=("speech", "chapter_marker", "speech")):
    """The case's three blocks with the given kinds, all stored at 700 ms."""
    segments = list(
        session.scalars(
            select(m.GenerationSegment)
            .where(m.GenerationSegment.plan_revision_id == case["revision_id"])
            .order_by(m.GenerationSegment.ordinal)
        )
    )
    for row, kind in zip(segments, kinds, strict=True):
        row.node_kind = kind
        row.silence_after_ms = 700
        row.speech_plan_json = {}
    session.flush()
    return segments


def _frozen_pauses(session, case, audio, segments=None):
    snapshot = {"audio": dict(audio), "tts": {"performance_enabled": False}}
    rows_ = list(
        session.scalars(
            select(m.GenerationSegment)
            .where(m.GenerationSegment.plan_revision_id == case["revision_id"])
            .order_by(m.GenerationSegment.ordinal)
        )
    )
    freeze_boundaries(session, case["revision_id"], snapshot, segments=segments)
    return [assembly_pause(row, snapshot) for row in rows_], snapshot


def test_chapter_defaults_and_bounds():
    assert BUILTIN_DEFAULTS["audio"]["chapter_silence_ms"] == 3000
    assert BUILTIN_DEFAULTS["audio"]["chapter_title_silence_ms"] == 1750


@pytest.mark.parametrize("key", ["chapter_silence_ms", "chapter_title_silence_ms"])
@pytest.mark.parametrize("value", [-1, 10001, "100", 1.5, True, None])
def test_chapter_pause_save_names_the_key(case, key, value):
    with pytest.raises(ValueError, match=f"{key} must be an integer from 0 to 10000"):
        case["services"]["workspace_settings"].update(
            case["session_id"], "audio", 0, {key: value}
        )


def test_chapter_pauses_resolve_through_settings_by_default(case):
    resolved, _ = case["services"]["workspace_settings"].resolve(
        case["session_id"], sections=["audio"]
    )
    assert resolved["audio"]["chapter_silence_ms"] == 3000
    assert resolved["audio"]["chapter_title_silence_ms"] == 1750


def test_chapter_marker_gets_before_and_after_pauses_and_others_keep_theirs(case):
    with case["services"]["database"].session() as session:
        _chapter_rows(session, case)
        pauses, snapshot = _frozen_pauses(session, case, CHAPTER_AUDIO)
        assert pauses == [3000, 1750, 700]
        assert snapshot["speech_boundaries"][case["segment_ids"][0]]["silence_after_ms"] == 3000
        # Away from a break nothing changes.
        _chapter_rows(session, case, kinds=("speech", "speech", "speech"))
        assert _frozen_pauses(session, case, CHAPTER_AUDIO)[0] == [700, 700, 700]


def test_longer_regular_pause_beats_the_chapter_pause(case):
    with case["services"]["database"].session() as session:
        _chapter_rows(session, case)
        audio = CHAPTER_AUDIO | {"chapter_silence_ms": 500, "chapter_title_silence_ms": 100}
        assert _frozen_pauses(session, case, audio)[0] == [700, 700, 700]


def test_chapter_boundary_after_markup_gets_the_chapter_pause_without_title_pause(case):
    with case["services"]["database"].session() as session:
        segments = _chapter_rows(session, case, kinds=("speech", "speech", "speech"))
        row = segments[0]
        row.speech_plan_json = {
            "speech_xml": f'<segment id="{row.id}" boundary_after="chapter">{row.text}</segment>'
        }
        assert _frozen_pauses(session, case, CHAPTER_AUDIO)[0] == [3000, 700, 700]
        # Both marks on one block: one pause, the longest.
        segments[1].node_kind = "chapter_marker"
        assert _frozen_pauses(session, case, CHAPTER_AUDIO)[0] == [3000, 1750, 700]
        # A marker that also ends its chapter takes the longer of the two.
        segments[1].speech_plan_json = {
            "speech_xml": (
                f'<segment id="{segments[1].id}" boundary_after="chapter">'
                f"{segments[1].text}</segment>"
            )
        }
        assert _frozen_pauses(session, case, CHAPTER_AUDIO)[0] == [3000, 3000, 700]


def test_voice_change_minimum_still_applies_after_the_chapter_rule(case):
    with case["services"]["database"].session() as session:
        segments = _chapter_rows(session, case)
        for row, voice in zip(segments, ["one", "two", "three"], strict=True):
            row.voice = voice
        audio = CHAPTER_AUDIO | {"voice_change_silence_ms": 3500}
        assert _frozen_pauses(session, case, audio)[0] == [3500, 3500, 700]
        audio = CHAPTER_AUDIO | {"voice_change_silence_ms": 2000}
        assert _frozen_pauses(session, case, audio)[0] == [3000, 2000, 700]


def test_manual_pause_overrides_the_chapter_pauses(case):
    with case["services"]["database"].session() as session:
        segments = _chapter_rows(session, case)
        segments[0].speech_plan_json = {"silence_override_ms": 400}
        segments[1].speech_plan_json = {"silence_override_ms": 0}
        for row, voice in zip(segments, ["one", "two", "three"], strict=True):
            row.voice = voice
        audio = CHAPTER_AUDIO | {"voice_change_silence_ms": 3500}
        assert _frozen_pauses(session, case, audio)[0] == [400, 0, 700]


def test_snapshot_without_chapter_keys_applies_no_chapter_rule(case):
    with case["services"]["database"].session() as session:
        _chapter_rows(session, case)
        old_audio = {key: value for key, value in CHAPTER_AUDIO.items() if "chapter" not in key}
        assert _frozen_pauses(session, case, old_audio)[0] == [700, 700, 700]
        # A frozen snapshot keeps what it froze when the setting changes later.
        _, frozen = _frozen_pauses(session, case, CHAPTER_AUDIO)
        frozen["audio"]["chapter_silence_ms"] = 9000
        rows_ = _chapter_rows(session, case)
        assert [assembly_pause(row, frozen) for row in rows_] == [3000, 1750, 700]


def test_subset_freeze_and_removed_blocks_find_the_marker_successor(case):
    with case["services"]["database"].session() as session:
        segments = _chapter_rows(session, case)
        # Only the block before the marker is given; its successor is not.
        snapshot = {"audio": dict(CHAPTER_AUDIO)}
        freeze_boundaries(session, case["revision_id"], snapshot, segments=[segments[0]])
        assert set(snapshot["speech_boundaries"]) == {segments[0].id}
        assert assembly_pause(segments[0], snapshot) == 3000
        # The marker alone gets its title pause.
        snapshot = {"audio": dict(CHAPTER_AUDIO)}
        freeze_boundaries(session, case["revision_id"], snapshot, segments=[segments[1]])
        assert assembly_pause(segments[1], snapshot) == 1750
        # A removed block between them does not hide the marker.
        segments = _chapter_rows(session, case, kinds=("speech", "speech", "chapter_marker"))
        segments[1].removed = True
        session.flush()
        snapshot = {"audio": dict(CHAPTER_AUDIO)}
        freeze_boundaries(session, case["revision_id"], snapshot, segments=[segments[0]])
        assert assembly_pause(segments[0], snapshot) == 3000


def test_chapter_freeze_reads_successor_kinds_with_one_bounded_query(case):
    from sqlalchemy import event

    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = " ".join(statement.lower().split())
        if "from generation_segments" in normalized:
            statements.append(normalized)

    with case["services"]["database"].session() as session:
        segments = _chapter_rows(session, case)
        engine = case["services"]["database"].engine
        snapshot = {"audio": dict(CHAPTER_AUDIO)}
        event.listen(engine, "before_cursor_execute", capture)
        try:
            freeze_boundaries(session, case["revision_id"], snapshot, segments=segments[:1])
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        # The successor of the last given row is read, and the answer is right.
        assert assembly_pause(segments[0], snapshot) == 3000
    reads = [sql for sql in statements if "node_kind" in sql and ">=" in sql]
    assert len(reads) == 1 and " limit " in reads[0] and "lead(" not in reads[0], statements
    assert len(statements) == 1, statements


def test_chapter_rule_skips_subtitle_cues(case):
    with case["services"]["database"].session() as session:
        segments = _chapter_rows(session, case)
        segments[0].node_kind = "subtitle_cue"
        segments[1].node_kind = "chapter_marker"
        session.flush()
        pauses, _ = _frozen_pauses(session, case, CHAPTER_AUDIO)
        assert pauses[0] == 700  # a subtitle cue before a marker gets no chapter pause
        segments[0].node_kind = "speech"
        segments[1].node_kind = "subtitle_cue"
        segments[2].node_kind = "chapter_marker"
        session.flush()
        pauses, _ = _frozen_pauses(session, case, CHAPTER_AUDIO)
        assert pauses[1] == 700  # a cue gets no chapter pause


def test_consecutive_chapter_markers_each_take_the_longest_pause(case):
    with case["services"]["database"].session() as session:
        _chapter_rows(session, case, kinds=("chapter_marker", "chapter_marker", "speech"))
        # The first marker is both a title and the block before a marker.
        assert _frozen_pauses(session, case, CHAPTER_AUDIO)[0] == [3000, 1750, 700]
        audio = CHAPTER_AUDIO | {"chapter_silence_ms": 1000}
        assert _frozen_pauses(session, case, audio)[0] == [1750, 1750, 700]


SCENE_AUDIO = CHAPTER_AUDIO | {"scene_silence_ms": 2000, "scene_heading_silence_ms": 1000}


def _scene_end(row):
    row.speech_plan_json = {
        "speech_xml": f'<segment id="{row.id}" boundary_after="scene">{row.text}</segment>'
    }


def test_scene_defaults_and_bounds():
    assert BUILTIN_DEFAULTS["audio"]["scene_silence_ms"] == 2000
    assert BUILTIN_DEFAULTS["audio"]["scene_heading_silence_ms"] == 1000


@pytest.mark.parametrize("key", ["scene_silence_ms", "scene_heading_silence_ms"])
@pytest.mark.parametrize("value", [-1, 10001, "100", 1.5, True, None])
def test_scene_pause_save_names_the_key(case, key, value):
    with pytest.raises(ValueError, match=f"{key} must be an integer from 0 to 10000"):
        case["services"]["workspace_settings"].update(
            case["session_id"], "audio", 0, {key: value}
        )


def test_scene_pauses_resolve_through_settings_by_default(case):
    resolved, _ = case["services"]["workspace_settings"].resolve(
        case["session_id"], sections=["audio"]
    )
    assert resolved["audio"]["scene_silence_ms"] == 2000
    assert resolved["audio"]["scene_heading_silence_ms"] == 1000


def test_scene_break_gets_the_scene_pause_and_others_keep_theirs(case):
    with case["services"]["database"].session() as session:
        segments = _chapter_rows(session, case, kinds=("speech", "speech", "speech"))
        _scene_end(segments[0])
        pauses, snapshot = _frozen_pauses(session, case, SCENE_AUDIO)
        assert pauses == [2000, 700, 700]
        assert snapshot["speech_boundaries"][segments[0].id]["boundary_after"] == "scene"


def test_heading_alone_gets_the_heading_pause(case):
    with case["services"]["database"].session() as session:
        _chapter_rows(session, case, kinds=("speech", "heading", "speech"))
        assert _frozen_pauses(session, case, SCENE_AUDIO)[0] == [700, 1000, 700]


def test_heading_that_opens_a_scene_gets_both_pauses(case):
    with case["services"]["database"].session() as session:
        segments = _chapter_rows(session, case, kinds=("speech", "heading", "speech"))
        _scene_end(segments[0])
        assert _frozen_pauses(session, case, SCENE_AUDIO)[0] == [2000, 1000, 700]


def test_chapter_and_scene_meet_at_the_longest_pause(case):
    with case["services"]["database"].session() as session:
        segments = _chapter_rows(session, case)
        _scene_end(segments[0])
        assert _frozen_pauses(session, case, SCENE_AUDIO)[0] == [3000, 1750, 700]
        audio = SCENE_AUDIO | {"chapter_silence_ms": 1500, "scene_silence_ms": 2600}
        assert _frozen_pauses(session, case, audio)[0] == [2600, 1750, 700]


def test_heading_that_ends_a_scene_takes_the_longest_pause(case):
    with case["services"]["database"].session() as session:
        segments = _chapter_rows(session, case, kinds=("speech", "heading", "speech"))
        _scene_end(segments[1])
        assert _frozen_pauses(session, case, SCENE_AUDIO)[0] == [700, 2000, 700]
        audio = SCENE_AUDIO | {"scene_heading_silence_ms": 2500}
        assert _frozen_pauses(session, case, audio)[0] == [700, 2500, 700]


def test_longer_regular_pause_beats_the_scene_pauses(case):
    with case["services"]["database"].session() as session:
        segments = _chapter_rows(session, case, kinds=("speech", "heading", "speech"))
        _scene_end(segments[0])
        audio = SCENE_AUDIO | {"paragraph_silence_ms": 2500, "scene_heading_silence_ms": 100}
        # The scene block's boundary resolves to the paragraph pause (2500), and the
        # heading's paragraph pause_kind does too: both beat the shorter break settings.
        segments[1].speech_plan_json = {"pause_kind": "paragraph"}
        assert _frozen_pauses(session, case, audio)[0] == [2500, 2500, 700]


def test_a_heading_at_a_chapter_break_takes_the_longest_pause(case):
    with case["services"]["database"].session() as session:
        segments = _chapter_rows(session, case, kinds=("heading", "chapter_marker", "speech"))
        # A heading before a chapter marker: the chapter pause (3000) beats the heading
        # pause (1000); a heading that ends a chapter takes the same one pause.
        assert _frozen_pauses(session, case, SCENE_AUDIO)[0][0] == 3000
        segments[1].node_kind = "speech"
        segments[0].speech_plan_json = {
            "speech_xml": f'<segment id="{segments[0].id}" boundary_after="chapter">'
            f"{segments[0].text}</segment>"
        }
        session.flush()
        assert _frozen_pauses(session, case, SCENE_AUDIO)[0][0] == 3000


def test_manual_pause_overrides_the_scene_pauses(case):
    with case["services"]["database"].session() as session:
        segments = _chapter_rows(session, case, kinds=("speech", "heading", "speech"))
        _scene_end(segments[0])
        segments[0].speech_plan_json["silence_override_ms"] = 300
        segments[1].speech_plan_json = {"silence_override_ms": 0}
        for row, voice in zip(segments, ["one", "two", "three"], strict=True):
            row.voice = voice
        audio = SCENE_AUDIO | {"voice_change_silence_ms": 3500}
        assert _frozen_pauses(session, case, audio)[0] == [300, 0, 700]


def test_voice_change_minimum_still_applies_after_the_scene_rule(case):
    with case["services"]["database"].session() as session:
        segments = _chapter_rows(session, case, kinds=("speech", "speech", "speech"))
        _scene_end(segments[0])
        for row, voice in zip(segments, ["one", "two", "three"], strict=True):
            row.voice = voice
        audio = SCENE_AUDIO | {"voice_change_silence_ms": 2600}
        assert _frozen_pauses(session, case, audio)[0] == [2600, 2600, 700]


def test_snapshot_without_scene_keys_applies_no_scene_rule(case):
    with case["services"]["database"].session() as session:
        segments = _chapter_rows(session, case, kinds=("speech", "heading", "speech"))
        _scene_end(segments[0])
        # Chapter keys alone: the scene break and the heading keep the paragraph pause.
        assert _frozen_pauses(session, case, CHAPTER_AUDIO)[0] == [700, 700, 700]


def test_scene_rules_skip_subtitle_cues(case):
    with case["services"]["database"].session() as session:
        # A cue carries no scene markup, and marked as a heading it gets no heading pause.
        _chapter_rows(session, case, kinds=("speech", "subtitle_cue", "speech"))
        assert _frozen_pauses(session, case, SCENE_AUDIO)[0] == [700, 700, 700]


def test_boundary_pause_gives_the_longer_of_paragraph_and_scene_pause():
    from pandrator.web.speech_boundaries import boundary_pause

    assert boundary_pause("scene", 700, SCENE_AUDIO) == 2000
    assert boundary_pause("scene", 700, SCENE_AUDIO | {"paragraph_silence_ms": 2500}) == 2500
    assert boundary_pause("scene", 700, CHAPTER_AUDIO) == 700
    assert boundary_pause("paragraph", 700, SCENE_AUDIO) == 700


@pytest.mark.parametrize(
    "same_voice,subtitle,gap,expected_gaps,expected_duration",
    [
        (False, False, 50, [0, 50], 200),
        (False, False, 100, [0, 100], 200),
        (True, False, 100, [0], 100),
        (False, True, 100, [0, 0], 200),
        (False, False, 0, [0, 0], 200),
    ],
)
def test_render_parts_defer_requested_voice_gap_to_assembly(
    same_voice, subtitle, gap, expected_gaps, expected_duration
):
    controls = deepcopy(CONTROLS)
    if same_voice:
        controls["cast"]["characters"]["c-two"] = {"voice": "one"}
    markup = (
        '<segment id="s"><speaker ref="c-one">A</speaker><speaker ref="c-two">B</speaker></segment>'
    )
    parts = build_render_parts(
        "AB",
        {
            "casting_enabled": True,
            "performance_enabled": False,
            "voice_change_silence_ms": 500,
            "in_segment_voice_change_silence_ms": gap,
            "_subtitle_timed": subtitle,
        },
        speech_xml=markup,
        segment_id="s",
        controls=controls,
    )
    combined, manifest = execute_render_parts(
        parts,
        synthesize=lambda text, settings: AudioSegment.silent(100),
        cancelled=lambda: False,
    )
    assert len(combined) == expected_duration
    assert [row["silence_before_ms"] for row in manifest] == expected_gaps
    assert [row["silence_policy"] for row in manifest] == [
        "assembly-v1"
    ] * len(manifest)
    assert sum(row["duration_ms"] for row in manifest) == expected_duration


def test_voice_gap_settings_do_not_change_multi_voice_provider_request_identity(
    case, monkeypatch
):
    from pandrator.web import generation_cast_runtime as casting

    monkeypatch.setattr(casting, "get_generation_controls", lambda session, sid: deepcopy(CONTROLS))
    monkeypatch.setattr(
        casting,
        "resolve_binding",
        lambda session, binding, settings, cache, **kwargs: dict(binding),
    )
    with case["services"]["database"].session() as session:
        row = session.get(m.GenerationSegment, case["segment_ids"][0])
        first, rest = row.text[:3], row.text[3:]
        row.speech_plan_json = {
            "speech_xml": f'<segment id="{row.id}"><speaker ref="c-one">{first}</speaker><speaker ref="c-two">{rest}</speaker></segment>'
        }
        snapshot = {
            "audio": {"voice_change_silence_ms": 0, "in_segment_voice_change_silence_ms": 0},
            "tts": {
                "service": "openai",
                "model": "tts-1",
                "voice": "alloy",
                "casting_enabled": True,
                "performance_enabled": False,
            },
        }
        freeze_cast_snapshot(session, case["revision_id"], snapshot, snapshot["tts"])
        before = AudioIdentityContext(session, snapshot).for_segment(row)
        padded = deepcopy(snapshot)
        padded["audio"]["in_segment_voice_change_silence_ms"] = 50
        after = AudioIdentityContext(session, padded).for_segment(row)
        assert before["settings_hash"] == after["settings_hash"]
        assert before["voice_reference_hash"] == after["voice_reference_hash"]
        assert before["performance_request_hash"] == after["performance_request_hash"]
        only_assembly = deepcopy(padded)
        only_assembly["audio"].update(
            sentence_silence_ms=999,
            clause_silence_ms=99,
            paragraph_silence_ms=1234,
            voice_change_silence_ms=500,
        )
        assert AudioIdentityContext(session, only_assembly).for_segment(row) == after
        incoming = {
            **padded["tts"],
            "voice_change_silence_ms": 500,
            "in_segment_voice_change_silence_ms": 100,
        }
        parts = segment_render_parts(incoming, padded, row.id, row.text)
        assert parts[1]["silence_before_ms"] == 50
        zero_parts = segment_render_parts(incoming, snapshot, row.id, row.text)
        assert zero_parts[1]["silence_before_ms"] == 0
        assert [
            part["silence_before_ms"]
            for part in segment_render_parts(incoming, only_assembly, row.id, row.text)
        ] == [0, 50]
        # Old snapshots preserve exactly the padding selected by their legacy key.
        for legacy_gap in (0, 80):
            legacy = deepcopy(snapshot)
            legacy["audio"].pop("in_segment_voice_change_silence_ms")
            legacy["audio"]["voice_change_silence_ms"] = legacy_gap
            legacy_parts = segment_render_parts(incoming, legacy, row.id, row.text)
            assert legacy_parts[1]["silence_before_ms"] == legacy_gap
            modern_equivalent = deepcopy(legacy)
            modern_equivalent["audio"]["in_segment_voice_change_silence_ms"] = legacy_gap
            assert AudioIdentityContext(session, legacy).for_segment(row) == AudioIdentityContext(
                session, modern_equivalent
            ).for_segment(row)
        missing = deepcopy(snapshot)
        missing["audio"] = {}
        assert (
            segment_render_parts(incoming, missing, row.id, row.text)[1]["silence_before_ms"] == 0
        )
        # Fixed subtitle timing excludes audible padding for either schema.
        for audio in (
            {"voice_change_silence_ms": 80},
            {
                "voice_change_silence_ms": 500,
                "in_segment_voice_change_silence_ms": 100,
            },
        ):
            subtitle = deepcopy(snapshot)
            subtitle["audio"] = audio
            subtitle["generation_control_snapshot"]["segments"][row.id]["subtitle_timed"] = True
            assert AudioIdentityContext(session, subtitle).for_segment(row) == before
        row.speech_plan_json = {}
        freeze_cast_snapshot(session, case["revision_id"], snapshot, snapshot["tts"])
        padded = deepcopy(snapshot)
        padded["audio"].update(voice_change_silence_ms=500, in_segment_voice_change_silence_ms=100)
        assert AudioIdentityContext(session, padded).for_segment(row) == AudioIdentityContext(
            session, snapshot
        ).for_segment(row)


def test_segment_parts_preserve_legacy_and_explicit_zero_gap_metadata():
    markup = (
        '<segment id="s"><speaker ref="c-one">A</speaker><speaker ref="c-two">B</speaker></segment>'
    )
    settings = {
        "casting_enabled": True,
        "performance_enabled": False,
        "voice_change_silence_ms": 80,
    }
    legacy = build_render_parts(
        "AB", settings, speech_xml=markup, segment_id="s", controls=CONTROLS
    )
    explicit_zero = build_render_parts(
        "AB",
        {**settings, "in_segment_voice_change_silence_ms": 0},
        speech_xml=markup,
        segment_id="s",
        controls=CONTROLS,
    )
    assert [part["silence_before_ms"] for part in legacy] == [0, 80]
    assert [part["silence_before_ms"] for part in explicit_zero] == [0, 0]


def test_api_omitted_pause_follows_defaults_but_explicit_zero_survives_split(case):
    response = case["client"].post(
        f"/api/v1/sessions/{case['session_id']}/generation-plan",
        json={
            "settings": {"sentence_silence_ms": 444},
            "segments": [
                {"text": "Automatic sentence."},
                {"text": "Explicit zero pause.", "silence_after_ms": 0},
            ],
        },
        headers=case["headers"],
    )
    assert response.status_code == 201, response.get_json()
    revision = response.get_json()["active_revision_id"]
    segments = rows(case, revision)
    assert segments[0].silence_after_ms == 444
    assert segments[0].speech_plan_json["pause_kind"] == "sentence"
    assert "silence_override_ms" not in segments[0].speech_plan_json
    assert segments[1].speech_plan_json["silence_override_ms"] == 0
    response = topology(case, revision, action="split", segment_id=segments[1].id, cursor=8)
    assert response.status_code == 201, response.get_json()
    descendants = rows(case, response.get_json()["plan_revision_id"])[1:]
    assert "silence_override_ms" not in descendants[0].speech_plan_json
    assert descendants[1].speech_plan_json["silence_override_ms"] == 0


def test_resegmentation_retains_only_terminal_explicit_pause(case):
    with case["services"]["database"].session() as session:
        terminal = session.get(m.GenerationSegment, case["segment_ids"][1])
        case["services"]["generation"]._apply_segment_changes(
            session,
            terminal,
            {"silence_after_ms": 0},
        )
    response = topology(
        case,
        case["revision_id"],
        action="resegment",
        segment_ids=case["segment_ids"][:2],
        boundaries=[11],
    )
    assert response.status_code == 201, response.get_json()
    descendants = rows(case, response.get_json()["plan_revision_id"])[:2]
    assert "silence_override_ms" not in descendants[0].speech_plan_json
    assert descendants[1].speech_plan_json["silence_override_ms"] == 0
    assert rows(case, case["revision_id"])[1].speech_plan_json["silence_override_ms"] == 0

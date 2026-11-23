"""Resolve audiobook pauses from the accepted speech structure and the audio settings.

A run freezes each block's boundary facts when it starts; the pause lengths are
resolved again from those facts and the current settings when the run is assembled.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Container, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from pandrator.logic.speech_markup import ParsedSpeechMarkup, parse_speech_markup
from pandrator.logic.speech_performance import content_hash

from . import models as m
from .generation_cast_runtime import remap_markup
from .generation_controls import get_generation_controls


def effective_speech_boundary(parsed: ParsedSpeechMarkup) -> str | None:
    """Return the explicit boundary, or infer a dialogue turn from final speech."""
    if parsed.boundary_after is not None:
        return parsed.boundary_after
    if parsed.spans and parsed.spans[-1].dialogue:
        return "dialogue_turn"
    return None


def record_continues_sentence(record: dict) -> bool:
    value = record.get("sentence_continues_after")
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "clause"}
    if value is not None:
        return bool(value)
    if str(record.get("pause_kind") or "").strip().lower() == "clause":
        return True
    # Legacy split pieces lack the explicit continuation flag; only their
    # terminal punctuation distinguishes an internal clause from the last part.
    if record.get("split_part") is not None:
        text = str(record.get("text") or record.get("original_sentence") or "").rstrip()
        return re.search(r"[.!?…。！？…][\"'”’)\]}]*$", text) is None
    return False


def record_pause_metadata(record: dict, *, is_subtitle: bool = False) -> dict:
    """Keep supplied pauses distinct from defaults when a plan is materialized."""
    if record.get("silence_after_ms") is not None:
        return {"silence_override_ms": max(0, int(record["silence_after_ms"]))}
    if is_subtitle:
        return {}
    boundary = str(record.get("speech_boundary_after") or "").strip().lower()
    if boundary == "continuation":
        kind = "clause"
    elif boundary == "dialogue_turn":
        kind = "sentence"
    else:
        paragraph = (
            bool(record.get("paragraph_break_after"))
            or str(record.get("paragraph") or "").lower() == "yes"
            or boundary in {"paragraph", "scene", "chapter"}
        )
        kind = (
            "paragraph"
            if paragraph
            else ("clause" if record_continues_sentence(record) else "sentence")
        )
    return {"pause_kind": kind}


def default_record_pause(record: dict, settings: dict, *, is_subtitle: bool = False) -> int:
    """Resolve initial pauses with a compatibility fallback for old runtime dicts."""
    explicit = record.get("silence_after_ms")
    if explicit is not None:
        return max(0, int(explicit or 0))
    if is_subtitle:
        return 0

    sentence_silence = max(
        0,
        int(
            settings.get("sentence_silence_ms", settings.get("silence_between_sentences", 250)) or 0
        ),
    )
    is_paragraph = (
        bool(record.get("paragraph_break_after"))
        or str(record.get("paragraph") or "").lower() == "yes"
    )
    boundary = record.get("speech_boundary_after")
    if boundary == "continuation":
        return 0
    if boundary == "dialogue_turn":
        return sentence_silence
    if boundary in {"scene", "chapter", "paragraph"}:
        is_paragraph = True
    if is_paragraph:
        return max(
            0,
            int(
                settings.get("paragraph_silence_ms", settings.get("silence_for_paragraphs", 700))
                or 0
            ),
        )
    if record_continues_sentence(record):
        if "clause_silence_ms" in settings:
            return max(0, int(settings["clause_silence_ms"]))
        return max(0, round(sentence_silence / 3))
    return sentence_silence


def boundary_pause(boundary: str | None, stored_ms: int, audio: Mapping[str, Any]) -> int:
    if boundary == "continuation":
        return 0
    if boundary == "dialogue_turn":
        return max(0, int(audio.get("sentence_silence_ms", 250)))
    if boundary == "chapter":
        return max(
            max(0, int(audio.get("paragraph_silence_ms", 700))),
            _setting_ms(audio, "chapter_silence_ms"),
        )
    if boundary in {"paragraph", "scene"}:
        return max(0, int(audio.get("paragraph_silence_ms", 700)))
    return max(0, int(stored_ms or 0))


def _setting_ms(audio: Mapping[str, Any], key: str) -> int:
    """A pause setting in milliseconds; a snapshot without the key applies none."""
    return max(0, int(audio.get(key) or 0))


@dataclass(frozen=True, slots=True)
class BoundaryBlock:
    """What a block's pause is resolved from, read from a row or from its columns."""

    id: str
    ordinal: int
    node_kind: str
    text: str
    stored_ms: int
    voice: str | None
    language: str | None
    speech_xml: str | None
    pause_kind: Any
    override: Any

    @classmethod
    def from_row(cls, row: m.GenerationSegment) -> BoundaryBlock:
        plan = row.speech_plan_json or {}
        return cls(
            id=row.id,
            ordinal=row.ordinal,
            node_kind=row.node_kind,
            text=row.optimized_text or row.text,
            stored_ms=row.silence_after_ms,
            voice=row.voice,
            language=row.language,
            speech_xml=plan.get("speech_xml"),
            pause_kind=plan.get("pause_kind"),
            override=plan.get("silence_override_ms"),
        )


def _revision_blocks(session, revision_id: str) -> list[BoundaryBlock]:
    """The revision's non-removed blocks in order, reading only the columns a pause needs."""
    segment = m.GenerationSegment
    plan = segment.speech_plan_json
    rows = session.execute(
        select(
            segment.id,
            segment.ordinal,
            segment.node_kind,
            segment.text,
            segment.optimized_text,
            segment.silence_after_ms,
            segment.voice,
            segment.language,
            func.json_extract(plan, "$.speech_xml"),
            func.json_extract(plan, "$.pause_kind"),
            func.json_extract(plan, "$.silence_override_ms"),
        )
        .where(segment.plan_revision_id == revision_id, segment.removed.is_(False))
        .order_by(segment.ordinal)
    ).all()
    return [
        BoundaryBlock(
            id=block_id,
            ordinal=ordinal,
            node_kind=node_kind,
            text=optimized_text or text,
            stored_ms=stored_ms,
            voice=voice,
            language=language,
            speech_xml=speech_xml,
            pause_kind=pause_kind,
            override=override,
        )
        for (
            block_id,
            ordinal,
            node_kind,
            text,
            optimized_text,
            stored_ms,
            voice,
            language,
            speech_xml,
            pause_kind,
            override,
        ) in rows
    ]


def blocks_before_chapter_marker(
    session,
    revision_id: str,
    rows: Sequence[m.GenerationSegment] | Sequence[BoundaryBlock],
) -> set[str]:
    """Ids of non-removed blocks whose next non-removed block is a chapter marker.

    The next block is read from the revision, so the answer does not depend on which
    rows a caller freezes (a playlist page, a run limited to selected blocks). One
    query over the index range from the rows' first ordinal: at most ``last - first + 1``
    ordinals lie in the rows' span, plus the successor of the last row.
    """
    if not rows:
        return set()
    first = min(row.ordinal for row in rows)
    last = max(row.ordinal for row in rows)
    following = session.execute(
        select(m.GenerationSegment.id, m.GenerationSegment.node_kind)
        .where(
            m.GenerationSegment.plan_revision_id == revision_id,
            m.GenerationSegment.removed.is_(False),
            m.GenerationSegment.ordinal >= first,
        )
        .order_by(m.GenerationSegment.ordinal)
        .limit(last - first + 2)
    ).all()
    return {
        block_id
        for (block_id, _kind), (_next_id, next_kind) in zip(
            following, following[1:], strict=False
        )
        if next_kind == "chapter_marker"
    }


def apply_chapter_pauses(
    pause: int,
    *,
    boundary: str | None,
    node_kind: str,
    before_marker: bool,
    audio: Mapping[str, Any],
) -> int:
    """Raise a resolved pause to the chapter pauses that apply; the longest wins."""
    if node_kind == "subtitle_cue":
        return pause
    if boundary == "chapter" or before_marker:
        pause = max(pause, _setting_ms(audio, "chapter_silence_ms"))
    if node_kind == "chapter_marker":
        pause = max(pause, _setting_ms(audio, "chapter_title_silence_ms"))
    return pause


def block_pause(
    block: BoundaryBlock,
    boundary: str | None,
    *,
    before_marker: bool,
    audio: Mapping[str, Any],
) -> int:
    """One block's pause before the voice-change minimum.

    The base pause comes from the boundary, else the block's ``pause_kind`` setting,
    else its stored pause. A manual pause replaces it and stops there; otherwise the
    chapter rule raises it.
    """
    if block.override is not None:
        return max(0, int(block.override))
    pause = boundary_pause(boundary, block.stored_ms, audio)
    if boundary is None and block.pause_kind in {"clause", "sentence", "paragraph"}:
        key = f"{block.pause_kind}_silence_ms"
        pause = max(0, int(audio.get(key, block.stored_ms)))
    # The longest applicable pause wins; a manual pause above is kept as set.
    return apply_chapter_pauses(
        pause,
        boundary=boundary,
        node_kind=block.node_kind,
        before_marker=before_marker,
        audio=audio,
    )


def _voice_id(voice_key: Any) -> str:
    """A short stable name for a rendered voice; equal voices get equal names."""
    return content_hash(voice_key)[:16]


def _block_voices(
    snapshot: dict, block: BoundaryBlock, review_ids: Container[str], cache: dict
) -> list[str]:
    """The voices a block starts and ends with, rendered from a run's frozen cast."""
    from .generation_cast_runtime import segment_render_parts
    from .generation_performance_snapshot import (
        performance_runtime_settings,
        segment_performance_settings,
    )

    settings = performance_runtime_settings(snapshot)
    if block.voice:
        settings["voice"] = settings["speaker"] = block.voice
    if block.language:
        settings["language"] = block.language
    if block.id in review_ids:
        # Its speaker plan entry no longer fits and is not frozen; only
        # its voices matter here, and those come from the cast freeze.
        settings["performance_enabled"] = False
    settings = segment_performance_settings(
        settings, snapshot, block.id, block.text, _service_config_cache=cache,
    )
    parts = segment_render_parts(
        settings, snapshot, block.id, block.text, _service_config_cache=cache,
    )
    return [_voice_id(parts[0]["voice_key"]), _voice_id(parts[-1]["voice_key"])]


# End voices rendered for a run's blocks whose frozen entries hold none (runs frozen
# with the voice-change minimum at 0, or before voices were recorded). A run's frozen
# cast never changes, so the run id stands for it; the block's text, voice override,
# language and review status complete the key. None records a block that cannot be
# rendered, so it is not retried on every check. Bounded, least recently used first out.
_VOICE_CACHE_LIMIT = 50_000
_VoiceKey = tuple[str, str, str, str | None, str | None, bool]
_voice_cache: OrderedDict[_VoiceKey, tuple[str, str] | None] = OrderedDict()
_voice_cache_lock = threading.Lock()


def _cached_block_voices(
    run_id: str | None,
    snapshot: dict,
    block: BoundaryBlock,
    review_ids: Container[str],
    cache: dict,
) -> list[str] | None:
    """A block's end voices for a run, rendered once per process; None when they cannot be."""
    if run_id is None:
        try:
            return _block_voices(snapshot, block, review_ids, cache)
        except ValueError:
            return None
    key: _VoiceKey = (
        run_id,
        block.id,
        content_hash(block.text),
        block.voice,
        block.language,
        block.id in review_ids,
    )
    with _voice_cache_lock:
        if key in _voice_cache:
            _voice_cache.move_to_end(key)
            cached = _voice_cache[key]
            return None if cached is None else list(cached)
    try:
        first, last = _block_voices(snapshot, block, review_ids, cache)
        value: tuple[str, str] | None = (first, last)
    except ValueError:
        value = None
    with _voice_cache_lock:
        _voice_cache[key] = value
        _voice_cache.move_to_end(key)
        while len(_voice_cache) > _VOICE_CACHE_LIMIT:
            _voice_cache.popitem(last=False)
    return None if value is None else list(value)


def _raise_voice_changes(
    result: dict[str, dict[str, Any]],
    blocks: Sequence[BoundaryBlock],
    voices: Mapping[str, Sequence[str]],
    gap: int,
) -> None:
    """Raise a pause to the voice-change minimum where the end voices differ.

    A block with a manual pause keeps it.
    """
    for left, right in zip(blocks, blocks[1:], strict=False):
        if left.override is not None:
            continue
        left_voices, right_voices = voices.get(left.id), voices.get(right.id)
        if (
            left_voices is not None
            and right_voices is not None
            and left_voices[1] != right_voices[0]
        ):
            result[left.id]["silence_after_ms"] = max(
                result[left.id]["silence_after_ms"], gap
            )


def _block_markup(block: BoundaryBlock, annotations: Mapping) -> str | None:
    entry = annotations.get(block.id) or {}
    return (entry.get("annotation") or {}).get("_speech_xml") or block.speech_xml


def _markup_boundary(
    block: BoundaryBlock, xml: str, characters: Any
) -> tuple[str | None, str]:
    """The boundary a block's speech markup ends on, and the text hash that guards it."""
    xml = remap_markup(xml, block.id, block.text, characters)
    parsed = parse_speech_markup(
        xml,
        expected_segment_id=block.id,
        expected_text=block.text,
        characters=characters,
    )
    return effective_speech_boundary(parsed), content_hash(block.text)


def _unresolved_speaker(error: ValueError) -> bool:
    message = str(error).lower()
    return "unresolved speaker" in message or "ambiguous speaker" in message


def _audiobook_record(session, revision_id: str) -> m.SessionRecord | None:
    revision = session.get(m.GenerationPlanRevision, revision_id)
    plan = session.get(m.GenerationPlan, revision.plan_id) if revision else None
    record = session.get(m.SessionRecord, plan.session_id) if plan else None
    return record if record is not None and record.workflow_kind == "audiobook" else None


def freeze_boundaries(
    session,
    revision_id: str,
    snapshot: dict,
    *,
    segments: Iterable[m.GenerationSegment] | None = None,
) -> None:
    """Record each block's boundary, pause and (when resolved) end voices in ``snapshot``."""
    record = _audiobook_record(session, revision_id)
    if record is None:
        return
    frozen = snapshot.get("performance_snapshot") or {}
    characters = (
        frozen["character_dictionary"]
        if "character_dictionary" in frozen
        else get_generation_controls(session, record.id)["characters"]
    )
    annotations = frozen.get("annotations") or {}
    review_ids: set[str] = set(frozen.get("review_segment_ids") or ())
    # A review block has no frozen entry, so its own markup is read; like the
    # cast freeze, read it with the current characters, loaded once if needed.
    current_characters: list[dict] | None = None
    result: dict[str, dict[str, Any]] = {}
    voices: dict[str, list[str]] = {}
    # One catalogue for this freeze; cast spans would otherwise each rebuild it.
    service_config_cache: dict = {}
    audio = snapshot.get("audio") or {}
    voice_gap = int(audio.get("voice_change_silence_ms") or 0)
    rows = (
        list(segments)
        if segments is not None
        else list(
            session.scalars(
                select(m.GenerationSegment)
                .where(
                    m.GenerationSegment.plan_revision_id == revision_id,
                    m.GenerationSegment.removed.is_(False),
                )
                .order_by(m.GenerationSegment.ordinal)
            )
        )
    )
    blocks = [BoundaryBlock.from_row(row) for row in rows]
    before_marker = (
        blocks_before_chapter_marker(session, revision_id, blocks)
        if _setting_ms(audio, "chapter_silence_ms")
        else set()
    )
    for block in blocks:
        xml = _block_markup(block, annotations)
        boundary, text_hash = None, None
        if xml:
            markup_characters = characters
            if block.id in review_ids:
                if current_characters is None:
                    current_characters = list(
                        get_generation_controls(session, record.id)["characters"]
                    )
                markup_characters = current_characters
            boundary, text_hash = _markup_boundary(block, xml, markup_characters)
        entry: dict[str, Any] = {
            "text_hash": text_hash,
            "boundary_after": boundary,
            "silence_after_ms": block_pause(
                block, boundary, before_marker=block.id in before_marker, audio=audio
            ),
        }
        if voice_gap and block.node_kind != "subtitle_cue":
            voices[block.id] = entry["voices"] = _block_voices(
                snapshot, block, review_ids, service_config_cache
            )
        result[block.id] = entry
    _raise_voice_changes(result, blocks, voices, voice_gap)
    snapshot["speech_boundaries"] = result


def resolve_run_boundaries(
    session,
    revision_id: str,
    run_snapshot: Mapping[str, Any],
    audio: Mapping[str, Any],
    *,
    segments: Iterable[m.GenerationSegment] | None = None,
    run_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """A run's ``speech_boundaries`` with every pause length from ``audio``.

    A run keeps from its start only the facts of each boundary: the boundary a
    block ends on, the text hash that guards it and, when they were resolved, the
    voices at its ends. Assembly, the export freshness check and the playlist with
    a run resolve the lengths from those facts, the block's own data and the
    current settings through the rules the run-start freeze uses, so a pause
    setting change reaches the re-assembly of an existing run and no take changes.

    A block the run did not freeze is read from its own markup with the run's
    frozen characters. In a run limited to selected blocks, markup that does not
    resolve leaves such a block markup-free (the run never read it). In a legacy run,
    which froze no boundaries, only a speaker absent from the run's characters does;
    any other markup error is raised, as the freeze would. Missing voices are
    rendered from the run's frozen cast only while the voice-change minimum is on.
    ``segments`` restricts the result to those rows; otherwise every non-removed
    block of the revision is read in one query. With ``run_id``, rendered voices are
    kept in process (``_cached_block_voices``), so the next check of that run does
    not render them again.
    """
    frozen_entries = run_snapshot.get("speech_boundaries")
    legacy_run = not isinstance(frozen_entries, Mapping)
    if not isinstance(frozen_entries, Mapping):
        frozen_entries = {}
    if _audiobook_record(session, revision_id) is None:
        return deepcopy(dict(frozen_entries))
    blocks = (
        [BoundaryBlock.from_row(row) for row in segments]
        if segments is not None
        else _revision_blocks(session, revision_id)
    )
    performance = run_snapshot.get("performance_snapshot")
    if not isinstance(performance, Mapping):
        performance = {}
    characters = performance.get("character_dictionary")
    # The parser expects a catalogue list; an empty list keeps the current cast out.
    characters = list(characters) if isinstance(characters, list) else []
    annotations = performance.get("annotations")
    if not isinstance(annotations, Mapping):
        annotations = {}
    review_ids: set[str] = set(performance.get("review_segment_ids") or ())
    voice_gap = _setting_ms(audio, "voice_change_silence_ms")
    before_marker = (
        blocks_before_chapter_marker(session, revision_id, blocks)
        if _setting_ms(audio, "chapter_silence_ms")
        else set()
    )
    render_snapshot = dict(run_snapshot)
    service_config_cache: dict = {}
    result: dict[str, dict[str, Any]] = {}
    voices: dict[str, list[str]] = {}
    for block in blocks:
        frozen = frozen_entries.get(block.id)
        known_voices = None
        if isinstance(frozen, Mapping):
            boundary = frozen.get("boundary_after")
            text_hash = frozen.get("text_hash")
            known_voices = frozen.get("voices")
        else:
            boundary, text_hash = None, None
            xml = _block_markup(block, annotations)
            if xml:
                try:
                    boundary, text_hash = _markup_boundary(block, xml, characters)
                except ValueError as error:
                    # A limited run never read this block, so markup it cannot
                    # resolve (a removed speaker, a stale plan) leaves it markup-free.
                    # A legacy run read every block: only a speaker absent from its
                    # characters is tolerated, as the playlist always did.
                    if legacy_run and not _unresolved_speaker(error):
                        raise
                    boundary, text_hash = None, None
        entry: dict[str, Any] = {
            "text_hash": text_hash,
            "boundary_after": boundary,
            "silence_after_ms": block_pause(
                block, boundary, before_marker=block.id in before_marker, audio=audio
            ),
        }
        if isinstance(known_voices, list) and len(known_voices) == 2:
            voices[block.id] = entry["voices"] = [str(value) for value in known_voices]
        elif voice_gap and block.node_kind != "subtitle_cue":
            rendered = _cached_block_voices(
                run_id, render_snapshot, block, review_ids, service_config_cache
            )
            # None: its voices cannot be rendered from the run (text changed since,
            # which assembly refuses on its own), so no voice-change minimum here.
            if rendered is not None:
                voices[block.id] = entry["voices"] = rendered
        result[block.id] = entry
    if voice_gap:
        _raise_voice_changes(result, blocks, voices, voice_gap)
    return result


def assembly_pause(segment, snapshot: dict) -> int:
    entry = (snapshot.get("speech_boundaries") or {}).get(segment.id)
    if entry is None:
        return max(0, int(segment.silence_after_ms or 0))
    if entry.get("text_hash") is not None and entry["text_hash"] != content_hash(
        segment.optimized_text or segment.text
    ):
        raise ValueError("Speech text changed after assembly boundaries were frozen.")
    return max(0, int(entry["silence_after_ms"]))

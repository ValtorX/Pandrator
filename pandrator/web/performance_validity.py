"""Judge an adopted speaker plan per segment instead of as a whole.

A plan's ``base_signature`` covers every segment of its revision, so one edit
makes the whole plan stale. Each plan unit also stores its own ``text_hash``:
an unchanged segment keeps its entry, an edited one keeps its markup when the
markup still fits the new text, and only the rest need a new review.

The adopted plan itself is never written: fitted entries are derived whenever a
snapshot is frozen or a plan is read.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from pandrator.logic.speech_performance import content_hash, validate_annotation

from . import models as m
from .performance_annotations import plan_annotations
from .speech_annotation_records import normalized_record, record_annotation, record_markup

# Elements whose own text is spoken. Events and delivery controls only
# contribute their tail, exactly as ``speech_markup._extract`` reads them.
_SPOKEN_TEXT_TAGS = frozenset({"segment", "dialogue", "speaker", "narrator", "span"})
_DIALOGUE_TAGS = frozenset({"dialogue", "speaker"})

REVIEW_MESSAGE = (
    "This block changed after its speaker plan was adopted and its speaker markup "
    "no longer fits the new text. Review a new speaker plan before generating it."
)


@dataclass(frozen=True, slots=True)
class PlanValidity:
    """Per-segment state of one speaker plan against its revision's segments.

    ``entries`` holds the frozen snapshot entry (``text_hash`` and ``annotation``)
    of every segment that keeps one: unchanged segments and fitted edits.
    ``changed_ids`` lists segments whose spoken text differs from the plan's
    (fitted, review, removed, blanked, or spoken now with no unit). ``review``
    lists ``(ordinal, id)`` of spoken segments that have no usable entry, in
    ordinal order.
    """

    entries: dict[str, dict[str, Any]]
    changed_ids: tuple[str, ...]
    review: tuple[tuple[int, str], ...]

    @property
    def review_ids(self) -> tuple[str, ...]:
        return tuple(segment_id for _ordinal, segment_id in self.review)


def _changed_range(old: str, new: str) -> tuple[int, int, str]:
    """Return the old range ``[start, end)`` and the text that replaces it."""

    limit = min(len(old), len(new))
    prefix = 0
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    limit -= prefix
    while suffix < limit and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    return prefix, len(old) - suffix, new[prefix : len(new) - suffix]


def _text_slots(
    root: ET.Element,
) -> tuple[str, list[tuple[ET.Element, str, int, int, bool]]]:
    """List every non-empty spoken string as (owner, attribute, start, end, dialogue).

    ``dialogue`` is the string's voice scope as ``speech_markup._scope_for``
    reads it: inside a ``dialogue`` or ``speaker`` element, unless a nearer
    ``narrator`` resets it.
    """

    slots: list[tuple[ET.Element, str, int, int, bool]] = []
    offset = 0

    def add(owner: ET.Element, attribute: str, value: str | None, dialogue: bool) -> None:
        nonlocal offset
        if not value:
            return
        slots.append((owner, attribute, offset, offset + len(value), dialogue))
        offset += len(value)

    def walk(element: ET.Element, inherited: bool) -> None:
        if element.tag not in _SPOKEN_TEXT_TAGS:
            return
        if element.tag == "narrator":
            dialogue = False
        elif element.tag in _DIALOGUE_TAGS:
            dialogue = True
        else:
            dialogue = inherited
        add(element, "text", element.text, dialogue)
        for child in element:
            walk(child, dialogue)
            add(child, "tail", child.tail, dialogue)

    walk(root, False)
    transcript = "".join(
        str(getattr(owner, attribute)) for owner, attribute, *_rest in slots
    )
    return transcript, slots


def _fit_markup(xml: str, old: str, new: str, segment_id: str) -> str | None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    transcript, slots = _text_slots(root)
    if transcript != old:
        return None
    start, end, replacement = _changed_range(old, new)
    # A pure insertion where two strings meet is ambiguous: it could belong to
    # either scope. A change across two strings crosses a span or event boundary.
    matches = [slot for slot in slots if slot[2] <= start and end <= slot[3]]
    if len(matches) != 1:
        return None
    owner, attribute, slot_start, slot_end, dialogue = matches[0]
    # Text added or removed at the edge of a spoken line could be the
    # narration around it (an attribution after a closing quote, say), so a
    # change that reaches a dialogue string's edge needs review.
    if dialogue and (start == slot_start or end == slot_end):
        return None
    value = str(getattr(owner, attribute))
    setattr(
        owner,
        attribute,
        value[: start - slot_start] + replacement + value[end - slot_start :],
    )
    root.attrib["id"] = segment_id
    # ElementTree writes a carriage return in text raw, and a reparse would
    # read it as a line feed; keep it as the character reference the canonical
    # markup uses.
    return ET.tostring(root, encoding="unicode").replace("\r", "&#13;")


def fit_entry(
    unit: Mapping[str, Any],
    record: Mapping[str, Any] | None,
    spoken_text: str,
    segment_id: str,
    characters: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Rebase one plan entry onto a segment's new spoken text, or return None.

    The entry fits when the change lies inside one spoken string of its markup
    and the rebased markup parses against the new text. A legacy (non-XML)
    annotation fits when it still validates against the new text.
    """

    if record is None:
        return None
    old = str(unit.get("spoken_text") or "")
    xml = record_markup(record)
    if xml is None:
        try:
            return validate_annotation(spoken_text, record_annotation(record) or {})
        except ValueError:
            return None
    if old == spoken_text:
        candidate: str | None = xml
    else:
        candidate = _fit_markup(xml, old, spoken_text, segment_id)
    if candidate is None:
        return None
    locked = record.get("locked", False)
    reason = record.get("reason") or ""
    try:
        return normalized_record(
            spoken_text,
            segment_id,
            {
                "speech_xml": candidate,
                "locked": locked if isinstance(locked, bool) else False,
                "reason": reason if isinstance(reason, str) else "",
            },
            characters,
        )
    except ValueError:
        return None


def plan_characters(plan: m.PerformancePlan) -> list[dict[str, Any]]:
    value = (plan.settings_json or {}).get("character_dictionary") or []
    return deepcopy(value) if isinstance(value, list) else []


def plan_validity(
    session,
    plan: m.PerformancePlan,
    revision_id: str,
    *,
    annotations: Mapping[str, Mapping[str, Any]] | None = None,
    counts_only: bool = False,
) -> PlanValidity:
    """Judge every plan entry against the revision's current segments.

    ``counts_only`` leaves ``entries`` empty, for a plan read that needs only
    the changed and review segments.
    """

    if annotations is None:
        annotations = plan_annotations(session, plan)
    characters = plan_characters(plan)
    units = {str(unit["id"]): unit for unit in plan.units_json}
    rows = session.execute(
        select(
            m.GenerationSegment.id,
            m.GenerationSegment.ordinal,
            m.GenerationSegment.text,
            m.GenerationSegment.optimized_text,
            m.GenerationSegment.removed,
        )
        .where(m.GenerationSegment.plan_revision_id == revision_id)
        .order_by(m.GenerationSegment.ordinal)
    ).all()
    entries: dict[str, dict[str, Any]] = {}
    changed: list[str] = []
    review: list[tuple[int, str]] = []
    seen: set[str] = set()
    for segment_id, ordinal, text, optimized_text, removed in rows:
        unit = units.get(segment_id)
        if unit is not None:
            seen.add(segment_id)
        spoken = optimized_text or text or ""
        if removed or not spoken.strip():
            if unit is not None:
                changed.append(segment_id)
            continue
        if unit is None:
            # Blank when the plan was made, spoken now: its text changed too.
            changed.append(segment_id)
            review.append((int(ordinal), segment_id))
            continue
        record = annotations.get(segment_id)
        text_hash = content_hash(spoken)
        if unit.get("text_hash") == text_hash:
            if record is not None and not counts_only:
                entries[segment_id] = {
                    "text_hash": text_hash,
                    "annotation": deepcopy(dict(record)),
                }
            continue
        changed.append(segment_id)
        fitted = fit_entry(unit, record, spoken, segment_id, characters)
        if fitted is None:
            review.append((int(ordinal), segment_id))
        elif not counts_only:
            entries[segment_id] = {"text_hash": text_hash, "annotation": fitted}
    changed.extend(segment_id for segment_id in units if segment_id not in seen)
    return PlanValidity(entries, tuple(changed), tuple(review))


__all__ = [
    "REVIEW_MESSAGE",
    "PlanValidity",
    "fit_entry",
    "plan_characters",
    "plan_validity",
]

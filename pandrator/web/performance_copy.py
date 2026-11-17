"""Copy a speaker plan's entries onto another revision of the same speech plan.

The source plan's segments are mapped to the target revision through the
lineage of their common ancestor, so a plan on a sibling revision (the one
active before a restore, say) is reached as well as one on an ancestor. Each
mapped entry is then judged against the target segment's current text with the
per-segment rule (``performance_validity.fit_entry``): an unchanged entry is
copied with its root id rebound, a changed one only while its markup still
fits. The source plan is never written.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from . import models as m
from .generation_cast_runtime import remap_markup
from .performance_annotations import plan_annotations
from .performance_carry_over import rebind_markup
from .performance_validity import fit_entry, plan_characters
from .speech_annotation_records import SPEECH_XML_KEY, normalized_record

MAX_LINEAGE_STEPS = 100
NO_COMMON_ANCESTOR = (
    "The performance source shares no ancestor with this speech plan within "
    f"{MAX_LINEAGE_STEPS} revisions."
)
# Unit fields an entry depends on besides its text, compared as the source plan
# recorded them with the target unit's. Across revisions the segment rows are
# compared too (voice reference, node kind, pause, paragraph break, and markup
# when the text is equal).
_UNIT_FIELDS = ("speaker", "voice", "language", "node_kind", "paragraph_break_after")
_COMPARISON_ID = "comparison"

Lineage = Mapping[str, Sequence[str]]


@dataclass(frozen=True, slots=True)
class PlanCopy:
    """Seeded records keyed by target segment id, and what was left behind.

    ``copied + skipped`` is the number of the source plan's annotation records.
    """

    entries: dict[str, dict[str, Any]]
    copied: int
    skipped: int


def _walk_parent(revision: m.GenerationPlanRevision) -> str | None:
    """A restore descends from its target; every other revision from its parent."""

    operation = revision.operation_json or {}
    if operation.get("action") == "restore":
        return operation.get("target_revision_id") or None
    return revision.parent_revision_id


def _ancestry(
    session, start: str, until: Mapping[str, int] | None = None
) -> tuple[list[tuple[str, Lineage]], bool]:
    """Walk up from ``start``: each revision with the lineage into it.

    A revision's lineage maps its walk parent's segment ids to its own. The walk
    stops at a revision in ``until`` (returning True), at a revision with no
    parent, at a cycle, or after ``MAX_LINEAGE_STEPS`` steps.
    """

    chain: list[tuple[str, Lineage]] = []
    seen: set[str] = set()
    cursor: str | None = start
    while cursor is not None and cursor not in seen and len(chain) <= MAX_LINEAGE_STEPS:
        revision = session.get(m.GenerationPlanRevision, cursor)
        if revision is None:
            break
        seen.add(cursor)
        lineage = (revision.operation_json or {}).get("lineage")
        chain.append((cursor, lineage if isinstance(lineage, Mapping) else {}))
        if until is not None and cursor in until:
            return chain, True
        cursor = _walk_parent(revision)
    return chain, False


def _one_to_one(chain: list[tuple[str, Lineage]], ancestor_index: int) -> dict[str, str] | None:
    """Map the ancestor's segment ids to ``chain[0]``'s, keeping only 1:1 pairs.

    ``None`` means the ancestor is ``chain[0]`` itself (the identity map).
    """

    if ancestor_index == 0:
        return None
    mapping = {
        old: list(dict.fromkeys(children))
        for old, children in chain[ancestor_index - 1][1].items()
    }
    for index in range(ancestor_index - 2, -1, -1):
        lineage = chain[index][1]
        mapping = {
            old: list(
                dict.fromkeys(child for parent in ids for child in lineage.get(parent, ()))
            )
            for old, ids in mapping.items()
        }
    counts = Counter(child for ids in mapping.values() for child in ids)
    return {
        old: ids[0]
        for old, ids in mapping.items()
        if len(ids) == 1 and counts[ids[0]] == 1
    }


def map_segments(session, source_revision_id: str, target_revision_id: str):
    """Return a function mapping a source segment id to its target id, or None.

    Raises ``ValueError`` when the two revisions share no ancestor within
    ``MAX_LINEAGE_STEPS`` steps of either.
    """

    if source_revision_id == target_revision_id:
        return lambda segment_id: segment_id
    # Stop at the source when it is an ancestor; walk the whole bound only when not.
    target_chain, found = _ancestry(session, target_revision_id, {source_revision_id: 0})
    if found:
        down = _one_to_one(target_chain, len(target_chain) - 1)
        up = None
    else:
        target_index = {
            revision_id: index for index, (revision_id, _) in enumerate(target_chain)
        }
        source_chain, found = _ancestry(session, source_revision_id, target_index)
        if not found:
            raise ValueError(NO_COMMON_ANCESTOR)
        down = _one_to_one(target_chain, target_index[source_chain[-1][0]])
        up = _one_to_one(source_chain, len(source_chain) - 1)
    # 1:1 pairs are unique on both sides, so the inversion loses nothing.
    source_to_ancestor = None if up is None else {new: old for old, new in up.items()}

    def target_of(segment_id: str) -> str | None:
        ancestor_id = (
            segment_id if source_to_ancestor is None else source_to_ancestor.get(segment_id)
        )
        if ancestor_id is None:
            return None
        return ancestor_id if down is None else down.get(ancestor_id)

    return target_of


_ROW_COLUMNS = (
    m.GenerationSegment.id,
    m.GenerationSegment.text,
    m.GenerationSegment.optimized_text,
    m.GenerationSegment.voice_id,
    m.GenerationSegment.node_kind,
    m.GenerationSegment.silence_after_ms,
    m.GenerationSegment.paragraph_break_after,
    m.GenerationSegment.speech_plan_json,
)


def _same_markup(left, right, characters: list[dict[str, Any]]) -> bool:
    left_xml = (left.speech_plan_json or {}).get("speech_xml") or None
    right_xml = (right.speech_plan_json or {}).get("speech_xml") or None
    if left_xml is None or right_xml is None:
        return left_xml == right_xml
    left_text = left.optimized_text or left.text
    right_text = right.optimized_text or right.text
    try:
        # Derived revisions store canonical markup, so a root-id swap usually
        # settles it; otherwise compare the canonical forms.
        if rebind_markup(left_xml, left.id, _COMPARISON_ID, left_text, characters) == (
            rebind_markup(right_xml, right.id, _COMPARISON_ID, right_text, characters)
        ):
            return True
        return remap_markup(left_xml, _COMPARISON_ID, left_text, characters) == (
            remap_markup(right_xml, _COMPARISON_ID, right_text, characters)
        )
    except ValueError:
        return False


def _same_structure(source, target, characters: list[dict[str, Any]]) -> bool:
    """The segment fields besides text that an entry depends on."""

    if (
        source.voice_id,
        source.node_kind,
        source.silence_after_ms,
        source.paragraph_break_after,
    ) != (
        target.voice_id,
        target.node_kind,
        target.silence_after_ms,
        target.paragraph_break_after,
    ):
        return False
    # Segment markup holds the text, so it can only match when the text does;
    # a changed text is judged by the entry's own fit instead.
    if (source.optimized_text or source.text) != (target.optimized_text or target.text):
        return True
    return _same_markup(source, target, characters)


def _rebound(
    record: Mapping[str, Any],
    old_id: str,
    new_id: str,
    text: str,
    characters: list[dict[str, Any]],
    same_characters: bool,
) -> dict[str, Any] | None:
    """An unchanged entry under its new id, or None when it no longer validates.

    The root id is swapped by string while the character list is the one the
    entry was validated against; otherwise the entry is checked again.
    """

    copied = deepcopy(dict(record))
    xml = copied.get(SPEECH_XML_KEY)
    if not isinstance(xml, str):
        return copied
    try:
        if same_characters:
            copied[SPEECH_XML_KEY] = rebind_markup(xml, old_id, new_id, text, characters)
            return copied
        locked = record.get("locked", False)
        reason = record.get("reason") or ""
        return normalized_record(
            text,
            new_id,
            {
                "speech_xml": rebind_markup(xml, old_id, new_id, text, characters),
                "locked": locked if isinstance(locked, bool) else False,
                "reason": reason if isinstance(reason, str) else "",
            },
            characters,
        )
    except ValueError:
        return None


def copy_plan_entries(
    session,
    source: m.PerformancePlan,
    revision_id: str,
    units: Sequence[Mapping[str, Any]],
    characters: list[dict[str, Any]],
) -> PlanCopy:
    """Seed a new plan on ``revision_id`` from ``source``'s still-valid entries.

    ``units`` are the new plan's units (current spoken text and ``text_hash``
    per target segment). An entry is copied when its segment maps 1:1 to a
    target unit; the source unit's speaker, voice, language, node kind and
    paragraph break, as recorded when the source plan was made, equal the target
    unit's; across two revisions, the two segment rows have the same voice
    reference, node kind, pause and paragraph break, and the same segment markup
    when their text is equal; and the entry's text is unchanged or its markup
    still fits the new text. An entry that no longer validates against
    ``characters`` is skipped.
    """

    target_of = map_segments(session, source.plan_revision_id, revision_id)
    annotations = plan_annotations(session, source)
    source_units = {str(unit["id"]): unit for unit in source.units_json or []}
    targets = {str(unit["id"]): unit for unit in units}
    same_characters = plan_characters(source) == characters
    # On the same revision every row is compared with itself, so none is read.
    same_revision = source.plan_revision_id == revision_id
    rows = {} if same_revision else {
        row.id: row
        for row in session.execute(
            select(*_ROW_COLUMNS).where(
                m.GenerationSegment.plan_revision_id.in_(
                    sorted({source.plan_revision_id, revision_id})
                )
            )
        )
    }

    def judged(old_id: str, record: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
        unit = source_units.get(old_id)
        target_id = target_of(old_id) if unit is not None else None
        target = targets.get(target_id) if target_id is not None else None
        if unit is None or target_id is None or target is None:
            return None
        if any(unit.get(key) != target.get(key) for key in _UNIT_FIELDS):
            return None
        if not same_revision:
            source_row, target_row = rows.get(old_id), rows.get(target_id)
            if source_row is None or target_row is None:
                return None
            if not _same_structure(source_row, target_row, characters):
                return None
        text = str(target["spoken_text"])
        if unit.get("text_hash") == target.get("text_hash"):
            copied = _rebound(record, old_id, target_id, text, characters, same_characters)
        else:
            copied = fit_entry(unit, record, text, target_id, characters)
        return None if copied is None else (target_id, copied)

    entries: dict[str, dict[str, Any]] = {}
    skipped = 0
    for old_id, record in annotations.items():
        result = judged(str(old_id), record)
        if result is None:
            skipped += 1
        else:
            entries[result[0]] = result[1]
    return PlanCopy(entries, len(entries), skipped)


__all__ = ["NO_COMMON_ANCESTOR", "PlanCopy", "copy_plan_entries", "map_segments"]

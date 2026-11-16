"""Carry an adopted speaker plan onto the revision a restore creates.

A restore (and so an edit copy) writes every segment again under a new id. The
target revision's adopted plan is written again as an adopted plan on the new
revision, with each entry as stored and each segment id mapped through the
restore's lineage. Story 2's per-segment judgement then decides, at read and
freeze time, which entries still fit; nothing is judged here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from sqlalchemy import select

from . import models as m
from .generation_cast_runtime import remap_markup
from .speech_annotation_records import SPEECH_XML_KEY

_ROOT_ID = re.compile(r"""\A(\s*<segment\b[^>]*?\bid\s*=\s*)(["'])([^"']*)\2""")


def _rebind_markup(xml: str, old_id: str, new_id: str, text: str, characters) -> str:
    """Swap the root id by string when the first start tag names ``old_id``.

    The markup already validated against ``text`` under the old id and nothing
    else in it holds a segment id, so no parse is needed; anything unusual falls
    back to ``remap_markup``.
    """

    match = _ROOT_ID.match(xml)
    if match is None or match.group(3) != old_id:
        return remap_markup(xml, new_id, text, characters)
    quote = match.group(2)
    return f"{match.group(1)}{quote}{new_id}{quote}{xml[match.end():]}"


CARRIED_FROM_PLAN_KEY = "carried_from_plan_id"
CARRIED_FROM_REVISION_KEY = "carried_from_revision_id"


def remap_plan_entries(
    units: Sequence[Mapping[str, Any]],
    annotations: Mapping[str, Mapping[str, Any]],
    id_map: Mapping[str, str],
    ordinals: Mapping[str, int],
    characters: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return plan units and annotation records rebound to new segment ids.

    Each unit takes its new ``id`` and ``ordinal``, and its ``speech_xml`` root
    is renormalized against the unit's own ``spoken_text`` (so an entry on old
    text stays on old text). XML annotation records are renormalized the same
    way; a legacy record has no id and is copied as is. An annotation with no
    unit is not carried. A unit whose id is not mapped raises ``KeyError``.
    """

    remapped_units: list[dict[str, Any]] = []
    spoken: dict[str, str] = {}
    for unit in units:
        old_id = str(unit["id"])
        new_id = id_map[old_id]
        mapped = deepcopy(dict(unit))
        mapped["id"] = new_id
        if old_id in ordinals:
            mapped["ordinal"] = ordinals[old_id]
        if isinstance(mapped.get("speech_xml"), str):
            mapped["speech_xml"] = _rebind_markup(
                mapped["speech_xml"], old_id, new_id, mapped["spoken_text"], characters
            )
        spoken[old_id] = mapped["spoken_text"]
        remapped_units.append(mapped)
    records: dict[str, dict[str, Any]] = {}
    for old_id, record in annotations.items():
        if old_id not in spoken:
            continue
        new_id = id_map[old_id]
        copied = deepcopy(dict(record))
        if isinstance(copied.get(SPEECH_XML_KEY), str):
            copied[SPEECH_XML_KEY] = _rebind_markup(
                copied[SPEECH_XML_KEY], old_id, new_id, spoken[old_id], characters
            )
        records[new_id] = copied
    return remapped_units, records


def carry_adopted_plan(
    session,
    target_revision_id: str,
    new_revision_id: str,
    lineage: Mapping[str, Sequence[str]],
) -> tuple[m.PerformancePlan, bool] | None:
    """Write the target's adopted plan onto the new revision.

    Returns the new plan and whether it is fresh (its signature matches the new
    revision), or ``None`` when the target has no adopted plan.
    """

    from .performance_annotations import plan_annotations
    from .speech_plan_context import plan_signature

    source = session.scalar(
        select(m.PerformancePlan)
        .where(
            m.PerformancePlan.plan_revision_id == target_revision_id,
            m.PerformancePlan.status == "adopted",
        )
        .order_by(m.PerformancePlan.adopted_at.desc(), m.PerformancePlan.id)
        .limit(1)
    )
    if source is None:
        return None
    id_map = {old: children[0] for old, children in lineage.items() if len(children) == 1}
    new_ordinals = {
        segment_id: ordinal
        for segment_id, ordinal in session.execute(
            select(m.GenerationSegment.id, m.GenerationSegment.ordinal).where(
                m.GenerationSegment.plan_revision_id == new_revision_id
            )
        )
    }
    ordinals = {old: new_ordinals[new] for old, new in id_map.items() if new in new_ordinals}
    settings = deepcopy(source.settings_json or {})
    characters = settings.get("character_dictionary") or []
    units, records = remap_plan_entries(
        source.units_json or [],
        plan_annotations(session, source),
        id_map,
        ordinals,
        characters if isinstance(characters, list) else [],
    )
    fresh = source.base_signature == plan_signature(session, target_revision_id)
    settings.update(
        {
            "expected_plan_revision_id": new_revision_id,
            CARRIED_FROM_PLAN_KEY: source.id,
            CARRIED_FROM_REVISION_KEY: target_revision_id,
        }
    )
    carried = m.PerformancePlan(
        session_id=source.session_id,
        plan_revision_id=new_revision_id,
        base_signature=(
            plan_signature(session, new_revision_id) if fresh else source.base_signature
        ),
        settings_json=settings,
        units_json=units,
        manual_annotations_json=records,
        status="adopted",
        version=1,
        job_id=None,
        adopted_at=m.utcnow(),
    )
    session.add(carried)
    session.flush()
    return carried, fresh

"""A restore or edit copy keeps the target revision's adopted speaker plan.

Identity and reuse are compared by ordinal, because a restore writes every
segment again under a new id. Work is counted, never timed.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import func, select

from pandrator.web import models as m
from pandrator.web import performance_carry_over as carry
from pandrator.web import speech_plan_context as context
from pandrator.web.generation_audio_identity import AudioIdentityContext
from pandrator.web.speech_annotation_records import record_markup
from pandrator.web.speech_plan_context import plan_signature
from pandrator.web.speech_plan_workspace import freeze_speech_snapshot
from tests.test_performance_plans import case as case  # noqa: F401
from tests.test_performance_plans import get
from tests.test_performance_segment_validity import (
    cast_case as cast_case,  # noqa: F401
)
from tests.test_performance_segment_validity import live_snapshot

SPLIT_CURSOR = 4


def revision_identities(case, revision_id: str) -> list[dict]:
    """Audio identities of one revision's segments, in ordinal order."""
    snapshot = live_snapshot(case)
    with case["services"]["database"].session() as session:
        context = AudioIdentityContext(session, snapshot)
        return [
            context.for_segment(session.get_one(m.GenerationSegment, segment_id))
            for segment_id in segment_ids(case, revision_id)
        ]


def active_revision(case) -> str:
    with case["services"]["database"].session() as session:
        return session.scalar(
            select(m.GenerationPlan.active_revision_id).where(
                m.GenerationPlan.session_id == case["session_id"]
            )
        )


def segment_ids(case, revision_id: str) -> list[str]:
    with case["services"]["database"].session() as session:
        return list(
            session.scalars(
                select(m.GenerationSegment.id)
                .where(m.GenerationSegment.plan_revision_id == revision_id)
                .order_by(m.GenerationSegment.ordinal)
            )
        )


def reuse_reasons(case) -> list[str]:
    items = case["services"]["generation"].list_segments(
        case["session_id"], fields=["id", "ordinal", "audio_reuse_reason"]
    )["items"]
    return [item["audio_reuse_reason"] for item in sorted(items, key=lambda i: i["ordinal"])]


def adopted_plans(case, revision_id: str) -> list[m.PerformancePlan]:
    with case["services"]["database"].session() as session:
        rows = list(
            session.scalars(
                select(m.PerformancePlan).where(
                    m.PerformancePlan.plan_revision_id == revision_id,
                    m.PerformancePlan.status == "adopted",
                )
            )
        )
        session.expunge_all()
        return rows


def signature_of(case, revision_id: str) -> str:
    with case["services"]["database"].session() as session:
        return plan_signature(session, revision_id)


def restore(case, target_revision_id: str) -> dict:
    return case["services"]["generation"].revise_topology(
        case["session_id"],
        active_revision(case),
        {"action": "restore", "target_revision_id": target_revision_id, "reason": "test"},
    )


def split_last(case, revision_id: str, **extra) -> dict:
    ids = segment_ids(case, revision_id)
    return case["services"]["generation"].revise_topology(
        case["session_id"],
        revision_id,
        {"action": "split", "segment_id": ids[2], "cursor": SPLIT_CURSOR,
         "text_layer": "display", **extra},
    )


@contextmanager
def counted_signatures():
    calls = {"signature": 0}
    real = context.plan_signature

    def counting(*args, **kwargs):
        calls["signature"] += 1
        return real(*args, **kwargs)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(context, "plan_signature", counting)
        yield calls


def edit_through_copy(case, index: int, text: str, how: str) -> None:
    """Edit one segment so that the edit is forked into a copy."""
    revision_id = active_revision(case)
    source_ids = segment_ids(case, revision_id)
    with case["services"]["database"].session() as session:
        if how == "review":
            session.add(
                m.SpeechPlanReview(
                    revision_id=revision_id, content_hash=signature_of(case, revision_id)
                )
            )
        else:
            session.add(
                m.GenerationRun(
                    session_id=case["session_id"], plan_revision_id=revision_id,
                    status="completed",
                )
            )
        revision = session.get_one(m.GenerationSegment, source_ids[index]).revision
    result = case["services"]["generation"].update_segment(
        source_ids[index], revision, {"text": text}
    )
    assert result["id"] != source_ids[index]  # a copy, not an in-place edit


@pytest.mark.parametrize("how", ["review", "run"])
def test_edit_copy_keeps_the_adopted_plan_and_every_other_reuse(cast_case, how):
    case, _plan, _before, _work = cast_case
    source_revision = case["revision_id"]
    source_units = adopted_plans(case, source_revision)[0].units_json
    edit_through_copy(case, 1, "Yet this defeat would be brief.", how)
    new_revision = active_revision(case)
    assert new_revision != source_revision
    carried = adopted_plans(case, new_revision)
    assert len(carried) == 1 and carried[0].version == 1
    assert [unit["id"] for unit in carried[0].units_json] == segment_ids(case, new_revision)
    read = get(case, {"id": carried[0].id})
    assert read["status"] == "adopted"
    assert (read["changed_segment_count"], read["review_segment_count"]) == (1, 0)
    assert reuse_reasons(case) == ["reusable", "not_completed", "reusable"]
    copied = revision_identities(case, new_revision)
    assert len(copied) == 3
    assert all("performance_error" not in value for value in copied)
    # The edited entry is fitted onto the copy's new text under the copy's id.
    edited_id = segment_ids(case, new_revision)[1]
    snapshot = live_snapshot(case)
    with case["services"]["database"].session() as session:
        freeze_speech_snapshot(session, new_revision, snapshot)
    record = snapshot["performance_snapshot"]["annotations"][edited_id]["annotation"]
    markup = record_markup(record)
    assert f'id="{edited_id}"' in markup and "Yet this defeat would be brief." in markup
    # The source plan is unchanged.
    source = adopted_plans(case, source_revision)
    assert len(source) == 1 and source[0].units_json == source_units


def test_edit_copy_whose_edit_does_not_fit_lists_only_that_segment(cast_case):
    case, _plan, _before, _work = cast_case
    edit_through_copy(case, 0, "He said, “Bah,” then left.", "review")
    new_revision = active_revision(case)
    carried = adopted_plans(case, new_revision)[0]
    new_ids = segment_ids(case, new_revision)
    read = get(case, {"id": carried.id})
    assert (read["changed_segment_count"], read["review_segment_count"]) == (1, 1)
    assert read["review_segments"] == [{"ordinal": 0, "id": new_ids[0]}]
    assert reuse_reasons(case)[1:] == ["reusable", "reusable"]
    response = case["client"].post(
        f"/api/v1/sessions/{case['session_id']}/generation-runs",
        json={"speech_plan_revision_id": new_revision},
        headers={**case["headers"], "Idempotency-Key": "carry-over-refusal"},
    )
    assert response.status_code == 409, response.get_json()
    assert new_ids[0] in response.get_json()["error"]["message"]


def test_restore_after_a_split_keeps_every_entry(cast_case):
    case, plan, before, _work = cast_case
    source_revision = case["revision_id"]
    source_identities = revision_identities(case, source_revision)
    split = split_last(case, source_revision)
    assert not adopted_plans(case, split["plan_revision_id"])  # a split carries nothing
    with counted_signatures() as calls:
        result = restore(case, source_revision)
    # One signature for the target (the fresh check), one for the new revision.
    assert calls["signature"] == 2
    assert result["review_required"]["performance"] is False
    new_revision = result["plan_revision_id"]
    carried = adopted_plans(case, new_revision)
    assert [item.id for item in carried] == [result["performance_plan_id"]]
    plan_row = carried[0]
    new_ids = segment_ids(case, new_revision)
    assert plan_row.base_signature == signature_of(case, new_revision)
    assert [unit["id"] for unit in plan_row.units_json] == new_ids
    assert [unit["ordinal"] for unit in plan_row.units_json] == [0, 1, 2]
    assert plan_row.settings_json["carried_from_plan_id"] == plan["id"]
    assert plan_row.settings_json["carried_from_revision_id"] == source_revision
    assert plan_row.settings_json["expected_plan_revision_id"] == new_revision
    assert plan_row.job_id is None
    read = get(case, {"id": plan_row.id})
    assert read["stale"] is False
    assert reuse_reasons(case) == list(before.values()) == ["reusable"] * 3
    assert revision_identities(case, new_revision) == source_identities
    source_units = adopted_plans(case, source_revision)[0].units_json
    for old, new in zip(source_units, plan_row.units_json, strict=True):
        assert new["text_hash"] == old["text_hash"]
        assert new["spoken_text"] == old["spoken_text"]
        assert ("speech_xml" in new) == ("speech_xml" in old)
    assert set(plan_row.manual_annotations_json) <= set(new_ids)
    first = case["segment_ids"][0]
    assert f'id="{new_ids[0]}"' in plan_row.units_json[0]["speech_xml"]
    assert first not in plan_row.units_json[0]["speech_xml"]
    assert len(adopted_plans(case, source_revision)) == 1


def test_restore_of_a_stale_source_carries_the_same_changes(cast_case):
    case, plan, _before, _work = cast_case
    source_revision = case["revision_id"]
    with case["services"]["database"].session() as session:
        session.get_one(m.GenerationSegment, case["segment_ids"][1]).text = (
            "Yet this defeat would be brief."
        )
    source_read = get(case, plan)
    assert (source_read["changed_segment_count"], source_read["review_segment_count"]) == (1, 0)
    source_signature = adopted_plans(case, source_revision)[0].base_signature
    with counted_signatures() as calls:
        result = restore(case, source_revision)
    assert calls["signature"] == 1  # only the target's: the plan was not fresh
    assert result["review_required"]["performance"] is True
    carried = adopted_plans(case, result["plan_revision_id"])[0]
    assert result["performance_plan_id"] == carried.id
    assert carried.base_signature == source_signature
    read = get(case, {"id": carried.id})
    assert read["stale"] is True
    assert (read["changed_segment_count"], read["review_segment_count"]) == (1, 0)
    assert [unit["id"] for unit in carried.units_json] == segment_ids(
        case, result["plan_revision_id"]
    )
    assert carried.units_json[1]["spoken_text"] == "Yet this defeat would be temporary."


def plans_on(case, revision_id: str) -> list[m.PerformancePlan]:
    with case["services"]["database"].session() as session:
        rows = list(
            session.scalars(
                select(m.PerformancePlan).where(m.PerformancePlan.plan_revision_id == revision_id)
            )
        )
        session.expunge_all()
        return rows


def test_target_without_an_adopted_plan_writes_none(case):
    from tests.test_performance_plans import create

    draft = create(case)
    superseded = create(case)
    with case["services"]["database"].session() as session:
        session.get_one(m.PerformancePlan, superseded["id"]).status = "superseded"
    before = {
        plan.id: (plan.status, plan.version, plan.units_json)
        for plan in plans_on(case, case["revision_id"])
    }
    assert {draft["id"], superseded["id"]} == set(before)
    result = restore(case, case["revision_id"])
    assert result["performance_plan_id"] is None
    assert result["review_required"]["performance"] is True
    assert not plans_on(case, result["plan_revision_id"])
    after = {
        plan.id: (plan.status, plan.version, plan.units_json)
        for plan in plans_on(case, case["revision_id"])
    }
    assert after == before


def test_restore_swaps_root_ids_without_parsing(cast_case, monkeypatch):
    case, _plan, _before, _work = cast_case
    source_revision = case["revision_id"]
    calls = []
    monkeypatch.setattr(carry, "remap_markup", lambda *args: calls.append(args))
    result = restore(case, source_revision)
    assert calls == []
    new_ids = segment_ids(case, result["plan_revision_id"])
    source = adopted_plans(case, source_revision)[0]
    carried = adopted_plans(case, result["plan_revision_id"])[0]
    for old, new in zip(source.units_json, carried.units_json, strict=True):
        assert new["speech_xml"] == old["speech_xml"].replace(
            f'id="{old["id"]}"', f'id="{new["id"]}"', 1
        )
    key = carry.SPEECH_XML_KEY
    compared = 0
    for old_id, new_id in zip(case["segment_ids"], new_ids, strict=True):
        old_record = source.manual_annotations_json.get(old_id)
        if old_record is not None and key in old_record:
            assert carried.manual_annotations_json[new_id][key] == old_record[key].replace(
                f'id="{old_id}"', f'id="{new_id}"', 1
            )
            compared += 1
    assert compared >= 1


def test_restore_carries_directions_from_completed_batches(cast_case):
    """Entries that came from analysis batches survive, folded into the new plan."""
    case, plan, before, _work = cast_case
    source_revision = case["revision_id"]
    with case["services"]["database"].session() as session:
        row = session.get_one(m.PerformancePlan, plan["id"])
        analysed = dict(row.manual_annotations_json or {})
        assert set(analysed) == set(case["segment_ids"])
        # Move every record into a completed batch, as an analysis run leaves it.
        last = session.scalar(
            select(func.max(m.PerformanceBatch.ordinal)).where(
                m.PerformanceBatch.performance_plan_id == row.id
            )
        )
        session.add(m.PerformanceBatch(
            performance_plan_id=row.id, ordinal=(last if last is not None else -1) + 1,
            segment_ids_json=list(analysed),
            status="completed", annotations_json=analysed,
        ))
        row.manual_annotations_json = {}
    source_identities = revision_identities(case, source_revision)
    assert reuse_reasons(case) == list(before.values())
    split = split_last(case, source_revision)
    result = restore(case, source_revision)
    assert split["plan_revision_id"] != result["plan_revision_id"]
    new_ids = segment_ids(case, result["plan_revision_id"])
    carried = adopted_plans(case, result["plan_revision_id"])[0]
    key = carry.SPEECH_XML_KEY
    assert set(carried.manual_annotations_json) == set(new_ids)
    for old_id, new_id in zip(case["segment_ids"], new_ids, strict=True):
        assert carried.manual_annotations_json[new_id][key] == analysed[old_id][key].replace(
            f'id="{old_id}"', f'id="{new_id}"', 1
        )
    with case["services"]["database"].session() as session:
        assert session.scalar(
            select(m.PerformanceBatch.id).where(
                m.PerformanceBatch.performance_plan_id == carried.id
            )
        ) is None
    assert revision_identities(case, result["plan_revision_id"]) == source_identities
    assert reuse_reasons(case) == ["reusable"] * 3


def test_split_merge_and_topology_batch_carry_nothing(cast_case):
    case, _plan, _before, _work = cast_case
    split = split_last(case, case["revision_id"])
    assert split["performance_plan_id"] is None
    assert not adopted_plans(case, split["plan_revision_id"])
    new_ids = segment_ids(case, split["plan_revision_id"])
    merged = case["services"]["generation"].revise_topology(
        case["session_id"], split["plan_revision_id"],
        {"action": "merge", "left_segment_id": new_ids[0], "right_segment_id": new_ids[1]},
    )
    assert not adopted_plans(case, merged["plan_revision_id"])
    current = merged["plan_revision_id"]
    response = case["client"].post(
        f"/api/v1/sessions/{case['session_id']}/generation-plan/topology/batch",
        json={"expected_revision_id": current, "operations": [{
            "action": "split", "segment_id": segment_ids(case, current)[-1],
            "cursor": SPLIT_CURSOR, "text_layer": "display",
        }]},
        headers={**case["headers"], "If-Match": f'"{current}"',
                 "Idempotency-Key": "carry-over-batch"},
    )
    assert response.status_code == 201, response.get_json()
    assert not adopted_plans(case, response.get_json()["plan_revision_id"])


def test_resegment_and_the_restore_of_its_draft_carry_nothing(cast_case):
    case, _plan, _before, _work = cast_case
    source_revision = case["revision_id"]
    service = case["services"]["generation"]
    with case["services"]["database"].immediate_session() as session:
        draft = service.revise_topology_in_session(
            session, case["session_id"], source_revision,
            {"action": "resegment", "segment_ids": [segment_ids(case, source_revision)[2]],
             "boundaries": [11]},
        )
    assert draft["is_draft"] is True and draft["performance_plan_id"] is None
    assert not adopted_plans(case, draft["plan_revision_id"])
    result = restore(case, draft["plan_revision_id"])
    assert result["performance_plan_id"] is None
    assert result["review_required"]["performance"] is True
    assert not adopted_plans(case, result["plan_revision_id"])


def test_remap_plan_entries_maps_ids_roots_and_legacy_records():
    xml = '<segment id="old">Hello there.</segment>'
    units = [
        {"id": "old", "ordinal": 4, "spoken_text": "Hello there.", "speech_xml": xml},
        {"id": "gone", "ordinal": 5, "spoken_text": "Bye.", "legacy_annotation": True},
    ]
    legacy = {"decision": "steer", "spans": []}
    annotations = {
        "old": {carry.SPEECH_XML_KEY: xml, "locked": True},
        "gone": legacy,
        "orphan": {"decision": "none"},
    }
    new_units, records = carry.remap_plan_entries(
        units, annotations, {"old": "new", "gone": "next"}, {"old": 0, "gone": 1}, []
    )
    assert [(unit["id"], unit["ordinal"]) for unit in new_units] == [("new", 0), ("next", 1)]
    assert 'id="new"' in new_units[0]["speech_xml"]
    assert 'id="new"' in records["new"][carry.SPEECH_XML_KEY]
    assert records["new"]["locked"] is True
    assert records["next"] == legacy and records["next"] is not legacy
    assert set(records) == {"new", "next"}
    assert units[0]["id"] == "old" and 'id="old"' in units[0]["speech_xml"]
    with pytest.raises(KeyError):
        carry.remap_plan_entries(units, annotations, {"old": "new"}, {}, [])

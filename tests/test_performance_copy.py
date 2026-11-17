"""``copy_from_id`` copies across restored, edit-copied and stale sources.

The source plan's segments are mapped to the active revision through their
common ancestor's lineage; each mapped entry is copied unchanged, rebased when
its text changed and still fits, or skipped. A copy seeds every valid entry in
every mode, and only the blocks left without one are batched for analysis.
Work is counted, never timed.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy

import pytest
from sqlalchemy import select

from pandrator.web import models as m
from pandrator.web import performance_carry_over as carry
from pandrator.web import performance_copy as copying
from pandrator.web.generation_controls import get_generation_controls, save_generation_controls
from pandrator.web.speech_annotation_records import SPEECH_XML_KEY, record_markup
from tests.test_performance_carry_over import (
    active_revision,
    edit_through_copy,
    restore,
    segment_ids,
    split_last,
)
from tests.test_performance_plans import adopt, create, get
from tests.test_performance_plans import case as case  # noqa: F401
from tests.test_performance_segment_validity import cast_case as cast_case  # noqa: F401
from tests.test_performance_segment_validity import edit_segment

MODES = ["manual", "llm"]
FITTING_EDIT = "Yet this defeat would be brief."
# Crosses the end of the dialogue, so the entry needs a new review.
NON_FITTING_EDIT = "He said, “Bah,” then left."


def copy(case, source_id: str, mode: str, **extra) -> dict:
    """Create a ``copy_from_id`` draft on the active revision."""
    case["revision_id"] = active_revision(case)
    options = {"mode": mode, "copy_from_id": source_id, "annotation_format": "xml", **extra}
    if mode == "llm":
        options["model_name"] = "local/test"
    plan = create(case, **options)
    # An llm copy queues analysis only when it left blocks to analyse.
    assert bool(plan["job_id"]) == (mode == "llm" and bool(batched(case, plan["id"])))
    if plan["job_id"]:
        # The queued analysis never runs here; free the session for the next step.
        with case["services"]["database"].session() as session:
            session.get_one(m.Job, plan["job_id"]).status = "canceled"
    return plan


def seeded(case, plan_id: str) -> dict[str, dict]:
    with case["services"]["database"].session() as session:
        return deepcopy(session.get_one(m.PerformancePlan, plan_id).manual_annotations_json)


def batched(case, plan_id: str) -> list[str]:
    with case["services"]["database"].session() as session:
        return [
            segment_id
            for ids in session.scalars(
                select(m.PerformanceBatch.segment_ids_json)
                .where(m.PerformanceBatch.performance_plan_id == plan_id)
                .order_by(m.PerformanceBatch.ordinal)
            )
            for segment_id in ids
        ]


def rebound(record: dict, old_id: str, new_id: str) -> dict:
    result = deepcopy(record)
    result[SPEECH_XML_KEY] = record[SPEECH_XML_KEY].replace(f'id="{old_id}"', f'id="{new_id}"', 1)
    return result


def counts(plan: dict) -> tuple[int, int]:
    return plan["copied_count"], plan["skipped_count"]


def plan_snapshot(case, plan_id: str):
    with case["services"]["database"].session() as session:
        row = session.get_one(m.PerformancePlan, plan_id)
        return row.status, row.version, deepcopy(row.units_json), deepcopy(
            row.manual_annotations_json
        )


@pytest.mark.parametrize("mode", MODES)
def test_fresh_source_onto_its_restore_copies_every_entry(cast_case, mode, monkeypatch):
    case, plan, _before, _work = cast_case
    source_revision = case["revision_id"]
    records = seeded(case, plan["id"])
    before = plan_snapshot(case, plan["id"])
    split_last(case, source_revision)
    restored = restore(case, source_revision)["plan_revision_id"]
    parses = []
    monkeypatch.setattr(copying, "remap_markup", lambda *args: parses.append(args))
    monkeypatch.setattr(carry, "remap_markup", lambda *args: parses.append(args))
    copied = copy(case, plan["id"], mode)
    # Unchanged entries are rebound by string, never parsed again.
    assert parses == []
    assert copied["plan_revision_id"] == restored
    assert counts(copied) == (3, 0)
    new_ids = segment_ids(case, restored)
    assert seeded(case, copied["id"]) == {
        new: rebound(records[old], old, new)
        for old, new in zip(case["segment_ids"], new_ids, strict=True)
    }
    assert batched(case, copied["id"]) == []
    assert copied["job_id"] is None  # nothing pending, so no analysis job either
    assert get(case, copied)["copied_count"] == 3
    assert plan_snapshot(case, plan["id"]) == before


@pytest.mark.parametrize("mode", MODES)
def test_sibling_source_is_mapped_through_the_common_ancestor(cast_case, mode):
    case, plan, _before, _work = cast_case
    source_revision = case["revision_id"]
    split = split_last(case, source_revision)["plan_revision_id"]
    # An adopted plan on the split revision: the revision active before the restore.
    case["revision_id"] = split
    sibling = adopt(case, create(case, copy_from_id=plan["id"], annotation_format="xml"))
    split_ids = segment_ids(case, split)
    sibling_records = seeded(case, sibling["id"])
    assert set(sibling_records) == set(split_ids)
    before = plan_snapshot(case, sibling["id"])
    restored = restore(case, source_revision)["plan_revision_id"]
    copied = copy(case, sibling["id"], mode)
    new_ids = segment_ids(case, restored)
    # Blocks 1 and 2 are unchanged since the common ancestor; the split halves
    # of block 3 have no 1:1 counterpart on the restored revision.
    assert counts(copied) == (2, 2)
    assert seeded(case, copied["id"]) == {
        new_ids[index]: rebound(sibling_records[split_ids[index]], split_ids[index], new_ids[index])
        for index in (0, 1)
    }
    assert batched(case, copied["id"]) == [new_ids[2]]
    assert plan_snapshot(case, sibling["id"]) == before


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("how", ["review", "run"])
def test_edit_copy_copies_unchanged_and_rebases_a_fitting_edit(cast_case, mode, how):
    case, plan, _before, _work = cast_case
    records = seeded(case, plan["id"])
    edit_through_copy(case, 1, FITTING_EDIT, how)
    copy_revision = active_revision(case)
    copied = copy(case, plan["id"], mode)
    new_ids = segment_ids(case, copy_revision)
    assert counts(copied) == (3, 0)
    entries = seeded(case, copied["id"])
    for index in (0, 2):
        old, new = case["segment_ids"][index], new_ids[index]
        assert entries[new] == rebound(records[old], old, new)
    edited = record_markup(entries[new_ids[1]])
    assert edited is not None
    assert f'id="{new_ids[1]}"' in edited and FITTING_EDIT in edited
    assert batched(case, copied["id"]) == []


@pytest.mark.parametrize("mode", MODES)
def test_edit_copy_skips_an_edit_that_no_longer_fits(cast_case, mode):
    case, plan, _before, _work = cast_case
    edit_through_copy(case, 0, NON_FITTING_EDIT, "review")
    copy_revision = active_revision(case)
    copied = copy(case, plan["id"], mode)
    new_ids = segment_ids(case, copy_revision)
    assert counts(copied) == (2, 1)
    assert set(seeded(case, copied["id"])) == set(new_ids[1:])
    assert batched(case, copied["id"]) == [new_ids[0]]


@pytest.mark.parametrize("mode", MODES)
def test_stale_source_is_copied_onto_its_revision_and_its_restore(cast_case, mode):
    case, plan, _before, _work = cast_case
    source_revision = case["revision_id"]
    edit_segment(case, 1, FITTING_EDIT)
    edit_segment(case, 0, NON_FITTING_EDIT)
    assert get(case, plan)["stale"] is True
    before = plan_snapshot(case, plan["id"])
    # Same revision: no 409.
    in_place = copy(case, plan["id"], mode)
    assert in_place["plan_revision_id"] == source_revision
    assert counts(in_place) == (2, 1)
    entries = seeded(case, in_place["id"])
    assert set(entries) == set(case["segment_ids"][1:])
    assert FITTING_EDIT in record_markup(entries[case["segment_ids"][1]])
    assert batched(case, in_place["id"]) == [case["segment_ids"][0]]
    # Its restore carries the stale plan; copying the source onto it judges the same way.
    restored = restore(case, source_revision)["plan_revision_id"]
    onto_restore = copy(case, plan["id"], mode)
    new_ids = segment_ids(case, restored)
    assert counts(onto_restore) == (2, 1)
    assert set(seeded(case, onto_restore["id"])) == set(new_ids[1:])
    assert batched(case, onto_restore["id"]) == [new_ids[0]]
    assert plan_snapshot(case, plan["id"]) == before


def topology_batch_split(case, revision_id: str) -> str:
    response = case["client"].post(
        f"/api/v1/sessions/{case['session_id']}/generation-plan/topology/batch",
        json={"expected_revision_id": revision_id, "operations": [{
            "action": "split", "segment_id": segment_ids(case, revision_id)[-1],
            "cursor": 4, "text_layer": "display",
        }]},
        headers={**case["headers"], "If-Match": f'"{revision_id}"',
                 "Idempotency-Key": f"copy-batch-{revision_id}"},
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["plan_revision_id"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("operation", ["split", "merge", "batch"])
def test_split_merge_and_batch_copy_only_one_to_one_blocks(cast_case, mode, operation):
    case, plan, _before, _work = cast_case
    records = seeded(case, plan["id"])
    source_revision = case["revision_id"]
    old = case["segment_ids"]
    if operation == "split":
        revision = split_last(case, source_revision)["plan_revision_id"]
    elif operation == "merge":
        revision = case["services"]["generation"].revise_topology(
            case["session_id"], source_revision,
            {"action": "merge", "left_segment_id": old[0], "right_segment_id": old[1]},
        )["plan_revision_id"]
    else:
        revision = topology_batch_split(case, source_revision)
    copied = copy(case, plan["id"], mode)
    new_ids = segment_ids(case, revision)
    if operation == "merge":
        # Blocks 1 and 2 became one block; block 3 is unchanged.
        assert counts(copied) == (1, 2)
        assert seeded(case, copied["id"]) == {new_ids[1]: rebound(records[old[2]], old[2], new_ids[1])}
        assert batched(case, copied["id"]) == [new_ids[0]]
    else:
        # Block 3 became two blocks; blocks 1 and 2 are unchanged.
        assert counts(copied) == (2, 1)
        assert seeded(case, copied["id"]) == {
            new_ids[index]: rebound(records[old[index]], old[index], new_ids[index])
            for index in (0, 1)
        }
        assert batched(case, copied["id"]) == new_ids[2:]


def test_unrelated_source_is_refused(cast_case):
    case, plan, _before, _work = cast_case
    # A fresh prepare starts a revision with no parent and no lineage.
    case["services"]["generation"].create_plan(
        case["session_id"], source_revision_id=None, settings={},
        segments=[{"text": "Another book.", "speaker": "A"}],
    )
    case["revision_id"] = active_revision(case)
    response = case["post"](
        "", {"expected_plan_revision_id": case["revision_id"], "copy_from_id": plan["id"]}
    )
    assert response.status_code == 422, response.get_json()
    assert copying.NO_COMMON_ANCESTOR in str(response.get_json())


def test_the_ancestor_search_is_bounded(cast_case, monkeypatch):
    case, plan, _before, _work = cast_case
    source_revision = case["revision_id"]
    split = split_last(case, source_revision)["plan_revision_id"]
    case["revision_id"] = split
    sibling = adopt(case, create(case, copy_from_id=plan["id"], annotation_format="xml"))
    restore(case, source_revision)
    case["revision_id"] = active_revision(case)
    # One step up from each side reaches the common ancestor; none does not.
    monkeypatch.setattr(copying, "MAX_LINEAGE_STEPS", 0)
    response = case["post"](
        "", {"expected_plan_revision_id": case["revision_id"], "copy_from_id": sibling["id"]}
    )
    assert response.status_code == 422, response.get_json()
    monkeypatch.setattr(copying, "MAX_LINEAGE_STEPS", 1)
    assert counts(copy(case, sibling["id"], "manual")) == (2, 2)


def test_stale_source_onto_its_revision_skips_blocks_whose_kind_or_break_changed(cast_case):
    case, plan, _before, _work = cast_case
    with case["services"]["database"].session() as session:
        session.get_one(m.GenerationSegment, case["segment_ids"][2]).node_kind = "heading"
        row = session.get_one(m.GenerationSegment, case["segment_ids"][0])
        row.paragraph_break_after = not row.paragraph_break_after
    assert get(case, plan)["stale"] is True
    copied = copy(case, plan["id"], "manual")
    assert counts(copied) == (1, 2)
    assert set(seeded(case, copied["id"])) == {case["segment_ids"][1]}


@contextmanager
def counted_lookups():
    """Revisions loaded by the ancestry walks and segment-row queries made."""
    calls = {"revisions": 0, "row_queries": 0}
    walk, query = copying._ancestry, copying.select

    def ancestry(*args, **kwargs):
        chain, found = walk(*args, **kwargs)
        calls["revisions"] += len(chain)
        return chain, found

    def select(*args, **kwargs):
        calls["row_queries"] += 1
        return query(*args, **kwargs)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(copying, "_ancestry", ancestry)
        patcher.setattr(copying, "select", select)
        yield calls


def test_same_revision_and_ancestor_copies_do_bounded_work(cast_case):
    case, plan, _before, _work = cast_case
    # Same revision (speech selection's copy): no walk and no row query.
    with counted_lookups() as calls:
        copied = copy(case, plan["id"], "manual")
    assert counts(copied) == (3, 0)
    assert calls == {"revisions": 0, "row_queries": 0}
    # An ancestor one step up: the target walk stops at it.
    split_last(case, case["revision_id"])
    with counted_lookups() as calls:
        copied = copy(case, plan["id"], "manual")
    assert counts(copied) == (2, 1)
    assert calls == {"revisions": 2, "row_queries": 1}


def test_an_entry_naming_a_removed_character_is_skipped(cast_case):
    case, plan, _before, _work = cast_case
    records = seeded(case, plan["id"])
    with case["services"]["database"].session() as session:
        revision = get_generation_controls(session, case["session_id"])["revision"]
        save_generation_controls(
            session, case["session_id"], expected_revision=int(revision),
            characters=[], cast={"narrator": {"voice": "Kore"}, "characters": {}},
        )
    # A pSSML draft, so the adopted plan's markup is not re-read as the starting point.
    case["revision_id"] = active_revision(case)
    copied = create(case, copy_from_id=plan["id"])
    # The dialogue entry names the removed speaker; the plain entries still validate.
    assert counts(copied) == (2, 1)
    entries = seeded(case, copied["id"])
    assert set(entries) == set(case["segment_ids"][1:])
    for segment_id in case["segment_ids"][1:]:
        assert record_markup(entries[segment_id]) == record_markup(records[segment_id])


def lock_adopted_entries(case, plan_id: str, indexes) -> None:
    with case["services"]["database"].session() as session:
        row = session.get_one(m.PerformancePlan, plan_id)
        records = deepcopy(row.manual_annotations_json)
        for index in indexes:
            records[case["segment_ids"][index]]["locked"] = True
        row.manual_annotations_json = records


def test_manual_draft_without_a_copy_starts_from_a_stale_adopted_plan(cast_case):
    case, plan, _before, _work = cast_case
    edit_segment(case, 1, FITTING_EDIT)
    assert get(case, plan)["stale"] is True
    draft = create(case, annotation_format="xml")
    assert (draft["copied_count"], draft["skipped_count"]) == (None, None)
    with case["services"]["database"].session() as session:
        units = session.get_one(m.PerformancePlan, draft["id"]).units_json
        adopted = session.get_one(m.PerformancePlan, plan["id"]).manual_annotations_json
    markup = [unit["speech_xml"] for unit in units]
    # Every block's markup comes from the adopted plan: the dialogue entry as
    # adopted, and the edited block rebased onto its new text.
    assert markup[0] == record_markup(adopted[case["segment_ids"][0]])
    assert "<dialogue>" in markup[0]
    assert f'id="{case["segment_ids"][1]}"' in markup[1] and FITTING_EDIT in markup[1]
    assert markup[2] == record_markup(adopted[case["segment_ids"][2]])
    assert seeded(case, draft["id"]) == {}


@pytest.mark.parametrize("mode", ["passive", "llm"])
def test_reanalysis_without_a_copy_keeps_valid_locked_entries(cast_case, mode):
    case, plan, _before, _work = cast_case
    lock_adopted_entries(case, plan["id"], (0, 1))
    edit_segment(case, 1, FITTING_EDIT)
    edit_segment(case, 0, NON_FITTING_EDIT)
    options = {"mode": mode, "annotation_format": "xml"}
    if mode == "llm":
        options["model_name"] = "local/test"
    draft = create(case, **options)
    entries = seeded(case, draft["id"])
    # Block 1 no longer fits and block 3 is not locked: both are analysed.
    assert list(entries) == [case["segment_ids"][1]]
    assert entries[case["segment_ids"][1]]["locked"] is True
    assert FITTING_EDIT in record_markup(entries[case["segment_ids"][1]])
    assert batched(case, draft["id"]) == [case["segment_ids"][0], case["segment_ids"][2]]
    assert (draft["copied_count"], draft["skipped_count"]) == (None, None)


def test_plan_reads_report_counts_only_for_a_copy(case):
    source = adopt(case, create(case))
    assert (source["copied_count"], source["skipped_count"]) == (None, None)
    copied = create(case, copy_from_id=source["id"])
    assert counts(copied) == (3, 0)
    listing = case["client"].get(case["base"]).get_json()
    by_id = {item["id"]: item for item in listing["items"]}
    assert counts(by_id[copied["id"]]) == (3, 0)
    assert (by_id[source["id"]]["copied_count"], by_id[source["id"]]["skipped_count"]) == (
        None, None,
    )
    summary = case["client"].get(f"{case['base']}/{copied['id']}?view=summary").get_json()
    assert counts(summary) == (3, 0)

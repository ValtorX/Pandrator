---
type: epic
title: "The speaker plan survives edits"
parent: initiative-long-audiobooks
covers: [R9, R10, R11, R12]
sources: []
companions: []
after: []
refined: true
risk: high
---

# The speaker plan survives edits

## Description

Editing a segment keeps the rest of the book's reuse status and adopted speaker plan, across edit-copied and restored revisions. This epic settles the edit-copy handling Epic 4 relies on and the pause rules Epic 5 relies on.

## Outcome

Someone correcting one line of a long book keeps the speaker plan and reusable audio for every other line, with no hand-copied `speech_xml`. A repeatable check on the synthetic 5,000-segment project shows it.

## Requirements

- R1 (parent R9): Editing one segment's spoken text, in place or through an edit copy, leaves every other segment's reuse status unchanged. The edited segment keeps its speaker markup while that markup still fits the new text; otherwise only that segment needs review, and a run that includes it is refused with the segment named.
- R2 (parent R10): An edit-copied or restored revision has an adopted speaker plan that holds every unchanged segment's entry, with no adopt step.
- R3 (parent R11): `create_performance_plan(copy_from_id=...)` copies the annotations of unchanged segments across restored and edit-copied revisions, including from a stale source plan, and reports how many it copied and how many it skipped.
- R4 (parent R12): Starting a run with no adopted speaker plan, when the revision's lineage has one, is refused with that plan named, unless the caller acknowledges it. The API, MCP and the UI all show the refusal.

## Constraints

- Reproduce R9-R12 on current upstream code before fixing them (story 1). The synthetic 5,000-segment project is the shared reproduction.
- Anything that changes real data runs on a throwaway copy of `%USERPROFILE%\pandrator-perf\real`, never on `real` itself, which epic 2's story 14 still uses. Only counts and timings are recorded.
- Regression tests count work and never assert wall-clock time.
- Existing response fields keep their meaning; new state comes as new fields.
- One branch, `epic-speaker-plan-carry-over`, is cut from fork `main` after upstream 0.14.4 is merged there. Nothing is staged, committed or pushed without the owner's go-ahead.
- Upstream: an issue with story 1's reproduction and root cause goes up first, then a focused PR per fix story, per `fork-maintenance.md` section 3. Nothing waits on upstream.
- Local paths and the book's title stay out of PRs, issues and this public branch.

## Done when

- On the synthetic project, editing one segment's spoken text in place, or through an edit copy, leaves every other segment's reuse status unchanged. The edited segment keeps its markup when that markup still fits.
- A restored revision keeps the adopted plan for unchanged segments.
- `copy_from_id` copies the unchanged segments' annotations across restored and edit-copied revisions, including from a stale plan, and reports copied and skipped counts.
- When a run would start with no adopted plan and the lineage has one, the API, MCP and UI refuse it, naming the plan, unless the caller acknowledges it.
- A throwaway copy of the real project shows the same on a released fork build.
- Each fix is in a fork release and verified here, and has been offered upstream: the issue after story 1, then focused PRs.

## Boundaries

- Belongs to siblings: audio identity speed (Epic 2), export and partial assembly (Epic 4), chapter pause settings (Epic 5).
- Out: inferring dialogue turns at assembly without a speaker plan. Such a run is refused instead (R4).
- Out: carrying the plan over automatically for split, merge and re-segment revisions. `copy_from_id` stays the way to do it there.
- Must not change: a segment whose text or speech really changed loses its reuse, and a take is only reused when its audio identity truly matches.
- Touch point: the MCP tools `create_performance_plan` and generation start are owned here. A new acknowledgement input carries no connection or credential (`tests/test_mcp_architecture.py`).

## References

- parent: `_bmad-output/initiative-long-audiobooks/initiative-long-audiobooks.md`, section Requirements
- upstream procedure: `_bmad-output/fork-maintenance.md`, section 3
- harness: `_bmad-output/scripts/large_project_seed.py`, `_bmad-output/scripts/large_project_timing.py`
- model: `_bmad-output/initiative-long-audiobooks/tickets/epic-large-project-responsiveness/`, especially `baseline-2-1.md` and `upstream-pr-drafts.md`

## Notes

- Decision (2026-11-13): Upstream check, with `upstream/release/0.14.4` at `76a4ac02`.
  - Open PRs #142-#144 and #4 touch none of R9-R12.
  - Since 0.14.2, upstream has not changed `performance_plans.py`, `audiobook_resegmentation.py` or `speech_boundaries.py`. The edit-copy path's latest change is our #136 (take inheritance).
  - `generation_audio_identity.py` was reworked by #126 and #131, so the R1 fix touches code the maintainer also edits.
  - A restore deliberately returns `review_required: {"performance": true}` (`generation_topology.py:1464`). That is upstream's review-after-restore design, which R2 departs from.
- Decision (2026-11-13): What the code shows.
  - A speaker plan's validity is one hash over the whole revision (`plan_signature`), so any edit makes the whole plan stale.
  - A stale plan fails `freeze_performance_snapshot` (`performance_plans.py:1123`). The identity pass then stores that error for the whole revision (`generation_audio_identity.py:659-664`), so every segment reads `audio_identity_unknown`. Each unit already stores its own `text_hash`.
  - An edit copy is a restore of the same revision (`speech_plan_workspace.py:261-283`), and a restore never touches `PerformancePlan`, so the new revision has no adopted plan.
  - `copy_from_id`:
    - It refuses a stale source with a 409 (`performance_plans.py:483`).
    - It walks lineage through restore jumps and can miss the source.
    - It copies a unit only on an exact `structure_key` match.
    - In `llm` or `passive` mode it keeps only locked entries.
    - It reports no counts.
  - With casting on and no adopted plan, nothing is frozen, so dialogue turns fall back to the stored paragraph pause in `freeze_boundaries`.
- Decision (2026-11-13): The owner settled the initiative's open question for R12: refuse unless acknowledged, rather than warn or carry the plan over at run start.
- Decision (2026-11-13): The owner settled three more questions:
  - R2 carries the plan over as adopted, not as a draft to adopt. A draft would leave every segment unknown after a forked edit and fail R1.
  - The edited segment keeps its markup while it still fits, rather than always needing review.
  - Upstream gets an issue after story 1, before any PR, because R1 and R2 change designs the maintainer chose (whole-plan staleness, review after restore).
- Decision (2026-11-13): Story 1 is the tracer bullet.
  - On current upstream code, it runs the synthetic project and a throwaway real copy through the real server and MCP paths: an in-place edit, a forced edit copy, a restore, `copy_from_id` in manual and llm modes from fresh and stale sources, and a run started with no adopted plan.
  - It records reuse counts, copied counts and the frozen pause per boundary, and cancels the run before TTS.
  - It measures rather than fixes, as epic 2's story 1 did.
- Decision (2026-11-13): Story 2 (per-segment validity) comes right after story 1, because it is the least certain story and touches identity code the maintainer edits.
  - Story 3 (carry-over) waits on story 2, because an edit lands on the copy after the restore, so a plan carried over whole would go stale at once.
  - Stories 4 (`copy_from_id`) and 5 (run guard) build on story 3's lineage mapping. They don't depend on each other, but one epic branch keeps them in sequence.
  - The refactor sweep (6) and the real-data story in a released build (7) close the epic.
  - Epic 4 waits on story 3, and Epic 5 on story 5.
- Decision (2026-11-13): Epic 3 does not wait for all of epic 2. Its entries need only 2.1 (the synthetic project) and 2.3 (the reworked identity pass), and its branch is cut once upstream 0.14.4 is merged into fork `main`. That is also when epic 2's story 14 runs, so the two may overlap, and story 1 works on a throwaway copy of `real`.
- Decision (2026-11-13): Story 5 waits on story 4 as well as story 3. Stories 2, 4 and 5 each add API fields or inputs, which means regenerating `openapi.json`, `api.generated.ts` and the static bundle, and story 5 also changes route and MCP registration counts. Running them in order keeps those generated files from colliding.
- Decision (2026-11-13): The owner approved the seven-story breakdown as drafted, and with it its three assumptions:
  - With a TTS context mode that puts neighbour text into a segment's request, that neighbour really changes and loses reuse. R1 applies only to segments whose identity does not include the edited text.
  - Automatic carry-over covers restore and edit copy only. Split, merge and re-segment revisions keep `copy_from_id` as their path.
  - The UI changes follow existing patterns, with no separate UX design. Story 2's count goes in the speaker plan panel's stale status line. Story 5's refusal uses the generation start's existing error presentation, plus a start-anyway action.
- Waits on epic-large-project-responsiveness because: it changes the audio identity path in `generation_audio_identity.py` that per-segment staleness also changes, and it provides the synthetic project.

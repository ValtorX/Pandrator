---
type: initiative
title: "A 5,000-segment audiobook can be produced in Pandrator without workarounds"
sources:
  - _bmad-output/initiative-long-audiobooks/brief-long-audiobooks.md
  - _bmad-output/deferred-work.md
companions: []
after: []
risk: high
---

# A 5,000-segment audiobook can be produced in Pandrator without workarounds

## Description

Producing a 5,167-segment multi-voice audiobook needed a patched install, restarts, hand-copied speaker plans and ffmpeg post-processing. When this is done, a book that size stays responsive, edits and partial regenerates keep existing work, chapter breaks sound right from the app itself, and each fix has gone upstream as a focused PR.

## Outcome

Someone producing a very long audiobook can list, edit, regenerate, assemble and export it through the app and MCP with none of the brief's workarounds.

## Requirements

- R1 (Problem 1): On a 5,000-segment project, `list_generation_segments` returns a page of up to 100 rows within the MCP client's 15 s read timeout, whatever the `limit` and `fields`.
- R2 (Problem 1): The presence of a 5,000-segment project does not push listing a 189-segment project past that timeout, including after the server has been running under load.
- R3 (Problem 1): On a 5,000-segment project, `list_speech_plan_revisions`, `get_speech_plan_status` and reads of a non-active revision return within 15 s.
- R4 (Problem 1): After a burst of reads their clients abandoned, new requests including `/api/v1/auth/status` succeed without a restart.
- R5 (Problem 1): Under read load, requests do not fail with `database is locked`, audit events are not diverted to the pending spool in bulk, and the WAL stops growing once the load ends.
- R6 (Problem 1): While generation admission for 1-15 segments runs on a 5,000-segment project, reads and the UI keep responding, and admission takes measurably less than its 640-741 s baseline.
- R7 (Problem 1, deferred-work item 1): The speech plan and voice check at the start of generation takes measurably less than its 328-452 s baseline, and no generation start route holds the write lock through it.
- R8 (deferred-work item 2): A cancel during the opening speech plan and voice check stops the check, so the worker is free again promptly.
- R9 (Problem 2): Editing one segment's spoken text leaves every other segment's reuse status unchanged.
- R10 (Problem 2): An `edit_copy` or restored revision keeps the adopted speaker plan for segments it did not change.
- R11 (Problem 2): `create_performance_plan(copy_from_id=...)` copies annotations for unchanged segments across a restored or edit-copied revision, including from a stale source plan.
- R12 (Problem 2): Starting a run with no adopted speaker plan, on a project that has one to carry over, no longer silently assembles dialogue turns with the 700 ms paragraph pause.
- R13 (Problem 3): With several assembled audio versions, export names which version it will use and never silently picks an old one.
- R14 (Problem 3): A `regenerate` run on part of a book can be assembled into a complete book using existing takes for the other segments.
- R15 (Problem 3): The default Generate-audio plan does not discard segment lineage and takes unless the user explicitly chooses to re-segment.
- R16 (Problem 3): Replacing a source with one of identical text raises no "different text" warning and keeps reuse.
- R18 (Feature requests): Separate settings set the silence before a chapter heading and after the spoken chapter title, and assembly applies them with no post-processing.
- R19 (Feature requests): An assistant can set a segment's pause through MCP.
- R20 (Owner additions): A build of the fork can be released and installed as the owner's working app, and the fork can keep taking upstream changes.
- R21 (Owner additions): The project's checks and builds work from a Windows checkout by a documented setup, or each remaining failure is explained.

## Constraints

- Each fix is offered to upstream `lukaszliniewicz/Pandrator` as a focused PR based on its current `main` (Goal), but no progress waits on upstream approving it (Owner additions).
- Fix query, caching and transaction scope first; consider storage-engine changes only after (Suggested order 1).
- Reproduce each problem on current upstream `main` before fixing it; a synthetic 5,000-segment project is the shared reproduction (Suggested order 1).
- `WORKER_LEASE_SECONDS` stays 30; the install's 19-hour lease patch is not replicated.
- The owner's installed app and database change only when the owner installs a fork release; any other check against them is read-only or on a local copy.
- Local paths and the book's title stay out of PRs and this public branch.

## Done when

- On a synthetic 5,000-segment project on upstream `main` plus these changes, a repeatable timing check shows R1-R3 inside 15 s and R6-R7 below their baselines.
- A copy of the real project's database shows the same, on the stock 30-second lease.
- The real book can be edited, partly regenerated, assembled and exported without the brief's workarounds.
- Chapter pauses come from settings, with no ffmpeg post-processing.
- Every fix is in a fork release the owner has installed, with no 19-hour lease patch, and has been offered upstream as a focused PR.

## Boundaries

- Tracer path: on a long book, list segments, edit one, regenerate part, assemble with chapter pauses, and export.
- Out: a storage-engine change, unless query, caching and transaction work is shown to be insufficient.
- Out: the 30-second lease failure itself, fixed by `415aaa8a` on `fix-generation-check-write-lock`.
- Out: Problem 4. The number was spoken; the source text just doesn't say "Chapter", and setting a title's spoken text remains the way to change that.
- Must not change: a take is only reused when its audio identity truly matches.
- Touch point: `pandrator_mcp` tool contracts — each epic owns the tools it changes: listing and plan reads (epic-large-project-responsiveness), `create_performance_plan` (epic-speaker-plan-carry-over), export and run selection (epic-export-and-regenerate), segment pause input (epic-chapter-pauses).

## References

- brief — `_bmad-output/initiative-long-audiobooks/brief-long-audiobooks.md`, sections Problem 1-4, Feature requests, Suggested order
- deferred work — `_bmad-output/deferred-work.md`
- prior fix — `_bmad-output/plan-generation-check-write-lock.md`

## Notes

- Source conflict: Problem 1 "admission holds the DB lock about 10 min" — job history and code show admission preparation runs without a writer (640+ s admits succeeded on the stock lease); the writer held through the opening check was the 30-second failure, now fixed. R6 is written against the observed slowness, and why reads stall during admission is for Epic 2 to establish.
- Source conflict: Problem 2 "on a large project, the edit creates an `edit_copy`" — code forks one whenever the revision is approved, already used by a run, or not the latest, whatever its size (`speech_plan_workspace.py:261-281`).
- Source conflict: Problem 2 "`copy_from_id` copies nothing" — lineage copying exists (`audiobook_resegmentation.py:26-90`) but matches on strict structure keys; whether it returns 0 needs a reproduction. The refusal of a stale source is confirmed (`performance_plans.py:458`).
- Source conflict: Suggested order 1 "reproduce on the installed commit" — the owner asked for current upstream `main`, 5 commits ahead, and the 0.14.2 notes mention none of these problems.
- Decision (2026-11-08): Four product epics, cut by distinct outcome, in the order responsiveness, speaker plan, export and regenerate, chapter pauses. Epic 2 builds the synthetic project and timing check the later ones reuse.
- Decision (2026-11-08): Generation start (admission, the opening check, cancel) stays in Epic 2, because it shares the audio identity cost with the reads. If Epic 2 outgrows about 12 stories, moving it to its own epic gets offered.
- Decision (2026-11-08): Reads are held to the 15 s MCP timeout; the R6 and R7 targets are set from Epic 2's baseline.
- Decision (2026-11-08): No separate architecture document for now, because Epic 2's profile has to come first.
- Decision (2026-11-08): Problem 4 is not a software defect and R17 is retired. The owner confirmed the "1." was generated; they expected "Chapter 1", which the source text doesn't say. This overrides the brief's "swallowed".
- Decision (2026-11-08): "Delivered" means in a fork release and verified here. Upstream PRs are still offered, but nothing waits on them. This replaces the same-day decision that an open upstream PR counted as delivered.
- Decision (2026-11-08): Windows dev setup and fork releases share a new opening epic as the platform baseline, because they have a distinct outcome, a CRLF build would fail the release's asset gate, and the lease fix can ship through it first.
- Open question: What should R12 do: carry the plan over, warn, or refuse? Epic 3 settles this when refined.

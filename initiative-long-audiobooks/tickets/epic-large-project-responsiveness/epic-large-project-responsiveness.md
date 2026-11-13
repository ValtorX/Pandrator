---
type: epic
title: "Large projects stay responsive"
parent: initiative-long-audiobooks
covers: [R1, R2, R3, R4, R5, R6, R7, R8]
sources: []
companions: []
after: []
refined: true
risk: high
---

# Large projects stay responsive

## Description

On a 5,000-segment project, segment listing, plan status and generation start stay within their limits, and heavy or abandoned reads no longer stall other requests. This epic also builds the synthetic project and timing check the later epics reuse, and the reworked audio identity path Epic 3 builds on.

## Outcome

Someone working on a very long book gets answers from the app and MCP without timeouts or restarts, shown by a repeatable timing check on a synthetic 5,000-segment project.

## Requirements

- R1 (parent R1): `list_generation_segments` returns a page of up to 100 rows within 15 s on a 5,000-segment project, whatever the `limit` and `fields`.
- R2 (parent R2): A 5,000-segment project does not push listing a 189-segment project past 15 s, including after the server has been running under load.
- R3 (parent R3): `list_speech_plan_revisions`, `get_speech_plan_status` and reads of a non-active revision return within 15 s on a 5,000-segment project.
- R4 (parent R4): After a burst of reads their clients abandoned, new requests including `/api/v1/auth/status` succeed without a restart.
- R5 (parent R5): Under read load, requests do not fail with `database is locked`, audit events are not diverted to the pending spool in bulk, and the WAL stops growing once the load ends.
- R6 (parent R6): While admission for 1-15 segments runs on a 5,000-segment project, reads and the UI keep responding, and admission beats its 640-741 s baseline by a target set from story 1.
- R7 (parent R7): The opening speech plan and voice check beats its 328-452 s baseline by a target set from story 1, and no generation start route holds the write lock through it.
- R8 (parent R8): A cancel during the opening check stops it, so the worker is free again promptly.

## Constraints

- Fix query, caching and transaction scope first; consider a storage-engine change only after.
- `WORKER_LEASE_SECONDS` stays 30.
- No audit event may be lost. Audit writes may be batched or delayed, and token `last_used_at` may become less precise.
- Regression tests count queries and work done, with no wall-clock assertions. The 5,000-segment timing script stays on `fork-files`, and results are quoted in PRs.
- The local copy of the real database stays outside the repo, and only timings from it are recorded.
- Local paths and the book's title stay out of PRs and this public branch.

## Done when

- A repeatable timing check on a synthetic 5,000-segment project shows listing and plan reads inside the 15 s MCP timeout.
- Admission and the opening check beat their baselines and meet targets set from this epic's baseline.
- After a burst of abandoned reads, new requests succeed without a restart, with no `database is locked` failures, and the WAL stops growing.
- No generation start route holds the write lock through the opening check, and a cancel during it frees the worker promptly.
- A copy of the real project's database shows the same, on the stock 30-second lease.
- Each fix is in a fork release and verified here, and has been offered upstream as a focused PR.

## Boundaries

- Belongs to siblings: fork release mechanics (Epic 1), speaker plan carry-over (Epic 3), export and partial assembly (Epic 4), chapter pauses (Epic 5).
- Out: a storage-engine change, unless query, caching and transaction work is shown to be insufficient.
- Out: the 30-second lease failure, fixed by `415aaa8a`.
- Must not change: a take is only reused when its audio identity truly matches.
- Must not change: default API responses keep their current fields and meaning.

## References

- parent — `_bmad-output/initiative-long-audiobooks/initiative-long-audiobooks.md`, section Requirements
- deferred work — `_bmad-output/deferred-work.md`

## Notes

- Decision (2026-11-08): Story 1 is the tracer bullet: it runs the synthetic project and a copy of the real database through the real server and MCP read paths and records where the time goes. It measures rather than fixes, because the profile decides what the fixes are. Story 2, the shared identity work, comes next because it is the least certain and the riskiest.
- Decision (2026-11-08): The order of stories 2-9 is provisional until story 1 reports. (Its "a re-slice then keeps their ids" was replaced on 2026-11-11 by the owner's numbering rule; see below.)
- Decision (2026-11-08): The cancel story builds on the fork release line carrying `415aaa8a`, and nothing here waits on upstream.
- Decision (2026-11-08): Owner approved profiling on a local copy of the real database, with the app stopped while copying.
- Decision (2026-11-08): Whether identity results may be cached across requests (story 2) and how abandoned reads are stopped (story 7) are left to those stories, informed by story 1's profile, so neither is gated on a question to the owner.
- Decision (2026-11-11): Upstream took our lease fix (#123) into its integration PR #125 and changed the opening-check path this epic works on. `prepare_segment_reference` no longer marks the run failed after a mismatch; the queue ends it. `jobs.py` adds `_finish_owned_cancellation_locked`, so a cancel that lands after the check wins over the failure. `AudioIdentityContext` keeps plan and revision rows for the whole pass. What this epic requires is unchanged; only how stories 10 and 11 are delivered changes. Once #125 merges, upstream is merged into fork `main` by `fork-maintenance.md` and then into this epic's branch, taking upstream's side in `prepare_segment_reference`, before story 10 or 11 starts, because both change those lines. Story 11 builds on upstream's cancel handling. Stories 3 to 9 do not wait. If #125 is still unmerged when story 10 is next, ask the owner. This replaces, for stories 10 and 11 only, the 2026-11-08 decision that nothing here waits on upstream.
- Decision (2026-11-11): Re-sliced after story 2, from a timing and profile of the committed code on a fresh copy of the real database (`baseline-2-1.md`, section Replan). Large listings now meet 15 s (6.1-6.3 s), but plan status (46.5 s), revision history (42.1 s) and the non-active revision (15.9 s) do not, admission (71-84 s) and the opening check (66-69 s) miss their targets, and after four clients abandoned 16 listings `auth/status` took 66 s to answer. SQL is now 0.04-3.7 s of each; the rest is CPU in the identity pass: performance compilation's language and package lookups, the whole-plan cast freeze and its deep copies, and plan status running the pass for every revision. What this epic requires is unchanged; only how it is delivered changes. A new story 3 cuts that shared cost first, as story 2 did; plan reads (4) go before listing (5), which now gives the headroom concurrent listings need. Story 5 is kept, not dropped, because the abandoned listings are what stalled the server.
- Decision (2026-11-11): Owner adopted story 1's proposed targets for R6 and R7: admission at most 60 s and the opening check at most 45 s, for 1 to 15 segments on the 5,167-segment plan. Story 9 may re-propose them with evidence.
- Decision (2026-11-11): Stories not yet started are renumbered to follow the new build order, by the owner's numbering rule (2026-11-08): new story 3; old 4 stays 4; old 3 to 5, 5 to 6, 6 to 7, 7 to 8, 8 to 9, 9 to 10, 10 to 11, 11 to 12, 12 to 13. Stories 1 and 2 keep their ids. Earlier log lines, commit messages, the two finished plans and the earlier sections of `baseline-2-1.md` keep the old numbers.
- Decision (2026-11-11): Upstream's 0.14.2 release work (`dd38e331`) rewrote 98 lines of `http_lifecycle.py` (request-global snapshots, audit target, per-request actor tokens), the file stories 6 and 7 change. Owner chose two merges: upstream `main` is merged into fork `main` by `fork-maintenance.md` (epic branch landed first) and back into this branch before story 6 starts; #125 follows in a second, smaller merge before story 10, as the decision above says. Stories 3 to 5 are done; stories 6 to 9 now wait on the first merge only. This replaces "Stories 3 to 9 do not wait" in the decision above.
- Decision (2026-11-11): Upstream PRs for this epic's finished work are offered soon after the first merge rather than at the epic's end, because upstream's #125 (`f4b5e53d`) started on the same identity cost. Identity cost (stories 2 and 3) goes first with the baseline timings; plan reads (4) and listing (5) follow as separate focused PRs. Each is built from upstream `main` per `fork-maintenance.md` section 3, with the owner's go-ahead to push.
- Decision (2026-11-11): Upstream merged #125 and released 0.14.3 (`fb72c018`) before the first merge was committed, so the two merges above became one. Fork `main` merges upstream 0.14.3 on `merge-upstream-v0.14.3` as `0.14.3+fork.1`, carrying over the uncommitted first merge's resolutions, and stories 6 to 10 all wait on that one merge. From the same merge on, code branches never edit `docs/release-notes-next.md`: the epic's release-note lines moved to `_bmad-output/fork-release-notes.md` and upstream PRs carry them in their descriptions.
- Decision (2026-11-12): From story 9's measurements (admission 9-11 s clean, at most 17.4 s with the UI's reads sampled), the owner tightened R6's admission target from 60 s to 30 s, for 1 to 15 segments on the 5,167-segment plan. Story 14 checks against it.
- Decision (2026-11-12): Story 9's review found the web UI's compact run-list poll costs 2.3-3.1 s of a core per call on the real project. The owner chose a new story 12 for it. The unstarted refactor sweep and real-data verification became 13 and 14 by the numbering rule.
- Decision (2026-11-12): Story 10 is not offered upstream. The maintainer's own open #131 makes the same change to `run_reviewable_generation` (guard first, freeze and identity pass in a plain session, recheck under a short write), and also covers regroup, early repair and a cancel during the pass. For this epic's "offered upstream" criterion, story 10 counts as delivered upstream by #131. Its writer-hold measurement (9.475 s to 0.170 s median on the real 5,167-segment plan) was posted on #131 as supporting evidence, with an offer of the stored-run equivalence test as a follow-up after #131 merges. When upstream with #131 is merged into the fork, take upstream's side in `workflow_generation_start.py` and its tests, and keep story 10's extra tests only where they still pass.
- Decision (2026-11-12): Story 11 (a cancel stops the opening check) is dropped. After stories 2 to 10 the opening check takes 4.0 s on both 5,167-segment plans, against a 328-452 s baseline. `test_cancel_during_identity_check_stops_before_provider_send` already shows a cancel during the check closing the job and run as cancelled with nothing sent to the provider; upstream #125 makes a late cancel win, and #131 checks the stop before the run is created. A hook inside the loop would save at most those 4 s. R8 is verified instead by the real-data story, which times a cancel during the opening check. Unstarted stories renumber by the owner's rule: 12 to 11, 13 to 12, 14 to 13.
- Decision (2026-11-12): Regroup (`voiceover_regroup.py`) and early repair (`voiceover_repair.py`) still freeze the plan and run the identity pass inside `BEGIN IMMEDIATE` on this branch; every other generation start route (admission, recast, the automatic and language preflights, the workflow start) no longer holds the writer through them. Upstream's open #131 moves both out of the writer. R7 is met on the fork once #131 is merged upstream and that merge reaches fork `main` and this branch, and story 13 (real data) waits for it. If #131 stalls, or lands without those two routes, a story of ours moves them out of the writer as story 10 did for the workflow start.
- Decision (2026-11-12): The owner scoped story 12 (refactor sweep) to the audit story 8 left open: the other `snapshot_session` callers and streaming loops are checked for a read that keeps a statement open across per-row work, and any found read their rows in full first, as story 8 did for the segment search, with no behaviour change. No other deferred work was collected for this epic.
- Decision (2026-11-13): Story 12 (refactor sweep) found one read that keeps a statement open across per-row work, the job-claim scan, and the owner kept it after measuring both alternatives (story 12 plan); it changed no code, and its result was posted on upstream #134. Upstream #131 reached fork `main` and this branch in `3b7b490e`, so the real-data story now waits only for upstream 0.14.4, to run on a `0.14.4+fork.1` build.
- Decision (2026-11-13): With the full plan status and history reads no longer requested by the web UI or MCP, the single revision detail (`generation-plan/revisions/<id>`, 6.5 s on the real 5,167-segment plan) is the heaviest read people hit, and the MCP status call that makes it (about 7 s) has only about twice R3's headroom. The owner added a story for it, built while the epic waits for 0.14.4, with a proposed target of at most 3 s. By the owner's numbering rule it becomes story 13 and the real-data story becomes 14; earlier log lines and commit messages keep the old numbers.
- Decision (2026-11-13): Story 13 (single revision detail) closed with no code change (owner, option C). Profiling on a fresh real copy put the read at 6.62 s, 6.3 s of it the identity pass, which parses each segment's markup four times and renders it twice; exact in-request trims reach about 6.0-6.3 s, and the 3 s target would need a high-risk restructure of identity and cast code upstream also edits. R3 is met (about 2.3x headroom), and the MCP tools degrade a detail timeout to a warning (story 4). Evidence in `baseline-2-1.md`; story 14 times the read again on the released build.
- Waits on epic-fork-releases because: the real-data check, with its cancel measurement, verifies a released build carrying `415aaa8a`.

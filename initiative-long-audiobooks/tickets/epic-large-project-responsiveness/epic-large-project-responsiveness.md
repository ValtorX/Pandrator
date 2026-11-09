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
- Decision (2026-11-08): The order of stories 2-8 is provisional until story 1 reports; a re-slice then keeps their ids.
- Decision (2026-11-08): The cancel story builds on the fork release line carrying `415aaa8a`, and nothing here waits on upstream.
- Decision (2026-11-08): Owner approved profiling on a local copy of the real database, with the app stopped while copying.
- Decision (2026-11-08): Whether identity results may be cached across requests (story 2) and how abandoned reads are stopped (story 6) are left to those stories, informed by story 1's profile, so neither is gated on a question to the owner.
- Waits on epic-fork-releases because: the cancel story needs `415aaa8a` on the release line, and the real-data check verifies a released build.

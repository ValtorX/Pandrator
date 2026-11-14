---
title: 'Re-preparing text and MCP generation stay responsive'
type: bugfix
ticket: 15
created: '2026-11-13'
status: done
baseline_revision: '9f0d0441375a5af5d876dbe1106064f5867ae6b8'
route: full
route_source: auto
risk: high
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/fork-maintenance.md'
  - '{project-root}/_bmad-output/initiative-long-audiobooks/tickets/epic-large-project-responsiveness/baseline-2-1.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 14's live check on the 5,167-segment book showed write requests stalling for 20-52 s around a re-run of Segment narration (the `prepare_text` stage), MCP `regenerate_segments` timing out at 15 s on first use, every MCP segment-listing page carrying the active revision's 873 KB restore `operation_json`, and a missing-only admission's write with 5,159 preserved-take clones unmeasured. Planning measured each on fresh copies of the real data (`work-2-15/`): the clone write holds the writer for 7.7 s, the plan store holds it for 1.9 s with 5,174 flushes, and the selection save holds it for 0.83-0.87 s, mostly loading all 46,267 artifact edges and all 15,324 session artifacts. A one-row listing page is 877 KB. The admission route itself holds the writer for 0.11-0.17 s.

**Approach:** Cut each measured writer hold. Clone preserved takes and store the plan's segments in bulk, with per-row preparation done before the writer. Load only the edges and artifact columns the selection walk needs. Return `operation_json` without its large per-segment maps unless the caller asks for them. Each change is reproduced on current upstream `main` first and offered upstream as its own focused PR.

**Decision (owner, 2026-11-13): find the live slowdown here, timeboxed (option A).** On the live app, between 17:54 and 18:16 UTC, every request ran 20-400 times slower than on a copy of the same data. That includes plan status (28-50 s against 0.5 s) and the MCP admission request (42.1 s against 0.13 s, which is what timed MCP out). The copies show nothing else running then. The story replays the owner's sequence on a copy run as the install runs it, finds the cause and fixes it here. Its scope may grow to whatever the cause needs. If the timebox ends without a cause, the story falls back to option B: the four fixes, plus a timestamped log of slow requests (over 10 s) and slow writer holds (over 1 s) with their call sites, and the owner's repeat on the next fork build becomes a new story.

## Boundaries & Constraints

**Always:**
- A take is reused or preserved only under the same conditions as today. The same takes are deactivated and cloned, with the same status, artifact, settings hash and duration.
- A stored plan's segments, ordinals, speech plans and active-revision flip are identical to today's.
- Tests count writer holds, statements, flushes and loaded rows, never wall-clock time.
- Default API responses keep their fields. Only `operation_json`'s oversized values move behind an explicit request, and the page says which keys it omitted.
- Fork-only timing tools stay on `fork-files` (`_bmad-output/scripts/`). Local paths and the book's title stay out of PRs and the public branch.
- `WORKER_LEASE_SECONDS` stays 30. No audit event may be lost.

**Never:**
- No storage-engine change, no new dependency, and no wall-clock assertion in tests.
- Never edit `docs/release-notes-next.md`; fork-visible notes go to `_bmad-output/fork-release-notes.md`.
- No change to reuse or identity decisions (stories 2-13 and upstream #146 own those).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Missing-only, many preserved | 5,159 completed, 8 missing | 8 generate. 5,159 clones are made active and their sources inactive, all in one bulk update and one bulk insert. | A row-state change before the write gives the existing guarded-start conflict. |
| Missing-only, none preserved | Every segment missing | No clone writes | Unchanged |
| Plan store, new revision | 5,167 records, some with speech markup | Same rows as today's store, inserted in bulk, with no per-row flush | An active revision changed under the writer is caught by the existing checks |
| Plan store, identical content | Digest matches the active revision | Existing segments kept, as today | Unchanged |
| Selection save | Session with 15k artifacts | Same `restored` and `cleared` result as today | Unknown artifact gives 404, a wrong role gives 422 |
| Listing, default | Revision with a 873 KB restore operation | `operation_json` keeps its small keys (`action`, `reason`, `batch`, revision ids) and drops values over 4 KB, which `operation_json_omitted` names | None |
| Listing, opt-in | `operation=full` | Whole `operation_json`, `operation_json_omitted: []` | Any other value gives 422 |

</frozen-after-approval>

## Code Map

- `pandrator/web/workspace.py:2018-2050` -- `start_in_session`'s preserved-take clone loop (3 `session.get` calls, an UPDATE and an INSERT per take, with autoflush) inside admission's `immediate_session` (`generation_admission.py:309`). Selection is in `prepare_start:1358-1375`. `collect_row_state` is rechecked at `:1942` in the writer.
- `pandrator/web/generation_selection.py:235,341,393` -- `compute_selection` sets `preserve_take_ids` in missing mode. `collect_row_state:81` holds the take, segment and artifact facts, so the loop's point reads are redundant.
- `pandrator/web/generation_plan_store.py:37-223` -- `store_generation_plan` flushes per segment (`:205`) and updates `speech_plan_json` after the flush. Its callers are `workflow_source.py:863` (inside Prepare text's only writer, `:837-879`), `workflow_generation_automatic.py:164`, `workflow_generation_binding.py:489`, `workflow_generation_start.py:152` and `speech_plan_workspace.py:607`. `GenerationSegment.id` is generated client-side (`new_id`), so ids can be assigned before insert.
- `pandrator/web/artifact_selection.py:50 (_edges), 337-403 (choose_artifact)` -- `_edges` selects every `ArtifactEdge`. `choose_artifact` loads full `Artifact` rows for the session and needs only `id` and `role`. The route is `workflow_routes.py:166-201`.
- `pandrator/web/generation_segment_reads.py:792-814` -- the listing page sets `operation_json` (`:808-812`). The no-plan early return is at `:234-245`. Query parsing is in `generation_routes.py:282-333`, with the strict bool helper at `:286-293` and the `view` enum pattern. `project_segments` (`generation_review.py:68-93`) keeps page keys. `public_link_http.py:90` allowlists the path.
- `pandrator/web/openapi.py:4388-4422` -- the listing's query parameters (`fields` and `view` enums). Regenerate `openapi.json`, `web/src/lib/api.generated.ts` and `pandrator/web/static/`.
- Readers of `operation_json`: `web/src/lib/GenerationWorkspace.svelte:1044` (reads `action` only), with its type at `web/src/lib/api-models.ts:1606`. `pandrator_mcp/tools/generation.py:208` passes it through. The MCP side needs no new argument: no MCP consumer uses `mapping` or `lineage`.
- Reproduce and measure with the planning probes in `work-2-15/` (`probe15.py`, `probe15b.py` and `probe15c.py` in the planning session's scratchpad, with their output in `%USERPROFILE%\pandrator-perf\work-2-15\probe*.txt`; fold what they do into the fork tools). Make every copy under `%USERPROFILE%\pandrator-perf\work-2-15\`, never inside the install or the repository. Extend `_bmad-output/scripts/admission_writer_holds.py` with `--mode missing-only --missing <n>`, which deactivates the active takes of the first n segments on the copy before start (the probe's method).
- Writer-free test patterns: `tests/test_web_workflow_handlers.py:2749` (`immediate_session(busy_timeout_ms=0)` from another connection during the work) and `tests/test_generation_edit_audio.py:~1148` (counts ORM loads and SELECTs).
- Existing tests to keep green: `tests/test_generation_missing_only.py`, `tests/test_generation_admission.py`, `tests/test_web_workflow_handlers.py` (prepare_text), `tests/test_web_artifact_selection.py`, `tests/test_web_workflow_selection_concurrency.py`, `tests/test_web_generation_topology.py:936,1052`, `tests/test_mcp_preview_and_generation.py:579`, `tests/test_generation_selected_takes.py:214`.

## Tasks & Acceptance

**Execution:**
- [x] Live slowdown replay (timebox: two complete replays, at most half a day): on a copy built from `live-check/pandrator.sqlite3` (the copy written at 18:18 UTC, with revisions 4-6) plus `real/`'s artifacts, voices and marker, with revision 6 active, run the app as the install runs it. That means `python -m pandrator.web.cli serve` under waitress on a free port that is not 8097 or 8098, and a separate `worker` process, with the 0.14.4+fork.1 code. Drive the audit timeline's sequence over HTTP: the UI's polls (workflow, sources/status, settings, `generation-plan/status?summary=true`, generation summary, jobs, an open `/events` stream), the selection save, four Run requests, the impact read, then an MCP `regenerate_segments` through the real `pandrator_mcp` client with its 15 s limit. Sample both processes with `uvx py-spy dump` and `py-spy record`. Time each request and every writer hold in both processes. When the slowdown appears, fix its cause in this story and add a counting test for it. Without a cause at the end of the timebox, add the option B diagnostic in `pandrator/web/http_lifecycle.py` and `pandrator/web/database.py`: a timestamped warning for requests over 10 s and writer holds over 1 s, with the route or opener and no secrets, plus a test for it. Record the replay either way in `baseline-2-1.md`.
- [x] Reproduce on upstream `main` (`f449f03f` or newer, in a temporary worktree outside the repo): rerun the probes on fresh real copies and record the clone write, plan store, selection hold and listing size. Upstream differs from the baseline only in identity files (#146).
- [x] `pandrator/web/workspace.py` -- in `start_in_session`, build the clone rows (new ids, `created_at`, source fields) from the row state already collected. Inside the writer, after the existing `collect_row_state` recheck, deactivate the sources with one chunked `UPDATE ... WHERE id IN` and insert the clones with one bulk `insert(AudioTake)`, with no per-take `session.get`. Stale mode keeps its `take_reuse_reason` check, done from the collected state. -- 7.7 s hold with 15.5k point reads and flushes.
- [x] `pandrator/web/generation_plan_store.py` -- split out `prepare_generation_plan_rows(...)`, which normalizes records, remaps and parses markup and computes the final `speech_plan_json` with pre-assigned segment ids. `store_generation_plan` accepts prepared rows (recomputing them when the `characters` read under the writer differs) and inserts them in bulk. Callers that already hold a writer keep working unchanged. -- per-segment flushes under the writer.
- [x] `pandrator/web/workflow_source.py` -- Prepare text prepares the plan rows before `immediate_session` and passes them in, so its writer only rechecks access, registers the artifact, inserts and flips. The digest stays the same.
- [x] `pandrator/web/artifact_selection.py` -- `_edges` limits to edges whose child belongs to the session, and `choose_artifact` selects `(id, role)` instead of full `Artifact` rows. Same traversal and result.
- [x] `pandrator/web/generation_segment_reads.py`, `generation_routes.py`, `workspace.py:540`, `openapi.py` -- add `operation=summary|full` (default `summary`). Summary drops top-level `operation_json` values whose compact JSON exceeds 4 KB and adds `operation_json_omitted` (sorted keys). Regenerate the contract, typed client and static bundle, and add `operation_json_omitted` to `api-models.ts`.
- [x] Tests -- writer-free and counting tests: a missing-only start issues no per-take SELECT and a constant number of write statements, and its clones equal the old loop's (kept as a reference algorithm in the test); the plan store issues no per-segment flush and gives the same rows as the reference; another connection's zero-timeout write commits while Prepare text prepares its rows; the selection walk loads no edge from another session, with the same result; the listing summary and opt-in, with 422 on a bad value. Add any new test file to a lane in `scripts/test_lanes.py`.
- [x] `_bmad-output/scripts/admission_writer_holds.py` -- add missing-only mode, plus `--route selection` and `--route plan-store` timing on a copy. Record before and after in `baseline-2-1.md` under "Story 15".
- [ ] Upstream -- build one focused branch per change from upstream `main` by `fork-maintenance.md` section 3 (clone write, plan store with Prepare text, selection walk, listing `operation_json`). Run `check_pr_branch.py`. Push and open PRs only on the owner's go-ahead, quoting the timings.

**Acceptance Criteria:**
- Given a fresh real copy with 8 segments missing, when a missing-only admission runs, then its commit transaction's median writer hold over 3 runs is at most 0.75 s and the run preserves the same 5,159 takes.
- Given 5,167 prepared records, when the plan is stored inside Prepare text's writer, then that writer hold is at most 0.75 s on the real copy and the stored rows equal the old store's.
- Given the real copy, when the selection save runs, then its writer hold is at most 0.25 s.
- Given the replayed live sequence on a copy run as the install runs it, when the slowdown's cause is found and fixed, then MCP `regenerate_segments` answers within 15 s on its first use, and the selection save and Run requests answer within a few seconds while a Prepare text job runs. If no cause is found within the timebox, the replay is recorded and the slow-request and slow-hold warnings are tested instead.
- Given the real book, when MCP lists one segment, then the response is under 16 KB, and `operation=full` returns the whole operation.
- Given the changed code, when `pixi`-equivalent focused checks run (ruff, basedpyright, the touched test files under SQLAlchemy 2.0.52 and 2.1.8, `check_docs`, the contract and asset checks, web `quality` and `test:unit`), then they pass.

## Implementation Notes

- **Owner evidence (2026-11-13, relayed during implementation):** "in the past working on a book with an agent whom was using the MCP, I had to stop and restart the app sometimes, because it would become much too slow and restarting would fix it." The slowdown builds up over uptime under MCP-driven use and a restart clears it, which fits planning's finding that every live request ran 20-400 times slower than on a freshly started copy. The replay therefore does not rely on a cold start: it runs the API and worker for a sustained MCP-style workload (including 15 s client timeouts the server keeps computing after) beside the UI's polls, tracks a fixed probe request's latency over time, and samples what accumulates (threads and their stacks via `py-spy dump`, RSS, SQLite connections and pool checkouts, waitress queue depth, growing in-process caches, requests still computing after their client gave up), comparing a degraded process's dump with a fresh one's.
- **Baseline moved (coordinator, 2026-11-13):** the epic branch was fast-forwarded to `9f0d0441` (fork `main` merging upstream #146, which touched only `generation_audio_identity.py`, `generation_cast_runtime.py`, `generation_performance_snapshot.py`, `generation_rendering.py` and three tests). `baseline_revision` now points at it. Every story 15 "before" timing comes from `9f0d0441`; the replay runs (replay-1 to replay-3) were taken on `5fd1df19`, the installed build, and are labelled so. Nothing is pushed or committed without the owner's word.
- **Live slowdown cause (replay, timebox used about 1.5 h):** abandoned recast previews. `POST .../generation-recast/preview` ran 317-348 s on a fresh copy (334 s CPU, about 300k statements) because `generation_recast._effective` called `apply_resolved_binding` without the context's `_service_config_cache` (every call rebuilt and deep-copied the TTS catalogue) and re-proved each retained pin per segment. It is a read-only POST, so neither the disconnect probe nor `ReadDisconnectChannel` watched it, and the loop had no abandonment check: every preview the Cast panel or MCP (15 s limit) gave up on ran to the end. Each CPU-bound preview makes every other thread wait for the GIL on each SQLite step, so row-heavy requests and writer holds stretch 10-40x per busy preview, and once previews fill the 12 waitress threads even trivial requests queue. Evidence: one preview in flight turned the 0.8 s selection save into 28.66 s with a 28.29 s writer hold (the live figure was 28.6 s); 45 min of a UI-plus-MCP workload took plan status summary from 20 s to 120 s timeouts, with 11 previews still running 380-2,492 s after their clients left; the same workload with only the catalogue cache passed stayed flat (plan status summary 1.8-3.5 s). A read-only `py-spy dump` of the owner's live API at 19:29-20:12 UTC showed six of 12 waitress threads in `generation_recast_preview` with 500-1,450 s of CPU each and five CLOSE_WAIT sockets. Harness: `_bmad-output/scripts/live_replay*.py`, `proc_sample.py`; results in `work-2-15/replay-{1,2,3}/`. Not yet confirmed from the install's audit: whether earlier previews ran between 17:50 and 18:30.

- **Fix for the cause (in scope by the owner's option A):** `generation_recast._effective` passes the context's catalogue cache to `apply_resolved_binding` and its proof memo and provider registry to `resolve_project_cast_binding` and `validate_retained_cast_resolution`; `resolve_project_cast_binding` forwards `_proofs` and proves its own access check and slot-lookup guard once per pass. A read-only POST can now be abandoned: `abandoned_requests.watches_disconnect(method, path)` names GET, HEAD and the recast preview POST, and both `http_lifecycle` (probe binding) and `api_server.ReadDisconnectChannel` use it; `prepare_recast` checks `raise_if_abandoned()` before each segment. Tests: `tests/test_generation_recast_cost.py` (same preview as the old path; catalogue builds 14 to at most 4, pin checks 20 to at most 2, access checks 40 to at most 2 on the 12-segment cast plan; the loop stops at the next segment; the route answers 499), plus waitress and predicate tests in `tests/test_abandoned_reads.py`. Not done: the Cast panel's single-flight or debounce of its preview refresh (fix D of the replay); with the server stopping an abandoned preview, the panel's `abort()` on refresh now frees the thread.
- **Acceptance status (2026-11-13, details in `baseline-2-1.md`, "Story 15"):** selection save hold 0.541 to 0.133 s (met); plan store hold 0.779 to 0.199 s (met); one-row listing 876,840 to 3,640 bytes, `operation=full` whole (met); replay-4 on the fixed tree: MCP `regenerate_segments` 0.216 s on first use, selection save 0.49 s and Run requests 0.78-4.06 s while Prepare text jobs run, no build-up over 28 minutes (met). **Not met:** missing-only admission's commit hold, 3.734 to 1.204 s median against 0.75 s; the remaining copy cost is SQLite's own insert and update of 5,159 indexed rows (about 0.45 s) on top of story 9's 0.65 s floor. Meeting it needs a change to how a run owns its preserved takes, which is the owner's call.
- **Option B diagnostics not added:** the replay found the cause inside the timebox, so the plan's fallback does not apply.

## Plan Change Log

- 2026-11-13, clone write: the plan said to build the clone rows from the row state already collected. That state holds each take's id, status, revision, artifact and creation time but not its `kind`, `settings_hash` or `duration_ms`, so `_clone_preserved_takes` reads those three columns in one query over the revision's active takes (and, for stale-only, the artifacts' `metadata_json` in chunks of 500) under the writer, after the existing `collect_row_state` recheck, instead of from a wider row state. The copies go in as one Core `executemany`. Widening `collect_row_state` would also change the recast preview's selection hash input. Every check reads the rechecked state; `take_reuse_reason` gained a column-value twin, `stored_take_reuse_reason`, with the same order of checks.
- 2026-11-13, selection walk: only `choose_artifact` passes `session_id` to `_edges`; `trash_stage_artifact`, `select_source_path` and `rerun_impact` still read every edge (they were not in the measured hold, and the impact read answers in 0.2 s).
- 2026-11-13, bulk inserts use `render_nulls=True`: without it SQLAlchemy's ORM bulk insert splits rows whose `None` columns differ into separate batches (6 INSERTs for 6 segments in the test).
- 2026-11-13, upstream reproduction: the "before" runs used `9f0d0441` in a detached worktree outside the repository (`work-2-15/src-before`), whose product code differs from upstream `main` `f449f03f` only in `pandrator/version.py` and `pandrator/web/identity.py`; no separate `f449f03f` run was made.
- 2026-11-13, upstream branches not built: section 3 builds each branch by cherry-picking commits, and nothing in this story is committed (owner's decision). The four product changes plus the recast fix are ready to split once the owner commits them.
- 2026-11-13, Prepare text's rows: `SourceWorkflowContext` gained an optional `_prepare_generation_plan_rows` (default `None`, so a context built without it stores as before). The digest still covers the new artifact id, so it is computed inside the writer.

## Review Triage Log

Quick lens, 2026-11-13, on the diff since `9f0d0441` (static bundle excluded):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | AC 1 (missing-only commit hold ≤0.75 s) not met: 1.204 s | medium | `baseline-2-1.md` Story 15 table; the remaining cost is SQLite's insert and update of 5,159 indexed rows on story 9's floor. Meeting it changes how a run owns preserved takes, outside the plan's settled approach | owner decision (raised at presentation; not deferred-work by owner rule) |
| 2 | Upstream task unchecked, so no PR branches | medium | Section 3 cherry-picks commits and nothing is committed; committing is the owner's call (AGENTS.md) | owner decision (raised at presentation) |
| 3 | "Reproduce on upstream" ran on `9f0d0441`, not `f449f03f` | false | `git diff --stat f449f03f 9f0d0441 -- pandrator pandrator_mcp` touches only `version.py` and `identity.py`, neither on a measured path; section 3's own before/after runs happen when the branches are built | rejected |
| 4 | MCP listing drops `operation_json_omitted`, so an MCP caller is not told which keys were left out | medium | `pandrator_mcp/tools/generation.py:208` builds the output by hand and never copies the new field; the frozen boundary says the page names omitted keys | patch (fixed, retested under 2.0.52 and 2.1.8) |
| 5 | `_clone_preserved_takes` checks segment status from `row_state` captured before `interrupt_generation`/`inherit_edit_copy_audio` in the same writer; docstring overstates it | low | No divergence today (`_copy_take` leaves completed takes alone), but the old loop read the segment after those calls; the source query already joins `GenerationSegment`, so reading its status there is a substitution | patch (fixed, retested under 2.0.52 and 2.1.8) |
| 6 | `store_generation_plan` does not check prepared rows against `settings` | low | Only caller (`workflow_source.prepare_text`) passes the same `artifact_settings` to both; a guard adds a settings digest and branch for a case no caller reaches | rejected |
| 7 | Clone comparison test cannot catch wrong `kind`/`settings_hash`/`duration_ms` | medium | `_seed_completed` gives every take `kind="tts"`, `settings_hash=None`, `duration_ms=100`, so a clone copying constants would match | patch (fixed, retested under 2.0.52 and 2.1.8) |
| 8 | Test name and comment claim one UPDATE "whatever their number", but `_CLONE_CHUNK=500` gives 11 on the book | low | Direct correction of the test's name and comment; the plan task specified a chunked UPDATE | patch (fixed, retested under 2.0.52 and 2.1.8) |
| 9 | Mid-write fault test no longer covers rolling back bulk-inserted segments | false | `tests/test_source_preparation_workspace_access.py:630` stores through the bulk path with prepared rows, then raises, and asserts the `GenerationSegment` count is unchanged | rejected |

## Design Notes

Planning measurements (fresh copies of `real`, in process, SQLAlchemy 2.0.52, cProfile on, so the holds are inflated):

| Operation | Writer hold | Main cost |
|---|---|---|
| Missing-only admission commit, 8 missing | 7.68 s | 15,505 `session.get` (5.9 s), 15,573 flushes (2.1 s) |
| `store_generation_plan`, 5,167 records | 1.89 s | 5,174 flushes (1.4 s) |
| Selection save | 0.83-0.87 s | all edges and all session artifacts |
| Admission route (MCP), regenerate 2 | 0.11-0.15 s | `snapshot_guard` |
| Listing, `limit=1` | none | 877,070 bytes, of which 873,410 are `operation_json` (`mapping`, `lineage`) |

The `operation_json` cut uses a size rule, not a key list, so later operations with large maps are covered too. `action`, `batch` and the revision ids, which the UI and MCP read, are always small.

## Verification

**Commands:**
- `<uv env> --with sqlalchemy==2.0.52 python -m pytest -q tests/test_generation_missing_only.py tests/test_generation_admission.py tests/test_web_workflow_handlers.py tests/test_web_artifact_selection.py tests/test_web_workflow_selection_concurrency.py tests/test_web_generation_topology.py tests/test_mcp_preview_and_generation.py tests/test_generation_selected_takes.py <new test files>` -- expected: pass; repeat with `sqlalchemy==2.1.8`
- `uvx ruff@0.17.3 check .`, `<uv env> --with sqlalchemy==2.0.52 python scripts/check_types.py`, `<uv env> python scripts/test_lanes.py check`, `<uv env> python scripts/check_docs.py` -- expected: clean (except the known `nemo_normalizer.py:98`)
- `python scripts/generate_web_contract.py`, `npm --prefix web run generate-api`, `npm --prefix web run build`, `npm --prefix web run check:assets`, `npm --prefix web run quality`, `npm --prefix web run test:unit` -- expected: regenerated files differ only by the new parameter, and the checks pass
- `<uv env> python _bmad-output/scripts/admission_writer_holds.py --root <fresh real copy> --mode missing-only --missing 8 --runs 3` and the `selection` and `plan-store` routes -- expected: targets above met; recorded in `baseline-2-1.md`

## Code Review

### 2026-11-13

**Code review complete.** 4 `decision-needed`, 5 `patch`, 0 `defer`, 22 rejected. Lenses: blind-hunter, edge-case-hunter, verification-gap and intent-alignment, on `9f0d0441..4d3ea915`.

- [x] [Review][Decision] MCP listing has no way to request the whole operation_json — MCP's default view is `full`, which used to pass the whole operation_json through. The server now defaults to `operation=summary`, and the MCP client never sends `operation`, so an MCP caller cannot ask for `mapping`/`lineage`. For example, a resegmentation draft's mapping over a 24,000-character range exceeds 4 KB. Options: (a) add `operation: "summary"|"full"` (default summary) to the MCP `list_generation_segments` input, pass it through, and name it in the tool description and audiobook guide; (b) send `operation=full` when `view=full`, which brings back the 900 KB MCP pages; (c) keep as is: MCP gets the summary plus `operation_json_omitted`. Recommended: (a) — it matches the intent's "unless asked for it"; the new MCP field is public surface. **Owner (2026-11-13): (a).** Done: `operation` on the MCP tool input, client and registration, with the tool description; tests in `tests/test_generation_segment_operation_summary.py`; fork release note extended.
- [x] [Review][Decision] Missing-only admission commit hold 1.204 s against AC 1's 0.75 s — carried over from the first review (triage #1) with no recorded answer. Options: (a) accept 1.204 s and amend AC 1; (b) keep AC 1 and add a story for runs that own preserved takes without copying them; (c) keep this story open. Recommended: (b) — the remaining cost is SQLite's own write of 5,159 indexed rows on story 9's floor, which this plan's approach cannot remove. **Owner (2026-11-13): (b).** Story 2.16 added to the epic's `tickets.toml` and now owns AC 1's target.
- [x] [Review][Decision] Upstream task open — carried over from the first review (triage #2). The work is now committed one commit per change (`c01fc544`, `230b5d73`, `59e69e9f`, `c810a683`, `4d3ea915`), so section 3 can run. Options: (a) build the focused branches now and run `check_pr_branch.py`, with pushes and PRs left to the owner; (b) close the story without them and track upstream separately. Recommended: (a). **Owner (2026-11-13): (a).** Done: on the owner's word the fixes were committed on the epic branch (`19179f5d`, `ce005f3f`, `ec5b3c81`). Five branches were built in `Pandrator-pr` from upstream `f449f03f` (see `upstream-pr-drafts.md` section 12). All pass `check_pr_branch.py`, ruff and the lane check, and their focused tests pass under SQLAlchemy 2.0.52 and 2.1.8 (`%USERPROFILE%\pandrator-perf\work-2-15-pr\`). Pushes, PRs, the pre-review and the fork PR review stay owner steps (section 3, steps 5, 8 and 9).
- [x] [Review][Decision] Scoped edge read may cut lineage through a session-less or foreign artifact — `Artifact.session_id` is nullable. `_edges(session, session_id)` drops edges whose child has no session, where the old walk continued through them. Unverified (maybe-false); if real, the selection save restores fewer stages. Options: (a) widen the scoped read to children in the session or with no session, and add a test with a session-less ancestor; (b) leave it, recording why no such lineage exists. Recommended: (a) — cheap, and it keeps the hold small. **Owner (2026-11-13): (a).** Done: `_edges` reads children in the session or with no session; `test_selection_walk_passes_through_a_session_less_ancestor` fails without the change.
- [x] [Review][Patch] Bulk copy untested past one 500-take chunk [tests/test_generation_preserved_take_clones.py]
- [x] [Review][Patch] Conflict tests skip the artifact-mismatch, deleted-artifact and stale-identity branches [tests/test_generation_preserved_take_clones.py]
- [x] [Review][Patch] MCP non-full view forwards the server's `operation_json_omitted` without its own projection [pandrator_mcp/tools/generation.py:208]
- [x] [Review][Patch] `_clone_preserved_takes` docstring misstates the statement count (one INSERT, not one per 500) [pandrator/web/workspace.py:2768]
- [x] [Review][Patch] Test reference loop omits the original `plan_revision_id` check [tests/test_generation_preserved_take_clones.py:31]

Patches applied (2026-11-13), uncommitted. Focused tests (`test_generation_preserved_take_clones`, `test_generation_segment_operation_summary`, `test_web_artifact_selection`, `test_mcp_preview_and_generation`, `test_mcp_generation_registration`, `test_mcp_application_client`, `test_generation_missing_only`, `test_web_workflow_selection_concurrency`, `test_mcp_architecture`): 197 passed, 1 skipped under SQLAlchemy 2.0.52 and 2.1.8 (uv env); ruff clean; types clean except the known `nemo_normalizer.py:98`; `test_lanes.py check` passes. The fixes are committed and the upstream branches are built, so the story is marked `done` under the owner's closing rule. The plan's Upstream task stays open for the push and the PRs.

Rejected:
- Selection save still in the writer, with no writer-hold test (false: hold 0.133 s measured; the 28.6 s was preview contention, fixed in replay-4).
- 20-52 s Run waits not shown gone (false: replay-4 has 0.78-4.06 s).
- No admission-path hunk for MCP regenerate (false: the cause was abandoned previews; replay-4 has 0.216 s).
- No evidence for R1/R5 (false: replay-4 has locked 0 and WAL 7-12 MB).
- Clone checks use pre-captured row_state (false: rechecked equal at the writer's start; changed columns read fresh).
- REST default changed with no release note (false: `fork-release-notes.md` line 15; the default is the frozen intent).
- OpenAPI response lacks `operation_json_omitted` (false: the response schema declares no page fields).
- Summary still loads the whole column (low; the payload goal is met; a deferred column adds complexity).
- Restore mutation response still returns the whole operation (low, pre-existing; would add a mutation parameter).
- Other plan-store callers parse inside the writer (low; they get the bulk insert; moving the parse restructures four callers the plan scoped out).
- Prepared rows unchecked against settings (false: the only caller passes the same settings and does not mutate records).
- Row preparation not cancellable mid-way (low; checked before and after; plumbing is extra complexity).
- Hand-coded column defaults drift silently (false: the bulk tests compare against ORM references that apply the defaults).
- Two bulk-insert paths (low; both measured; the change log explains `render_nulls`).
- `identities[segment_id]` KeyError, raised twice (false: same subscript as the replaced loop).
- Read-only POST allowlist unenforced or path mismatch (low; no URL prefix is served; an architecture test is extra machinery).
- Unscoped `_edges` in three sibling functions (low; recorded in the change log as outside the measured hold).
- Test hygiene (low; ruff passes; follows existing patterns).
- waitress request without `path` (false: the real-waitress preview test would fail).
- Commit `4d3ea915` message says chunked reads and per-chunk inserts (low; already committed; rewording is the owner's call).

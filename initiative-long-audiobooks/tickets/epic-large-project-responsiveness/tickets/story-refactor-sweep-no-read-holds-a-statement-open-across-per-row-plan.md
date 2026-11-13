---
title: 'Refactor sweep: no read holds a statement open across per-row work'
type: refactor
ticket: '12'
created: '2026-11-13'
status: done
baseline_revision: 'f7d1e865497190c0caa9556bd8c127ef1548ee54'
route: oneshot
route_source: auto
risk: low
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 8 made the segment search read its rows in full before matching, but left the other `snapshot_session` callers and streaming loops unaudited. A read that keeps an SQLite statement open while it does per-row work holds a WAL read mark for that whole time, so a checkpoint cannot copy past it. The sweep found exactly one such read in the app: the job queue's candidate scan, `JobQueue._next_claimable_job_id` (`pandrator/web/jobs.py:1031-1070`), streams `yield_per=128` rows and checks each one's resource keys while the statement stays open. It runs on every worker poll while any resource is claimed, first in a plain `session()` and then inside the claim's `immediate_session()`.

**Approach:** Read the candidates in keyset pages of 128 rows, each fetched in full with `.all()` before its rows are checked, ordered by `(created_at, id)` as today, stopping at the first claimable job. That keeps today's early stop and memory bound, and closes the statement before any per-row work. The claim picks the same job as before, and no response, schema or other query changes. Tests come first and fail on the unchanged code. Do not stage or commit.

**Decision (owner, 2026-11-13):** Leave `jobs.py` unchanged; this replaces the Approach above. Measured on 5,000 queued jobs, the scan's statement stays open 1.3 ms when the first job can run and 10.8 ms when every job is blocked, and its per-row work is in-memory set checks. Keyset pages cost up to 4 times as much, and a single full read holds the statement open 6 times longer in the common case. The scan is recorded as checked and kept, the built change and its two tests are reverted, and the story's outcome is the audit with no code change.

</frozen-after-approval>

## Implementation Notes

Oneshot: one function in `jobs.py` changes (about 20 lines) plus two tests in an existing file and lane, so a full plan adds nothing.

### Sweep: what keeps a statement open

Probe (`select` forms on a 500-row WAL table, first row taken, then a write and `PRAGMA wal_checkpoint(PASSIVE)` from another connection), same result under SQLAlchemy 2.0.52 and 2.1.8:

- Finished after the first row (the ORM fetches every row on first fetch): `select(Model)`, `select(Model.col)`, `.mappings()` on those, `select(subquery.c...)` where the subquery or `union_all` comes from ORM selects, `select(Model, subquery.c...)`, and an ORM select with a `json_each` `IN` subquery.
- Still open: `select(Model.__table__)`, `select(Model.__table__.c...)`, `text()`, raw `sqlite3` cursors, and any `yield_per`.

So only those four forms can hold a statement across per-row work. Every app site using one (excluding shipped migrations):

| Site | Form | Outcome |
|------|------|---------|
| `jobs.py:1059` `_next_claimable_job_id` | `yield_per=128`, per-row key checks | Kept (owner decision): in-memory checks; statement open 1.3-10.8 ms per poll at 5,000 jobs, less than either alternative in the common case |
| `projects/copy_snapshot.py:192` `_rows` | Core table | Kept: the list comprehension only copies each row; nothing else runs until all are read |
| `projects/copy_snapshot.py:659, 689, 729` media revision headers and rows | Core table | Kept: each row is only stored in a dict |
| `projects/copy_snapshot.py:365-381` `_unsupported` | reflected Core table | Kept: one `count()` row per chunk |
| `session_purge.py:458` external references | Core column | Kept: `.scalars().all()` reads in full before the loop |
| `public_links/authorization.py:32, 54, 82, 108, 115` | `text()` | Kept: `scalar_one_or_none`, `.first()` or `.scalars().all()` |
| `artifacts.py:390` | `text("BEGIN IMMEDIATE")` | Not a read |
| `database.py:141, 272` | raw `sqlite3` | Kept: `fetchone()` |
| `legacy_migration.py:83, 102, 313` | raw `sqlite3` | Kept: `fetchone()` or a list comprehension of `dict(row)`; one-time import |
| `model_maintenance.py:124` (Manager database) | raw `sqlite3` | Kept: `fetchall()` before the loop |
| `pandrator_manager/state/migrations.py:274`, `doctor.py:88` (Manager database) | raw `sqlite3` | Kept: a set of versions, and `fetchall()` |
| `pandrator/web/migrations/versions/0011, 0015, 0022` | Core / raw loops with writes | Out: shipped revisions are never edited, and they run once before the server starts |

### Sweep: `snapshot_session` callers

290 calls in 111 files (`grep -rn "snapshot_session()" pandrator`). None of them reaches a still-open form above except through rows the table already marks kept: `projects/copy_jobs.py` (6) reaches `copy_snapshot`, `public_links/*` reach `authorization`, and `session_purge.py` (4) is listed above. All the others read through ORM-enabled statements, which the probe shows are buffered: per-row work in them runs with no statement open. Inside a `snapshot_session` the explicit `BEGIN` holds the snapshot for the whole block whatever the statements do; how long those blocks last was stories 9 to 11's work, not this sweep's. No function yields inside a `with ...session()` block (AST scan of `pandrator/`), and the SSE stream (`api_routes.py:1793`) opens a fresh snapshot per poll.

Callers by file (calls): access/sound_execution_policy (1), accounts/browser_sessions (1), accounts/commands (1), agent_run_commands (1), api_routes (21), artifact_routes (8), audio_import_routes (2), audiobook_generation_preparation (1), audiobook_setup (1), auth (2), automation_routes (1), bundles (3), comparison_export_render (3), context_budget (1), credentials (3), delivery_publication (1), delivery_runtime (3), dispatch (4), dispatch_preview (1), evidence_catalogue (1), evidence_execution (4), generation_admission (1), generation_history_reads (2), generation_operations (2), generation_provider_preflight (1), generation_recast (1), generation_routes (2), generation_runtime_speech (1), generation_speech_preparation (1), generation_summary (1), global_settings (2), google_voice_design (3), google_voice_design_routes (2), http_lifecycle (1), http_session_access (1), jobs (1), knowledge_routes (1), outcome_plans (1), pdf_publication (1), project_cast_settings (1), project_export_bundle_routes (1), project_export_bundles (5), project_folder_routes (1), project_operations (2), project_readiness (1), project_segment_settings (1), project_sharing_routes (3), project_tts_settings (1), projects/copy_jobs (6), provider_routes (8), provider_settings (1), public_link_http (1), public_links/resources (1), public_links/reviews (8), public_links/service (5), quick_transcription (5), quick_transcription_execution (3), quick_transcription_routes (1), service_routes (3), session_flow_routes (1), session_purge (4), session_purge_job_routes (1), session_purge_routes (1), session_routes (2), sound_generation_worker (1), sound_imports (1), sound_mix_admission (2), sound_mix_claims (2), sound_mix_service (1), sound_mix_snapshot (1), sound_project_service (1), sound_resolution_service (1), sound_resolution_worker (2), sound_routes (2), sound_service (6), sound_workflow_outputs (1), source_cleaning_dispatch (1), source_cleaning_dispatch_routes (1), source_routes (2), speech_catalogue_access (1), speech_inputs (4), speech_plan_workspace (1), stt_providers (1), subtitle_review (3), subtitle_review_mutations (1), training_lifecycle (1), translation_project_routes (4), tts_catalogue_service (3), video_previews (4), voice_catalog_routes (6), voice_library (2), voice_recording_uploads (1), voice_routes (9), web_research (2), workflow_audio_preview (3), workflow_generation_execution (4), workflow_handlers (10), workflow_inputs (1), workflow_output_assembly (4), workflow_plans (2), workflow_routes (4), workflow_snapshot (1), workflow_source (5), workflow_source_cleaning (2), workflow_speech_execution (4), workflow_voice (13), workflows (1), workspace (2), workspace_recovery_routes (1), workspace_routes (7), workspace_settings (2). All under `pandrator/web/`; outcome for each: kept, buffered ORM reads.

### The change

- `pandrator/web/jobs.py`: add `Job.created_at` as a third selected column of the candidate `statement` (`session.scalar(statement.limit(1))` still returns `Job.id`). Replace the `yield_per` loop with keyset pages: `statement.where(or_(Job.created_at > last_created, and_(Job.created_at == last_created, Job.id > last_id))).limit(128)`, `.all()`, then the unchanged key check per row; stop at the first claimable job, or when a page returns fewer than 128 rows. `Job.created_at` is non-null. In the plain session a page may see rows another worker changed since the previous page; that only affects the hint, which `claim` already rechecks under `immediate_session`.
- `tests/test_job_resource_scheduling.py` (lane `web-18-serial`, so no lane change), written first:
  - Open statement: inside `database.session()`, call `_next_claimable_job_id` with more than 128 blocked candidates and one claimable job after them. Patch `pandrator.web.jobs.canonical_resource_key` so each call commits a write to a scratch table from a separate `sqlite3` connection and records whether `PRAGMA wal_checkpoint(PASSIVE)` copied every frame. Assert every call recorded a complete checkpoint, and that the claimable job is returned. Never call it inside `claim()`, whose `BEGIN IMMEDIATE` would block the probe's write.
  - Ties across a page boundary: 150 blocked waiters and one claimable job all share one `created_at`, with ids ordered so the claimable job sorts last; `claim()` returns it. The existing 150-waiter test covers distinct timestamps.
- No user-visible change, so no release-note line.

### Built, then held at review

- Built as planned. The open-statement test failed on the baseline (`all([True, False, False, ...])`) and passes now. uv env: the six focused job and WAL files give 80 passed under SQLAlchemy 2.0.52 and 2.1.8; lane `web-18-serial` gives 717 passed, 3 skipped; ruff, vulture and `test_lanes.py check` are clean; types report only `nemo_normalizer.py:98`.
- The quick review asked whether each page re-reads the queue. `EXPLAIN QUERY PLAN` shows that both the `or_`/`and_` cursor and a row-value cursor seek `idx_jobs_status_created`. But every query still sorts the candidates it matches in a temporary B-tree. So when every job is blocked, page k re-sorts the rows after its cursor, and the total is about n²/256 rows. The baseline sorted once, too: SQLite visits every candidate before returning the first row, whatever `yield_per` does.
- Measured on a scratch database, best of 5, uv env, with every job blocked behind one held key (the case this function exists for):

| Queued jobs | baseline `yield_per` | keyset pages of 128 | one full read |
|-------------|----------------------|---------------------|---------------|
| 500 | 1.4 ms | 2.3 ms | 1.5 ms |
| 2,000 | 4.3 ms | 10.8 ms | 5.0 ms |
| 5,000 | 10.6 ms | 42.9 ms | 12.6 ms |

- So the frozen Approach (keyset pages, to keep the early stop and memory bound) costs up to 4 times the baseline per worker poll. One `.all()` of the single query costs about the same as the baseline, needs no cursor, and so has no tie or format edge cases. It holds every candidate's id and keys in memory, a few hundred KB at 5,000 jobs. Choosing between them changes the frozen Intent, so it is an Open Question for the owner.

- Second measurement, 5,000 queued jobs, best of 7, timing how long the statement stays open:

| Case | baseline: open = total | one full read: open | one full read: total |
|------|------------------------|---------------------|----------------------|
| every job blocked | 10.8 ms | 8.1 ms | 11.1 ms |
| first job claimable | 1.3 ms | 8.3 ms | 8.3 ms |

- This corrects the bullet above: on a populated queue the baseline returns the first claimable job in 1.3 ms, so SQLite does not sort every candidate first (that plan came from an empty database). Most of a full read's time is fetching and decoding rows, and the statement stays open for all of it. A full read shortens the open time by 2.7 ms per poll only when every job is blocked. When the first job can run, it holds the statement open 6 times longer than the baseline.

### Outcome

The owner chose to leave `jobs.py` unchanged (decision in the Intent). `git restore` reverted `pandrator/web/jobs.py` and `tests/test_job_resource_scheduling.py`, and `git diff --stat f7d1e865` is now empty: the story changes no code, so the ticket's "a test covers each read that changed" has nothing to cover. The checks recorded above ran against the built change; the reverted tree is byte-identical to the baseline, which story 11 verified.

## Review Triage Log

Pass 1, quick lens, on the built keyset change. 3 findings: 1 medium, 0 low, 1 false, 1 maybe-false.

- medium, HALT, then resolved by the owner's decision: keyset pages cost more per poll than the old scan. The stated mechanism ("the cursor can't seek") is false, since `EXPLAIN QUERY PLAN` shows both cursor forms seeking `idx_jobs_status_created`. The cost is real anyway: up to 4 times the baseline when every job is blocked (Implementation Notes). The change was reverted.
- maybe-false, moot: a stored `created_at` in another string format could make the keyset repeat a page forever. Every writer goes through the ORM `DateTime` type today. The keyset is gone.
- false, moot: the tie test hangs, rather than fails, under a cursor that repeats rows. That test is gone with the change.

## Verification

`<uv env>` is as defined in `AGENTS.md`.

**Commands:**
- `git diff --stat f7d1e865497190c0caa9556bd8c127ef1548ee54` -- expected: empty, since the owner's decision leaves the code unchanged.
- The commands below were run on the built keyset change before it was reverted; they apply again only if the code changes.
- `<uv env> --with sqlalchemy==2.0.52 python -m pytest -q tests/test_job_resource_scheduling.py tests/test_job_heartbeat_expiry.py tests/test_job_terminal_lease_fencing.py tests/test_web_job_concurrency.py tests/test_audio_cpp_job_resources.py tests/test_wal_size.py` and the same with `sqlalchemy==2.1.8` -- expected: all pass.
- `<uv env> python scripts/test_lanes.py run web-18-serial` -- expected: pass.
- `uvx ruff@0.17.3 check .`, `<uv env> vulture`, `<uv env> python scripts/test_lanes.py check` and `<uv env> --with sqlalchemy==2.0.52 python scripts/check_types.py` -- expected: clean apart from `nemo_normalizer.py:98`.

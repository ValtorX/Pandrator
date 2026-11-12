---
title: 'Heavy reads stop holding the WAL back'
type: bugfix
ticket: '8'
created: '2026-11-12'
status: done
baseline_revision: 'b9df5a5c762bdbde035f1f2f929743044319ee46'
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** On 2026-11-12 a fresh synthetic copy ran a 60 s `plan_status` burst: 4 heavy clients abandoning at 15 s and 24 `auth_status` clients at a 0.5 s pace. The run had no `database is locked` records and no audit spools, but the WAL grew from 0 to 60 MB during the load. Nothing ever shrinks it: SQLite reuses a WAL from its start after a complete checkpoint but never truncates the file, so the install keeps its high-water size. The live install's copy carried a 59 MB WAL. Investigation found no HTTP heavy read holding one snapshot for its whole run, since all of them use plain `session()`, so each SELECT is its own short read. The one exception is the segment search (`q`), which iterates a Core cursor lazily while it matches every row of the revision. An open reader of that kind stops a checkpoint from copying anything (probe: `checkpointed 0`).

**Approach:** Have every connection set `PRAGMA journal_size_limit`, so the first write after a complete checkpoint truncates the WAL back to the limit once load ends. Read the search rows in full before matching, so no heavy read keeps a statement open across per-row work. Extend the timing harness to record the WAL size over a burst and after it, and prove the WAL shrinks with no lock failures on both data roots.

## Boundaries & Constraints

**Always:** WAL mode, `busy_timeout` 30000, `foreign_keys` and SQLite's default auto-checkpoint stay as they are. The size limit is one module constant in `database.py`, applied in the existing `connect` listener. Search results, their order and their match offsets are unchanged. No audit event is lost, and audit and token write behaviour is unchanged. Repo tests count frames, bytes and checkpoint results, with no wall-clock assertions. The harness reads WAL size by `stat` only and never checkpoints or writes to the database itself.

**Never:** An explicit or background `wal_checkpoint` (PASSIVE, FULL, RESTART or TRUNCATE) run by the app, because the blocking modes hold the writer and would make audit writes spool. No change to `wal_autocheckpoint`, `synchronous`, the pool, `snapshot_session` or `immediate_session`. Do not touch admission or the opening check, whose snapshot scopes belong to stories 9 to 11. No change to the storage engine, OpenAPI or response fields. Do not stage or commit.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Load ends after growth | WAL grew past the limit while readers overlapped; then readers finish and two writes commit | WAL file at most the limit | none |
| Normal cycling | WAL stays under the limit between auto-checkpoints | no truncation; behaviour as today | none |
| Reader still open | a `snapshot_session` holds a snapshot while writes commit | WAL may grow; no truncation until the reader ends | none |
| Search listing | `list_segments` with `q` on a revision | rows fetched in full before matching; a checkpoint during matching copies every frame | none |
| New connection | any engine connection | `PRAGMA journal_size_limit` reads the constant | a failing pragma closes the connection, as the listener does today |

</frozen-after-approval>

## Code Map

- `pandrator/web/database.py:53-76`: `create_database_engine`. The `connect` listener sets three pragmas. Add `WAL_SIZE_LIMIT_BYTES = 8 * 1024 * 1024` at module level and `PRAGMA journal_size_limit = {WAL_SIZE_LIMIT_BYTES}` after `journal_mode`. Read the constant at connect time, so a test can monkeypatch it before it builds a `Database`. At 8 MiB the limit is about twice one auto-checkpoint cycle (1,000 pages of 4 KiB), so normal cycling never truncates. `upgrade_database` uses raw `sqlite3.connect` for migrations and needs no change.
- SQLite behaviour this rests on: after a complete checkpoint, the next write transaction restarts the WAL, and that transaction's commit truncates the file to `max(limit, bytes needed)`. Restart needs no reader on a WAL read mark. A reader that starts once every frame has been copied reads from the database file and does not block it. After load, each request's audit commit supplies those writes: the first one auto-checkpoints, because the WAL is over 1,000 pages, and the second one restarts and truncates.
- `pandrator/web/generation_segment_reads.py:277-296`: `searchable_rows = session.execute(select(id, text, optimized_text)...)` is iterated lazily with `literal_matches` per row. Add `.all()`, keeping the loop body.
- No other heavy read pins a snapshot (sweep of `list_segments`, `revision_history`, `grouped_revision_history`, `speech_plan_status`, `revision_detail` and the request lifecycle). All of them use plain `session()`, the ORM results are buffered (probe), and none of them writes. SSE (`api_routes.py:1805`) opens a fresh `snapshot_session` on each 1 s poll.
- Test templates: `tests/test_sqlite_write_retries.py:25-33` (a `Database` on `tmp_path`, `immediate_session` DDL, `dispose`), and the lane is `web-18-serial` (`scripts/test_lanes.py:935`). The search test can reuse the 12-segment cast plan fixture chain from `tests/test_audio_identity_cost.py`, imported as `tests/test_abandoned_reads.py` does, including its autouse `no_network`. A plain plan is enough if seeding one is simpler (`tests/test_generation_search.py`).
- `_bmad-output/scripts/large_project_timing.py`: `read_burst` (614-671), the burst block in `main` (1266-1287), `print_table` (1083-1117) and `parse_args` (1125-1176). The parent knows `args.root`, so the WAL path is `args.root / "pandrator.sqlite3-wal"` (missing counts as 0). `stop_server` disposes the engine and then calls `os._exit`. The final size is therefore not meaningful, so sample before stopping. The harness tests are in `_bmad-output/scripts/test_large_project_tools.py`.

## Tasks & Acceptance

**Execution:**
- [ ] `tests/test_wal_size.py` (new): write these first and see them fail on the unchanged code.
  - A new connection reports `journal_size_limit` equal to `WAL_SIZE_LIMIT_BYTES`.
  - Growth then shrink: monkeypatch the limit to 64 KiB. Hold a `snapshot_session` open on one thread and commit writes (wide rows) until the WAL is well over the limit, asserting the file grew. Close the reader, then commit two small writes and assert the WAL file is at most the limit.
  - Normal cycling: keep the default limit, and grow the WAL to about 1 MiB with a reader held. Then release the reader and commit two writes. Assert the file is not truncated: its size is unchanged, because it is under the limit.
  - Search: during a `list_segments(q=...)` call, patch `literal_matches` (as imported in `generation_segment_reads`) to wrap the original. On its first call it commits one write from a separate `sqlite3` connection and runs `PRAGMA wal_checkpoint(PASSIVE)`, recording `(busy, log, checkpointed)`. Assert `log == checkpointed`, and that the listing's items equal a run without the patch.
- [ ] `pandrator/web/database.py`: add the constant and the pragma.
- [ ] `pandrator/web/generation_segment_reads.py`: add `.all()` on the search rows.
- [ ] `scripts/test_lanes.py`: add `tests/test_wal_size.py` to `web-18-serial` beside `tests/test_sqlite_write_retries.py`.
- [ ] `_bmad-output/scripts/large_project_timing.py`: a `WalSampler` (a thread that `stat`s the WAL every 1 s) runs across the burst. `results["burst"]["wal"]` records `start_bytes`, `peak_bytes`, `burst_end_bytes`, `after_load_reads_bytes` (after the small after-load reads) and `settled_bytes`. `settled_bytes` is taken after `wait_for_idle` and then 3 sequential `auth_status` calls. `print_table` prints one WAL line. Cover the sampler, a missing WAL file, and the results keys in `test_large_project_tools.py`.
- [ ] `_bmad-output/fork-release-notes.md`: one Fixes line saying that after heavy use the database's write-ahead log shrinks back to a small size, instead of staying at its largest.

**Acceptance Criteria:**
- Given a fresh copy of `synthetic`, when the harness runs `--only burst --burst-operation plan_status --light-workers 24 --light-pace 0.5`, then the server log reports 0 database-locked records and 0 audit spools, and `settled_bytes` is at most 8 MiB and below `burst_end_bytes`.
- Given a fresh copy of `real`, when the same run is made, then the same holds.
- Given the same run against the baseline commit's app on another fresh copy of each root (reference only), when it finishes, then its WAL figures are recorded beside this change's in Implementation Notes.
- Given the existing listing-search, SQLite retry, audit and abandoned-read tests, when run, then they pass unchanged.

## Implementation Notes

WAL sizes in MiB from `--only burst --burst-operation plan_status --light-workers 24 --light-pace 0.5` on fresh copies, each with 0 audit spools and 0 database-locked records. Environment: uv env, SQLAlchemy resolved from ranges.

| Root | Build | start | peak | burst end | after-load reads | settled |
|------|-------|-------|------|-----------|------------------|---------|
| synthetic | this change | 0.04 | 52.3 | 52.3 | 8.0 | 8.0 |
| real | this change | 0.04 | 12.4 | 8.0 | 8.0 | 8.0 |
| synthetic | baseline (`b9df5a5c`) | 0.0 | 56.6 | 56.3 | 56.6 | 56.6 |
| real | baseline (`b9df5a5c`) | 0.0 | 13.4 | 13.4 | 13.4 | 13.4 |

- Real: the WAL had already shrunk to the limit (8,388,608 bytes) before the burst ended, because the light `auth_status` writes kept restarting it once the heavy readers left. So `settled_bytes` equals `burst_end_bytes` instead of being below it; it is at most 8 MiB, and well below the peak and the baseline.
- The search rows are an ORM-enabled `select`, which SQLAlchemy already buffers with `fetchall`, so the new `.all()` is defensive and the search test passes with or without it. It guards against the statement staying open across matching if the query form changes.
- The new search test patches `generation_search.literal_matches`, since `list_segments` imports it inside the function.
- The growth test holds the reader until the WAL passes 5 MiB, because SQLite's auto-checkpoint only fires at 1,000 frames; a smaller WAL would never be checkpointed and so never truncated.
- The Intent's claim that the search iterates a lazy cursor was wrong: the ORM buffers that select, as noted above. The fix that matters for R5 is the size limit.
- Matrix audit (step 3): the "New connection" row's failure path had no test. `tests/test_web_database_bootstrap_lifetime.py` now lists the size-limit pragma among the configuration statements, so a failure there is shown to close the connection and its cursor (20 passed).
- Step 3 verification, uv env: the six focused files gave 83 passed under SQLAlchemy 2.0.52 and the same under 2.1.8. Harness tests: 27 passed. Lane `web-18-serial`: 712 passed, 3 skipped. Ruff, `test_lanes.py check` and vulture are clean, and types report only `nemo_normalizer.py:98`.

## Plan Change Log

## Review Triage Log

Pass 1, quick lens. 7 findings: 0 high, 0 medium, 5 low, 2 false.

- false, rejected: AC2 is called unmet on `real` because `settled_bytes` equals `burst_end_bytes` (8 MiB). On `real` the WAL already shrank from its 12.4 MiB peak to the limit before the burst ended, and it stayed there afterwards. That is the "shrinking or stable once the load ends" the ticket's Verify asks for. "Below burst end" was this plan's wording, not a defect in the code, and the fix would edit the plan.
- low, rejected: the search test passes with or without `.all()`. The ORM-enabled select was already buffered, as Implementation Notes record. No code makes the test discriminate, the `.all()` costs nothing, and the fix would remove one or the other for no behavioural gain.
- low, patch: `test_wal_under_the_limit_is_not_truncated` grew the WAL to only about 1 MiB, so it never crossed an auto-checkpoint and could not fail. It now crosses one under the limit and asserts the size is unchanged.
- low, patch: the WAL Fixes line sat under story 7's heading for upstream PR #132. It now has its own story 8 heading.
- low, patch: in the harness, `peak_bytes` ignored `after_load_reads_bytes`. It is now the maximum of every recorded figure.
- low, patch: the harness discarded the three settle calls' results, so a failed settle still recorded `settled_bytes`. Their statuses are now recorded.
- low, patch: `wal_bytes` treated any `OSError` as a missing file. It now catches only `FileNotFoundError`.

Checks after the patches, uv env:
- The seven focused files, now including `test_web_database_bootstrap_lifetime.py`, give 103 passed under SQLAlchemy 2.0.52 and the same under 2.1.8.
- Harness tests: 27 passed.
- Lane `web-18-serial`: 712 passed, 3 skipped.
- Ruff, `test_lanes.py check` and vulture are clean. Types report only `nemo_normalizer.py:98`.
- The burst timings were not rerun after the patches. The patches touch only tests, the harness's bookkeeping and the release note.

## Design Notes

Why a size limit and not an app-run checkpoint: R5 asks that the WAL stop growing once load ends. During load, overlapping short readers plus each request's audit write keep the WAL from restarting, and that is SQLite working as designed. A `RESTART` or `TRUNCATE` checkpoint could cap growth during load, but it holds the writer while it waits for readers. Audit writes give up after 250 ms and spool, and R5 forbids bulk spooling. `journal_size_limit` costs nothing while load runs, and it truncates on the first restart that load allows.

Measured growth under load: 60 MB in 60 s with 28 concurrent clients, which is far heavier than the UI and one MCP client. It is bounded by the length of the load and returned once the load ends.

## Verification

`<uv env>` is as defined in `AGENTS.md`.

**Commands:**
- `<uv env> python -m pytest -q tests/test_wal_size.py tests/test_sqlite_write_retries.py tests/test_generation_search.py tests/test_abandoned_reads.py tests/test_routine_request_writes.py tests/test_web_database_efficiency.py`: expected to pass under SQLAlchemy 2.1.8 and 2.0.52.
- `<uv env> python scripts/test_lanes.py run web-18-serial`: expected to pass.
- `uvx ruff@0.17.3 check .`, `<uv env> vulture`, `<uv env> python scripts/test_lanes.py check` and `<uv env> --with sqlalchemy==2.0.52 python scripts/check_types.py`: expected clean, apart from `nemo_normalizer.py:98`.
- `<uv env> python -m pytest -q _bmad-output/scripts/test_large_project_tools.py`: expected to pass.
- `<uv env> python _bmad-output/scripts/large_project_timing.py --root %USERPROFILE%\pandrator-perf\work-2-8\<fresh copy> --only burst --burst-operation plan_status --light-workers 24 --light-pace 0.5 --timeout 900`: run on `synthetic` and `real` for this change, and on further fresh copies for the baseline. Expected: as the first three ACs say.

## Code Review

### 2026-11-12

Code review complete. 0 decision-needed, 5 patch, 0 defer, 20 rejected. Lenses: blind-hunter, edge-case-hunter, verification-gap, intent-alignment; none failed.

- [x] [Review][Patch] Release note says the WAL shrinks after heavy use without saying it needs the next write [_bmad-output/fork-release-notes.md:15]
- [x] [Review][Patch] WAL sampler stops at burst end, so peak_bytes can miss later growth [_bmad-output/scripts/large_project_timing.py:1333]
- [x] [Review][Patch] `_grow_under_reader` loops forever if the WAL never grows [tests/test_wal_size.py:96]
- [x] [Review][Patch] Search test bypasses the short Windows temp root (`tempfile` in place of `tmp_path`) [tests/test_wal_size.py:143]
- [x] [Review][Patch] `tests/test_wal_size.py` is out of alphabetical place in `web-18-serial` [scripts/test_lanes.py:936]

Rejected:
- low: the only read-shortening change is not on the burst's path. Heavy reads were already short (Implementation Notes); the verified outcome comes from the size limit, and the fix would edit the frozen intent.
- false: the size limit does not bound growth during load. R5 asks only that growth stop once load ends; Design Notes accept growth during load.
- low: the harness test does not assert settled at most peak or under the limit. On the small seeded root that could never fail; the real-root runs check it.
- false: the WAL tests use a bare `Database`. That is the plan's chosen repo-test surface; the harness runs are the end-to-end proof.
- low: no test asserts zero locked errors or spools. No production change touches those paths, and the recorded runs show 0 and 0.
- false: the diff carries no Verify output. The runs are in Implementation Notes.
- false: the release note describes only the after-load outcome. That matches what changed; the wording is handled by the first patch.
- false: `.all()` loads every segment into memory. The ORM select was already fetched in full before matching, so memory use is unchanged.
- false: other streaming loops and `snapshot_session` callers were not audited. ORM results are buffered the same way, the Code Map records the sweep, and snapshot scopes belong to stories 9 to 11.
- low: the 5 MiB grow target is coupled to the 8 MiB constant. It matters only if the constant is lowered, and then the setup assert fails naming the limit.
- false: the WAL tests are slow. Measured 0.17 s per grow test and 1.57 s for search.
- false: the release note leaves out the search fix. `.all()` changes no behaviour.
- low: settled is sampled even when `wait_for_idle` returned None. The results already record that None and the audit-spool count.
- low: `print_table` rounds to 0.1 MiB and omits settle statuses. The JSON keeps exact bytes and statuses, and missing keys are already skipped.
- false: raw `sqlite3.connect` writers miss the limit. The two cited sites only read; every app writer goes through `create_database_engine`.
- false: the `int()` cast is redundant. It keeps the interpolated SQL an integer whatever a test patches in.
- low: `.all()` changes nothing and the search test passes without it (verification-gap). Already recorded in Implementation Notes; the test guards against a switch to streaming.
- low: the sampler thread dies on a non-`FileNotFoundError` `OSError`. Narrowed on purpose in pass 1; the traceback prints and `stop()` raises, so it is not silent.
- low: settle runs when `wait_for_idle` is None (edge-case). Same as above.
- low: the `.all()` claim (edge-case). Same as the verification-gap item.

Patches applied:
- The release note now says the log shrinks the next time the app saves anything.
- The harness's sampler keeps running until `settled_bytes` is taken, so `peak_bytes` covers the after-load reads and the idle wait.
- `_grow_under_reader` stops after 2,000 rounds and fails with the size it reached.
- The search test uses `tmp_path`.
- `tests/test_wal_size.py` now sits after `test_subtitle_settings_snapshot.py`.

Checks after the patches, uv env: `test_wal_size.py`, `test_generation_search.py` and `test_web_database_bootstrap_lifetime.py` give 36 passed under SQLAlchemy 2.0.52 and the same under 2.1.8. Harness tests: 27 passed. `test_lanes.py check` and ruff are clean. Lane `web-18-serial` and the burst timings were not rerun; the patches touch only tests, lane order, the harness's sampling and the release note.

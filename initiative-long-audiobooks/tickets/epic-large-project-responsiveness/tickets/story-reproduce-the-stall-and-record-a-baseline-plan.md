---
title: 'Reproduce the large-project stall and record a baseline'
type: chore
ticket: '1'
created: '2026-11-10'
status: built
baseline_revision: 'ebefb88c8d6ab538158bfb5af8ddff4c50213bfe'
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

**Problem:** Listing, plan reads, admission and the opening check stall on a 5,000-segment book. Nothing can reproduce the stall on demand or say where the time goes, so the fixes in stories 2-8 would have to be guessed.

**Approach:** Build a fork-files seed script for a synthetic 5,000-segment project, plus a 189-segment one. Build a timing script that starts the real server on a data root, calls the same endpoints with the same parameters as the MCP tools, and times admission and the opening check from job events. Its profile mode names the costly functions and SQL. Run it on the synthetic root and on a local copy of the real data. Record timings, top costs and proposed targets for admission and the check. Measure only, and change no application code.

**Decisions (owner, 2026-11-10):** The real copy is taken from the live `data/` folder: `pandrator.sqlite3` with its `-wal` and `-shm` files, plus `voices/` and `artifacts/`, copied while the app is closed. The plan stays whole despite running over the token guideline.
**Decision (owner, 2026-11-11):** The copy also includes `migration-web-v1.json`, the 350-byte migration marker. Without it, startup replaces the database with an empty one.

## Boundaries & Constraints

**Always:** Scripts live in `_bmad-output/scripts/` and run under `<uv env>` from the repo root, so they import the checkout's `pandrator`. Data roots stay outside the repo, under `%USERPROFILE%\pandrator-perf\`. The server uses a free port other than 8097 or 8098. The recorded results hold only timings, counts and function names: no paths, no book title, no text. Opening-check timing stops at the run's "Loading speech provider settings" progress event, after which the script cancels the run, so no TTS engine is needed.

**Never:** Write to the owner's install or its database, stop their app, or touch ports 8097/8098. Change code under `pandrator/` or `tests/`. Use wall-clock assertions in a repo test. Commit or stage.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Seed | empty `--root`, `--segments 5000` | migrated DB, owner, 5,000- and 189-segment audiobook sessions with revisions, takes, artifacts and voices | refuses a non-empty root unless `--force` |
| Time synthetic | seeded root | per-operation table: median and max of N runs, status codes, plus JSON results file | a request over `--timeout` (default 120 s) is recorded as timed out and the run continues |
| Time real copy | copied data root, existing owner | same table; the token is created in-process on the copy | refuses a root whose `pandrator.instance.lock` is held live |
| Admission | 1 and 15 segments, `regenerate` | admit duration, queue wait, check duration, read latency sampled during admission | a failed job records its error and the run continues |

</frozen-after-approval>

## Code Map

- `scripts/run_web_e2e_server.py` -- template for serving: `create_app(data_root, testing=False)`, `probe_gpu` patch, a `Worker` thread with `WorkflowHandlers`, `waitress.serve`. Use a different cookie name and port.
- `tests/test_session_view_performance.py:51-175` -- seeding template: `generation.create_plan(...segments)`, `_plan` with provenance, `_seed_reusable_takes` (artifacts registered with `IDENTITY_KEY` from `AudioIdentityContext.for_segment`, `AudioTake` active), and the `_statement_log` `before_cursor_execute` listener.
- `pandrator/web/auth.py:261,404` -- `initialize_owner`, `create_api_token(label, scopes=ALL_SCOPES)` returns the raw token. Requests use `Authorization: Bearer`.
- Endpoints and MCP defaults (`pandrator_mcp/clients/application_generation.py:192-290`):
  - `GET /api/v1/sessions/{sid}/generation-segments?cursor=0&limit=50&audio_version_side=after`. Variants: `view=compact&take_history=selected`, `fields=...`, `limit=100`, `plan_revision_id=<non-active>`. Handler `generation_segment_reads.py:160-794`; suspects l.629, 641-646, 729-751 and 754-771.
  - `GET .../generation-plan/status` (with and without `summary=true`). Handler `speech_plan_workspace.py:318-452`: `plan_signature`, a full segment load l.413-425, `grouped_revision_history`.
  - `GET .../generation-plan/revisions?limit=50` (with and without `summary=true`). Handler `generation_review.py:96-256`: audio reuse across revisions × segments.
  - `GET .../generation-plan/revisions/{id}` (non-active). `GET /api/v1/auth/status`.
- Admission: `POST .../generation-admissions` (needs `Idempotency-Key`; body `GenerationStartRequest`, `schemas.py:871`). Worker `run_generation_admission` (`generation_admission.py:200`). Duration is the job's `finished_at - started_at`, and `result_json` holds the child `job_id`.
- Opening check: `_run_generation` (`workflow_generation_execution.py:212-386`). It runs from the progress event "Checking speech plan and voice references" to "Loading speech provider settings", read from `GET /api/v1/jobs/{id}/logs`. Cancel with `POST /api/v1/jobs/{id}/cancel`.
- `pandrator/runtime/paths.py` -- the DB is always `<root>/pandrator.sqlite3`. Startup migrates to the head.
- Real data: install `data/` holds the 422 MB DB, `voices/` (27 MB) and `artifacts/` (89 MB). Consistent backups are in `data/backups/`.

## Tasks & Acceptance

**Execution:**
- [x] `_bmad-output/scripts/large_project_seed.py` -- build the synthetic root. It has a `--shape` from real-copy row counts: revisions per plan, takes per segment, voices and speakers, text length, provenance, speech references, and inactive takes. Defaults match the counts recorded from the real copy.
- [x] `_bmad-output/scripts/large_project_timing.py` -- serve the root with a worker, mint a token, run the read matrix (large, small, small again after a 60 s read-load burst) and the admission scenarios. Print a table and write JSON. `--profile` runs each operation in-process under cProfile with an SQL statement and time listener, and prints the top functions and statements.
- [x] `_bmad-output/scripts/test_large_project_tools.py` -- small-N tests: seed shape counts, the timing script's job-event parsing and result schema, the timeout path, and refusal of a non-empty root or a live lock.
- [x] Real copy -- copy the owner's chosen source to `%USERPROFILE%\pandrator-perf\real\`, then record its shape counts (counts only).
- [x] `_bmad-output/initiative-long-audiobooks/tickets/epic-large-project-responsiveness/baseline-2-1.md` -- tables for both datasets, the top costs per operation, and proposed targets for admission and the opening check. Note any gap between synthetic and real, and rerun after making the synthetic data more realistic.

**Acceptance Criteria:**
- Given a seeded synthetic root, when the timing script runs, then it prints per-operation timings for every row of the read matrix and both admission sizes, and exits 0.
- Given the real copy, when the timing script runs, then it prints the same table, and the owner's install is unchanged.
- Given both runs, when the synthetic and real timings are compared, then each operation's slow or fast verdict (15 s limit) agrees, or the gap is recorded with what changed in the seed.
- Given `baseline-2-1.md`, when read, then it names the top costs per operation from profile mode and proposes numeric admission and check targets with their reasoning.

## Review Triage Log

Pass 1, quick lens. 1 high, 4 medium, 6 low, 0 false, 1 maybe-false; 1 intent gap resolved by the owner.

Outcome (2026-11-11): every patch row was applied and verified (19 tests pass, ruff clean, install DB sizes and mtimes unchanged). The fresh seed-4 run agrees with the real copy on every verdict but the small `segments_limit100` (real 15.5 s, synthetic 14.7 s, on the 15 s line); `baseline-2-1.md` records that gap and the seed change that would close it, as AC3 allows.

- medium, patch: the target reasoning subtracts the lease monitor's SQL time, but that monitor runs on another thread, outside cProfile's main-thread residual. Corrected, removing the catalogue rebuild alone leaves about 62 s for admission and 54 s for the check, so the targets also need the secondary costs. The doc is reworded.
- medium, patch: AC1 was not shown by one run on the final seed. The large rows and the after-load phase were carried over from seed 3. A full timing run on seed 4 replaces the synthetic column.
- low, patch: several operations' top costs were inferred, not profiled: small-project operations, large compact/fields/limit100, full revisions and the 15-segment admission. A profile run covers them.
- medium, patch: results JSON is written only at the end, and two profile sections were lost this way. It is now written after each phase.
- intent gap, resolved: the copy included `migration-web-v1.json`, which the frozen decision did not list. The owner approved it on 2026-11-11, and it is recorded above. The install is unchanged: `data/pandrator.sqlite3`, `-wal` and `-shm` sizes and mtimes still match the pre-copy listing (21:52 and 21:53 on 2026-11-10), checked after the runs.
- high, patch: nothing stopped the scripts from running on a closed install's `data/`, where startup migrates and writes. They now refuse a root inside a Manager-style install.
- medium, patch: the harness worker registered every handler. A copied DB with queued jobs would have run them for real, including LLM calls with keyring credentials. The worker now handles only `generation.admit` and `generation.run`, and a root with queued or running jobs is refused. The real copy had none (236 succeeded, 11 failed, 5 canceled), so no unrelated job ran.
- low, patch: `--only` did not gate the burst and after-load phase. They now run only when it is absent or names `burst`.
- low, patch: `Seeder.close` ran `initialize_owner` before cleanup, so an error there masked the original failure. Cleanup now runs in `finally`.
- low, patch: the `seed_takes(regenerate=)` parameter was dead. It is removed.
- low, patch: the baseline recorded the owner's TTS model name. It now says "an audio.cpp model".
- low, patch: the Rerunning profile command did not match what was run. It is updated after the new profile run.
- maybe-false, rejected: the module-scoped `seeded_root` is shared by tests that start a server on it, so `test_seed_matches_the_requested_shape` might depend on file order. The admission it runs is cancelled before synthesis and adds no takes, voices or references, so the counts that test asserts should not change. Running the file in reverse order would settle it. Even if true, it would be low.

## Design Notes

- Opening-check timing does not depend on TTS: the script cancels at the "Loading speech provider settings" event. If the check fails on the copy, the failure reason is recorded.
- The owner's app may be running during the measurements and can compete for CPU. Each table records whether it was.

## Verification

**Commands:**
- `<uv env> python -m pytest -q _bmad-output/scripts/test_large_project_tools.py` -- expected: pass
- `uvx ruff@0.17.3 check _bmad-output/scripts` -- expected: pass
- `<uv env> python _bmad-output/scripts/large_project_timing.py --root %USERPROFILE%\pandrator-perf\synthetic` -- expected: table printed, exit 0


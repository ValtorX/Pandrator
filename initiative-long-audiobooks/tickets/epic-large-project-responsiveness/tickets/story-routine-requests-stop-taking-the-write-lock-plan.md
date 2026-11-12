---
title: 'Routine requests stop taking the write lock'
type: bugfix
ticket: '6'
created: '2026-11-12'
status: done
baseline_revision: '8051c406612064b529fb61ef96c323da2004c327'
route: full
route_source: auto
risk: high
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Every bearer-authenticated request validates its token inside `BEGIN IMMEDIATE` (`auth.py:536`), holding SQLite's single writer across the hash check and account queries just to set `last_used_at`. Every authenticated request then writes its own audit row in a second write transaction with a 250 ms busy timeout. Under read load these writers queue behind each other, and longer under GIL contention, which is how the brief saw `database is locked` on `BEGIN IMMEDIATE` and hundreds of "Audit writer busy" spools.

**Approach:** Resolve bearer tokens in a read snapshot, the way browser sessions already do, and write `last_used_at` (token and automation client) only when it is at least 60 s old, as a short conditional update whose busy failure is ignored. Write audit rows by group commit: concurrent requests' events go into one write transaction per batch, and each request still returns only after its own event is committed or durably spooled, so no event is lost and durability matches today's.

## Boundaries & Constraints

**Always:** Every authenticated request still produces exactly one audit event, in the database or the spool, before its response is sent. Token authorization checks (expiry, revocation, instance, origin, scopes, account state, client) stay evaluated on every request against current committed data. Busy spool, replay, pruning, redaction, the actor projection and the existing timeouts (`AUDIT_WRITE_TIMEOUT_MS` 250, replay 0) keep their behaviour. A non-busy failure of one event never loses the other events in its batch. Tests count statements and transactions; no wall-clock assertions beyond the existing ones.

**Never:** A background audit thread or an in-memory buffer that a request returns before writing. Changing `record_command_in_session`, the spool file format, the audit or token schema, or API response fields. Touching browser-session activity (already coalesced at 30 s), the WAL or checkpoints (story 8), or abandoned reads (story 7).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Repeated reads | one token, many requests within 60 s of its last touch | principal resolved each time; no `BEGIN IMMEDIATE` and no `UPDATE api_tokens` from resolution | none |
| Touch due | `last_used_at` null or ≥ 60 s old (token or its client) | one conditional `UPDATE` sets it to now; a concurrent request that loses the race updates nothing | busy on the touch: logged at debug, request still authenticated |
| Invalid token | revoked, expired, wrong instance or origin, scope or account mismatch | `None`, no write | none |
| Concurrent audits | N threads record at once | N events committed in fewer than N write transactions; all present, each with its actor | none |
| Writer held | another connection holds `BEGIN IMMEDIATE` | every event of the batch is spooled, one warning per event, request returns after spooling | spool failure is raised in that event's own request |
| Bad event | one event's write raises a non-busy error inside a batch | the others are committed by a per-event retry; that event's request raises as today | no spool for non-busy errors |

</frozen-after-approval>

## Code Map

- `pandrator/web/auth.py:526-633` -- `resolve_api_token`: move to `self.database.snapshot_session()`, compute the principal there, then touch. Validation logic unchanged. `_aware`, `utcnow` already imported; add `update` from sqlalchemy.
- `pandrator/web/accounts/browser_sessions.py:215-329` -- the pattern to mirror: snapshot read, then a write only when `touch_needed`. Do not change this file.
- `pandrator/web/database.py:112-150` -- `snapshot_session` (read-only, explicit `BEGIN`), `immediate_session(busy_timeout_ms=)`; `is_sqlite_busy` at line 23.
- `pandrator/web/audit.py:119-195` -- `record()`: keep the event construction and sanitizing; replace the write with group commit. `_spool`, `_drain_pending`, `_add_actor`, `_prune_in_session` reused as they are. `_EVENT_FIELDS` is what a retry or spool needs.
- `pandrator/web/http_lifecycle.py:263-288, 721-761` -- callers (`resolve_api_token`, `audit.record` in `after_request`). No change expected; its "Audit projection failed" log stays the error path.
- `tests/test_audit_contention.py`, `tests/test_audit_replay_admission.py` -- existing contracts the group commit must keep: one spool per busy event within 1 s, timeouts `[250, 0]` for one record (write then replay), actor-projection failure raises with no spool.
- `tests/test_browser_auth_activity.py:44-70, 139-210` -- statement-capture and `utcnow`-patch templates for the token tests.
- `tests/test_actor_attribution_integration.py:54-108` -- `case` fixture (real app, bootstrap), `api_principal`, `record_audit`.
- `scripts/test_lanes.py:540-541` -- lane `web-06-serial` holding the audit tests; a new test file joins the same lane.
- `_bmad-output/scripts/large_project_timing.py:322-347, 548-576, 762-770` -- `serve_child`, `read_burst`, `stop_server`; the burst results get spool and lock counts.

## Tasks & Acceptance

**Execution:**
- [ ] `pandrator/web/auth.py` -- add `API_TOKEN_ACTIVITY_INTERVAL = timedelta(seconds=60)` and `API_TOKEN_TOUCH_TIMEOUT_MS = 250`. Resolve in `snapshot_session`; record whether the token's or client's `last_used_at` is null or older than the interval. After the snapshot closes, when due, run one `immediate_session(busy_timeout_ms=API_TOKEN_TOUCH_TIMEOUT_MS)` with conditional `UPDATE`s (`id` match, `revoked_at IS NULL`, `last_used_at IS NULL OR < now - interval`). Catch only `is_sqlite_busy` errors, log at debug, keep the principal.
- [ ] `pandrator/web/audit.py` -- group commit: a per-service `threading.Condition`, a FIFO of pending entries (field dict, actor, outcome), and a leader flag. A recording thread appends its entry; if no leader, it leads and writes batches of up to `AUDIT_BATCH_LIMIT = 200` from the queue front until its own entry is done, then hands leadership off and notifies; otherwise it waits until done or leaderless. A batch: one `immediate_session(busy_timeout_ms=AUDIT_WRITE_TIMEOUT_MS)`, add all, flush, add actors, prune when any id `% 100 == 0`. Busy → spool each entry (one existing warning each). Non-busy with more than one entry → retry each alone. The leader catches everything so no entry stays pending; an entry's error re-raises in its own thread. After its own entry committed, the leader runs the existing `_drain_pending(...)` call outside the lock.
- [ ] `tests/test_routine_request_writes.py` (new) -- token: repeated resolutions inside the window issue no `BEGIN IMMEDIATE` and no `UPDATE api_tokens`; due touch writes once and the client's too; a busy writer during a due touch still authenticates; revoked/expired tokens write nothing; repeated bearer reads through the app's test client (`GET /api/v1/auth/status`) open no token write. Audit: concurrent records from a `Barrier` of threads all land with actors in fewer write transactions than events, with no spool; a held writer spools every event of the batch; one failing event in a batch leaves the others committed and raises only in its thread.
- [ ] `scripts/test_lanes.py` -- add the new file next to `tests/test_audit_replay_admission.py`.
- [ ] `_bmad-output/scripts/large_project_timing.py` -- in `serve_child`, a logging handler counting "Audit writer busy" warnings and records mentioning `database is locked`; print them as a prefixed JSON line when stdin closes; `stop_server` reads it back; `print_table` and the results show them for the burst. Cover the parsing in `_bmad-output/scripts/test_large_project_tools.py`.
- [ ] `_bmad-output/fork-release-notes.md` -- one Fixes line: busy reads no longer lock the database or divert audit records; an API token's "last used" time updates at most once a minute.

**Acceptance Criteria:**
- Given a token used within the last 60 s, when any number of authenticated reads arrive, then resolving it opens no write transaction and every request still gets a fresh authorization decision.
- Given N concurrent authenticated requests and no other writer, when they complete, then exactly N audit events with actors exist and the spool is empty.
- Given the synthetic root, when the timing script runs its read-load burst, then it reports zero audit spools and zero `database is locked` records.
- Given the existing audit and browser-activity tests, when run, then they pass unchanged.

## Implementation Notes
- Implemented by a Sonnet 5.5 subagent (high effort); every task is done. Files: `pandrator/web/auth.py`, `pandrator/web/audit.py`, `tests/test_routine_request_writes.py` (new), `scripts/test_lanes.py` (`web-06-serial`), `tests/test_account_authentication_integration.py`. In `_bmad-output/` (fork-files, uncommitted): `scripts/large_project_timing.py`, `scripts/test_large_project_tools.py`, `fork-release-notes.md`.
- Deviation: `test_expiry_is_checked_after_writer_admission` lost its `api_token` variant. It asserted that a token's expiry is judged after waiting for the writer, but resolution no longer takes a writer. The `authorization_code` variant stays. Token expiry is covered in the new file.
- Deviation: the conditional touch uses `last_used_at <= now - interval`. The plan's `<` would skip a touch at exactly 60 s, which contradicts "at least 60 s old".
- Expiry and every other check are evaluated once, at snapshot time; the touch re-checks only revocation and staleness.
- Matrix audit (step 3) found gaps and added tests for them:
  - invalid tokens: origin, scope and account mismatch;
  - a failed spool raises only in its own request.
- The read-load burst on the synthetic root recorded 64 requests, 0 audit spools and 0 locked records. The default load, 4 clients for 60 s, is light. A rerun with 12 clients (`--load-workers 12`, SQLAlchemy 2.1.8) overlapped a full `web-06-serial` lane run. It sent 48 requests, all abandoned at 15 s, and 11 were still running afterwards. It also recorded 0 audit spools and 0 locked records, and the after-load small reads were all fast.
- Final verification after the review patches (uv, Python 3.11, Windows):
  - The six focused test files gave 78 passed under SQLAlchemy 2.0.52 and again under 2.1.8.
  - `web-06-serial` under 2.1.8: 472 passed, 11 skipped.
  - ruff and vulture are clean, and `test_lanes.py check` passes.
  - `check_types.py` under 2.0.52 reports only `nemo_normalizer.py:98`.
  - `test_large_project_tools.py`: 22 passed.
  - The harness burst with `--light-workers 24 --light-pace 0.5` sent 53 heavy requests and 2,425 light ones. Every light request returned 200, with a 0.44 s max, and the server log showed 0 audit spools and 0 locked records.
- Not committed: `AGENTS.md` forbids committing without the owner's instruction, which overrides step 5's commit.

## Plan Change Log

## Review Triage Log

Pass 1, quick lens. 12 findings: 0 high, 3 medium, 7 low, 1 false, 1 maybe-false.

- medium, patch: the HTTP bearer test's bound `begin_immediates <= audit_inserts + 10` allowed one extra writer per request, so a per-request token writer could come back unnoticed. The bound is tightened to equality.
- medium, patch: the concurrent-touch test wrote the same `_BASE` from both threads. An unconditional `UPDATE` would have passed too, so the matrix row "the loser updates nothing" was unverified. The test now asserts the `UPDATE` rowcounts sum to 1.
- medium, patch: the "scope mismatch" case only exercised an unparseable scope. The client scope check (`scopes <= allowed_scopes`) was untested, though the implementation notes claimed it. A client-narrowed token case is added.
- low, patch: the busy-touch test asserted `< 2.0 s` of wall clock, which the plan's Always rule forbids. The assertion is deleted.
- low, patch: `_gate_first_batch` waited for the queue alone. A first batch holding several entries made the gate spin to its 10 s deadline, so the tests were slow and timing-dependent. It now counts the batch plus the queue.
- low, patch: the timing harness stored the server-lifetime log counts under `burst`, which mislabelled them as burst figures. They are now `server_logs`. Counts are not lost: the runs waited for idle (`server busy 0.0s more`) before stopping.
- low, patch: `start_server` waited the full startup timeout when the child crashed, because `started` was only set on READY. It is now also set when output ends.
- low, patch: the counter on the root logger disabled `logging.lastResort`, and `jobs.py`'s `basicConfig` became a no-op, so server warnings vanished from the timing run's console. `basicConfig()` now runs first.
- low, rejected: a held writer makes the leader spool a batch serially, so a request waits for its batch's fsyncs. Batch size is capped by the server's worker threads (waitress: 12 in the harness, at least 6 in `cli.py:484`), so this adds about a dozen fsyncs only while another writer holds the lock past 250 ms. A fix would need parallel spooling.
- low, rejected: a non-`Exception` `BaseException` (KeyboardInterrupt or SystemExit in a request thread) can leave the leader's own entry queued for a later leader to commit. Ordinary use never reaches it, and a fix adds another branch.
- false: "touch re-checks revocation, but a token revoked between snapshot and touch still authenticates". That is the same window as any request resolved just before a revoke, and the browser path has it too whenever no touch is due. The plan keeps the principal from the snapshot. The wording note is plan-only.
- medium, patch (was maybe-false; now settled): AC3 evidence was weak, because the heavy-listing burst could not show the symptom. The same 12-client burst on the baseline commit, in a scratch worktree, also gave 0 spools. A mixed load settled it, using a scratch script with 4 heavy listings plus 24 `auth/status` clients for 60 s on the synthetic root under SQLAlchemy 2.1.8:
  - Unpaced: the baseline gave 90 audit spools, 11 connection errors and a light max of 8.9 s from 4,997 requests. The new code gave 0 spools, 0 errors and a light max of 2.3 s from 10,967 requests. Its heavy listings all passed the 15 s client timeout, because twice as many light requests now took the CPU instead of waiting on the lock.
  - Paced at 0.5 s per light client: the baseline gave 24 spools, with heavy at 4.67 s median and 6.28 s max, and light at 1.99 s max. The new code gave 0 spools, with heavy at 4.67 s median and 5.21 s max, and light at 0.39 s max.
  - So there is no heavy-read regression at a fixed request rate.
  - Patch: the harness burst gains `--light-workers` and `--light-pace`, so later stories can rerun this check.

## Design Notes

Group commit keeps today's guarantee (committed or spooled before the response) while turning N audit writers into roughly one per batch. The leader drains from the queue front, so its own entry is reached within the queue length it joined behind; handing off at that point bounds every request's wait. Sketch:

```python
with cond:
    queue.append(entry)
    while not entry.done and leader: cond.wait()
    lead = not entry.done; leader = leader or lead
while lead and not entry.done:   # take batch under lock, write outside it
    ...; with cond: cond.notify_all()
if lead: with cond: leader = False; cond.notify_all()
```

Build a fresh `AuditEvent` from the entry's field dict for each attempt; after a rolled-back flush the old instance holds a stale id.

## Verification

**Commands:**
- `<uv env> python -m pytest -q tests/test_routine_request_writes.py tests/test_audit_contention.py tests/test_audit_replay_admission.py tests/test_actor_attribution_integration.py tests/test_browser_auth_activity.py tests/test_account_authentication_integration.py` -- expected: pass, also with `--with sqlalchemy==2.0.52`
- `<uv env> python scripts/test_lanes.py run web-06-serial` (the lane holding the audit tests) -- expected: pass
- `uvx ruff@0.17.3 check .` and `<uv env> --with sqlalchemy==2.0.52 python scripts/check_types.py` -- expected: clean except the known `nemo_normalizer.py:98`
- `<uv env> python scripts/test_lanes.py check` -- expected: pass
- `<uv env> python -m pytest -q _bmad-output/scripts/test_large_project_tools.py` -- expected: pass
- `<uv env> python _bmad-output/scripts/large_project_timing.py --root %USERPROFILE%\pandrator-perf\synthetic --only burst --light-workers 24 --light-pace 0.5` -- expected: the server log reports 0 audit spools and 0 locked records (the heavy-only default burst cannot show the symptom; see Review Triage Log)

## Code Review

### 2026-11-12

Full review (blind, edge-case, verification-gap and intent lenses): 0 `decision-needed`, 9 `patch`, 0 `defer`, 27 rejected.

- [x] [Review][Patch] Token touch "due" ignores this process's own recent attempts. There is no back-off after a busy skip, so each request waits 250 ms while the writer is held, and every concurrent request takes the writer when the interval ends. [pandrator/web/auth.py:531]
- [x] [Review][Patch] The group-commit test gate does not fix the batch shape, so the transaction bound is loose and the failing-entry tests can pass with the entry alone. [tests/test_routine_request_writes.py:317]
- [x] [Review][Patch] The batch-wide prune trigger is untested. [pandrator/web/audit.py:281]
- [x] [Review][Patch] The harness's server log counts and `--light-workers` wiring are not tested end to end. [_bmad-output/scripts/test_large_project_tools.py:161]
- [x] [Review][Patch] The release note overstates the change: reads still take the writer for their audit row, and a held writer still spools. [_bmad-output/fork-release-notes.md:16]
- [x] [Review][Patch] A future `last_used_at` (clock stepped back) freezes the touch until the clock catches up. [pandrator/web/auth.py:599]
- [x] [Review][Patch] The harness loses its server log counts on an interrupted run and hides them when they were not collected. [_bmad-output/scripts/large_project_timing.py:1259]
- [x] [Review][Patch] Token tests have small holes: the due-minus-1µs step checks no UPDATE, and the concurrent test never checks that its threads finished. [tests/test_routine_request_writes.py:141]
- [x] [Review][Patch] The plan's AC3 verification command runs the heavy-only burst, which cannot show the symptom. [this plan, Verification]

Rejected:
- false: The token is not re-checked after writer admission. The request linearizes at its snapshot, and the frozen Approach keeps that principal; expiry at lookup is tested.
- false: Browser sessions still wait on the writer. The Never list forbids touching them.
- low: Only the leader replays the spool. The events are durable, and having followers replay brings back per-request writer contention.
- low: Followers wait with no deadline. They wait only for the batches ahead; a timed fallback is new machinery.
- low: One BaseException is shared across threads and followers re-raise interrupts. This is unreachable from waitress worker threads (pass 1 already rejected it).
- low: One bad event costs serial single writes. Batches are capped near the worker count; bisection is new logic.
- false: A non-busy touch error fails the request. The plan catches only busy errors, and the old code failed the same way.
- false: Skipped touches are logged only at DEBUG. That is the plan's matrix.
- false: `_BASE` is fixed at import. Every test is clock-relative, and no path fails.
- false: The harness raises StopIteration without `auth_status`. `read_matrix` always has it.
- false: Negative `--light-workers` and `--light-pace` are accepted. They are harmless.
- false: The light timeout has no flag. That is a missing option, not a defect.
- false: The harness sets attributes on Popen with `type: ignore`. That is style only.
- false: The review diff's path headers are doubled. That is a review artifact.
- low: (edge) A BaseException gives followers SystemExit. Same as the shared-exception item.
- low: (edge) The leader's entry stays queued after a BaseException. Unreachable in the server (pass 1).
- low: (edge) Followers wait for every batch ahead. Same as the no-deadline item.
- low: (edge) A non-busy failure followed by busy retries runs up to 200 x 250 ms. It needs a buggy event and a held writer at once; short-circuiting is new logic.
- false: (edge) A revoke between snapshot and touch. Same linearization as any request answered just before a revoke.
- low: (verification) An already-spooled entry gets a later BaseException. Same unreachable branch.
- false: (edge) StopIteration without `auth_status`. Same as above.
- low: (edge claim) "No event lost" fails on the BaseException path. Same unreachable branch.
- false: (intent) Non-overlapping reads still take BEGIN IMMEDIATE for audit. That is the frozen Approach's group commit; the wording is fixed in the release-note patch.
- false: (intent) The HTTP test relies on less than 60 s of real time. It makes 11 quick requests and has no failure path.
- false: (intent) "Recorded" includes the spool, and HTTP concurrency is untested. The frozen intent defines it so, and the harness measured it.
- false: (intent) The WAL clause of R5. It stays with story 8, which owns WAL and checkpoints.
- false: (intent) The api_token expiry variant was removed. Same as the first item.

Patches applied. Verification (uv, Python 3.11, Windows):
- The six focused test files: 81 passed under SQLAlchemy 2.1.8 and again under 2.0.52.
- `web-06-serial` under 2.1.8: 475 passed, 11 skipped.
- ruff, vulture and `test_lanes.py check` are clean. `check_types.py` under 2.0.52 reports only `nemo_normalizer.py:98`.
- `test_large_project_tools.py`: 23 passed.
- Not rerun: the timing harness burst on the synthetic root.

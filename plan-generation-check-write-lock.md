---
title: 'Keep generation.run heartbeats alive during the identity check'
type: bugfix
ticket: ''
created: '2026-11-08'
status: built
baseline_revision: '112501b2868632b186c9fb579c574567ebd824a7'
route: oneshot
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

**Problem:** `_run_generation` sets `run.status = "running"`, and the next query autoflushes it, so the write lock is held through `plan_signature` and `plan_audio_identities` (5.5-7.5 minutes on a 5,167-segment book). The job's own heartbeat cannot commit, its 30-second lease expires, voice access checks are denied and swallowed, and the job fails at about 31 seconds with the misleading "A voice reference changed after generation was queued".

**Approach:** Commit the status in a short transaction, then run the check with no writer open, using a plain `session()` so the lease proof sees renewed leases (a pinned `snapshot_session` would not). Prove the lease again before reporting changed inputs, so a lost lease is not reported as a voice change. `WORKER_LEASE_SECONDS` stays 30. Speeding up the check, admission and segment listing are out of scope. No staging or committing: `AGENTS.md` forbids it unless the user asks.

</frozen-after-approval>

## Implementation Notes

Oneshot: under 100 changed lines in one function plus tests. `bind_project_tts_reference` only mutates the snapshot dict, so the identity path has no writes.

- `session()` rather than `snapshot_session()` is required: the lease proof reads `Job.lease_expires_at`, which a pinned snapshot would never see renewed. Swapping it in makes the heartbeat test fail.
- The status now commits first, so a run shows `running` during the check instead of `queued`.
- Files: `pandrator/web/workflow_generation_execution.py`, `tests/test_generation_workspace_execution.py`, `docs/release-notes-next.md`.
- Proof with the real monitor thread, the stock 30-second lease and a 35-second check, run ad hoc: clean `112501b2` fails with `worker_lease_expired`; this branch succeeds.
- The workflow's commit step was skipped: `AGENTS.md` forbids committing unless the user asks.
- Environment limits on Windows with uv: the suite needs a short `--basetemp`, because default pytest paths exceed the path limit (27 failures on clean upstream). With a short path, 3 tests still fail, the same 3 on clean upstream: `test_generation_ignores_saved_preview_routing_metadata`, `test_preview_service_override_keeps_cast_and_reports_cached_readiness`, `test_distinct_cast_bindings_share_one_catalogue_build`. Five `test_web_backend_architecture.py` tests also fail on both.

## Plan Change Log

## Review Triage Log

- medium, defer: `run_reviewable_generation` still holds a writer through the identity check (`workflow_generation_start.py:201-212`). Confirmed by reading it. It predates this change. The release note was narrowed to queued runs.
- medium, defer: a cancel during the check lands but does not stop the check, so the worker stays busy and stale reconciliation closes the job. Before this change the cancel request blocked on the writer, so this is no regression. The fix needs cancellation inside `plan_audio_identities`.
- medium, patch: the failure branch left `pause_requested` set on a terminal run, because reconciliation skips non-active runs (`jobs.py:382`). Now cleared, with a test.
- low, patch: the heartbeat test could flake if the real monitor renewed between the test's heartbeat and its read. It now compares with the earlier lease; the snapshot mutation still fails it.
- low, patch: the moved `plan_changed` path had no test. Added one.

## Verification

**Commands:**
- `<uv env> python -m pytest -q tests/test_generation_audio_identity.py tests/test_generation_workspace_execution.py tests/test_generation_cast_runtime.py tests/test_job_heartbeat_expiry.py tests/test_web_job_concurrency.py tests/test_generation_run_speech_preparation.py` -- expected: pass
- `uvx ruff@0.17.3 check pandrator/web/workflow_generation_execution.py` plus the changed tests -- expected: pass
- `<uv env> python scripts/check_docs.py` -- expected: pass

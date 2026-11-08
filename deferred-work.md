- source_plan: `_bmad-output/plan-generation-check-write-lock.md`
  summary: `run_reviewable_generation` holds a write lock through `freeze_speech_snapshot` and `plan_audio_identities`, so the same lease failure can occur on that route.
  evidence: `pandrator/web/workflow_generation_start.py:201-212` runs both inside `immediate_session`, and it serves `workflow.continue` and `audiobook.generate_audio`. Not observed in the install's history: 7 `workflow.continue` jobs succeeded, the longest in 42 seconds. Whether a long book reaches this path is unverified.
- source_plan: `_bmad-output/plan-generation-check-write-lock.md`
  summary: A cancel during the opening identity check does not stop the check, leaving the worker busy for the rest of it.
  evidence: `plan_audio_identities` never reads `cancel_event`, and the monitor stops renewing after a cancel (`pandrator/web/jobs.py:1772-1778`), so stale reconciliation closes the job about 30 seconds later while the worker finishes the check. Fixing it needs a cancellation hook in the identity loop, and speeding up the check would shrink it.

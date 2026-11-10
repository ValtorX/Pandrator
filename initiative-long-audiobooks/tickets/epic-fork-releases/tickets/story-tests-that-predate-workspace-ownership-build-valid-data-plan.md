---
title: 'Tests that predate workspace ownership build valid data'
type: chore
ticket: '8'
created: '2026-11-09'
status: built
baseline_revision: '509c1938e4afa068ca6e44186262f46f67dd9af4'
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-ci-failure-classification.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 37 tests in 7 files fail on every platform because they predate workspace ownership (`68c444a1`): seeds insert jobs, artifacts, usage events and a voice with no workspace or project (A1, 28), 5 tests build cross-session rows the scope triggers now refuse (A2), and 4 SQL budgets predate the workspace access reads (E1).

**Approach:** Tests only. Seeds take their ownership from the product's own resolvers. Cross-session tests keep their read-side check by suspending the one trigger their seed trips, inside that test, and restoring it. E1 budgets are raised only after the new reads are shown to be fixed cost at two segment counts, with the reason written beside each budget.

**Verify (from the ticket):** All 37 tests pass on Windows and on fork CI, each changed SQL budget's reason is recorded, and the 60-segment bounds in `test_session_view_performance` still pass.

**Decisions:**

- Every A2 defence still exists in product code (Code Map), so each keeps its case and suspends a trigger rather than dropping it.
- Owner, 2026-11-09: each E1 budget is the measured count plus 2 (36, 48, 49 and 42 if the measurements hold), so one more fixed read doesn't fail CI; per-segment growth is still caught by the 60-segment bounds.
- Owner, 2026-11-09: keep the full plan despite its size (about 2,100 tokens).

## Boundaries & Constraints

**Always:** Resolve ownership through `job_creation_ownership`, `resolve_artifact_ownership`, `usage_creation_ownership` or the session's project, never hand-picked IDs. Suspend only the named trigger(s) a seed trips, only around that seed, and restore them from their own `sqlite_master` SQL before the read under test. Keep every assertion's meaning. Record findings in the classification file under "Resolved by story 8".

**Never:** No product, migration or schema change. No autouse fixture, global trigger removal or `PRAGMA` switch. No new test file or lane change. No budget raised without the two-count measurement. No staging or committing.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| A1 seed | Job, Artifact, UsageEvent or Voice for a session | Row carries the session's workspace and project; test reaches its assertions | None expected |
| A2 seed | Foreign-session row a trigger refuses | Trigger suspended for the seed, restored after; the read still hides the foreign row | Helper re-creates the trigger even if the seed raises |
| E1 budget | Scoped SELECT count at 3 and at more segments | Access reads (workspace, project, membership, translation root, settings) equal at both counts; budget set per the decision | Growth with segments: stop, record it, and ask the owner instead of raising |

</frozen-after-approval>

## Code Map

- `tests/web_test_support.py` -- add `suspended_triggers(connection_or_session, *names)`: read each trigger's SQL from `sqlite_master` (assert it exists), `DROP TRIGGER`, yield, re-execute the SQL in `finally`. Precedent: `tests/test_job_access_policy.py:721-726`.
- `tests/test_generation_summary.py` -- seeds `add_source_artifact` (65), `add_run` (90), `add_usage_event` (129), and inline `m.Job` (585, 617) need ownership. A2: the foreign usage event in `test_active_run_usage_matches_usage_summary_and_refreshes` (~399-420, `pandrator_execution_scope_usage_events_insert`) and the foreign job link in `test_latest_assembly_and_cross_session_isolation` (585-604, `pandrator_execution_scope_generation_runs_update`). Defences: `pandrator/web/generation_summary.py:107-108,239,260`.
- `tests/test_speech_plan_audio_reuse.py` -- `m.Artifact` at 218 and 717. A2: `artifact.session_id = other_session_id` at ~572 (`pandrator_media_scope_artifacts_update`). Defence: `pandrator/web/speech_plan_audio_reuse.py:394`.
- `tests/test_playlist_pause_projection.py` -- `m.Artifact` at 238 and 326.
- `tests/test_speech_plan_preview.py` -- `m.Voice` at 332 needs `workspace_id` (`session_project(...).workspace_id`, `pandrator/web/projects/identity.py:107`). E1 budgets 25/35/36 at 163-165, assert at 182; measured 34/46/47. Seeds via `_seed_segment_read_takes` (70).
- `tests/test_speech_selection_context.py:78` -- E1 budget 30, measured 40.
- `tests/test_generation_operations.py:303` -- A2: run moved to own session while its job stays foreign (`pandrator_execution_scope_generation_runs_update`). Defence: `pandrator/web/generation_operations.py:142-155`. File already uses `job_creation_ownership` (92).
- `tests/test_web_workflow_agent_projection.py:370-386` -- A2 `[foreign-session]`: `_add_run` (155) gives a foreign `AgentRun` the case's job (`pandrator_execution_scope_agent_runs_insert`). Defence: `pandrator/web/workflow_snapshot.py:132`.
- Trigger names: `migrations/versions/0077_media_scope_consistency.py`, `0079_execution_scope_consistency.py`. A seed may trip a second trigger once the first is suspended; name each one the run reveals.
- `tests/test_session_view_performance.py` -- 60-segment bounds; run, don't change.

## Tasks & Acceptance

**Execution:**
- [x] `tests/web_test_support.py` -- add `suspended_triggers` -- one audited way to emulate corrupt history.
- [x] `tests/test_generation_summary.py`, `tests/test_speech_plan_audio_reuse.py`, `tests/test_playlist_pause_projection.py`, `tests/test_speech_plan_preview.py` -- give A1 seeds ownership (one helper per file where several seeds share it).
- [x] The 5 A2 tests -- wrap only the refused seed in `suspended_triggers`; check by temporarily removing each product defence that its test then fails, and restore it.
- [x] E1 -- with a temporary probe, count scoped SELECTs at the 3-segment fixture and at 12 segments, grouped by table; then set each budget to measured + 2 in `tests/test_speech_plan_preview.py:163-165` and `tests/test_speech_selection_context.py:78` with a one-line reason comment each.
- [x] `findings-ci-failure-classification.md` -- add "Resolved by story 8" under A1, A2, E1 with the A2 trigger names and the E1 measurements.

**Acceptance Criteria:**
- Given a Windows checkout, when the 7 files run, then all 37 listed tests pass and none of the 51 others regress.
- Given each A2 test with its product defence temporarily removed, when it runs, then it fails.
- Given the scope tests in `test_execution_scope_consistency` and `test_media_scope_consistency`, when run after this change, then their results are unchanged from baseline (the triggers are intact).

## Design Notes

Suspension, not deletion, keeps the database's rules for the rest of the test:

```python
with database.session() as db, suspended_triggers(db, "pandrator_execution_scope_agent_runs_insert"):
    db.add(AgentRun(session_id=foreign_id, job_id=case.job_id, ...))
    db.flush()
```

The seed must flush inside the block, since SQLite fires triggers at statement time.

## Verification

`<uv env>` from `AGENTS.md` with `--with sqlalchemy==2.0.52`; `pixi` isn't installed. Unset `PYTEST_ADDOPTS`.

**Commands:**
- `<uv env> python -m pytest -q tests/test_generation_summary.py tests/test_playlist_pause_projection.py tests/test_speech_plan_audio_reuse.py tests/test_speech_plan_preview.py tests/test_generation_operations.py tests/test_web_workflow_agent_projection.py tests/test_speech_selection_context.py` -- expected: 88 passed.
- `<uv env> python -m pytest -q tests/test_session_view_performance.py` -- expected: pass.
- `<uv env> python -m pytest -q tests/test_execution_scope_consistency.py tests/test_media_scope_consistency.py` -- expected: same failures as baseline (Q1, story 11), no new ones.
- `<uv env> python scripts/test_lanes.py check` and `uvx ruff@0.17.3 check tests` -- expected: pass.

**Manual checks:**
- Fork CI: needs a push the owner approves; the owner confirms the 37 pass there.

## Implementation Notes

- A subagent implemented the plan; I checked its diff and reran the Verification commands. Environment: `<uv env>` with SQLAlchemy 2.0.52, Python 3.11, Windows, `PYTEST_ADDOPTS` unset; FFmpeg not checked.
- Results: the 7 files give 89 passed (the 37, the 51 others, and one new helper test). `test_session_view_performance` gives 20 passed and 1 failed: `test_catalogue_builds_are_request_scoped_and_bounded`, cluster D2 (story 10). The 60-segment bounds pass. The scope files give 21 failed, 2 passed and 67 errors, the same as baseline (Q1, story 11). Lanes check and ruff pass.
- A2 triggers suspended: `pandrator_execution_scope_usage_events_insert` and `pandrator_execution_scope_generation_runs_update` (`test_generation_summary`), `pandrator_media_scope_artifacts_update` (`test_speech_plan_audio_reuse`), `pandrator_execution_scope_generation_runs_update` (`test_generation_operations`), `pandrator_execution_scope_agent_runs_insert` (`test_web_workflow_agent_projection`). The operations seed also tripped `pandrator_execution_scope_generation_runs_insert`; the run is now created validly in the foreign session and then moved, so only the update trigger is suspended. The subagent removed each product defence in turn and saw its test fail, then restored it (`git diff pandrator` is empty).
- E1: scoped SELECT counts by table were identical at 3 and 12 segments. Budgets 36, 48, 49 and 42 (measured 34, 46, 47 and 40, plus 2), with the reason beside each.
- Fix after implementation: in the driver's legacy transaction mode, a `DROP TRIGGER` issued before any write commits on its own, so a seed whose flush failed lost the trigger for good (shown by a probe). `suspended_triggers` now opens a transaction first. `test_suspended_trigger_is_restored_after_the_block_and_after_a_failed_seed` in `tests/test_generation_summary.py` covers the matrix's restore-on-failure row; that file is now LF in the working tree, which matches the index.
- The findings file's "Resolved by story 8" section is uncommitted on `fork-files`. Fork CI is not run: it needs a push the owner approves.

## Plan Change Log

## Review Triage Log

Pass 1, quick lens: 0 high, 1 medium, 2 low, 2 false, 0 maybe-false. Nothing deferred.

| Finding | Verdict | Route | Evidence and action |
|---|---|---|---|
| Budget comments say `test_session_view_performance` bounds per-segment growth; it only bounds `list_segments` with casting and performance off, and "membership" is not a measured table | medium | patch | Confirmed by reading `tests/test_session_view_performance.py:176-236`. The budgets stay safe for another reason: one read added per segment adds 3 in the 3-segment fixture, more than the 2 of headroom. Comments and findings rewritten to say so. The frozen owner decision states the same wrong premise; its outcome stands, and the owner is told. |
| Findings file says 88 passed and omits the helper's transaction fix | low | patch | Direct correction: 89 passed, transaction and new test described. |
| `_add_run` suspends the agent-run trigger for any foreign run, not only one linking this session's job | low | patch | Today's only caller is correct; the condition is narrowed to `job_id` unset or this case's job, a one-line change. |
| Fork CI half of the ticket's Verify is not met | false | reject | Not a defect in the change: the plan leaves it to the owner, because it needs a push they approve. |
| `test_speech_plan_audio_reuse` resolves ownership inline in two seeds rather than through one helper | low | reject | Cosmetic; both use the product resolver as required, and a helper adds code for no behaviour change. |


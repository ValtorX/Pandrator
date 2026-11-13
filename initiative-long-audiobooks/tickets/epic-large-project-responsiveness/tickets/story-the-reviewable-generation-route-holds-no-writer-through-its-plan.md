---
title: 'The reviewable generation route holds no writer through its check'
type: bugfix
ticket: '10'
created: '2026-11-12'
status: built
baseline_revision: '0d5076fd8ca984b17dc9cbcb0c7af358fc700ab7'
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

**Problem:** The workflow's `generate_audio` stage starts its run through `run_reviewable_generation` (`pandrator/web/workflow_generation_start.py:201-237`). That route freezes the speech snapshot (whole-plan cast freeze) and runs `plan_audio_identities` over the whole revision inside `database.immediate_session()`. On a 5,000-segment plan that holds SQLite's single writer for seconds, so the job's own heartbeat and every other request's writes and audits wait. `415aaa8a` moved the same work out of the writer for queued runs (`workflow_generation_execution.py:281`), but not here. R7 requires that no generation start route holds the write lock through the check.

**Approach:** Do what admission's `prepare_start` does: take `snapshot_guard` and run the freeze, the identity pass and the hash in a plain `database.session()` with no writer open. Then, in a short write transaction, re-prove the revision, compare a fresh `snapshot_guard` with the one taken before the freeze, and insert the run. Measure the route's writer holds before and after on a real copy, and confirm with the timing script that the opening check meets its 45 s target.

## Boundaries & Constraints

**Always:** The stored run (`settings_snapshot_json` including `generation_audio_identities`, `settings_hash`, `sequence_number`, `status`, `job_id`) is identical to today's for unchanged inputs. Every existing `RevisionConflict` check still raises with its current message. A change to any input `snapshot_guard` covers, made after the guard is taken and before the run is inserted, raises `RevisionConflict` and creates no run. The freeze and identity pass use a plain session (the lease proof must see renewals, as in `415aaa8a`), never `snapshot_session`. Regression tests prove the writer is free with a zero-busy-timeout write from another connection, with no wall-clock assertions. Timing results hold timings and counts only: no paths or titles.

**Never:** Changing `run_generation`'s opening check, admission, `snapshot_guard`'s content, `freeze_speech_snapshot` or `plan_audio_identities`. Caching across requests or jobs. Removing the route's own identity pass (the opening check compares against it). Cancel handling (story 11). Stage or commit.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Writer free during the pass | another connection writes with busy timeout 0 while the freeze or the identity pass runs | the write commits; the run is then created as today | none |
| Inputs change during the pass | generation controls, a voice, or a segment's text change while the identity pass runs | no `GenerationRun` row; nothing written by the route | `RevisionConflict`, the `start_in_session` message |
| Revision replaced during the pass | the plan's active revision changes while the pass runs | no run | `RevisionConflict`, the existing "no longer available" message |
| Unchanged inputs | JSON or SRT source, with and without an explicit revision | run snapshot, identities and `settings_hash` equal to the old code's | none |

</frozen-after-approval>

## Code Map

- `pandrator/web/workflow_generation_start.py:172-237` -- the change. Today `snapshot` and a first `frozen_hash` are built at 172-200, then `immediate_session` (201) runs `assert_current_revision`, the expected-revision check, `freeze_speech_snapshot` (209), `plan_audio_identities` (212), the final `frozen_hash` (213), `sequence_number` and the `GenerationRun` insert. New order: open `context.database.session()`; `assert_current_revision` and the expected-revision check (fail fast); `guard = snapshot_guard(session, session_id, plan_revision_id)` taken before the freeze; `freeze_speech_snapshot`; `plan_audio_identities`; the final hash (`json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)`, unchanged). Then `immediate_session`: `assert_current_revision`, the expected-revision check, `snapshot_guard(...) != guard` → `RevisionConflict("Speech, cast, or voice references changed while generation was being prepared. Refresh and try again.")`, `sequence_number`, insert. Drop the dead first `frozen_hash` (192-200) only if nothing reads it: it is overwritten at 213 and `settings_hash` is only a fallback.
- `pandrator/web/generation_start_preparation.py:14` `snapshot_guard` -- reuse. It covers session revision, `plan_signature`, revision settings and operation, review, controls, performance plans, project speech references, voices and samples. That is the guard `start_in_session` relies on (`workspace.py:1919-1922`).
- `pandrator/web/workspace.py:1422-1491` `prepare_start` -- the pattern: plain session, guard, freeze, identities. Without `_missing_*_selection` callbacks, `bind_project_tts_reference` (`project_tts_settings.py:74`) only reads or raises, so the freeze writes nothing.
- `pandrator/web/workflow_generation_execution.py:247-300` -- the opening check that `run_generation` runs after this route; already outside the writer. No change.
- `pandrator/web/database.py:92` `session`, `:138` `immediate_session(busy_timeout_ms=...)`.
- `tests/test_web_workflow_handlers.py:2586` `_reviewable_start_source`, `:2595-2700` -- template: `mock.patch.object(self.handlers, "run_generation", ...)` then `self.handlers._run_reviewable_generation(payload, self.progress, threading.Event())`. Lane `web-17-serial`.
- `tests/test_generation_workspace_execution.py:341` `during_identity_check` -- the wrapper pattern around `generation_audio_identity.plan_audio_identities`. Lane `web-07-10-serial`. Other route tests: `tests/test_generation_audio_identity.py:653` (`web-05-serial`), `tests/test_generation_plan_review.py:313` (`web-06-serial`), `tests/test_workflow_generation_language_preflight.py`, `tests/test_web_translation_source_repair.py`.
- `_bmad-output/scripts/admission_writer_holds.py` -- `HoldRecorder`, `describe`, `main`. `large_project_timing.build_services`, `select_projects` (`large.session_id`).

## Tasks & Acceptance

**Execution:**
- [ ] `_bmad-output/scripts/admission_writer_holds.py` -- first, on unchanged code, add `--route reviewable`: for each run, call the app's workflow handlers' `_run_reviewable_generation` on the large project with `source_artifact_id` from the active revision's `settings_json["_source_artifact_id"]`, `speech_plan_revision_id` the active revision and `settings: {}`, with `run_generation` replaced by a stub that returns `{}`, under the execution actor the worker uses. Refuse when the revision names no source artifact or it is not `.json`/`.srt`. Record every hold over 250 ms with its frames, and the route's longest hold; delete the created `GenerationRun` after each run so repeats are equal. Cover argument wiring and refusal in `_bmad-output/scripts/test_large_project_tools.py`. Run it with `--runs 3` on a fresh `real` copy under `%USERPROFILE%\pandrator-perf\work-2-10\before\` and keep the JSON.
- [ ] `tests/test_web_workflow_handlers.py` -- first, on unchanged code, add tests that fail there: (a) wrapping `plan_audio_identities` and `freeze_speech_snapshot` (via the modules the route imports from), another connection's `immediate_session(busy_timeout_ms=0)` write commits during each, and a job's `JobQueue.heartbeat` succeeds during the identity pass; (b) changing generation controls during the identity pass raises `RevisionConflict` and leaves no `GenerationRun`; (c) replacing the active revision during the pass raises the "no longer available" conflict. Add (d): for unchanged inputs the stored snapshot and `settings_hash` equal those computed by a reference that runs the old order in one `immediate_session` (keep the old body as a test helper).
- [ ] `pandrator/web/workflow_generation_start.py` -- the reorder above.
- [ ] Timing -- on fresh copies under `%USERPROFILE%\pandrator-perf\work-2-10\after\`, one root at a time: `admission_writer_holds.py --route reviewable --runs 3` on `real`; `large_project_timing.py --skip-reads --skip-burst --admission-sizes 1,15` on `real` and `synthetic`. Append "After story 9: the reviewable route (story 10)" to `baseline-2-1.md`: route holds before and after, the opening check against 45 s, and the 328-452 s original baseline.
- [ ] `_bmad-output/fork-release-notes.md` -- one Fixes line: starting generation from the workflow on a long plan no longer blocks other requests' writes while it checks the plan and voices.

**Acceptance Criteria:**
- Given the route on a test plan, when another connection writes with no busy timeout during the freeze and during the identity pass, then both writes commit and the run is created.
- Given a fresh `real` copy, when the route runs three times, then no writer hold over 250 ms contains the freeze or the identity pass, and the route's longest hold is at most 0.75 s (median), against the before measurement.
- Given fresh `real` and `synthetic` copies, when the timing script runs admission of 1 and 15 segments, then the opening check is at most 45 s, recorded in `baseline-2-1.md`.
- Given the focused tests below, when run under SQLAlchemy 2.0.52 and 2.1.8, then they pass.

## Implementation Notes

- Route change as planned in `workflow_generation_start.py`: plain session for guard, freeze, identity pass and hash; short `immediate_session` re-proves the revision, compares a fresh `snapshot_guard`, inserts the run. The old `test_worker_final_guard_rechecks_active_revision_before_freeze` asserted the old order (freeze not called on a stale revision); it is now `..._before_run_insert` and asserts the conflict and no run.
- Realistic measurement: `--route reviewable` builds the payload from `WorkflowService.resolve_stage(session, "generate_audio", {})` (flattened `settings`, `resolved_settings_snapshot`, `settings_hash`) and passes `resolved_snapshot=` and `settings_hash=` as `workflow_handlers.py` does. The stage picks a different source than the revision on the real copy, so the revision's `_source_artifact_id` is kept. The first run with `settings: {}` understated the hold (0.800 s before, 0.141 s after); the figures with the stage's snapshot are 9.475 s before and 0.170 s after (medians, 3 runs each, fresh copies under `work-2-10/before2` and `after2`). `baseline-2-1.md` has both.
- The inputs-changed test is a subTest over controls and a segment's text. The unchanged-inputs equivalence test is a subTest over JSON with an explicit revision, JSON without one (reference uses `explicit=False`) and an SRT source (the route materializes the plan; reference uses `explicit=False`). The SRT case was practical in the handler test class.
- The script gives the `real` copy's missing JSON source a stand-in `[]` file and runs under `HostActor("pandrator-cli")`.
- Review fixes: the equivalence test now rebuilds the reference from a deep copy of the snapshot the route passes into `freeze_speech_snapshot` (recorded by a wrapper, with the `explicit` flag asserted equal to the expected value), then freezes, plans identities and hashes it in one `immediate_session`, and compares that with the stored snapshot and `settings_hash`. The inputs-changed test gained a voice subTest (a `Voice` added to the personal workspace from another connection; the guard hashes the library's voices and the conflict is raised). The revision-replaced test now makes a different real revision active through a second `_store_generation_plan(force_new=True)`. `baseline-2-1.md` says the tool times only `BEGIN IMMEDIATE` holds, and that the zero-busy-timeout regression test is what shows the plain session takes no writer.

## Plan Change Log

## Review Triage Log

Before review, the coordinator made three fixes:
- The route measurement used `settings: {}`; it now uses the workflow stage's resolved snapshot, 9.48 s → 0.17 s.
- The inputs-changed test gained a segment-text variant.
- The equivalence test gained JSON-without-revision and SRT cases.

Pass 1, quick lens. 5 findings: 0 high, 1 medium, 4 low, 0 false.

| # | Finding (quick) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | The equivalence test refreezes the stored, already-frozen snapshot, so it does not compare with the old order | medium | patch | Confirmed at `_assert_stored_like_single_writer` (`tests/test_web_workflow_handlers.py:2811-2836`): it pops only the identities, then refreezes. `speech_plan_frozen`, the `text` flags and the bindings carry over, so a wrong `explicit` would pass. Fix: build the reference from a deep copy of the snapshot the route passes into the freeze, and assert `explicit`. |
| 2 | No voice change in the inputs-changed test | low | patch | The matrix row names controls, a voice or a segment's text; the subTests cover only two (`:2785`). Adding a third subTest is a direct addition. |
| 3 | The revision-replaced test clears the active revision instead of replacing it | low | patch | `:2803` sets `active_revision_id = None`. The switch case is covered only at the write stage, by the renamed preflight test that patches `immediate_session`. Fix: switch to a real second revision during the pass. |
| 4 | The holds script runs the route as `HostActor`, not the worker's `UserActor` | low | reject | Only the user branch of `require_current_session_access` (`current_session_policy.py:48-49`) is skipped. That is a few indexed lookups against a 0.17 s hold. Where the freeze and the identity pass run does not depend on the actor. Running as a leased user actor would mean building a lease in the script, for no visible gain. The substitution is recorded in Implementation Notes. |
| 5 | `HoldRecorder` sees only `BEGIN IMMEDIATE` holds, so "0 slow holds containing the check" is partly true by construction | low | patch | A plain session's write opens with pysqlite's implicit `BEGIN`, which the cursor events never see. The fix is one sentence in `baseline-2-1.md`: the regression probe, not the tool, shows that the plain session takes no writer. |

Patches 1, 2, 3 and 5 applied by the implementer:
- The equivalence test builds its reference from a pre-freeze copy recorded at the freeze call, and asserts `explicit`.
- A voice is added during the pass, as a third inputs-changed subTest.
- A real second revision replaces the active one during the pass.
- One sentence added to the baseline.

Reverified (uv, Python 3.11, Windows):
- The eight focused files: 223 passed and 111 subtests under SQLAlchemy 2.0.52, and again under 2.1.8.
- `web-17-serial`: 1024 passed, 1 skipped.
- `test_large_project_tools.py`: 41 passed.
- ruff is clean on the repository and `_bmad-output/scripts`. Types report only `nemo_normalizer.py:98`. The lane check and vulture are clean.

## Design Notes

- Why the guard is taken before the freeze: the plain session reads each statement at the latest committed state, so a change landing between the guard and the freeze makes the freeze newer than the guard and the write-stage comparison catches it. Taking the guard after the freeze would miss a change landing between them. `prepare_start` uses the same order.
- The opening check in `run_generation` recomputes identities and the plan signature against this snapshot, so anything outside `snapshot_guard`'s coverage still fails the run before synthesis, as today for admission.
- The guard adds one `snapshot_guard` (0.07-0.16 s on the real copy, story 9) to the write transaction; the freeze and the whole-plan pass it removes are several seconds there.

## Verification

**Commands:**
- `<uv env> --with sqlalchemy==2.0.52 python -m pytest -q tests/test_web_workflow_handlers.py tests/test_generation_audio_identity.py tests/test_generation_plan_review.py tests/test_generation_workspace_execution.py tests/test_workflow_generation_language_preflight.py tests/test_web_translation_source_repair.py tests/test_source_processing_children_http.py tests/test_web_backend_architecture.py` -- expected: pass, then again with `--with sqlalchemy==2.1.8`
- `<uv env> python scripts/test_lanes.py run web-17-serial` -- expected: pass
- `uvx ruff@0.17.3 check .`; `<uv env> --with sqlalchemy==2.0.52 python scripts/check_types.py` (only `nemo_normalizer.py:98`); `<uv env> python scripts/test_lanes.py check`; `<uv env> vulture` -- expected: clean
- `<uv env> python -m pytest -q _bmad-output/scripts/test_large_project_tools.py` -- expected: pass
- `<uv env> python _bmad-output/scripts/admission_writer_holds.py --root <fresh real copy> --route reviewable --runs 3` -- expected: no hold over 250 ms contains the freeze or identity pass; longest-hold median at most 0.75 s
- `<uv env> python _bmad-output/scripts/large_project_timing.py --root <fresh copy> --skip-reads --skip-burst --admission-sizes 1,15` on `real` and `synthetic` -- expected: opening check at most 45 s

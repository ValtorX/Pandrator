---
title: 'Admission meets its target without stalling reads'
type: bugfix
ticket: '9'
created: '2026-11-12'
status: done
baseline_revision: '26d18b531c05ccc54f6a79790e2af4f3e1005818'
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

**Problem:** Re-measured at `26d18b53` (stories 2-8 in), admission of 1 and 15 segments already meets its 60 s target: 13.4 / 13.6 s on a fresh real copy and 11.7 / 12.6 s on synthetic. With the small listing sampled every 2 s it takes 15.4-17.1 s, and the listing stays at or under 1.3 s, `auth/status` at or under 1.0 s. Story 1 found no writer held during preparation; the original stall was CPU-heavy reads sharing one interpreter with admission. What still blocks other requests is admission's final write transaction (`generation_admission.py:309`), which holds SQLite's writer for 1.5-1.9 s. Of that, 0.83-0.93 s is `inherit_edit_copy_audio`: on the real plan, an edit copy, it hydrates every segment and take of the revision and its ancestors to find that nothing needs copying. Every request audited in that window waits out its 250 ms busy timeout and is spooled: 2-7 "Audit writer busy" spools per admission run.

**Approach:** Make the edit-copy reconcile find its candidates in SQL and hydrate only those, so a reconcile with nothing to copy loads no segment or take rows, with the same takes copied as today. Then measure the writer hold, the UI's repeated reads and the listings during admission on both datasets, and record the measurements and the explanation of the stall.

## Boundaries & Constraints

**Always:** `inherit_edit_copy_audio` copies exactly the takes the current code copies, in the same order, with the same status, activation and revision, and returns the same count; `publish_to_edit_copy` and `_copy_take` keep their behaviour. Both callers (`start_in_session`, `prepare_segment_edit_targets`) still reconcile inside their own write transaction. Regression tests count statements or loaded rows, with no wall-clock assertions. Timing results hold timings and counts only: no paths or titles.

**Never:** Narrowing admission's identity pass to the selected segments (the opening check compares the whole plan). Changing the opening check's transaction (story 10), the audit busy timeout or spool, guards (`snapshot_guard`, `_same_snapshot`, `collect_row_state`), the run snapshot's content or `settings_hash`, or API responses. Caching across requests or jobs. Stage or commit.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Nothing to copy | edit copy whose targets already hold every ancestor take's artifact | returns 0; no `GenerationSegment` or `AudioTake` row hydrated beyond the revision and ancestor lookup | none |
| Late arrival | ancestor gained a completed take after the copy | one clone, activated as today when the target has no active or a non-completed active take | none |
| Same artifact twice | two ancestor revisions hold takes of one artifact for one ordinal | one clone only (first in `created_at desc, id` order) | none |
| Target unusable | target removed, different `source_segment_ids_json`, or no target at that ordinal | nothing copied for it | none |
| Stale or mismatched | ancestor take `stale`, or text/input differs | clone with status `stale`, activated only when the target has no active take | none |
| Not an edit copy | revision without `edit_copy` restore ancestry | returns 0 before any segment query | none |

</frozen-after-approval>

## Code Map

- `pandrator/web/generation_edit_audio.py:168-204` -- `inherit_edit_copy_audio`. Today: `targets` (every segment of the revision by ordinal), `existing_by_id` (every take of the revision), `rows` (ancestor segment, take and artifact for every `completed`/`stale` take, ordered `AudioTake.created_at.desc(), AudioTake.id`). Replace with: one candidate query over the same ancestor join that also joins the target segment by `(plan_revision_id == revision_id, ordinal == source.ordinal)` and keeps rows with `NOT EXISTS` a take on that target with the same `artifact_id`, same order, selecting ids only. No candidates → return 0. Otherwise hydrate the candidates' source, take and artifact, their target segments, and all takes of those targets, then run the unchanged loop over `_copy_take`. `_copy_take` keeps its own `removed`, `source_segment_ids_json` and in-memory `existing` checks, which also handle a second candidate with the same artifact.
- same file `:25` `edit_copy_ancestors`, `:121` `_copy_take`, `:207` `publish_to_edit_copy` -- reuse; no change.
- `pandrator/web/workspace.py:1972` (`start_in_session`) and `pandrator/web/speech_plan_workspace.py:271` -- the two callers; no change.
- `pandrator/web/generation_admission.py:309-355` -- the write transaction measured; no change.
- `tests/test_generation_edit_audio.py` -- `case` fixture (`GenerationRegenerationTests`), `execute`, `edit`, `page`; `test_inheritance_is_idempotent_and_does_not_cross_real_restore:311` builds an edit copy; `test_capture_selection_guards_uses_narrow_take_queries:67` is the statement-capture template. Lane `web-13-serial`.
- `_bmad-output/scripts/large_project_timing.py:531` `read_matrix` -- `ui_runs_compact`, `ui_generation_summary` and `ui_jobs` are already added, uncommitted, during planning. `:825` `run_admission`, `:788` `ReadSampler` -- sampling during jobs.
- Scratch `C:/Users/Valtor/AppData/Local/Temp/claude/c8be89bfd31384b7/1e5f80d0-25d8-47b8-893a-f2d5742cbfd6/scratchpad/writer_holds.py` (planning only; it imports the timing script by path) -- times every `BEGIN IMMEDIATE` to commit or rollback, with the calling frames, and the pieces inside admission's commit transaction. Promote it to `_bmad-output/scripts/admission_writer_holds.py`.

## Tasks & Acceptance

**Execution:**
- [ ] `tests/test_generation_edit_audio.py` -- first, on unchanged code, add a test that keeps the current algorithm as a reference function in the test file. Build an edit copy over two ancestor revisions covering every matrix row: an artifact already present on its target; a late completed take; one artifact under two ancestors; a removed target; a target with different source ids; a stale take; a target with no active take; and a target whose active take is not completed. Run the reference on one copy of the state and the new code on another. Compare return values and every `AudioTake` of the revision (segment, artifact, parent, status, `is_active`, revision), and segment `status`. Then add a test where the reconcile has nothing to copy: a `load` event on `GenerationSegment` and `AudioTake` counts zero hydrated rows, and `SELECT` statements are bounded (revision, ancestors, one candidate query).
- [ ] `pandrator/web/generation_edit_audio.py` -- the candidate query and targeted hydration.
- [ ] `_bmad-output/scripts/admission_writer_holds.py` -- promote the scratch script: an admission of each size through `Worker.run_once` with no profiler; holds over 250 ms and per-piece times inside the admission commit; results as JSON beside the root like the timing script. `--runs N` repeats each size on the same root.
- [ ] `_bmad-output/scripts/large_project_timing.py` -- add `--ui-interval` (default 0): while admission and the check run, sample the large project's `ui_runs_compact`, `plan_status_summary`, `ui_generation_summary` and `segments_default` at that interval, beside the existing `auth/status` and small-listing samplers. Cover the option's wiring in `_bmad-output/scripts/test_large_project_tools.py`.
- [ ] Timing -- on fresh copies of `real` and `synthetic` under `%USERPROFILE%\pandrator-perf\work-2-9\after\`, one root at a time: `admission_writer_holds.py --runs 3`; `large_project_timing.py --skip-reads --skip-burst --admission-sizes 1,15` clean, then with `--listing-interval 2 --ui-interval 10`. Append an "After story 8: admission (story 9)" section to `baseline-2-1.md`. It gives the before figures from this plan and the `before` results in `work-2-9`, the after figures, and "Why reads stalled during admission": no writer is held while admission prepares; the only long writer hold is the commit transaction, cut by this story; story 1's stall came from heavy reads sharing the interpreter.
- [ ] `_bmad-output/fork-release-notes.md` -- one Fixes line: starting generation on an edited long plan no longer blocks other requests' writes for over a second.

**Acceptance Criteria:**
- Given the edit-copy fixture, when the old and new reconcile run on the same state, then the copied takes, their fields, segment statuses and return values are identical.
- Given an edit copy with nothing to copy, when it is reconciled, then no segment or take row is hydrated.
- Given a fresh real copy, when admission of 1 and 15 segments runs three times each, then its commit transaction holds the writer for at most 0.75 s (median), against 1.51-1.52 s before.
- Given fresh copies of both datasets, when the timing script runs admission of 1 and 15 segments with the listing and UI sampled, then admission is at most 60 s, and every sampled read during admission and the check is under 15 s. Both are recorded in `baseline-2-1.md` with the audit spool counts.
- Given the focused tests below, when run under SQLAlchemy 2.0.52 and 2.1.8, then they pass.

## Implementation Notes

- Implemented by a Sonnet 5.5 subagent (high effort). Files: `pandrator/web/generation_edit_audio.py`, `tests/test_generation_edit_audio.py`. Under `_bmad-output/` (fork-files, uncommitted): `scripts/admission_writer_holds.py` (new), `scripts/large_project_timing.py`, `scripts/test_large_project_tools.py`, `baseline-2-1.md`, `fork-release-notes.md`.
- The candidate query also skips removed targets (`target.removed IS false`). This cannot change results, because `_copy_take` already declines them and `removed` is non-null. The status filter is written `(AudioTake.status + "")`, so SQLite does not choose the global status index: 0.04 s against 0.10 s on the real copy.
- The holds script starts its timer after `BEGIN IMMEDIATE` returns, so lock waits are not counted as holds. When a run records no per-piece times, it falls back to the longest hold the admission job opened. In the final 3x2 run every run had its pieces recorded.
- Results: commit-transaction hold medians 0.62 s (1 segment) and 0.68 s (15) against 1.51-1.52 s. Single runs went up to 0.80 s. Admission: real 9.6 / 9.3 s, synthetic 10.7 / 11.1 s clean. The slowest read sampled during admission was 5.5 s. Details are in `baseline-2-1.md`, "After story 8: admission (story 9)".
- Matrix audit (step 3) found that the "no target at that ordinal" row was not covered. The fixture now adds an orphan ancestor segment at ordinal 10 with a completed take.
- Verified (uv, Python 3.11, Windows):
  - The four focused files: 107 passed under SQLAlchemy 2.0.52 and again under 2.1.8.
  - `web-13-serial`: 60 passed.
  - `test_large_project_tools.py`: 29 passed.
  - ruff, lanes and vulture are clean; types report only `nemo_normalizer.py:98`.
  - After the orphan case, the reconcile tests were rerun: 3 passed under both versions.
- The fork release-notes header now names stories 7, 8 and 9.

## Plan Change Log

## Review Triage Log

Pass 1, quick lens. 2 findings: 0 high, 1 medium, 0 low, 1 false.

| # | Finding (quick) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | `admission_writer_holds.py` `describe()` falls back to the admission job's longest hold without marking it. `summarize()` counts that hold in the median and the within-target verdict, and `main()` ignores `run_once()`'s result and the job status. | medium | patch | Confirmed at `describe()`, lines 169-174: the fallback entry looks the same as a real commit hold, and `summarize()` takes every `admission_commit`. A stale or failed admission's short `check_job` transaction would then pass the 0.75 s check of AC 3. Fix: mark the fallback, record the job status, and exclude both from the median, with a test. |
| 2 | The "Not an edit copy" matrix row is not exercised | false | reject | `test_inheritance_is_idempotent_and_does_not_cross_real_restore` (`tests/test_generation_edit_audio.py:311`) turns the revision's ancestry into a `user_restore` and asserts `inherit_edit_copy_audio` returns 0. The early return before any segment query (`generation_edit_audio.py:189-191`) is unchanged code. |

Patch 1 applied by the implementer in `_bmad-output/scripts/admission_writer_holds.py`:
- a fallback commit is marked;
- each run records whether a job was claimed and the admission job's status;
- `summarize()` counts only certified runs and reports `excluded_runs`.

The test is `test_writer_hold_summary_excludes_uncertain_and_failed_runs`.

The implementer said every run of the first measurement had real commit pieces. That was wrong: the 1-segment run 3 (0.60 s) had none. The measurement was rerun on a fresh real copy with the fixed script:
- 1 segment: 0.69 / 0.69 s, plus one fallback that was excluded; median 0.69 s.
- 15 segments: 0.68 / 0.63 / 0.62 s; median 0.63 s.
- Every admission job succeeded.

`baseline-2-1.md` now quotes these figures. Reverified: `test_large_project_tools.py` 30 passed; ruff clean. Production code did not change after the step 3 verification.

## Design Notes

- Measured during planning (real copy, no profiler): the commit transaction holds the writer 1.51-1.52 s: `start_in_session` 1.39-1.43 s, in it `inherit_edit_copy_audio` 0.83-0.93 s. Each of `stable_hash` (3 calls), `deepcopy` (3 calls) and `snapshot_guard` takes 0.06-0.15 s; flushes take 0.07 s. The check's writer holds stay under 0.16 s. A 1.3 s hold from `quick_transcription.cleanup` (maintenance thread) was seen once; it has no connection to admission. After this story the hold should be about 0.6 s. The remaining pieces decide the transaction's guards or the stored snapshot, so they stay.
- The UI repeats `generation-runs?view=compact&limit=25` every 10 s while a run is active. It decodes up to 25 run snapshots of about 8 MB each: 2.46 s median on the real large project, 0.07 s on the small one. That is inside 15 s, but a full core per tab for a quarter of the time. No story owns it; the final report raises it to the owner.
- Admission now beats its target about fourfold. The epic lets this story re-propose targets; the final report proposes a tighter one to the owner rather than changing the adopted 60 s here.

## Verification

**Commands:**
- `<uv env> --with sqlalchemy==2.0.52 python -m pytest -q tests/test_generation_edit_audio.py tests/test_web_generation_regeneration.py tests/test_generation_admission.py tests/test_project_segment_retention.py` (the files that exercise `inherit_edit_copy_audio`, `prepare_segment_edit_targets`, `publish_to_edit_copy` or admission) -- expected: pass, then again with `--with sqlalchemy==2.1.8`
- `<uv env> python scripts/test_lanes.py run web-13-serial` -- expected: pass
- `uvx ruff@0.17.3 check .`; `<uv env> --with sqlalchemy==2.0.52 python scripts/check_types.py` (only `nemo_normalizer.py:98`); `<uv env> python scripts/test_lanes.py check`; `<uv env> vulture` -- expected: clean
- `<uv env> python -m pytest -q _bmad-output/scripts/test_large_project_tools.py` -- expected: pass
- `<uv env> python _bmad-output/scripts/admission_writer_holds.py --root <fresh real copy> --runs 3` -- expected: admission commit hold median at most 0.75 s for 1 and 15 segments
- `<uv env> python _bmad-output/scripts/large_project_timing.py --root <fresh copy> --skip-reads --skip-burst --admission-sizes 1,15 --listing-interval 2 --ui-interval 10` on `real` and `synthetic` -- expected: admission at most 60 s, every sampled read under 15 s

## Code Review

### 2026-11-12

Code review: 2 `decision-needed`, 10 `patch`, 0 `defer`, 17 rejected. Lenses: blind-hunter, edge-case-hunter, verification-gap, intent-alignment; none failed.

- [x] [Review][Decision] The compact run-list poll's cost is noted but owned by no story — While a run is active, each open tab repeats `generation-runs?view=compact&limit=25` every 10 s. On the real project each call costs 2.3-3.1 s of a core, about a quarter of a core per tab. That is inside R6's 15 s, but R6 also says the UI keeps responding. Options: a) new story in this epic to stop the compact run list decoding whole run snapshots; b) fix it inside story 9; c) accept it. Recommended: a — it is a different read path from admission, and this story may not change API responses.
- [x] [Review][Decision] The admission target stays 60 s; tightening is left to the owner in prose — Admission measures 9-11 s clean and at most 17.4 s with the UI reads sampled. Options: a) tighten to 30 s; b) tighten to 20 s, as the baseline suggests; c) keep 60 s. Recommended: a — about 3x the clean figure, and still 1.7x the worst sampled run.
- [x] [Review][Patch] The batched loading in `inherit_edit_copy_audio` is never tested past one batch: run the equivalence test with `_BATCH` patched to 2 [pandrator/web/generation_edit_audio.py:28]
- [x] [Review][Patch] The planner hint `(AudioTake.status + "")` has no query-plan test: assert with EXPLAIN QUERY PLAN that `ix_audio_takes_status` is not used [pandrator/web/generation_edit_audio.py:205]
- [x] [Review][Patch] HoldRecorder keys its hold state on the thread, not the connection: first probe for commit or rollback events from another connection during an open hold; key the state on the connection if any are seen, otherwise reject [_bmad-output/scripts/admission_writer_holds.py:569]
- [x] [Review][Patch] The Writer holds table is split by a paragraph [_bmad-output/initiative-long-audiobooks/tickets/epic-large-project-responsiveness/baseline-2-1.md:403]
- [x] [Review][Patch] `counts_toward_verdict` has two lines joined into one [_bmad-output/scripts/admission_writer_holds.py:201]
- [x] [Review][Patch] `--sizes` is not validated against the 15 available segment ids [_bmad-output/scripts/admission_writer_holds.py:758]
- [x] [Review][Patch] Admission detection relies on a four-frame window of the stack [_bmad-output/scripts/admission_writer_holds.py:537]
- [x] [Review][Patch] `capture_selection_guards` still hard-codes 500 beside the new `_BATCH` [pandrator/web/generation_edit_audio.py:114]
- [x] [Review][Patch] The baseline's intro flags and "about 0.6 s" do not match the holds measurement [_bmad-output/initiative-long-audiobooks/tickets/epic-large-project-responsiveness/baseline-2-1.md:377]
- [x] [Review][Patch] The `--ui-interval` help and the baseline call all four large UI reads heavy [_bmad-output/scripts/large_project_timing.py:1213]

Rejected:
- No automated 60 s or 15 s verdict (false): the epic forbids wall-clock tests, and ACs 3-4 are manual timing runs recorded as planned.
- The fix reaches only edit copies (false): the plan scopes it to the real plan, an edit copy.
- The stall explanation is not isolated by an experiment (false): the plan asks for it from story 1's profile and these hold measurements, both cited.
- The release note overstates (false): "over a second" is accurate at 0.63-0.69 s.
- Doubled `_bmad-output` paths in the patch, reported twice (false): an artifact of how the review generated the diff.
- A nested BEGIN IMMEDIATE resets pieces (false): it would fail on busy and fail the admission, and every admission succeeded.
- Setup before the try leaks resources (low): the process exits, the tool test fails loudly, and the fix needs guarded cleanup.
- The candidate query skips the source-ids rule, reported twice (low): performance only, and nearly unreachable because edit-copy ancestry stops at split, merge and restore; a SQL JSON comparison adds risk.
- The commit message gives "9-14 s" for before (low): fixing it means rewriting a commit; the baseline document is right.
- `instrument` returns None for an inherited method (false): every instrumented attribute is defined on its own class.
- The verdict can rest on one run (low): the summary already lists the counted holds and the excluded runs.
- The check phase records no job status (low): its figures are informational and in no AC.
- The baseline compares unlike measurements (false): the planning before figures are labelled, and the sampled rows are declared not comparable.
- The read-matrix additions are unchecked, `ui_jobs` is timed twice, `schema_version` is unchanged (low): rarely visible, and the added keys are compatible.
- The release-notes header omits story 9's upstream status (false): it accurately names PRs for stories 7 and 8 only.

Resolution (2026-11-12):
- Decisions: the owner made the compact run-list poll a new story 12 in this epic; the refactor sweep and the real-data verification became 13 and 14. The owner tightened the admission target to 30 s. Both are recorded in the epic's Notes and in `baseline-2-1.md`, "Owner decisions".
- HoldRecorder was settled before patching. A probe on the seeded root saw the credentials cache reload (`jobs._event` → `redact` → `credentials._load_values`, its own session) commit inside an open hold in 2 of 12 runs, which ends the recorded hold early. Holds are now keyed on the connection that began them. This is the cause of the "one run in six" fallback; the baseline now explains it, and its certified medians stand.
- All 10 patches applied:
  - The equivalence test also runs with `_BATCH = 2`; it fails if the existing-takes slice is broken.
  - New test `test_reconcile_candidate_query_avoids_the_global_status_index`; it fails without `+ ""` (the plan then shows `IX_AUDIO_TAKES_STATUS`).
  - New tool tests: another connection's commit does not end a hold (fails on the old code); bad `--sizes` values are rejected; a size beyond the plan is refused.
  - Admission is detected on the full stack.
  - `capture_selection_guards` uses `_BATCH`.
  - Baseline and help-text wording corrected.
- Verified (uv, Python 3.11, Windows): the four focused files, 108 passed under SQLAlchemy 2.0.52 and again under 2.1.8. `test_large_project_tools.py`, 35 passed. Ruff is clean on the repo and on `_bmad-output/scripts`. Types report only `nemo_normalizer.py:98`. The lane check passes, and `tickets.py find 2.12` resolves.

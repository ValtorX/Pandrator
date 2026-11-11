---
title: 'Schema-pinned tests run at the revision they pin'
type: chore
ticket: '11'
created: '2026-11-10'
status: done
baseline_revision: '84645183cf3b4508ba63353054d7f09c7faab4fc'
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

**Problem:** 145 tests (clusters Q1 to Q3) fail on every platform. Q1's 140 move a database to an older revision and then use `Job` or `UploadSessionRecord`, whose current models name `external_effect_json` (0084) and `attach_to_session` (0087). Q2's 2 delete a project at revision 0066 or 0068, where the `project_link_*` tables that revision 0001 builds from today's metadata hold a key to a table that only exists from 0073. Q3's 3 insert voice samples that 0078's triggers refuse.

**Approach:** Migration tests stay at their pinned revision and write their `Job` and `UploadSessionRecord` seeds through one new helper that writes only the columns the table has at that revision. Tests that downgraded only to get rid of triggers, and then ran product code, stay at head and write their corrupt rows under the existing `suspended_triggers`. Q2 drops the `project_link_*` tables at the old revision, and Q3 uses `suspended_triggers`.

## Boundaries & Constraints

**Always:** Tests only, plus `tests/web_test_support.py`. A test checking a migration runs at the revision it pins, and never against a schema that never existed. Product code runs only at head, the one schema the product ever sees. Keep each test's assertions; change only how it reaches its state.

**Never:** Edit a shipped migration or product code. Add the missing columns to an old schema. Hook into SQLAlchemy internals (engine events that rewrite statements, `_return_defaults_columns`): the triage showed ORM inserts re-add default columns, and the shim would have to hold under SQLAlchemy 2.0.52 and 2.1.8. No lane, CI or dependency change.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Seed at an old revision | Transient `Job(...)` objects, database at 0078 | Rows hold the set values plus the model's Python defaults in the columns 0078 has; each object's `id` is readable afterwards | A set attribute whose column the revision lacks raises, naming it |
| Explicit null on a defaulted column | Attribute set to `None` | Stored as NULL, not replaced by the default | None |
| Read at an old revision | `get_at_revision(session, Job, id)` at 0074 | The object loads; an attribute the revision lacks raises on access | None |
| Pinned trigger removed | One `CREATE TRIGGER` deleted from 0079 locally | That file's tests fail | Restore the migration afterwards |

</frozen-after-approval>

## Code Map

- `tests/web_test_support.py` -- has `suspended_triggers(session, *names)`; the new helper goes beside it.
- `pandrator/web/models.py:661,1449` -- the two newer columns; `attach_to_session` has a Python default, which is why ORM inserts can't simply omit it. Migrations 0075 to 0087 add no other columns.
- Q1 seed sites: `tests/test_execution_scope_consistency.py:142-182`, `tests/test_library_scope_consistency.py:203-208`, `tests/test_media_scope_consistency.py:311-347,509`, `tests/test_project_graph_consistency.py:232-239`, `tests/test_mandatory_ownership.py:212,270`. Old-schema reads: `tests/test_project_graph_consistency.py:507,515`, `tests/test_media_scope_consistency.py:1230`.
- `tests/test_library_scope_consistency.py:1395` -- runs `save_in_session` at 0078. If it selects `Job` there, run it at head instead: 0078's triggers exist at head too.
- Downgrade-to-dodge-triggers tests: `tests/test_sound_workspace_lifecycle.py:615,638,671` (0077/0076), `tests/test_voice_workspace_execution.py:264` (0077), `tests/test_job_media_workspace.py:265-301` (0074/0075). `job_ownership` (`pandrator/web/job_identity.py:154`) reads the record in memory and queries only Workspace, WorkflowRun, Session and Project.
- Q2: `tests/test_research_cache_ownership.py:354`, `tests/test_pronunciation_workspace_ownership.py` `test_project_foreign_key_cascades_when_project_is_deleted`. Tables `project_link_submissions`, `project_link_sessions` (key to `browser_auth_sessions`), `project_links`; 0087 creates them only if absent.
- Q3: `tests/test_voice_workspace_library.py` `_new_sample` and its 3 corrupt calls; trigger `pandrator_library_scope_voice_samples_insert` (0078).

## Tasks & Acceptance

**Execution:**
- [ ] `tests/web_test_support.py` -- add `insert_at_revision(session, *objects)`, `get_at_revision(session, model, ident)` and `drop_project_link_tables(connection)`, sharing one private present-columns lookup (`PRAGMA table_info`) -- one helper for every Q1 file.
- [ ] Q1 migration files (five above) -- replace `session.add_all`/`add` of `Job` and `UploadSessionRecord` seeds with `insert_at_revision`, and old-schema reads with `get_at_revision` or column selects.
- [ ] `tests/test_sound_workspace_lifecycle.py`, `tests/test_voice_workspace_execution.py` -- in the 4 failing tests, drop the downgrade and write the tampered row under `suspended_triggers` naming the triggers the downgrade removed.
- [ ] `tests/test_job_media_workspace.py` -- in `test_retained_job_checks_reject_inconsistent_roots_without_repair`, enqueue at head first. `no_workspace` and `source_missing_with_survivor` then downgrade and read with `get_at_revision`; the head cases corrupt under `suspended_triggers`.
- [ ] `tests/test_mandatory_ownership.py` -- add tests of the helpers at 0074, which lacks both newer columns: one per matrix row except the trigger check (seed with defaults and readable `id`, explicit `None` on a defaulted column, a set attribute the revision lacks raising, `get_at_revision` raising on a missing attribute).
- [ ] Q2 files -- call `drop_project_link_tables` before the delete.
- [ ] `tests/test_voice_workspace_library.py` -- `_new_sample` gains a flag that writes under `suspended_triggers`; the 3 corrupt calls set it.
- [ ] `findings-ci-failure-classification.md` -- add "Resolved by story 11" with the proof.

**Acceptance Criteria:**
- Given SQLAlchemy 2.0.52 and then 2.1.8 on Windows, when the 11 changed test files run, then all 145 pass and no other test in those files newly fails.
- Given one trigger deleted from 0079, 0078 or 0076 in the working tree, when that migration's test file runs, then tests fail; after restoring it, they pass.
- Given the downgrade tests, when they run, then product code ran only at head.

## Implementation Notes

- `get_at_revision` returns a plain row object rather than an ORM instance; an attribute the revision lacks raises `AttributeError`. `job_ownership` takes it because it reads only attributes.
- No model column is both nullable and defaulted, so the explicit-`None` matrix row is shown with `Job.status`: the `NOT NULL constraint failed` error proves NULL reached the database instead of the default.
- `test_missing_persisted_candidate_workspace_stops_worker_before_provider` needs foreign keys off, which can't change mid-transaction, so it writes through a `Session` on a plain engine under `suspended_triggers`.
- `test_save_in_session_flushes_item_and_candidate_in_scope_order` passes at 0078 after the seed change, so `save_in_session` selects no `Job` there and stays at its pinned target, as the Code Map allowed.
- `test_normalization_rejects_missing_archived_and_mismatched_owners_before_ffmpeg` still downgrades to 0074 and runs product code there: NULL ownership is impossible at head (NOT NULL from 0075), and the voice tables gained no columns after 0074. It passes and was not one of the 145.
- Trigger-removal check (implementer, migrations restored afterwards): without `pandrator_execution_scope_workflow_runs_update` (0079), 3 failures in `test_execution_scope_consistency`. Without `pandrator_library_scope_voice_samples_insert` (0078), 5 in `test_library_scope_consistency`. Without `pandrator_project_graph_projects_insert` (0076), 7 in `test_project_graph_consistency`.

## Plan Change Log

## Review Triage Log

Pass 1, quick lens: 0 high, 1 medium, 2 low, 1 false, 0 maybe-false. Nothing deferred.

| Finding | Verdict | Route | Evidence and action |
|---|---|---|---|
| `source_missing_with_survivor` runs `job_ownership` at 0075, whose `_project` and `_session` reads query the old schema | medium | patch | True: with `session_id` NULL it queries projects, sessions and workspaces at 0075. Head refuses that state only through triggers (0079 jobs update, 0076 projects update), so it can be written at head under `suspended_triggers`. The plan's task line bundled it with `no_workspace`; the frozen boundary "Product code runs only at head" settles it, and the fix is local to the test. A later column on those tables would otherwise break it again. |
| `job_ownership` receives a `_RevisionRow`, not a `Job` | low | reject | After the patch above, only `no_workspace` passes it, and `job_ownership` raises on its first line (`job_identity.py:157-158`) before touching ORM state. An ORM stand-in at 0074 would need the machinery this story ruled out. |
| Other tests in the changed files still downgrade and run product code | low | patch (partly) | `test_foreign_preferred_variant_is_never_projected` and `test_cross_workspace_commands_reject_before_any_change_even_when_committed` downgrade to 0077 only to drop 0078 triggers, the pattern the Approach removes: patched to `suspended_triggers` at head. The 0074 normalization test needs NULL ownership, which head can't hold; recorded in Implementation Notes. `save_in_session` at 0078 is the plan's allowed case; recorded too. |
| The explicit-`None` test never reads a stored NULL back | false | reject | The `NOT NULL constraint failed: jobs.status` error is raised by SQLite on receiving NULL, so the row was written with NULL and not the default `queued`. No model column is nullable and defaulted. |
| `_columns` in `test_mandatory_ownership.py` copies `_present_columns` | low | patch | Byte-for-byte copy of the shared lookup. Use the shared one. |

## Design Notes

A seed at an old revision is the test's own data, so it bypasses the ORM: `insert_at_revision` reads each object's set attributes from its instance state, fills the model's Python defaults for unset ones, inserts through `sqlalchemy.table()` with the real column types (so JSON and datetimes bind), and writes the values back so `job.id` works. Product code at an old revision can't work honestly, because it inserts and selects through today's models. Those tests downgraded only to remove triggers, so suspending exactly those triggers at head tests the same reader fence on a real schema. The `no_workspace` case keeps 0074, because NOT NULL from 0075 makes the state impossible at head.

`test_job_media_workspace`'s `no_project` and `different_session` belong to Q5 (story 12), but sit in the same block this story rewrites, so they're fixed here; story 12's Q5 drops to 8.

## Verification

**Commands:** (`<uv env>` from `AGENTS.md`)
- `<uv env> --with sqlalchemy==2.0.52 python -m pytest -q -n 4 <the 11 files>` -- expected: no failures except Q5's other 8 in `test_job_media_workspace.py`.
- Same with `--with sqlalchemy==2.1.8` -- expected: the same.
- Trigger-removal check, once each for 0079, 0078 and 0076 -- expected: failures, then green after restoring.
- `uvx ruff@0.17.3 check tests` and `<uv env> python scripts/test_lanes.py check` -- expected: clean.

**Manual checks:**
- Fork CI on a pushed commit (the owner's go-ahead) -- expected: none of these 145 fail on Ubuntu or Windows.

## Code Review

### 2026-11-10

**Code review complete.** 0 `decision-needed`, 4 `patch`, 0 `defer`, 34 rejected. Lenses: blind hunter, edge case hunter, verification gap (no gaps), intent alignment; none failed.

- [x] [Review][Patch] `no_workspace` accepts any ValueError, so the comment's "raises before any query" goes unchecked [tests/test_job_media_workspace.py:289]
- [x] [Review][Patch] Private `_present_columns` imported across modules; rename to `present_columns` [tests/web_test_support.py:78]
- [x] [Review][Patch] Replay-fence comment reads as the reverse of the code [tests/test_sound_workspace_lifecycle.py:690]
- [x] [Review][Patch] Needless `*[...]` unpack in the upload seed [tests/test_media_scope_consistency.py:310]

**Rejected**

- low: Other files still downgrade to 0074 and run product code (voice execution, upload lifecycle, provider resolution and routes, usage scope). All 74 tests there pass, and none is among the 145. The voice case is recorded above. A fix needs NULL ownership, which head can't hold.
- false: The `no_workspace` no-repair check is a tautology. `_RevisionRow` has no `__setattr__`, so an assignment changes the value read back and the check catches it.
- low: `_RevisionRow` is passed into `job_ownership`. The test comments on this, and an ORM stand-in would need machinery the plan ruled out.
- false: The harness is left at 0074. Teardown only disposes the database.
- false: The downgrade tests nothing. The NULL is stored and read back from the database.
- low: The project-link drop treats a symptom, with a hard-coded list. Revision 0001 has shipped, and deriving the list adds machinery that no caller needs.
- false: `test_usage_workspace_ownership.py:948` could pass for the link-table reason. With the drop stubbed out, the link tables raise `OperationalError` ("no such table: main.browser_auth_sessions"), not `IntegrityError`.
- false: A set relationship is ignored (raised twice). Every seed sets foreign key columns.
- false: Defaults that are neither scalar nor callable, or that need a context, are mishandled (raised twice). The seeded models' defaults are scalars or zero-argument callables.
- false: An explicit None on a JSON column is stored as SQL NULL (raised twice). No caller sets a JSON attribute to None, and the frozen matrix asks for NULL.
- false: Writing skips autoflush, so pending parents are missed (raised twice). Every call site flushes first.
- false: Composite primary keys (raised twice). Both seeded models have single-column keys.
- false: `_RevisionRow` has no `__setattr__` guard. Nothing assigns to it.
- false: A plain engine relies on SQLite's foreign-key default. CPython's SQLite leaves foreign keys off, and the test passes on both drivers.
- low: The helper tests live in a migration file. The plan's task line put them there.
- low: Helper cases are untested. None can be reached by a caller, and both Q2 tests exercise `drop_project_link_tables`.
- false: A pending or persistent object, or one added by cascade, is reinserted. Every seed is a fresh transient object.
- false: The revision's table has NOT NULL columns that are no longer mapped. No migration in range drops a column from `jobs` or `upload_sessions`.
- false: The table is absent at the revision. Both tables exist from 0001.
- false: copying or pickling `_RevisionRow` raises `KeyError`. Nothing copies or pickles it.
- false: `drop_project_link_tables` at or after 0073 drops live tables. Its callers run at 0066 and 0068.
- low: `test_voice_workspace_execution:413` still downgrades. This is the same item as the first rejection.
- false (intent): Tests running at head break the title. The frozen Approach keeps tests that downgraded only to dodge triggers at head.
- false (intent): "One shared helper" read strictly. Seeds share one module, and the tests that dodged triggers reuse `suspended_triggers`, as planned.
- false (intent): Scope goes beyond the 145. This is recorded in Design Notes, the triage log and the findings.
- low (intent): Product code runs at 0074 through a non-ORM object. This is the same item as the `_RevisionRow` rejection.
- false (intent): Fork CI is unconfirmed, and the trigger-removal check is not encoded. This is a manual check waiting on the owner's push, not a code defect.
- false (intent): The Q3 tests never pinned a revision. The intent describes Q3 as rows that triggers refuse.

All 4 patches applied on 2026-11-10. `uvx ruff@0.17.3 check tests` is clean. The 11 files give 217 passed and 8 failed under SQLAlchemy 2.0.52 and again under 2.1.8. The 8 are Q5's, which story 12 owns, the same result as the build.

### Fork CI

Fork CI, 2026-11-10, at `74312c86` (owner-approved `workflow_dispatch` on `epic-fork-releases`): Python quality run `42908352327` succeeded. Web migration preview run `42908347707` failed in 9 of 65 jobs, against 14 at story 10's run `42851994115`. By test name, pytest failures fell from 205 distinct tests to 5, and none is new. The 5 are `test_generation_ignores_saved_preview_routing_metadata` (backlog `bug-saved-preview-settings-fail-generation`), `test_assembly_select_count_does_not_scale_with_segments` (`bug-assembly-checks-access-for-every-segment`), two `test_web_generation_regeneration` checkpoint tests (`bug-alternate-take-model-refused-on-audio-cpp`) and `test_windows_temp_root::test_restrict_leaves_only_the_current_user`, which failed at story 10's run too; it is a fault in story 6's `restrict_to_current_user`, fixed afterwards (see story 6's follow-up). E2E: one failing test per job in both runs, a different one each time (now `epub-content-settings.spec.ts:49` on Ubuntu Firefox and `project-ux.spec.ts:174` on Windows Chromium). Nothing outside `tests/` changed between the two commits, so these are unrelated to this story. None of this story's tests fails on CI, so the story is marked done.

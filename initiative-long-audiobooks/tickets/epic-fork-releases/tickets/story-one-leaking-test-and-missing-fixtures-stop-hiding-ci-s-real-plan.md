---
title: 'One leaking test and missing fixtures stop hiding CI''s real result'
type: bugfix
ticket: '3'
created: '2026-11-09'
status: done
baseline_revision: 'ebe7194d1dc35157ff6b576a22a5b13b3e89fdce'
route: full
route_source: auto
risk: low
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-ci-failures.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `test_session_principal_kind_scope_and_request_cache_contract` leaves a `UserActor` in the context, so about 150 later tests in `web-17` run as a stale user and fail. 35 standalone workflow tests also fail for lack of an actor. Together they hide CI's real result.

**Approach:** Stop the leak, add an autouse check that fails any test leaving an actor set, and give the 35 tests the existing `host_execution_actor` fixture.

**Verify (from the ticket):** On Windows, the 35 listed tests pass, `web-17-serial` fails only the 6 tests assigned elsewhere (`test_audio_cpp_preset_catalogue_wiring`, `test_sound_composition`, 3 in `test_sound_export_integration` and the `test_sound_mix` symlink test), and a test written to leak an actor fails the new check.

**Decisions:**

- Owner, 2026-11-09: proceed to implementation without a separate approval if the spec has no open questions. Nothing is deferred to `deferred-work.md`.
- Found while planning: the leaking test's `project_link` subtest also fails when run alone, because the product deliberately refuses that kind from a browser session (`http_lifecycle.py:237-239`). No ticket holds it and the Verify needs it, so this story fixes it and keeps the refusal asserted.

## Boundaries & Constraints

**Always:** Test-only change. The check runs for every test in every lane and must not import the web app, because a test that never imported it cannot have set an actor. A failed check clears the actor so later tests stay clean. Report the environment used.

**Never:** No autouse actor (two `test_web_workflow_handlers` tests fail under one, by design of the product). No product code changes. No staging or committing. No marks on the tests assigned elsewhere: the real-bug tests in `test_web_generation_regeneration`, `test_worker_pins_prevent_provider_side_effects_on_hash_artifact_and_revision_drift` (story 10) and the 6 in the Verify keep their current result.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Function leak | Test body calls `current_execution_actor.set(actor)` | Test is reported as an error naming the actor type; the next test starts with no actor | Check clears the actor before failing |
| unittest leak | `setUp` or `setUpClass` sets an actor with no cleanup | Same | Same |
| Fixture leak | Generator fixture sets an actor and never resets | Same | Same |
| Scoped actor | `execution_actor_scope` or `host_execution_actor` | Passes | No error expected |
| No web import | Test that never imports `pandrator.web` | Passes, and the module stays unimported | No error expected |

</frozen-after-approval>

## Code Map

- `tests/test_web_security.py:332` -- the leaking test. `principal()` sets the actor at `http_lifecycle.py:258`, and a bare `test_request_context` skips the restore hooks at `:520` and `:619`. `execution_actor_scope` works as a decorator, as at `tests/test_voice_reference_setup.py:39`.
- `tests/conftest.py` -- holds `host_execution_actor`; add the check here. Read the module through `sys.modules.get("pandrator.web.accounts.execution_actor")`.
- `pandrator/web/accounts/execution_actor.py:141-153` -- `current_execution_actor`, `execution_actor_scope`. Do not change.
- `tests/test_training_lifecycle.py:132` -- the convention: `@pytest.mark.usefixtures("host_execution_actor")` per test, including `unittest.TestCase` methods. No file uses a module-level `pytestmark` for it.
- Findings, section "The 35 tests missing an actor" -- the authoritative list of 28 functions in 6 files.
- `scripts/test_lanes.py:438` -- `web-17-serial`; every new test file must be listed.

Measured at baseline, Windows, uv with SQLAlchemy 2.0.52: `web-17-serial` has 164 failures. The whole suite has 472 failures and 102 errors, and a probe found this test is the only one that leaves an actor set, so no other test needs a leak fix.

## Tasks & Acceptance

**Execution:**
- [ ] `tests/test_web_security.py` -- import `execution_actor_scope`, decorate the test with `@execution_actor_scope(None)`, loop over `PRINCIPAL_KINDS - {"project_link"}`, and add a subtest asserting a `project_link` browser session resolves to `None` -- stops the leak and corrects the stale subtest while keeping the refusal covered.
- [ ] `tests/conftest.py` -- add an autouse fixture that, after the test, fails with `pytest.fail(..., pytrace=False)` if the actor is not `None`, clearing it first; name the actor's type only, not its repr -- catches leaks without importing the app or printing identity data.
- [ ] `tests/test_generation_edit_audio.py`, `tests/test_voice_reference_setup.py`, `tests/test_web_cli_database_lifetime.py`, `tests/test_web_generation_first_regeneration.py`, `tests/test_web_generation_regeneration.py`, `tests/test_web_voice_delete_confirmation.py` -- add the `usefixtures` mark to the listed functions only, importing `pytest` where missing -- gives each its explicit host provenance.
- [ ] `tests/test_execution_actor_leak_guard.py` -- new; in `tmp_path`, copy `tests/conftest.py` beside generated test files and run `sys.executable -m pytest` with `PYTHONPATH` set to the repo, covering the I/O matrix -- proves the check end to end in a separate process, so its failures don't fail the real run.
- [ ] `scripts/test_lanes.py` -- list the new file in `web-17-serial` after `tests/test_web_security.py` -- required by `check`.

**Acceptance Criteria:**
- Given the 35 listed tests, when they run on Windows, then all pass.
- Given `web-17-serial`, when it runs on Windows, then its only failures are the 6 named in the Verify.
- Given the whole suite compared by test name with the baseline, when it runs, then no test newly fails and no test reports a leak.

## Implementation Notes

- Results, Windows, uv from `AGENTS.md` with SQLAlchemy 2.0.52. `pixi` isn't installed, so other versions resolve from ranges.
- `web-17-serial` went from 164 failures to the 6 in the Verify, using a short temp path. With the default temp path it shows 8: the other 2 are `test_performance_plans::test_real_generation_runner_applies_frozen_performance_to_each_short_request[1]` and `[2]`, which fail opening a take WAV. They pass at `%TEMP%\p13` and also failed at baseline. Story 6's temp path fix covers them.
- Whole suite at `-n 10`, compared by test name before the last two guard-test edits: failures and errors went from 559 to 519 entries, with none new, and no test reported a leak. The 40 fixed are the 35 listed, the `test_web_security` subtest and 4 in `test_litellm_speech_policy`, which this change doesn't touch and which pass alone (12 of 12), so their baseline failure comes from order under xdist. After the last edits only the guard test, `test_web_security`, the lane and ruff were rerun.
- The 102 errors still in the suite are in 6 `web-07` files, the largest being `test_execution_scope_consistency` with 67. The one run alone fails with `table jobs has no column named external_effect_json`; the others weren't checked. These are for story 4, which records and assigns what `web-07` reveals.
- In the six marked files, 3 tests still fail as the plan expects: `test_worker_pins_prevent_provider_side_effects_on_hash_artifact_and_revision_drift`, `test_grouped_regeneration_checkpoint_uses_output_run_local_takes` and `test_resume_checkpoint_filtering_uses_streaming_batch_path`.
- `PYTEST_ADDOPTS` is parsed shell-style, so backslashes in a Windows `--basetemp` are stripped and the directory lands inside the repo. Use forward slashes. A run here did this; the stray directory was removed.
- Step 5's commit was skipped, because `AGENTS.md` overrides it. Nothing is staged or committed.

## Plan Change Log

## Review Triage Log

Pass 1, quick lens: 0 high, 1 medium, 3 low, 0 false, 0 maybe-false. All were in `tests/test_execution_actor_leak_guard.py` and all were patched. Nothing was deferred.

| Finding | Verdict | Route | Evidence and action |
|---|---|---|---|
| "Stays unimported" is asserted before the guard's teardown | medium | patch | The guard's logic runs after `yield`, so an import added there would pass. Added a second generated test that repeats the assertion. |
| No test asserts that a leak fails the run | low | patch | Every `leak_run` consumer discarded the exit code. Added `test_a_leak_fails_the_run`. |
| Actor-type message checked once across all leaks | low | patch | One message satisfied all four rows. Moved to per-test results from `--junitxml`. |
| `outcomes[name]` raises `KeyError` on a collection failure | low | patch | That hid the subprocess output. Now uses `.get`, so the assertion prints it. |
| Child pytest inherits `PYTEST_ADDOPTS` (found by the orchestrator during verification) | low | patch | Options for the outer run, such as `--basetemp`, reached the child. The child's environment now drops it. |

## Design Notes

The check compares with `None` rather than a value captured before the test. Capturing would miss a `setUpClass` leak, because every test in that class would start with the leaked actor. Nothing in the suite relies on a wider-scoped actor: the probe saw no test start with one.

A leak is reported as an error at teardown, because the fixture is the only place that sees the body, its fixtures and `tearDown`. It still fails the run.

## Verification

`<uv env>` is the one in `AGENTS.md`, with `--with sqlalchemy==2.0.52`. `pixi` isn't installed here, so results resolve from ranges with SQLAlchemy pinned.

**Commands:**
- `<uv env> python -m pytest -q <the 6 files, selecting the listed tests>` -- expected: all 35 pass.
- `<uv env> python -m pytest -q tests/test_execution_actor_leak_guard.py tests/test_web_security.py` -- expected: pass.
- `<uv env> python scripts/test_lanes.py run web-17-serial`, with `PYTEST_ADDOPTS=--basetemp=<short path, forward slashes>` until story 6 lands -- expected: only the 6 named failures.
- `<uv env> python -m pytest -n 10 -q -rfE --tb=no tests` -- expected: compared by name with the baseline, no new failures.
- `<uv env> python scripts/test_lanes.py check` and `uvx ruff@0.17.3 check .` -- expected: pass.

## Code Review

### 2026-11-09

**Code review complete.** 0 `decision-needed`, 3 `patch`, 0 `defer`, 21 rejected. Full diff `ebe7194d..HEAD`, with blind-hunter, edge-case-hunter, verification-gap (no gaps) and intent-alignment.

- [x] [Review][Patch] The `project_link` subtest doesn't show the refusal is what returned `None` [tests/test_web_security.py:357]
- [x] [Review][Patch] The no-print check misses the actor's label [tests/test_execution_actor_leak_guard.py:172]
- [x] [Review][Patch] The child's `PYTHONPATH` drops existing entries [tests/test_execution_actor_leak_guard.py:112]

**Rejected**

- `false`: no baseline in the guard. No fixture binds a wider-scoped actor, and Design Notes explain the choice.
- `low`: wider-scoped leaks are attributed to the first test. Still reported, and exact attribution needs a hookwrapper.
- `false`: `sys` not imported. `conftest.py:1` imports it.
- `false`: `host_execution_actor` missing from the child, lane unsuitable. It is in the copied conftest, and the lane is serial.
- `false`: per-test marks leave ordering luck. The guard stops an actor outliving a test, and marks are the convention.
- `false`: the `project_link` change is unexplained. Decisions above and the commit message record it.
- `low` (twice): other pytest variables or an ini above `tmp_path` reach the child. Nothing in the repo sets them, and a list adds upkeep for no shown failure.
- `false`: contract undocumented. The failure message names both remedies.
- `false`: later tests in a leaking class run unbound. The matrix requires clearing, and the leak is reported.
- `false`: module eviction hides a leak. The only `sys.modules` patches name other modules and restore.
- `low`: `tearDownClass` or module teardown leaks surface on the next test. Not silent, and the fix needs a hookwrapper.
- `false`: stale `project_link` exclusion. A rename or removal fails loudly.
- `low`: truncated junit XML raises `ParseError`. Needs a kill during the report write, and the fix needs a try/except.
- `false`: a plugin imports `pandrator.web` first. The repo declares no `pytest11` entry point.
- `false`: "fails any test" overclaims. Later tests in that scope hold no actor once it is cleared.
- `false`: Verify clauses 1 and 2 aren't in the diff. They are run results, recorded here and rerun.
- `false`: the guard is proved against a copy. The copy comes from the real file at run time.
- `false`: `test_web_security` goes beyond the intent. Decisions record it and Verify needs it.
- `false`: marks on `TestCase` and the count of 35. The marks match the findings at 28 functions and 35 items.
- `false`: the lane may not be Verify's. It is listed in `web-17-serial`.

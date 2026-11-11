---
title: 'Test lanes run on Windows without hand settings'
type: chore
ticket: '6'
created: '2026-11-09'
status: done
baseline_revision: '0a07679dcd57f9e324e9c7d854fd9d1c63b0c88f'
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

**Problem:** On Windows without long paths, pytest's default temp root (`%TEMP%\pytest-of-<user>\pytest-N`, about 57 characters) pushes test paths past 260 characters, so about 25 tests fail with a misleading `FileNotFoundError` unless `PYTEST_ADDOPTS=--basetemp=<short path>` is set by hand.

**Approach:** On Windows, when no `--basetemp` (or `PYTEST_DEBUG_TEMPROOT`) was given, `tests/conftest.py` gives the run its own short, unique temp root under a dedicated folder on the temp drive, `C:\tmp\pt\<4 random chars>`, restricted to the current user. Other platforms and an explicit `--basetemp` are untouched.

**Verify (from the ticket):** Every lane in `python scripts/test_lanes.py`, run from the owner's checkout with no `PYTEST_ADDOPTS`, reports no path-length errors.

**Decisions:**

- Owner, 2026-11-09: implement without a separate approval if the spec has no open questions. Nothing goes to `deferred-work.md`.
- The root sits near the drive root because nothing shorter exists without a system change: the W1 tests fail at a 38-character root and pass at 5 (limit about 26), and `%TEMP%` alone is 33. Long paths and `subst` are system settings, which the epic rules out.
- Owner, 2026-11-09 (code review, renegotiating this section): nothing goes directly under `C:\`; the root is `C:\tmp\pt` instead of `C:\pt`. Finished runs are kept by count (`tmp_path_retention_count`, 3) rather than for 24 hours, which had kept 27 GB in a day. A held lock file protects every running session.

## Boundaries & Constraints

**Always:** Respect an explicit `--basetemp`, from the command line or `PYTEST_ADDOPTS`. Give each run its own directory, so concurrent lanes never share or delete one another's. Prune only inside a root this code created and marked, and never a run whose session is still running. If the short root can't be used, fall back to pytest's default with a config-time warning. No fork-only content.

**Never:** No effect on Linux or macOS. No change to the lane command, time limits or CI workflows. No system setting. No private `_pytest` API. No new dependency. No marks or skips on path-length tests. No staging or committing.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Windows, no basetemp | `win32`, `option.basetemp is None` | `basetemp` = new `<drive>:\tmp\pt\<random>` (pytest creates it), its lock held; root marked and restricted to the user | No error expected |
| Explicit basetemp | `--basetemp` or `PYTEST_DEBUG_TEMPROOT` given, or an xdist worker | Unchanged | No error expected |
| Other platform | not `win32` | Unchanged | No error expected |
| Finished runs | Marked root, runs whose lock is released | All but the newest `tmp_path_retention_count` removed; running ones never | Removal errors ignored |
| Foreign folder | `<drive>:\tmp\pt` exists unmarked and not empty | Left alone; pytest default | Config-time warning naming the remedy |
| Root not usable | `mkdir`, the restriction or the lock fails, or temp is on a network share | pytest default | Config-time warning |

</frozen-after-approval>

## Code Map

- `tests/conftest.py` -- root conftest, an initial conftest for every lane. Add a `pytest_configure` with `@pytest.hookimpl(tryfirst=True)`: `_pytest.tmpdir`'s own `pytest_configure` reads `config.option.basetemp`, so ours must run first. Keep the existing fixtures.
- `tests/windows_temp_root.py` -- new helper; `tests/` is a package, like `tests/web_test_support.py`. Inject platform, temp dir and clock so it tests on any OS. Use `tempfile.gettempdir()`'s anchor, `tempfile.mkdtemp(prefix="", dir=root)` and `shutil.rmtree(..., ignore_errors=True)`.
- xdist passes `basetemp/popen-gwN` to workers through `option.basetemp`, so workers take the explicit row. pytest removes and recreates a given basetemp, hence a fresh directory per run.
- `tests/test_windows_temp_root.py` -- new, covers the matrix with `tmp_path` as the drive root.
- `scripts/test_lanes.py:140` -- add that file to `fast-xdist` beside `tests/test_test_lanes.py`.
- Path-length tests to watch (`findings-ci-failure-classification.md`): W1 (25, session bundle and evidence files), C1 (10, `test_generation_cast_runtime`, `test_project_speech_plan_copy`), and two `test_performance_plans` cases in `web-17-serial`.
- `docs/release-notes-next.md` -- no entry; test harness only.

## Tasks & Acceptance

**Execution:**
- [x] `tests/windows_temp_root.py` -- choose, create, mark and prune the root, make the run directory, decide whether to override -- small hook, testable off Windows.
- [x] `tests/conftest.py` -- add the hook, with a config-time warning on fallback -- every lane and single-file run gets the root.
- [x] `tests/test_windows_temp_root.py` -- unit-test every matrix row.
- [x] `scripts/test_lanes.py` -- add the test file to `fast-xdist`.
- [x] Run every lane on Windows with no `PYTEST_ADDOPTS` and check for path-length failures -- the ticket's Verify.
- [x] Update the W1 rows in `findings-ci-failures.md` and the memory note on short basetemps.

**Acceptance Criteria:**
- Given a Windows checkout with no `PYTEST_ADDOPTS`, when every lane runs through `run <lane>`, then no W1, C1 or `web-17` path-length test fails and no other failure names a path of 248 characters or more.
- Given two lanes run at once, when both finish, then neither failed because of the other's temp directory.
- Given Linux CI, when the lanes run, then temp paths are pytest's default.

## Implementation Notes

- **The ticket's Verify is met locally.** All 28 lanes ran through `run <lane> --junitxml` on Windows with `PYTEST_ADDOPTS` unset, five at a time, so lanes also ran concurrently (AC 2). The environment was uv from `AGENTS.md` with SQLAlchemy 2.0.52, without pixi. Every W1 test and both `web-17` `test_performance_plans` cases pass. The three failures in C1's files are the classified D1, D2 and B7 assertion failures, not path errors. No failure message or traceback in any JUnit file names a path of 200 characters or more, and no log shows a `pytest-of-` path. Lane logs are in `%TEMP%\claude\lr\`.
- Totals by lane match the known clusters: `web-22` (110 failed, 71 errors) and `web-23` (30 failed, 9 errors) are the largest; none is new to this change.
- `C:\pt` held the marker and one folder per run afterwards. Linux is unaffected by construction (`sys.platform` gate); AC 3 rests on that and CI's next run.
- After the review patches, `web-07`, `web-17` and `web-21` reran side by side with the same per-lane totals (32, 6 and 6 failed), no path-length failure and no `pytest-of-` path. `tests/test_windows_temp_root.py` now has 11 tests, including the hook and the race.
- The findings file has a "Resolved by story 6" line under the W1 point; its W1 table rows still name story 6, which remains the assignment. The memory note was rewritten. The findings edit and this plan are uncommitted on `fork-files`.

## Plan Change Log

- 2026-11-09, code review: the owner moved the root to `C:\tmp\pt` and chose count-based retention (Decisions above). Run names shrank to 4 characters so `C:\tmp\pt\abcd` stays 14 characters. Runs hold a lock file; the root is restricted with `icacls`; `PYTEST_DEBUG_TEMPROOT` is respected.

## Review Triage Log

Pass 1, quick lens: 0 high, 1 medium, 4 low, 1 false, 0 maybe-false. Nothing deferred.

| Finding | Verdict | Route | Evidence and action |
|---|---|---|---|
| Two lanes creating `C:\pt` for the first time race; the loser falls back to the long default | medium | patch | `root.exists()` then `mkdir(parents=True)` without `exist_ok`, and the marker is written after `mkdir`, so a concurrent run can see an unmarked root. Fix: `exist_ok=True`, and adopt an empty unmarked root. |
| An interrupted first run leaves an empty unmarked root that every later run refuses | low | patch | Same cause, same fix; the empty-root adoption self-heals it. |
| `test_uncreatable_root_raises` tests the foreign-folder branch, not `mkdir` failing | low | patch | A file at `<drive>/pt` makes `exists()` true. Test now makes `mkdir` itself fail. |
| The real `short_root_path` is never tested | low | patch | `setUp` patches it out in every test. Direct test added. |
| The conftest hook and its warning are untested | low | patch | Tests stop at `make_run_dir`. Hook test with a fake config added. |
| No evidence of the Verify run, yet the findings call W1 resolved | false | reject | The lanes ran with JUnit output before review; the plan's notes were not yet written. Now recorded above. |
| The copied conftest in `test_execution_actor_leak_guard` now depends on `tests` resolving through `PYTHONPATH` | low | reject | It works today and passed in its lane, and making the copy self-contained adds machinery for a case nobody meets. Its inner runs' `C:\pt` folders are pruned after a day. |

## Design Notes

`C:\tmp\pt\abcd` is 14 characters, 25 with xdist's `\popen-gw10`, under the limit of about 26. The lock file is created first with `O_EXCL`, so names never collide, and pytest creates the folder on first use. Windows refuses to delete a file another process holds open, so failing to delete a lock means its session is running. Python's handles aren't inherited by child processes, so a leftover server can't keep a run alive. Retention reads the public `tmp_path_retention_count` ini, so no private API.

## Verification

`<uv env>` from `AGENTS.md` with `--with sqlalchemy==2.0.52`; `pixi` isn't installed. Unset `PYTEST_ADDOPTS` throughout.

**Commands:**
- `<uv env> python scripts/test_lanes.py check` -- expected: pass.
- `<uv env> python -m pytest -q tests/test_windows_temp_root.py tests/test_test_lanes.py` -- expected: pass.
- `uvx ruff@0.17.3 check tests scripts` -- expected: pass.
- `<uv env> python scripts/test_lanes.py run <lane> --junitxml <path>` for every lane -- expected: no path-length failure; others already classified.

**Manual checks:**
- `C:\tmp\pt` holds the marker, at most the newest 3 finished runs plus running ones, and grants only the current user (`icacls`); no lane output shows a `pytest-of-` path.

## Code Review

### 2026-11-09

Code review (full diff `0a07679d..0ce71320`, four lenses, none failed): 2 `decision-needed` (both resolved by the owner), 12 `patch` plus 1 found while verifying, 0 `defer`, 5 rejected.

- [x] [Review][Decision] Run folders pile up (27 GB, 38 runs in `C:\pt` after one day) — `--basetemp` bypasses pytest's keep-3 retention, and the 24-hour prune keeps every run from the day. The 24-hour rule is in the frozen I/O matrix. Options: (1) keep the newest `tmp_path_retention_count` finished runs (3 by default), with running ones protected by their lock; (2) keep 24 hours, and also remove a run's folder at session end when every test passed; (3) keep 24 hours as is. Recommended: 1 — it is the retention the lanes had before, honours the ini setting, and bounds disk to a few runs. **Resolved by the owner: option 1**, and the root moves to `C:\tmp\pt` (nothing directly under `C:\`); the 11 hand-made folders and `C:\pt` were deleted with the owner's go-ahead.
- [x] [Review][Decision] Contributors aren't told about `<drive>:\pt` — nothing points someone who finds the folder to its source. Options: (1) one bullet in `AGENTS.md` "Running and verifying", with the backup copy refreshed; (2) no `AGENTS.md` change, relying on docstrings, the fallback warning and the memory note. Recommended: 1 — `AGENTS.md` is where someone looks. **Resolved by the owner: option 1**, describing `C:\tmp\pt`.
- [x] [Review][Patch] Restrict the root to the current user; it inherits `C:\`'s "Authenticated Users: Modify" [tests/windows_temp_root.py:212]
- [x] [Review][Patch] Hold a lock file for each run so pruning never deletes a live run; create the lock first and let pytest create the folder [tests/windows_temp_root.py:229]
- [x] [Review][Patch] Re-check the marker after listing, so a lane racing the root's creation doesn't call it foreign [tests/windows_temp_root.py:218]
- [x] [Review][Patch] Test that the hook forwards an explicit `--basetemp` through the real `make_run_dir` [tests/test_windows_temp_root.py:153]
- [x] [Review][Patch] Windows-only test that a real session's `tmp_path` lands under the short root [tests/test_windows_temp_root.py]
- [x] [Review][Patch] Respect `PYTEST_DEBUG_TEMPROOT` [tests/windows_temp_root.py:247]
- [x] [Review][Patch] Refuse a UNC temp directory [tests/windows_temp_root.py:205]
- [x] [Review][Patch] Turn a `gettempdir()` `OSError` into the fallback [tests/windows_temp_root.py:249]
- [x] [Review][Patch] Docstring claims a long-path condition the code doesn't test [tests/windows_temp_root.py:1]
- [x] [Review][Patch] Fallback warning names a remedy [tests/windows_temp_root.py:221]
- [x] [Review][Patch] Test gaps: stray file makes a root foreign, files and marker survive pruning, `mkdtemp` failure, `patch.object`, setUp comment [tests/test_windows_temp_root.py]
- [x] [Review][Patch] No empty run folder for `--help` or `--collect-only` (covered by the lock patch) [tests/windows_temp_root.py:252]

- [x] [Review][Patch] Found while verifying: pruning left a finished run half-deleted, because `rmtree(ignore_errors=True)` can't remove read-only files on Windows (git objects from a test repo). `remove_tree` now clears the read-only bit and retries, as pytest does [tests/windows_temp_root.py]
- [x] [Review][Patch] Owner's request during review: `tests/test_voice_collection_workspace.py` and `tests/test_voice_workspace_ownership.py` hardcoded `/tmp/...` (`C:\tmp\pandrator-voice-*-worker` on Windows); both fixtures now use `tmp_path`. Both files pass (30 tests) and the empty leftover folders were removed [tests/test_voice_collection_workspace.py:48, tests/test_voice_workspace_ownership.py:94]

Verification after the patches (uv env from `AGENTS.md`, SQLAlchemy 2.0.52, no pixi, `PYTEST_ADDOPTS` unset): `ruff check tests scripts` and `ruff format --check` on the three files pass; `test_lanes.py check` passes; `tests/test_windows_temp_root.py` and `tests/test_test_lanes.py` give 43 passed on Windows, including the Windows-only lock, ACL and session-basetemp tests. `fast-xdist` ran beside the other lanes, and `web-07-serial`, `web-17-serial` and `web-21-serial` then ran together. Each failed exactly the tests it failed in the build's run (10, 32, 6 and 6), with no `pytest-of-` path and no fallback warning; `fast-xdist` had 17 more passes from the new tests. `C:\tmp\pt` grants only the owner's account. Each start pruned it to the newest three finished runs plus running ones, and it held 2.0 GB afterwards. A second process can't delete a held lock, and can once it is closed. Not covered: Linux CI (gated by `sys.platform`), the other 24 lanes after these patches, and the UNC and `icacls` failure paths, which only fake-driven tests reach.

Rejected:
- tempfile-based tests may still overflow — false: the Verify run over all 28 lanes found no path-length failure.
- Fallback reverts to the long root — false: the frozen intent requires that fallback.
- Fix lives in conftest, not the lane runner — false: the approved approach.
- Persistent root and pruning are outside the intent — false: the frozen approach and matrix specify both.
- Leak-guard's copied conftest relies on `PYTHONPATH` — low: passes today, and a self-contained copy adds machinery for a case nobody meets.

### Follow-up fix, 2026-11-10

Fork CI (runs `42851994115` and `42908347707`) failed `test_restrict_leaves_only_the_current_user` on the Windows runners: after `restrict_to_current_user` the folder still held SYSTEM, Administrators and the runner account, as explicit entries with no `(I)` flag. `/inheritance:r` removes only inherited entries, so on any folder that already holds explicit ones the root was not restricted to the current user. It passed locally only because local folders get inherited entries. Fix: `tests/windows_temp_root.py` runs `icacls <root> /reset` before the existing `/inheritance:r /grant:r` call. New test `test_restrict_removes_explicit_entries_too` makes the entries explicit with `/inheritance:d` first. The old code leaves 4 entries there; with the fix, `tests/test_windows_temp_root.py` passes 25 of 25 (Windows, `<uv env>`, Python 3.11) and ruff is clean. Committed as `f6aefbe2` with the owner's go-ahead and pushed. On fork CI run `42916821305`, the `windows-fast-xdist` job succeeded, and its JUnit report shows both `test_restrict_leaves_only_the_current_user` and `test_restrict_removes_explicit_entries_too` passed, not skipped.

With the strict expected-failure markers in `cee8fa56`, fork run `42922628333` passed every Python lane on Ubuntu and Windows (63 of 65 jobs). The 2 failures were single flaky browser tests, a different test on each run.

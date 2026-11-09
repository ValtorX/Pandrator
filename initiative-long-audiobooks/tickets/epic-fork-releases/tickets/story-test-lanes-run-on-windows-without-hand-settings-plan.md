---
title: 'Test lanes run on Windows without hand settings'
type: chore
ticket: '6'
created: '2026-11-09'
status: built
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

**Approach:** On Windows, when no `--basetemp` was given, `tests/conftest.py` gives the run its own short, unique temp root under a dedicated folder at the temp drive's root (`C:\pt\<8 random chars>`). Other platforms and an explicit `--basetemp` are untouched.

**Verify (from the ticket):** Every lane in `python scripts/test_lanes.py`, run from the owner's checkout with no `PYTEST_ADDOPTS`, reports no path-length errors.

**Decisions:**

- Owner, 2026-11-09: implement without a separate approval if the spec has no open questions. Nothing goes to `deferred-work.md`.
- The root sits at the drive root because nothing shorter exists without a system change: the W1 tests fail at a 38-character root and pass at 5 (limit about 26), and `%TEMP%` alone is 33. Long paths and `subst` are system settings, which the epic rules out.

## Boundaries & Constraints

**Always:** Respect an explicit `--basetemp`, from the command line or `PYTEST_ADDOPTS`. Give each run its own directory, so concurrent lanes never share or delete one another's. Prune only inside a root this code created and marked. If the short root can't be used, fall back to pytest's default with a config-time warning. No fork-only content.

**Never:** No effect on Linux or macOS. No change to the lane command, time limits or CI workflows. No system setting. No private `_pytest` API. No new dependency. No marks or skips on path-length tests. No staging or committing.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Windows, no basetemp | `win32`, `option.basetemp is None` | `basetemp` = new empty `<drive>:\pt\<random>`; root holds a marker file | No error expected |
| Explicit basetemp | `--basetemp` given, or an xdist worker | Unchanged | No error expected |
| Other platform | not `win32` | Unchanged | No error expected |
| Stale runs | Marked root, subdirs untouched over 24 hours | Those removed; newer kept | Removal errors ignored |
| Foreign folder | `<drive>:\pt` exists unmarked | Left alone; pytest default | Config-time warning |
| Root not creatable | `mkdir` raises `OSError` | pytest default | Config-time warning |

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

`C:\pt\k3j9x0a1` is 14 characters, 25 with xdist's `\popen-gw10`, under the limit of about 26. `mkdtemp` is race-free, so no lock. Pruning by age replaces pytest's "keep 3", which needs private API; a day far outlasts any lane.

## Verification

`<uv env>` from `AGENTS.md` with `--with sqlalchemy==2.0.52`; `pixi` isn't installed. Unset `PYTEST_ADDOPTS` throughout.

**Commands:**
- `<uv env> python scripts/test_lanes.py check` -- expected: pass.
- `<uv env> python -m pytest -q tests/test_windows_temp_root.py tests/test_test_lanes.py` -- expected: pass.
- `uvx ruff@0.17.3 check tests scripts` -- expected: pass.
- `<uv env> python scripts/test_lanes.py run <lane> --junitxml <path>` for every lane -- expected: no path-length failure; others already classified.

**Manual checks:**
- `C:\pt` holds the marker and one directory per run; no lane output shows a `pytest-of-` path.

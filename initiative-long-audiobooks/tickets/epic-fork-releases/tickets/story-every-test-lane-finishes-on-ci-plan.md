---
title: 'Every test lane finishes on CI'
type: chore
ticket: '4'
created: '2026-11-09'
status: done
baseline_revision: 'f94fd9f6f853c940b402d36d09792b4a1e2b93de'
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-ci-failures.md'
  - '{project-root}/_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-ci-failure-classification.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `web-07-serial` is cancelled at its job time limit on both operating systems, so about three quarters of its tests, including the lease fix's, never report a CI result, and the true failure total is unknown.

**Approach:** Split the lane into six lanes that finish with room to spare, run them on both operating systems, and add a check that every lane stays in the CI matrix. Record each failure the split reveals in the findings and assign it to a story.

**Verify (from the ticket):** On a fork CI run, every lane finishes within its time limit on Ubuntu and Windows, the lease fix's tests report a result, and `python scripts/test_lanes.py check` passes.

**Decisions:**

- Owner, 2026-11-09: proceed to implementation without a separate approval if the spec has no open questions. Nothing is deferred to `deferred-work.md`.
- A failure that is more than a session's work goes to a new story, per the ticket. The ticket tree is the owner's, so a proposed story is named in the findings and put to the owner rather than added to `tickets.toml` unasked.
- Owner, 2026-11-09, after the CI run: fix the FFmpeg install in this story, because a failed install doesn't fail its step and that undermines the CI results later stories rely on. Add the two proposed stories as 11 and 12, renumbering the old 11 and 12.

## Boundaries & Constraints

**Always:** Keep every file's relative order, so each new lane is a contiguous run of the old one and order inside it is unchanged. Keep lane names in the `web-NN-serial` form. Keep the change small enough to offer upstream: no fork-only content in `scripts/` or `.github/`. Report the environment used.

**Never:** No test or product code fixes: this story exposes failures and assigns them. No change to job time limits, pytest options or xdist use in serial lanes. No new dependency, so no `import yaml`. No staging, committing or pushing without the owner's direct go-ahead. No marks on failing tests.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Lane missing from an OS | A manifest lane has no `web-preview.yml` entry for one OS | The new test fails and names the lane and OS | Test failure |
| Unknown lane in the matrix | A matrix entry names a lane not in the manifest | The new test fails and names it | Test failure |
| Several lanes in one entry | `*-services` lists four lanes across folded lines | All four count as covered | No error expected |

</frozen-after-approval>

## Code Map

- `scripts/test_lanes.py:574` -- `web-07-serial`, 304 files. Insert five lane headers inside the tuple rather than moving lines, so the diff stays small. Leave the odd indentation at `:683-696` alone.
- `.github/workflows/web-preview.yml:65,123` -- the matrix. New lanes get one `name`/`os`/`lanes` entry per OS, placed with `web-17` and `web-18` at `:157-168`. `timeout-minutes` at `:39` stays.
- `.github/workflows/windows-serial-lanes.yml:17-21` -- the manual Windows list; add the new lanes.
- `tests/test_test_lanes.py` -- add the matrix check here, in the fast lane. No test reads the workflows today. PyYAML is only transitive, so parse with `re`.
- `tests/test_generation_workspace_execution.py` -- the lease fix's tests (`415aaa8a`), which land in the sixth lane.
- Findings -- `findings-ci-failures.md` ("Not reachable" row, "Limits", "Where each group went") and its classification file. These are on `fork-files`, and committed with `git -C _bmad-output`.

Measured at `f94fd9f6`, Windows, uv with SQLAlchemy 2.0.52, `-n 10 --dist=loadfile`: 5,312 tests in 304 files, 75.1 minutes of test time, 218 failures and 80 errors in 57 files. CI's cutoffs (24% in 35 minutes on Ubuntu, 29% in 45 on Windows) match this local time at a ratio of 1.5 on both, so the whole lane needs about 113 CI minutes. Every other Python job finishes: the slowest is `windows-services` at 25.7 of 45 minutes.

Split by measured time, each about 12.5 local and 19 CI minutes plus setup:

| Lane | First file | Last file | Files | Tests |
|---|---|---|---|---|
| `web-07-serial` | `test_passage_markers` | `test_project_copy_media` | 46 | 875 |
| `web-19-serial` | `test_provider_workspace_access` | `test_generation_cast_source_compatibility` | 27 | 460 |
| `web-20-serial` | `test_cast_source_boundaries` | `test_source_processing_children` | 40 | 603 |
| `web-21-serial` | `test_source_processing_children_http` | `test_protected_workflow_routes` | 54 | 790 |
| `web-22-serial` | `test_workspace_routes` | `test_project_sound_workspace_access` | 43 | 842 |
| `web-23-serial` | `test_voice_workspace_ownership` | `test_web_provider_modalities` | 94 | 1,742 |

## Tasks & Acceptance

**Execution:**
- [x] `scripts/test_lanes.py` -- split `web-07-serial` per the table, with a one-line comment saying why -- gives each lane a finishable budget without reordering.
- [x] `.github/workflows/web-preview.yml` -- add `web-19` to `web-23` for both operating systems -- runs the new lanes.
- [x] `.github/workflows/windows-serial-lanes.yml` -- add the new lanes -- keeps the manual run covering the same files.
- [x] `tests/test_test_lanes.py` -- add a test that each OS's matrix entries cover exactly the manifest's lanes, covering the I/O matrix -- stops a lane silently dropping out of CI.
- [x] Run each new lane serially with the exact CI command, and compare by test name with the whole-lane run -- shows what the split alone changes.
- [x] `findings-ci-failures.md` -- group the failures by cause, map each group to its story, and name any proposed new story -- required by the ticket. Windows results first, CI's after the run.
- [x] Owner gate -- ask for the go-ahead to commit and push, then have CI run on the branch and read its results per lane on both operating systems.

**Acceptance Criteria:**
- Given the new manifest, when `check` runs, then it passes and every test file of the old lane is in exactly one of the six.
- Given a fork CI run of this branch, when it completes, then no Python job is cancelled and the JUnit artifact of every lane exists on both operating systems.
- Given that run, when the lease fix's tests are looked up, then each reports a result on both operating systems.
- Given every failure in the six lanes, when the findings are read, then each is assigned to a story, a backlog bug or a named proposed story.

## Implementation Notes

- **The ticket's Verify is met on fork run `42638129618`, at `93c0e68e3277e8fa9f6eda4119fcf9440aaacc4e`.** All 50 Python jobs finished within their limits with JUnit artifacts, the six lanes took 12.2 to 17.8 minutes on Ubuntu and 18.1 to 22.8 on Windows, the lease fix's tests report a result on both, and `test_lanes.py check` passed as a step of each job. The run's own conclusion is `cancelled`, because one E2E job ran out of time in its browser install; that job runs no lane.
- CI shows 255 failures in the six lanes on Ubuntu and 334 on Windows, and both reconcile with the local 293 by per-file counts. Details are in `findings-ci-failures.md`, "CI's result for the split". Per-test results come from log summaries rather than the JUnit files, which need a download.
- A failed Chocolatey install doesn't fail its step, which caused 16 failures on `windows-web-20`. The owner asked for the fix in this story: `e2ca83b817f12aed9da3282ebbc14e9b89dcd57b` retries the install and fails the step if `ffprobe` is still missing, in both the Python and E2E jobs. Its script was run here against a stubbed `choco`, passing on the first and third tries and failing when `ffprobe` never appears. **No CI run covers this commit yet**, so one is needed after the push.
- The review is the owner's next step in another session. It covers `f94fd9f6..93c0e68e3277e8fa9f6eda4119fcf9440aaacc4e` plus `e2ca83b817f12aed9da3282ebbc14e9b89dcd57b`. Between them sits `7e96cf5b`, the merge of `3b4484ce`, which belongs to story 5.
- Results, Windows, uv from `AGENTS.md` with SQLAlchemy 2.0.52. `pixi` isn't installed. The six lanes ran side by side, each serially with CI's command and its own short temp root, in 655 to 760 seconds each.
- Compared by test name with the whole-lane run: the same 5,312 tests, 293 failing or erroring against 298, and none failing only after the split. The 5 that failed only in the whole-lane run were path-length failures, from the longer temp path `xdist` creates.
- The six lanes concatenated match the old lane's 304 files in the same order, checked against `f94fd9f6`.
- All 293 are assigned in `findings-ci-failures.md`, with 253 in new clusters in the classification file. None is a new product bug. Stories 11 and 12 cover 215 of them, added with the owner's approval after the CI run.
- The implementing agent skipped the serial runs, reading 75 minutes of summed test time as wall clock, so the orchestrator ran them. The whole-lane run took 9 minutes at `-n 10`.
- Story 3 recorded 102 errors in 6 `web-07` files, from a whole-suite run. These runs show 80 errors in 3 files. That difference wasn't investigated.

## Plan Change Log

## Review Triage Log

Pass 1, quick lens: 0 high, 0 medium, 6 low, 1 false, 0 maybe-false. Nothing was deferred. The orchestrator also removed a leftover in the patched parser: the `- ` handling on `os:`, which the split has already removed.

| Finding | Verdict | Route | Evidence and action |
|---|---|---|---|
| Findings not updated, and no serial comparison recorded | false | reject | Both were still in progress when the lens read them. Both are now done and recorded above. |
| No CI run yet, so criteria 2 and 3 are unproven | low | owner gate | True, and the plan's own gate. Stays open until the owner approves the push. |
| `_matrix_problems` reads only two OS names | low | patch | An unknown lane under another `os:` passed. Now loops over every OS in the matrix too, with a `macos-latest` case. |
| `exclude:` could count as coverage, and the regex isn't anchored to `python-lanes` | low | patch | `exclude:` is used elsewhere in the file. The parser now reads only the entries directly under the `python-lanes` `include:`. |
| An entry whose first key isn't `name` is credited to the previous entry, and a missing `os` gives a bare `KeyError` | low | patch | Replaced the line parser with a shorter one that splits entries first and names the entry it can't read. |
| A quoted `os:` or an inline comment on `lanes:` gives a misleading message | low | reject | Each still fails loudly, the file uses neither, and handling them adds complexity. |
| The lane comment is two lines where the plan says one | low | patch | Now one line. |

## Design Notes

Contiguous runs keep related files together and keep any order dependence inside a lane as it was, so a new failure after the split points at a real dependence on earlier lanes.

Six lanes rather than five leaves about 12 minutes of room on Ubuntu's 35, with growth expected. Time was measured under contention on another OS, so the margin also covers that error. The CI run is what settles it.

The ticket's CI run does not have to touch `main`: `web-preview.yml` has `workflow_dispatch` and exists on the default branch, so the owner can start it on `epic-fork-releases` from the Actions page after a push.

## Verification

`<uv env>` is the one in `AGENTS.md`, with `--with sqlalchemy==2.0.52`. `pixi` isn't installed here. CI resolves SQLAlchemy 2.1.8 from ranges, so Windows results here can differ from CI's in the `sqlite:///C%3A` group.

**Commands:**
- `<uv env> python scripts/test_lanes.py check` -- expected: pass.
- `<uv env> python -m pytest -q tests/test_test_lanes.py` -- expected: pass.
- `uvx ruff@0.17.3 check scripts tests/test_test_lanes.py` -- expected: pass.
- `<uv env> python scripts/test_lanes.py run <lane> --junitxml <path>` for each of the six, with `PYTEST_ADDOPTS=--basetemp=<short path, forward slashes>` until story 6 lands -- expected: no failure absent from the whole-lane run, or each one explained.

**Manual checks:**
- On the CI run, read each job's conclusion and duration, and download or read the JUnit results for the failure list. Logs need a GitHub sign-in, and `gh` isn't installed.

## Code Review

### 2026-11-09

**Code review complete.** 0 `decision-needed`, 10 `patch`, 0 `defer`, 14 rejected. Range: `f94fd9f6..93c0e68e` plus `e2ca83b8`. Lenses: blind hunter, edge case hunter, verification gap and intent alignment; none failed.

- [x] [Review][Patch] Manual Windows lanes run with no FFmpeg [.github/workflows/windows-serial-lanes.yml:39]
- [x] [Review][Patch] The FFmpeg step has no time limit [.github/workflows/web-preview.yml:216] -- a precaution: no stall has been observed. The bound is 10 minutes against a measured maximum of 51 seconds across the run's 33 Windows jobs.
- [x] [Review][Patch] Lane names in `windows-serial-lanes.yml` aren't checked against the manifest [tests/test_test_lanes.py]
- [x] [Review][Patch] The duplicate branch, an absent OS and parser failures are untested [tests/test_test_lanes.py]
- [ ] [Review][Patch] No CI run covers HEAD, and the lease fix's results are inferred from no `FAILED` line [this plan, Implementation Notes] -- the checks are written below, and stay open until the owner's run.
- [x] [Review][Patch] The install script is copied into each job [.github/actions/install-windows-ffmpeg/action.yml]
- [x] [Review][Patch] The retry sleeps after its last attempt, with a fixed wait [.github/actions/install-windows-ffmpeg/action.yml:11]
- [x] [Review][Patch] The matrix parser isn't bounded to the `python-lanes` job [tests/test_test_lanes.py:98]
- [x] [Review][Patch] The lane comment omits the rule and runs to 110 characters [scripts/test_lanes.py:574]
- [x] [Review][Patch] The E2E job's time limit has no row in "Where each group went" [findings-ci-failures.md]

**Checks after the patches**, Windows, uv from `AGENTS.md` with SQLAlchemy 2.0.52; `pixi` isn't installed. Nothing here is committed.

- `test_lanes.py check` passes; `tests/test_test_lanes.py` has 19 passed, 11 subtests passed; `ruff@0.17.3` passes on `scripts` and that file; `actionlint-py` 1.7.12.25, through `uvx`, passes on both workflows.
- Each new guard failed when broken on purpose, then the files were restored: the parser's job bound, keeping duplicates, the listed-twice branch, a dead manual lane and a bare `choco` in the manual workflow.
- The action's script ran against a stubbed `choco` with a clean PATH: passes on the first and third tries, sleeping 30 then 60 seconds, and exits 1 with its message when `ffprobe` never appears. That used Windows PowerShell 5.1, because `pwsh` isn't installed here and CI uses `pwsh`.
- Rechecked independently: the six lanes concatenate to the baseline's 304 files in order, with no other lane changed, and the public API confirms the plan's job list and durations for run `42638129618`. JUnit files and logs need a sign-in, so they weren't read.

**Rejected:**

- `false`: the guard checks `ffprobe` but not `ffmpeg`, and an absent `ffmpeg` shim (two lenses). The package ships and shims both together, and a failed install goes to `lib-bad`.
- `false`: retries need `--force`. A failed install isn't registered, and a successful one creates the shims.
- `false`: a failed install passes if `ffprobe` is already on PATH. W9 shows the runner has none first, and if it had, the tests would have what they need.
- `false`: `check` doesn't see the matrix. Verify names it as a manifest check, and the plan puts the matrix rule in a test.
- `false`: the workflow needs a manual dispatch on this branch. Known, and in the design notes.
- `false`: one test goes to a backlog bug rather than a story. The epic's 2026-11-09 decision puts product faults there.
- `false`: the intent's wording changed during the work. An owner-approved renumbering, recorded in the epic.
- `low`: the parser drops per-entry data. Real only after a future hand edit, loud at runtime for duplicate names and combined lanes, and the fix reshapes the parser.
- `low`: jitter, a cached FFmpeg download and a fuller `throw` message. Extra machinery; `choco`'s output sits just above the `throw`.
- `low`: nothing warns as a lane nears its limit. A lane that outruns it is cancelled visibly, and a warning is new CI machinery.
- `low`: the guard runs only in `fast-xdist`. Dropping that lane from both operating systems is a large visible edit.
- `low`: the run's conclusion is `cancelled`. Verify says lanes, and that job runs none. Its findings row is the patch above.
- `low`: counts are firm only by per-file count, and 102 against 80 errors wasn't investigated. The limits are stated, and CI's own counts reconcile with the lane runs, so the old whole-suite figure feeds nothing.
- `low`: the baseline is by lane and cluster, not per test. Later stories check clusters and rerun CI themselves.

**The owner marked this story done on 2026-11-09, before this run.**

**Still open, for the owner's gate:** one fork CI run of the committed fixes, started by `workflow_dispatch` on `epic-fork-releases`, because a push to this branch doesn't trigger `web-preview.yml`. On that run, read these:

1. `windows-*` jobs: `Install Windows FFmpeg` succeeds through the new local action and its `timeout-minutes`, which actionlint accepts but no run has exercised.
2. The `web-23` JUnit files on both operating systems: each `test_generation_workspace_execution` test has a result, which settles criterion 3 by test name.
3. `E2E (ubuntu-latest, firefox, 2/2)`: whether it finishes this time.

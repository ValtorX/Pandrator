---
title: 'Every test lane finishes on CI'
type: chore
ticket: '4'
created: '2026-11-09'
status: built
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
- [ ] `findings-ci-failures.md` -- group the failures by cause, map each group to its story, and name any proposed new story -- required by the ticket. Windows results first, CI's after the run. Windows results are recorded; CI's are pending.
- [ ] Owner gate -- ask for the go-ahead to commit and push, then have CI run on the branch and read its results per lane on both operating systems.

**Acceptance Criteria:**
- Given the new manifest, when `check` runs, then it passes and every test file of the old lane is in exactly one of the six.
- Given a fork CI run of this branch, when it completes, then no Python job is cancelled and the JUnit artifact of every lane exists on both operating systems.
- Given that run, when the lease fix's tests are looked up, then each reports a result on both operating systems.
- Given every failure in the six lanes, when the findings are read, then each is assigned to a story, a backlog bug or a named proposed story.

## Implementation Notes

- **Not finished: the ticket's Verify needs a fork CI run.** Acceptance criteria 2 and 3, and CI's half of the findings, wait on it. The owner approved the commit and push on 2026-11-09: the code is `93c0e68e3277e8fa9f6eda4119fcf9440aaacc4e` on `epic-fork-releases`. A push to that branch doesn't trigger CI, so the owner starts `Web migration preview` on it from the Actions page.
- Results, Windows, uv from `AGENTS.md` with SQLAlchemy 2.0.52. `pixi` isn't installed. The six lanes ran side by side, each serially with CI's command and its own short temp root, in 655 to 760 seconds each.
- Compared by test name with the whole-lane run: the same 5,312 tests, 293 failing or erroring against 298, and none failing only after the split. The 5 that failed only in the whole-lane run were path-length failures, from the longer temp path `xdist` creates.
- The six lanes concatenated match the old lane's 304 files in the same order, checked against `f94fd9f6`.
- All 293 are assigned in `findings-ci-failures.md`, with 253 in new clusters in the classification file. None is a new product bug. Two proposed stories cover 215 of them and wait on the owner's decision; `tickets.toml` is unchanged.
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

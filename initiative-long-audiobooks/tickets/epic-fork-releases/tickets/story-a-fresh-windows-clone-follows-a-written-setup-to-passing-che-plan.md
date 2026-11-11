---
title: 'A fresh Windows clone follows a written setup to passing checks'
type: chore
ticket: '14'
created: '2026-11-10'
status: built
baseline_revision: 'cee8fa56c36c0962f7b22f46b788bfd4d52fcdbd'
route: full
route_source: auto
risk: low
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/fork-maintenance.md'
  - '{project-root}/_bmad-output/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Stories 1–13 made the checks work from the owner's Windows checkout, but the setup that got there (Node 24, uv, the LF refresh, Playwright browsers) lives in plan notes, some AGENTS.md lines are stale, the remaining failures are scattered across findings, and no fix has been offered upstream.

**Approach:** Write a Windows setup into AGENTS.md (and its backup), correct the lines the epic made obsolete, prove it by following only that text in a fresh clone in a new folder through lint, types, every test lane, web quality, the browser build and E2E, record every remaining failure with its reason, and offer the upstream-useful fixes as focused PRs with the owner's go-ahead.

## Boundaries & Constraints

**Always:** Follow only the written setup in the fresh clone; fix the text, not the clone, when it falls short. Overwrite `_bmad-output/backup/repo-instructions.backup.txt` with `/AGENTS.md` after every change. Run `_bmad-output/scripts/check_pr_branch.py` on each PR branch. Keep local paths and the book's title out of PRs.

**Never:** Stage or commit (fork-files or code). Push, open a PR, or install anything on the owner's machine without the owner's direct go-ahead for that action. Change registry or system settings, enable long paths. Fix product bugs (backlog owns them). Touch the owner's install or its port.

**Decisions (owner, 2026-11-10):**
- The fresh clone checks out fork `main`. The owner had the epic land there first: `main` was fast-forwarded to `cee8fa56c36c0962f7b22f46b788bfd4d52fcdbd` and pushed to `origin` on 2026-11-10.
- The agent installs Playwright's Chromium and Firefox from the fresh clone (`npx playwright install chromium firefox`), with the owner's OK given.
- Verify with the uv route only. pixi 0.83.0 is documented as the canonical alternative and marked unverified on Windows.
- Upstream: offer three PRs now (the lease fix `415aaa8a`; the LF rules `3b4484ce` + `0a07679d`; the Windows temp root `0ce71320` + `a5918f5b` + `f6aefbe2`). Each push and PR still needs its own go-ahead. Hold the leak fix `f94fd9f6` and the stories 7–12 test repairs until upstream #120 lands. Drop the lane split and the FFmpeg CI changes, which #120 supersedes.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Setup complete | Fresh clone, written steps only | All named checks pass or fail only as listed | No error expected |
| Step missing from text | A check fails for a setup reason | Text amended, step rerun from the amended text | Note the gap in Implementation Notes |
| New unexplained failure | Test fails, not in the known list | Investigate: test/setup fault fixed here; product fault raised to owner | Never xfail or skip to hide it |
| Port 8098 busy | Manager or another server on 8098 | Setup says to stop it before `test:e2e` | E2E not run against a foreign server |
| Flaky E2E | One browser test fails once | Rerun that spec; recorded as flaky if it passes | Repeat failure is a real failure |

</frozen-after-approval>

## Code Map

- `AGENTS.md` -- whole file is a `bmad:context` managed block (lines 1–126). Stale: line 2 header; "Running and verifying" lacks Windows setup (pixi never installed here; system Node 24.22.2 works; winget has no Node 24; `fnm exec` can't start `npm`); line ~59 lanes (web-07 split into web-07, web-19..23; lanes must be in `web-preview.yml` and `windows-serial-lanes.yml`, enforced by `tests/test_test_lanes.py`; autouse execution-actor leak guard); line ~60 temp root (`PYTEST_DEBUG_TEMPROOT` honoured, foreign `\tmp\pt` falls back); line ~101 contract regen writes CRLF on Windows (`git diff --ignore-cr-at-eol`); line ~123 LF pitfall should add `tests/fixtures/release-0.13.0-rich/** -text`; CI resolves SQLAlchemy 2.1.8 / EbookLib 0.21 from ranges.
- `_bmad-output/backup/repo-instructions.backup.txt` -- identical copy today.
- `web/playwright.config.ts:21-30` -- E2E server on 127.0.0.1:8098, `python -m scripts.run_web_e2e_server`, reuses an existing server outside CI; Chromium and Firefox projects. `pandrator_manager/launcher.py:590` defaults to 8098.
- `_bmad-output/backlog/` -- five bugs; four have strict xfails since `cee8fa56`; `bug-security-log-drops-events-on-windows` owns 2 Windows skips; `bug-long-data-paths-break-generation-on-windows`.
- `tests/platform_support.py` -- Windows skips: 29 symlink (WinError 1314), 3 FIFO, 1 POSIX-mode.
- `_bmad-output/fork-maintenance.md` §3 -- upstream PR procedure; decisions: offer lease fix `415aaa8a`; LF rules `3b4484ce`+`0a07679d`; temp root `0ce71320`+`a5918f5b`+`f6aefbe2`. Hold leak fix `f94fd9f6` and stories 7–12 test repairs until upstream #120 lands. Drop lane split `93c0e68e` and FFmpeg CI `e2ca83b8`/`388a1bb7` (superseded by #120). Never offer `ebe7194d`, `84645183`, `4c12bc41`. Upstream #120 is still open.
- Remotes: `origin` ValtorX, `upstream` lukaszliniewicz. `origin/main` = `origin/epic-fork-releases` = `cee8fa56`.

## Tasks & Acceptance

**Execution:**
- [ ] `AGENTS.md` -- add a "Windows setup" section (tools, versions, install sources, LF check, `npm ci`, Playwright browsers, port 8098, order of checks) and correct the stale lines above -- the ticket's written setup.
- [ ] `<fresh clone folder>` -- clone `origin` (default branch `main`), follow only the setup text; run lint, types, `test_lanes.py check`, every lane, web quality, build + `check:assets` + clean `git status`, `test:unit`, `test:e2e` -- proves R9. Lanes run in background, a few at a time.
- [ ] `_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-fresh-clone.md` -- per check: command, result; each remaining failure/skip/xfail with reason and owning bug; upstream PR list (PR link or why not offered) -- the record Done-when asks for. AGENTS.md points to it in one line.
- [ ] `_bmad-output/backup/repo-instructions.backup.txt` -- overwrite with final `/AGENTS.md`.
- [ ] upstream PR branches -- per approved fix: branch from `upstream/main`, cherry-pick, focused checks, `check_pr_branch.py`, then on the owner's go-ahead push to `origin` and open the PR against upstream.

**Acceptance Criteria:**
- Given a new folder and only the written setup, when every listed check is run, then each passes or fails only with a reason listed in `findings-fresh-clone.md`.
- Given the findings file, when read, then each upstream-useful fix shows its upstream PR link or why it wasn't offered.
- Given `/AGENTS.md` and the backup, when compared, then they are identical.

## Design Notes

The Windows setup goes after the closing `<!-- /bmad:context -->` marker so a `bmad-project-context` refresh keeps it; in-block corrections are edited in place (a refresh re-derives them). The failure list lives in the findings file, not AGENTS.md, so agent instructions stay short. The fresh clone goes in a short sibling path (e.g. `C:\Users\Valtor\Source\Pandrator-fresh`), uses the `<uv env>` (pinned SQLAlchemy 2.0.52 for types), and is deleted only on the owner's word.

## Verification

**Commands:**
- `uvx ruff@0.17.3 check .` -- expected: pass.
- `<uv env> --with sqlalchemy==2.0.52 python scripts/check_types.py` -- expected: only `nemo_normalizer.py:98`.
- `<uv env> python scripts/test_lanes.py check`, then `run <lane>` for every lane from `list` -- expected: only listed failures (4 strict xfails pass as xfail).
- `npm --prefix web run quality`, `build`, `check:assets`, `test:unit`; `git status --short pandrator/web/static` -- expected: pass, empty.
- `<uv env> npm --prefix web run test:e2e` -- expected: pass or only recorded flakes.
- `cmp AGENTS.md _bmad-output/backup/repo-instructions.backup.txt` -- expected: identical.
- `uv run --no-project python _bmad-output/scripts/check_pr_branch.py` on each PR branch -- expected: no fork-only changes.


## Implementation Notes

- The fresh clone is `C:\Users\Valtor\Source\Pandrator-fresh`, on `main` at `cee8fa56`. It is kept until the owner says to delete it.
- **Gaps found in the written setup, all fixed in the text:**
  - `uv run` cannot start `npm`, so the setup uses `npm.cmd`.
  - Specs run through the npm script.
  - Playwright's Firefox does not start on this machine ("side-by-side configuration is incorrect").
- **Two test faults, fixed and left uncommitted:**
  - `tests/test_installer_metadata_coordination.py`: under a uv environment the pid comes from the launcher's child.
  - `tests/test_manager_control_plane.py`: the test read the pid file before it was written.
- **Raised to the owner, not fixed:**
  - `project-ux.spec.ts:174` fails every time on Chromium, probably a product bug.
  - Under the uv route, tests that terminate a `sys.executable` child leave the real interpreter running (`test_installer_uninstall.py`).
  - Playwright's Firefox runtime is missing on this machine.
- **Upstream PR branches were not built.** Cherry-picking makes commits, which need the owner's go-ahead. Upstream #120 merged during the story, and upstream `main` is now `83d4835d`.
- The orchestrator wrote `findings-fresh-clone.md`, because the subagent's Write was refused.
- 17 leaked `time.sleep(60)` processes were stopped. They came from this story's lane runs.

## Review Triage Log

**Quick lens, pass 1:** 9 patch, 1 low-accepted, 1 false, 2 owner decision.

- **Patch (medium):** lanes bullet claimed every lane must be in `windows-serial-lanes.yml`.
  - *Evidence:* `test_manual_windows_workflow_names_only_manifest_lanes` checks only for unknown names, and the workflow lists 9 lanes.
  - *Fix:* text corrected.
- **Patch (medium):** lane names given as `web-07` and so on.
  - *Evidence:* the manifest names them `web-NN-serial`.
  - *Fix:* corrected.
- **Patch (medium):** step 5 had no runnable command, because plain `list` prints file paths.
  - *Fix:* gave a `list --json` | `xargs -P 3` command, verified on two lanes.
- **Patch (low):** the uv Node recipe was offered without E2E support.
  - *Fix:* step 1 now names system Node 24 and marks the recipe partial and unverified.
- **Patch (medium):** step 7 told agents to stop whatever is on port 8098.
  - *Fix:* now says check the port, ask the owner, and never stop their app or Manager.
- **Patch (low):** steps 8–9 carried findings detail (a per-file count).
  - *Fix:* removed the count and pointed to the findings file. The skip categories and the cleanup command stay as setup guidance.
- **Patch (low):** the findings said "all platform reasons" though two skips are about missing data.
  - *Fix:* reworded. Counts per reason are not recorded, and that is noted.
  - *Accepted as low:* recounting all 117 skips needs another full run.
- **Patch (low):** the findings did not say where the fixed lanes were rerun.
  - *Fix:* recorded the fresh-clone rerun, and the post-review rerun in the owner's checkout (`installer-serial` 487/0, `mcp-serial` 1518/0).
- **Patch (low):** a possible Playwright version mismatch for Firefox.
  - *Evidence:* the local `@playwright/test` is 1.64.1 and installed `firefox-1555`, the same build CI downloads.
  - *Fix:* noted in the findings.
- **Patch (low):** a 110-character line in `test_installer_metadata_coordination.py`.
  - *Fix:* wrapped. Ruff passes.
- **False:** "`escaped_child_spec` leaks a grandchild under uv".
  - *Evidence:* running `test_manager_control_plane.py` with two other files left 0 `sleep(60)` processes. Per-file runs showed only `test_installer_uninstall.py` leaking.
- **Owner decision (medium):** no PR branches were built and `check_pr_branch.py` never ran.
  - *Why:* building them means cherry-pick commits, which the Never rule and AGENTS.md reserve for the owner.
  - *Status:* raised at presentation.
- **Owner decision (medium):** the held items were not re-assessed after #120 merged.
  - *Status:* raised at presentation with the PR branches.

## Code Review

### 2026-11-10

**Code review complete.** 1 `decision-needed`, 13 `patch`, 0 `defer`, 9 rejected. Scope: code `cee8fa56..cda93a86` plus fork-files `2311dfc6..12a6e602`, plan excluded. Lenses: blind, edge case, verification gap, intent; none failed.

- [x] [Review][Decision] Three upstream rows end "owner to decide" — The leaked-actor fix `f94fd9f6`, the stories 7–12 test repairs and this story's test fixes have neither a PR link nor a reason, so acceptance criterion 2 is unmet while they stay open. Options: (1) re-check each against upstream `main` `83d4835d` and offer what still applies, each push on the owner's go-ahead; (2) record "not offered" with a reason now; (3) leave pending, story stays in review. Recommended: (2) for the stories 7–12 repairs and this story's fixes, (1) for `f94fd9f6`. The leak fix is a test-isolation bug that upstream may still have; the rest fixes fork-stale tests or applies only under a venv launcher.
- [x] [Review][Patch] Step 5 lane command has a line break inside a Python string and cannot run [AGENTS.md:136]
- [x] [Review][Patch] Step 9 and findings item 4 state a launcher-leak mechanism a review probe contradicts, and a broader rule than the tests follow [AGENTS.md:141]
- [x] [Review][Patch] Upstream row points this story's fixes at "commits after `c30d2faf`", which drops two of the four [findings-fresh-clone.md:398]
- [x] [Review][Patch] Amended setup steps were not rerun as written [AGENTS.md:136]
- [x] [Review][Patch] Findings say fixes now in `c30d2faf` are uncommitted [findings-fresh-clone.md:343]
- [x] [Review][Patch] Skip paragraph says "first four" over three platform items and leaves out the POSIX signal tests [findings-fresh-clone.md:326]
- [x] [Review][Patch] `installer-serial` counts 306 and 487 are unexplained [findings-fresh-clone.md:338]
- [x] [Review][Patch] Metadata test accepts any descendant pid, on every platform [tests/test_installer_metadata_coordination.py:116]
- [x] [Review][Patch] pid-file race fixed at three readers rather than at the writer [tests/test_manager_control_plane.py:113]
- [x] [Review][Patch] `upstream-pr-drafts.md` says the PRs are open and also that opening them is the owner's step; "PR 3" [upstream-pr-drafts.md:3]
- [x] [Review][Patch] Bug ticket names the spec by line number, which moves after the merge; AC 2 leaves out CI [bug-manual-speaker-choice-lost-before-save.md:20]
- [x] [Review][Patch] Managed-block line "stop stale servers first" contradicts step 7 [AGENTS.md:57]
- [x] [Review][Patch] Unverified: other 5 s gates in the lifetime test could expire on a loaded runner; record as a watch item [findings-fresh-clone.md:376]

**Rejected**
- false: "the uv `xargs` failure hides results": it is covered by the step 5 patch. The traceback is visible.
- false: "the 5 s to 30 s change lets the worker outlive the 10 s teardown join". The worker ends at t0+T and the join at T+10 for both values, so the condition is unchanged.
- false: "metadata `finally` leaves the publisher running". Closing stdin makes `readline()` mismatch and the publisher raise. The probe also shows the launcher's end stops its child.
- false: "the STARTUP_TIMEOUT comment states an inferred cause as fact". The CI traceback shows the 5 s wait expired, which is all the comment says.
- false: "the WinError 32 teardown and unclosed applications are unaddressed". The findings already record them as consequences of the first failure. They predate this change.
- false: "absolute worktree path in `upstream-pr-drafts.md`". It is not in a PR body, and the path policy covers PRs.
- false: "no CI marker for the known E2E failure". The bug ticket records the decision not to add one.
- rejected: "the setup is absent from a fresh clone". AGENTS.md is gitignored by owner policy, so a fix would edit the intent.
- rejected: "only uv verified", "no pitfall bullet deleted", "Firefox not run and skips not counted". Each is an owner decision, a descriptive note with nothing named, or a recorded and raised environment fault.

**Patches applied (uncommitted).** Owner's checkout, Windows, `<uv env>` (pyproject ranges):
- `uvx ruff@0.17.3 check` on the two edited tests: pass.
- The four touched test files: 75 passed, 1 skipped.
- Corrected step 5 pipeline, run as written for `installer-serial` and `manager-serial`, 0 failures:
  - `installer-serial`: 306 passed, 39 skipped, 142 subtests. That makes the JUnit total of 487 exactly.
  - `manager-serial`: 667 passed, 17 skipped.
- Its full lane list was dry-run: 27 lanes besides `fast-xdist`.
- No `time.sleep(60)` processes were left behind.
- `AGENTS.md` and the backup are identical.
- The fresh clone was not rerun.

**Decision resolved (owner, 2026-11-10): the recommended split.**
- **Re-check of `f94fd9f6` against upstream `83d4835d`:** upstream fixed the leak itself in `dd38e331` (#120). Request teardown now restores the execution actor, and the test installs those hooks with `self.guards.register()`.
- **Outcome:** all three rows now read "not offered", each with its reason, in `findings-fresh-clone.md`.
- **Why the other two rows were not offered:**
  - Every stories 7–12 commit conflicts with upstream `main`.
  - This story's test fixes are specific to the virtualenv launcher, or answer an inferred CI cause.

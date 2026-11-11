# Findings: a fresh Windows clone, following the written setup

Story 14 of `epic-fork-releases`. Run on 2026-11-10 from a fresh clone of fork `main` at
`cee8fa56c36c0962f7b22f46b788bfd4d52fcdbd`, in a new short-path folder, following only the
"Windows setup" section of `/AGENTS.md`.

The environment was the uv route: Python 3.11 resolved from the `pyproject.toml` ranges, with
SQLAlchemy pinned to 2.0.52 for types only. Node was 24.22.2 (system), FFmpeg 9.0.1 was on PATH,
pixi was not installed, and port 8098 was free.

## Gaps the written setup had, now fixed in the text

- `uv run npm ...` cannot start `npm`, because on Windows it is a `.cmd` shim. The setup now uses
  `npm.cmd` under `<uv env>`.
- Run single specs through the npm script. A bare `npx playwright` fetched a newer Playwright
  (1.65.0) into the npm cache.
- Playwright's Firefox may not start on Windows (see E2E). The setup now says how to recognise this
  and to record Firefox as not run.
- Stale lines in the managed block were corrected:
  - the lane names after the `web-07` split;
  - the CI lane-listing rule and the execution-actor leak guard;
  - the temp-root override and fallback;
  - CRLF output from the contract generators;
  - the `tests/fixtures/release-0.13.0-rich/** -text` rule;
  - CI resolving SQLAlchemy 2.1.8 and EbookLib 0.21.

## Checks

| Check | Result |
| --- | --- |
| `git ls-files --eol web pandrator/web/static pandrator_manager/recovery_ui/static/model-groups.js \| grep -c w/crlf` | 0 |
| `npm ci --prefix web` | pass |
| `uvx ruff@0.17.3 check .` | pass |
| `<uv env> --with sqlalchemy==2.0.52 python scripts/check_types.py` | only `nemo_normalizer.py:98` (nemo is excluded on win32) |
| `test_lanes.py check`, `check_docs.py`, `vulture` | pass |
| All 28 lanes from `test_lanes.py list` | about 15,500 passed, 117 skipped, 4 strict xfails; one failure in `installer-serial` and one manager flake, both fixed below |
| `npm --prefix web run quality` | pass |
| `npm --prefix web run build`, then `check:assets` | pass (`0.14.2-3ea216416b35`) |
| `git status --short pandrator/web/static` | empty |
| `npm --prefix web run test:unit` | 175 of 175 |
| `npm.cmd --prefix web run test:e2e` | Chromium: 455 passed, 11 skipped, 1 failed. Firefox: not run (see E2E). |

## Expected failures and skips

The four strict xfails each name an open backlog bug in `_bmad-output/backlog/`:

| Test | Bug |
| --- | --- |
| `test_generation_workspace_execution.py::test_generation_ignores_saved_preview_routing_metadata` | `bug-saved-preview-settings-fail-generation` |
| `test_web_database_efficiency.py::test_assembly_select_count_does_not_scale_with_segments` | `bug-assembly-checks-access-for-every-segment` |
| `test_web_generation_regeneration.py::test_grouped_regeneration_checkpoint_uses_output_run_local_takes` | `bug-alternate-take-model-refused-on-audio-cpp` |
| `test_web_generation_regeneration.py::test_resume_checkpoint_filtering_uses_streaming_batch_path` | `bug-alternate-take-model-refused-on-audio-cpp` |

The 117 skips were not counted by reason. Their reasons fall into these groups. The first four are platform reasons; the last two are data the environment lacks:

- symlink tests without the symlink privilege (`WinError 1314`);
- FIFO and POSIX-mode tests (`tests/platform_support.py`);
- the two security-log file-sink tests, owned by `bug-security-log-drops-events-on-windows`;
- "no independent DFN2 verification present";
- "installed audio.cpp source specs are unavailable".

`test_web_api_shutdown` and `test_xtts_training_process` are Linux-only and are settled on CI.

## Failures found and their status

The two test fixes were applied to the fresh clone and to the owner's checkout. `installer-serial` was rerun in the fresh clone (306 passed), and `test_manager_control_plane.py` three times. After review, the line-wrapped fix was rerun in the owner's checkout with the documented `xargs` lane command: `installer-serial` 487 tests, 0 failures, and `mcp-serial` 1518 tests, 0 failures, both under `<uv env>`.

1. **Fixed, test fault.**
   - **Test:** `tests/test_installer_metadata_coordination.py` (lane `installer-serial`).
   - **Fault:** it compared the reported pid with `Popen.pid`. Under a uv environment `sys.executable` is a launcher, so the reporting interpreter is its child: `Popen` pid 25788 against reporting pid 98712.
   - **Fix:** the test now accepts the child or a descendant, then holds that pid. The edit is uncommitted on the code branch.
2. **Fixed, test fault.**
   - **Test:** `tests/test_manager_control_plane.py`, at three sites.
   - **Fault:** the test read `child.pid` between the file's creation and its write, and got an empty string.
   - **Fix:** it now waits for a non-empty file. The edit is uncommitted on the code branch.
3. **Open, owner to decide.** `web/tests/project-ux.spec.ts:174` fails on Chromium every time: 4 of 4 runs alone, plus the full run.
   - **What fails:** after the UI reports "Block directions and speaker assignments saved", the saved `speech_xml` has the `<ins>` direction but no `<speaker ref="...">` for the chosen character.
   - **History:** it also failed on CI run 42908347707.
   - **Reading:** probably a product bug. No backlog bug exists for it yet.
4. **Open, owner to decide: test processes leak under the uv route.**
   - **Mechanism:** under `<uv env>` on Windows, `sys.executable` is a launcher. Tests that `Popen([sys.executable, ...])` and later `terminate()` it stop only the launcher, and the real interpreter keeps running.
   - **Evidence:** reproduced directly — after `terminate()`, the child of the launcher was still alive.
   - **Measured:** running each file that spawns such a child showed `tests/test_installer_uninstall.py` as the only file that left processes behind. The run of `test_manager_control_plane.py` with two other files left none.
   - **Worst case:** `tests/test_installer_uninstall.py` leaves 5 `python -c "import time; time.sleep(60)"` processes per run. These did not exit after 60 s; 18 had built up over this story's lane runs, and they were stopped by hand.
   - **Not affected:** CI, which uses a real interpreter, and presumably pixi (not verified).
   - **Clean up after a lane run:** `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? { $_.CommandLine -match 'time\.sleep\(60\)' } | % { Stop-Process -Id $_.ProcessId -Force }`

## E2E

- Playwright's `firefox.exe` does not start on the owner's machine: "side-by-side configuration is incorrect". The error stayed after `playwright install --force firefox`.
- Every Firefox test failed in about 1 ms with `browserType.launch: spawn UNKNOWN`, so Firefox E2E was not run.
- The browsers came from the project's own Playwright 1.64.1 (`web/node_modules`). The build, `firefox-1555`, is the one CI downloads, so a version mismatch is ruled out.
- The probable fix is a missing Windows runtime component. Installing it is the owner's call.
- `readiness-summary.spec.ts` passed 8 of 8 repeats on Chromium.

## CI after the push of `main` at `cee8fa56`

- **Pandrator Manager, run 42930053649, Windows.**
  - **What failed:** `test_manager_operation_lifetime.py::test_startup_restoration_waits_for_recovered_component_update` (AssertionError), plus a teardown `WinError 32` on `manager.sqlite3`.
  - **History:** neither the test nor `pandrator_manager/` changed since the last green run (42721766846).
  - **Locally:** 6 of 6 passes, alone and in its lane.
  - **Reading:** the test does not close three applications and relies on 5 s thread gates, so a loaded runner can hold the database open at teardown. That is a reading, not a proven cause.
  - **Status:** open, timing-dependent.
- **Web migration preview, run 42930053446, `ubuntu-web-01`.** 239 passed and 0 failed. The job failed only at artifact upload (`ENOTFOUND`), which is GitHub infrastructure.
- **Same run, E2E Windows Firefox shard 3/4.**
  - **`readiness-summary.spec.ts:70`:** a deep-equality failure. It also failed on Ubuntu Firefox in run 42922628333. Two consecutive runs on two operating systems makes it likely a real Firefox-only failure, but it could not be checked here because Firefox does not start.
  - **`sound-model-selection.spec.ts:420`:** a 120 s download timeout, seen once.
- **Earlier one-off E2E failures:**
  - `output.spec.ts:238` passed on Chromium here.
  - `epub-content-settings.spec.ts:49` is Firefox-only and was not checked.

## Upstream PRs

Upstream #120 merged while this story ran, and upstream `main` is now `83d4835d`. Upstream still
has none of the three fixes below. The conflict results come from `git merge-tree` against that
commit.

| Fix | Commits | Against upstream `main` | Status |
| --- | --- | --- | --- |
| Lease heartbeat during the opening identity check | `415aaa8a` | conflicts only in `docs/release-notes-next.md` (left out of the PR) | awaiting go-ahead |
| LF rules for browser build inputs and outputs | `3b4484ce`, `0a07679d` | first clean; second conflicts in `tests/test_frontend_architecture.py` | awaiting go-ahead |
| Short Windows test temp root | `0ce71320`, `a5918f5b`, `f6aefbe2` | clean in order | awaiting go-ahead |
| Leaked execution actor | `f94fd9f6` | conflicts in `tests/test_web_security.py` | held for #120, which has now landed: owner to decide |
| Test repairs of stories 7–12 | many | not checked | held for #120, which has now landed: owner to decide |
| The two test fixes of this story | uncommitted | not checked | owner to decide |
| Lane split; FFmpeg CI | `93c0e68e`; `e2ca83b8`, `388a1bb7` | — | dropped: #120 reworks the same workflows |
| Fork version and identity | `ebe7194d`, `84645183`, `4c12bc41` | — | never offered: fork-only |

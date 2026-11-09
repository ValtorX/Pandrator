---
title: 'Windows-only test failures pass or skip with a reason'
type: chore
ticket: '7'
created: '2026-11-09'
status: built
baseline_revision: 'a5918f5b6673ce6c514158478875c025c72f82df'
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

**Problem:** On a Windows checkout 31 tests fail with the locked SQLAlchemy and 64 more under 2.1.8 (CI's range install), for platform reasons rather than product faults: symlinks need a privilege, POSIX modes and FIFOs don't exist, `"` is not a legal file name character, fixtures hold files open, write CRLF or lack attributes, a venv launcher hides the child pid, `str(URL.create(...))` breaks configparser, and a hashed fixture is checked out as CRLF. Four sleep tests pass only because Git Bash puts `sleep` on PATH, and one dubbing test lacks its `host_model_acquisition` mark on every platform.

**Approach:** Fix the fixture wherever the behaviour exists on Windows; skip, with a reason naming the cause, only where it can't: symlinks when Windows refuses the privilege (WinError 1314), POSIX modes, FIFOs, and the security log file sink until its backlog bug is fixed. Two small helpers in `tests/platform_support.py` hold the shared skip and the portable child process.

**Decisions:**

- Owner, 2026-11-09: implement without a separate approval if the spec has no open questions. Nothing goes to `deferred-work.md`.
- G3 (`SCHEMA_HEAD == TARGET` pins in `test_project_speech_reference_store` and `test_voice_provider_job_effects`, 3 tests) stays with story 10, which the ticket tree assigns it to and which waits on this story for the shared files. Here those files lose only their `sqlite:///C%3A` fault, so under 2.1.8 they fail the same G3 assertion as under 2.0.52.

## Boundaries & Constraints

**Always:** Linux and macOS run every changed test exactly as before, the same assertions and paths. A symlink skip happens only on WinError 1314; any other `OSError` still fails. Each skip's reason states its cause, and the security log skip names `bug-security-log-drops-events-on-windows`. Split a test rather than skip it when only part of it is POSIX-only.

**Never:** No product code change. No new dependency. No change to lanes, CI workflows or system settings. No private `_pytest` API. No staging or committing.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Symlink, no privilege | Windows raises WinError 1314 | Test skipped: "Creating symlinks on Windows needs Developer Mode or the symlink privilege (WinError 1314)" | None |
| Symlink, privileged | Windows CI runner or Linux | Symlink created, test runs | None |
| Symlink, other failure | Any other `OSError` | Re-raised, test fails | None |
| Child process | `direct_python()` on Windows in a venv | The base interpreter, so the pid is the script's own | None |

</frozen-after-approval>

## Code Map

- `tests/platform_support.py` -- new. `symlink_or_skip(link, target, *, target_is_directory=False)` and `direct_python()`, which returns `sys._base_executable` when `sys.platform == "win32"` and `sys.prefix != sys.base_prefix`, else `sys.executable` (`venv` and `multiprocessing` read the same attribute; it is a sys attribute, not `_pytest`).
- Symlink sites to route through the helper: `test_audio_cpp_processing.py:478`, `test_audio_cpp_generation_assets.py:75,84`, `test_audio_cpp_yue2_adapter.py:121,132`, `test_session_output_storage.py:400,451`, `test_sound_mix.py:349`, `test_project_bundle_file_witnesses.py:226`, `test_project_copy_files.py:240,454`, `test_quick_transcription_execution_access.py:337`, `test_delivery_publication.py:165,172`. Leave the existing ad-hoc skips in `test_mcp_sounds.py:1386` and `test_project_export_bundles.py:496` alone.
- `tests/test_speech_input_files.py:71` -- parametrize over `file_symlink`, `directory_alias`, `directory`, `fifo` (FIFO param `skipif(not hasattr(os, "mkfifo"))`). `:88` -- move the two `S_IMODE` asserts into their own test, `skipif(os.name == "nt")`, so the cleanup half runs on Windows. `:123` -- the hook compares `path == "source.wav"`; on Windows `os.stat` lacks `dir_fd`, so the product opens `parent / name`. Match by base name instead; the product's `fstat` identity check (`speech_input_files.py:108`) catches the swap on Windows too. Skip only if that proves false.
- Sleep: `test_audio_cpp_processing.py:410,422,438` and `test_qwen_asr_transcription.py:398,400` -- replace `["sleep", "30"]` with `[direct_python(), "-c", "import time; time.sleep(30)"]`.
- `tests/test_workflow_media_edit_render.py:503` -- `direct_python()` in place of `sys.executable` (W4).
- `tests/test_model_acquisition_providers.py:23` -- `_Response.raw = None`; a real `requests.Response` always has it (`kobold_qwen_stream.py:51`).
- `tests/test_dubbing_transcription.py:289` -- add `@pytest.mark.usefixtures("host_model_acquisition")`, as its siblings have (J1).
- `tests/test_security_event_log.py:152,193` -- `skipIf(not hasattr(os, "fchmod"))` with the backlog name; Python 3.13 adds `fchmod` on Windows, so the skip lifts itself.
- `tests/test_web_media_edit.py:164` -- `self.database.dispose()` before `create_app` (WinError 32 on the legacy rename).
- `sqlite_url(path)` from `pandrator/web/database.py:49` replaces `str(URL.create("sqlite", database=str(path)))` in `test_project_speech_reference_store.py:67`, `test_workspace_invitation_migration.py:29` (also fixes `test_voice_provider_job_effects`, which imports its `config`), `test_installed_wheel_upgrade.py:35`, `test_credential_resource_storage.py:163`, `test_private_transcription_owners.py:135`; drop unused `URL` imports.
- `.gitattributes` -- `tests/fixtures/release-0.13.0-rich/** -text`. Confirmed: its text files are `i/lf w/crlf` with `core.autocrlf=true`, and the test hashes raw bytes. Then refresh those working-tree files (clean status, delete, `git checkout --` those paths only).
- `tests/test_media_input_command_builders.py:317,434` -- the two tests that create `tmp_path / _MEDIA_A` use a name without `"` on Windows only; argv-only tests keep `_MEDIA_A`.
- `tests/test_source_cleaning_workspace_access.py:45` -- `write_text(..., newline="\n")` (W5).
- `tests/test_provider_workspace_access.py:33` -- `no_network` lets `socket.connect` reach a loopback address on Windows only, where asyncio's `socketpair` connects to `127.0.0.1` (W6); `create_connection` stays denied.
- `tests/test_credential_workspace_scope.py:403` -- build the `file-path:` reference from `Path(Path.cwd().anchor, "synthetic", "fixture")`; on Linux the string is unchanged (W7).

## Tasks & Acceptance

**Execution:**
- [x] `tests/platform_support.py`, `tests/test_platform_support.py` -- helpers and their tests (1314 skips, other errors raise, `direct_python` off a venv); add the test to `fast-xdist` in `scripts/test_lanes.py`.
- [x] Symlink, sleep and launcher sites listed above -- use the helpers.
- [x] `tests/test_speech_input_files.py` -- parametrize, split the mode asserts, portable replacement hook.
- [x] The single-fix files above (L3, J1, L2, L4, W3, W5, W6, W7, sqlite group).
- [x] `.gitattributes` and refresh the release fixture.
- [x] `findings-ci-failures.md` -- mark the story-7 groups resolved, as story 6 did for W1.

**Acceptance Criteria:**
- Given this Windows checkout run from PowerShell, so no Git Bash `sleep`, when the 27 files and `test_platform_support.py` run, then none of the 31 fails, the sleep tests pass, and every skip's reason states its cause.
- Given SQLAlchemy 2.1.8, when the six sqlite files run, then no test fails on `invalid interpolation syntax`; only the 3 G3 tests fail.
- Given the lanes holding these files, when each runs through `scripts/test_lanes.py run`, then none of these tests fails.

## Implementation Notes

- Baseline on this checkout, before any change: 31 failures with SQLAlchemy 2.0.52 across the 27 files, and 64 with 2.1.8 in the six sqlite files (128 `invalid interpolation syntax` lines). The four `sleep` tests passed only because the run started from Git Bash.
- Every Code Map item landed as written. The `source.wav` hook needed no fallback skip: matching by base name lets the product's `fstat` identity check catch the swap on Windows.
- `.gitattributes` gained `tests/fixtures/release-0.13.0-rich/** -text`; the fixture's working files were deleted and checked out again, so `git ls-files --eol` shows no `w/crlf` there. Other Windows checkouts made before this rule need the same refresh.
- After the build, from PowerShell with `PYTEST_ADDOPTS` unset: 3 failed (the G3 pins), 444 passed, 20 skipped with SQLAlchemy 2.0.52; 3 failed (the same G3 pins, no interpolation error), 72 passed with 2.1.8. Skips: 16 symlink (WinError 1314 on this unprivileged machine), 1 FIFO, 1 POSIX modes, 2 security log naming the backlog bug.
- The implementer ran `fast-xdist` and `web-07`, `09`, `12`, `17` to `23` through `run <lane>`: no failure in a changed file; the rest are the clusters other stories own.
- Matrix audit: the "privileged" row's real-symlink test skips here, so `test_symlink_or_skip_forwards_to_symlink_creation` covers it without the privilege.
- Review patches, pass 1:
  - In `test_delivery_publication` and `test_audio_cpp_processing`, the identical-path and no-side-effect checks now run before the first symlink.
  - `no_network` permits a connect only on win32, and only while `socket.socketpair` runs.
  - The security log file test skips on `nt`, naming the bug and the `0o600` check.
  - The release fixture's bullet in the findings is annotated.
- Verification after the patches, from PowerShell with `PYTEST_ADDOPTS` unset:
  - The 28 files: 3 failed (G3), 445 passed and 20 skipped with SQLAlchemy 2.0.52; 3 failed (G3) and 72 passed with 2.1.8.
  - The 69 files importing `no_network`: 863 passed, 6 failed. All six are known clusters (B8, K1, I1, M1), and none comes from the guard.
  - `fast-xdist`: 2 failed, both story 10's (`test_epub_narration`, G5).
  - `test_lanes.py check` and `ruff check tests scripts` pass.
  - Not run here: Linux, and any symlink test with the symlink privilege.
- Uncommitted: the tests, `tests/platform_support.py`, `scripts/test_lanes.py`, `.gitattributes` on `epic-fork-releases`; this plan and the findings edit on `fork-files`.

## Plan Change Log

## Review Triage Log

Pass 1, quick lens: 0 high, 2 medium, 2 low, 2 false, 0 maybe-false. Nothing deferred.

| Finding | Verdict | Route | Evidence and action |
|---|---|---|---|
| `test_delivery_publication` skips its identical-path check with the symlink checks; `test_audio_cpp_processing` loses its no-download, no-run and source-intact checks the same way | medium | patch | Confirmed at `test_delivery_publication.py:166-181` and `test_audio_cpp_processing.py:479-484`: the "must differ" half needs no symlink. Run those parts before the first symlink. |
| `no_network` lets any Windows connect to `127.0.0.1` through, including urllib3's, in every file importing the fixture | medium | patch | Confirmed: urllib3 calls `sock.connect`, which `guarded_connect` passes for `127.0.0.1`, so a test could reach a real local server. Permit loopback only while `socket.socketpair` runs (CPython 3.11's Windows fallback connects through the patched method). |
| `test_file_target_is_private_and_symlinks_are_rejected` would fail for other reasons once `os.fchmod` exists | low | patch | Confirmed by reading: it asserts mode `0o600` and makes a bare symlink. Dormant under `requires-python <3.13`. Skip it on `nt`, naming the bug and the POSIX mode check, and use `symlink_or_skip`. |
| The release fixture's bullet in `findings-ci-failures.md` isn't marked resolved | low | patch | Confirmed at the "Failures the first count missed" bullet. Annotate it. |
| AC 1 says "none of the 31", but 3 G3 tests remain | false | reject | The frozen Decisions give G3 to story 10, and the 3 fail on the same G3 assertion before and after. The fix would edit this plan. |
| A skip raised inside `mutate` in `test_project_bundle_file_witnesses` bypasses the product's rollback | false | reject | The skip still reaches pytest as a skip, and the leftovers sit under `tmp_path`, which the temp root prunes. No test or user outcome changes; a capability probe would add machinery to a fixture whose data root the product scans. |

## Design Notes

```python
def symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as error:
        if getattr(error, "winerror", None) != 1314:
            raise
        pytest.skip(SYMLINK_PRIVILEGE_REASON)
```

## Verification

`<uv env>` from `AGENTS.md`; pixi isn't installed. Run from PowerShell with `PYTEST_ADDOPTS` unset.

**Commands:**
- `<uv env> --with sqlalchemy==2.0.52 python -m pytest -q -rs <the 27 files> tests/test_platform_support.py` -- expected: no failure except G3's 3; skips list reasons.
- `<uv env> --with sqlalchemy==2.1.8 python -m pytest -q <the six sqlite files>` -- expected: only G3's 3 fail.
- `<uv env> --with sqlalchemy==2.0.52 python scripts/test_lanes.py run <lane>` for `fast-xdist` and each serial lane holding a changed file -- expected: none of these tests fails.
- `<uv env> python scripts/test_lanes.py check` and `uvx ruff@0.17.3 check tests scripts` -- expected: pass.
- `git ls-files --eol tests/fixtures/release-0.13.0-rich` -- expected: no `w/crlf`.

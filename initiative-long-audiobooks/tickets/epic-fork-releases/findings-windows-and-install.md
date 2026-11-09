# Findings: Windows dev checks and the install path

Gathered 2026-11-08 from a Windows 11 worktree at `112501b2` (upstream `main`), with a read of the Manager code. Evidence for `epic-fork-releases`; nothing here was fixed.

## Check results on Windows

Environment: uv 0.12.7, ffmpeg on PATH, Node 22.23.2 (`web/package.json` wants `>=24 <25`), `core.autocrlf=true`, `core.longpaths` unset, `LongPathsEnabled=0`. `pixi` and `gh` on neither shell's PATH.

| Check | Result |
|---|---|
| `uvx ruff@0.17.3 check .` | Pass |
| Type check with `sqlalchemy==2.0.52` | Only the known `nemo_normalizer.py:98` |
| Test lane check, docs check, vulture | Pass |
| `fast-xdist` with `PYTEST_ADDOPTS=--basetemp=<short path>` | 3616 passed, 14 failed, 3 skipped (the skips aren't from ffmpeg) |
| `npm ci`, `npm run test:unit` | Pass, 175 of 175 |
| `npm run quality` from a CRLF checkout | Fails: 424 files at the prettier step, and "Manager model grouping is stale" |
| `npm run build` from a CRLF checkout | Leaves 311 changed paths in `pandrator/web/static`; hash `fbc73230e2de` against `3ea216416b35`; `check:assets` still reports a match, so it misses this |
| `quality`, `build`, `check:assets` from a `git clone -c core.autocrlf=false` | All pass, tree clean, under Node 22 |
| `test:e2e` | Not run: no Playwright browsers installed |

The index holds LF throughout; 2,105 tracked files are CRLF only in the working tree.

## Windows-specific problems

1. **Line endings.** See the table. `keep-browser-build-lf` has an unpushed commit, `3b4484ce`, adding LF rules for `web/**`, `pandrator/web/static/**` and `model-groups.js` with a guard test. It has not been verified against these results.
2. **Long paths.** Default pytest temp paths exceed the limit (about 27 failures). `test_lanes.py run` has no basetemp option; `PYTEST_ADDOPTS` works.
3. **12 tests in the fast lane:**
   - Symlinks need privileges (`WinError 1314`): `test_audio_cpp_processing::test_same_source_and_destination_are_rejected`, `test_speech_input_files::test_rejects_symlinks_directories_and_fifo_without_blocking` (also needs `os.mkfifo`), `test_audio_cpp_generation_assets::test_package_and_nested_paths_cannot_escape_through_symlinks`, `test_audio_cpp_yue2_adapter::test_cache_path_rejects_symlink_package_and_destination`.
   - POSIX file behaviour: two more in `test_speech_input_files`, one expecting mode `0o700` and `test_path_replacement_during_open_is_detected`.
   - A `sleep` binary is assumed: `test_qwen_asr_transcription::test_cancel_and_timeout` and 3 of the 4 `test_audio_cpp_processing` tests that call it.
   - Windows-only code paths: `test_model_acquisition_providers::test_read_ahead_copies_actor_and_authorizer_and_propagates_retry_denial` (the fake lacks `response.raw`, read at `kobold_qwen_stream.py:51`) and a `test_dubbing_transcription` prefetch test raising `ModelAcquisitionDenied` (cause inferred).

## Failures not specific to Windows

10 tests, read as out of date but not proven to be. A real product bug could be among them, especially the first group.

- `test_generation_ignores_saved_preview_routing_metadata` ("Retained speech binding disagrees with selected settings"), `test_preview_service_override_keeps_cast_and_reports_cached_readiness` ("expected call not found"), `test_distinct_cast_bindings_share_one_catalogue_build` (`assert 0 == 1`).
- 5 in `test_web_backend_architecture.py`, on literal counts and tuples (`1800 != 350`, `355 != 413`, `46 != 48`, a missing `secret_redactor`).
- `test_tts_generation_limits` (`450 == 540`), `test_epub_narration::test_page_list_cannot_create_duplicate_consumed_note_chapter`.

## Upstream CI

No workflow sets autocrlf, basetemp or longpaths. Windows jobs run Python tests only; web quality, the build and the clean-tree gate run on Ubuntu. `python-quality.yml` and `web-contract.yml` trigger on `main` only, and `web-preview.yml` on `main` and `codex/webui*`.

## How the install works

- **Clone.** The Manager clones `repo_url` with dulwich and no ref, so it gets the remote's default branch (`pandrator_manager/operations/source_tasks.py:92`). `repo_url` is hardcoded (`pandrator_manager/components/builtin.py:813`) with no override. Nothing checks a commit's origin.
- **Slots.** Activation moves staging to `app/versions/<sha>` and writes `app/current.json` (`activation_tasks.py:45-152`). Old slots stay beside the new one.
- **Runtime.** A slot holds its own environment at `.pixi/envs/default` (`runtime_specs.py:66`), and the install has `bin/pixi.exe`, so a hand-activated slot has to prepare its environment.
- **Update trap.** A Manager Update or Repair of the app re-clones the hardcoded upstream URL and re-points `current.json`.
- **Self-update.** Manager updates need signatures from keys embedded in `releases/trust.py:25`, and runtime APIs refuse replacements. They swap the Manager bundle and, by inference, leave app slots alone.
- **Fork Manager.** It needs its own key (`scripts/generate_manager_release_key.py`), a `trust.py` entry, a changed manifest URL, bundles from `build_manager_release_bundle.py` and `build_manager_appimage.py`, and manual signing with `sign_manager_release.py --repository`. No workflow publishes one.

## Versions

`pyproject.toml:7` is the app version. A local version such as `0.14.2+fork.1` is valid PEP 440 and parses with `packaging.Version`. `pandrator/version.py` reads only `pyproject.toml`, and `tests/test_version.py` compares only those two, so the fork version breaks neither and the app reports it. `web/package.json` also carries a version, and `web/svelte.config.js:44` puts it in the build's version name, so changing that value would change `pandrator/web/static`; leave it alone. Activation strips characters outside letters, digits and `._-` from slot names (`activation_tasks.py:59-61`), so name a hand-made slot by commit, as the Manager does.

## Whether a hand-activated slot is accepted

From the code, not yet run: inspection reads only `app/current.json` and the slot's markers, with no Manager database state (`builtin.py:78-96`). `revision` is optional. The slot must sit under `versions/` with `pyproject.toml` and `pandrator/web/cli.py` present. An active release bundle would override the pointer (`builtin.py:79-86`).

# Findings: why CI fails on fork main

Gathered 2026-11-09 at `ebe7194d` (`v.0.14.2+fork.1`). Evidence for `epic-fork-releases`; nothing here was fixed. Per-cluster detail is in [findings-ci-failure-classification.md](findings-ci-failure-classification.md).

## Headline

Upstream's `main` has not had a green `Web migration preview` run since `e0b9fb24` on 2026-10-12. The fork fails the same tests as upstream at `112501b2`, so none of this came from the fork.

CI reports about 270 failed tests per OS in the lanes that finish, but that overstates the work. One test causes about 150 of them, and almost all the rest are tests left behind by the workspace and access work in `68c444a1` (2026-11-01). Two tests found real product bugs. One lane never finishes, so the true total is unknown.

## Groups

Counts are distinct tests on Windows with `pixi.lock` versions, each file run alone, unless stated.

| Group | Tests | Cause | Fix | Confidence |
|---|---|---|---|---|
| Contamination | About 150 in `web-17` | `test_web_security.py::PrincipalContractTests::test_session_principal_kind_scope_and_request_cache_contract` leaves a `UserActor` in the context, so every later test in the lane runs as a stale user | About 2 test lines | Verified: without it, `web-17` goes from 162 failures to 11 |
| Missing actor | 35 | Standalone workflow tests lack the existing `host_execution_actor` fixture | About 10 test lines, 6 files | Verified: all 35 pass with an actor |
| Stale tests | 113 | Fixtures with no workspace, literal counts, moved seams and stubs that predate the access work | About 200 test lines, about 45 files | Mixed: each cluster is marked verified or read |
| Platform | 7 | POSIX assumptions, plus 3 local symlink-privilege failures seen in lanes | Skip marks and one-line fixture fixes | Mostly verified |
| Real bugs | 3 tests, 2 bugs | See below | About 5 to 15 product lines for the first; second not sized | Reproduced |
| Author's choice | 1 | `test_web_database_efficiency`: 85 SELECTs at 3 segments, 667 at 100 | Throttle `prove()` or relax the test | Measured |
| `sqlite:///C%3A` | 11 on Windows CI | Tests pass `str(URL.create(...))` to configparser under SQLAlchemy 2.1.8; the product uses `sqlite_url` | About 1 line per file | Reproduced with 2.1.8 only; several of these tests also carry a stale literal |
| Not reachable | Unknown | `web-07` hits its time limit at 24% on Ubuntu and about 29% on Windows, on both fork and upstream | Split the lane, then measure | Observed |

Ten further failures in the classification were artefacts of this triage's long temp path and two came from its actor probe. Neither appears in CI, and both are excluded above.

## Real bugs

Both raise "Retained speech binding disagrees with selected settings" from `pandrator/web/generation_speech_inputs.py:71`.

1. **Alternate take with another model is refused on audio.cpp.** With a selected voice, picking another model of the same service for an alternate take returns 409 and generates nothing. Two product comments say this should work (`generation_cast_runtime.py:61`, `project_tts_settings.py:114`). Reproduced over HTTP with the UI's payload shape, not clicked through in the running UI. Tests: two in `test_web_generation_regeneration.py`.
2. **Stale preview keys fail a generation job.** The job fails if `defaults.tts` holds preview routing keys. The code is wrong with high confidence; how often it is reached is uncertain, because the UI doesn't send those keys, although `PUT defaults.tts` accepted them in the test. Test: `test_generation_workspace_execution.py::test_generation_ignores_saved_preview_routing_metadata`. `findings-windows-and-install.md` read this one as probably out of date. It sits in `web-07`, which likely explains its absence from CI's failure lists: the logs stop before the end of the lane (inference).

## What was ruled out

- **Dependency drift, as the main cause.** CI installs from ranges, with SQLAlchemy 2.1.8 against the locked 2.0.52. Lanes `web-11`, `12`, `13`, `16` and `17` failed the same 219 tests on both version sets. Only the `sqlite:///C%3A` group depends on the version, and only that group was shown to.
- **An actor leak between real requests.** By reading, `http_lifecycle.py:520` sets the actor to `None` per request and `:619` restores it in teardown. The leak needs `principal()` called in a bare `test_request_context`, which skips those hooks. This was not tested under a running server.

## Product gaps found along the way

No test requires these today.

- **Security log on Windows.** A file sink can't call `os.fchmod`, so it reports enabled but drops every event after one warning (`security_events.py:157-159`).
- **Windows path length.** Without long paths enabled, soundtrack export broke with a 74-character data root and takes with a 102-character one, with a misleading `FileNotFoundError`.
- **Lost test coverage.** Five tests can no longer build the cross-session rows they used to prove reads ignore, because triggers now forbid those rows. Updating them needs a decision to drop the case or test it another way, so the read-side defence doesn't silently lose its check.

## Limits of this triage

- Run on Windows. Ubuntu facts come from CI logs. Project copy failing on Ubuntu too is inferred, because `web-07` doesn't get that far there.
- `test_web_api_shutdown` (4, Ubuntu only) is stale by reading only, with moderate confidence.
- The rest of `web-07` was never seen, so more failures may appear once it finishes.
- E2E jobs were not examined. They pass on the fork, while one failed on upstream.

## Failures the first count missed

Found while writing the stories, by comparing CI's failure list with the classification. All three are on `ebe7194d`.

- `test_epub_narration::test_page_list_cannot_create_duplicate_consumed_note_chapter`, both operating systems. Reproduced here. It stops at its own check that the fixture's navigation holds a `page-list`, before reaching the product. The cause isn't known yet: the fixture or the EPUB library may have changed. Assigned to story 10.
- `test_xtts_training_process::test_silent_training_cancellation_reaps_parent_and_child_and_preserves_unrelated`, Ubuntu only: "Process fixture did not become ready". It is skipped on Windows, so it wasn't run here. Assigned to story 10.
- `test_release_upgrade_preservation::test_published_release_tree_survives_normal_startup`, Windows only. Reproduced here as a hash mismatch on a fixture file. A line-ending change is the likely cause, which is inferred. Assigned to story 7.

## The 35 tests missing an actor

Each passes with an actor set. Assigned to story 3.

- `test_generation_edit_audio` (18): `test_a_later_text_edit_cannot_be_marked_current_by_an_older_request`, `test_cross_revision_regeneration_yields_and_resumes_original_run`, `test_edit_during_synthesis_keeps_old_result_stale_and_other_blocks_current`, `test_failed_replacement_resumes_parent_and_keeps_stale_audio`, `test_failed_requested_regeneration_is_missing_and_finalizes_partial`, `test_inheritance_is_idempotent_and_does_not_cross_real_restore`, `test_interrupting_a_targeted_output_does_not_expand_resume_to_full_plan`, `test_manual_take_selection_after_queuing_is_not_overwritten`, `test_partial_run_ignores_cross_plan_and_unguarded_clones`, `test_paused_replacement_retains_parent_resume_permission` (4 parameters), `test_regeneration_follows_an_unrelated_edit_and_selects_the_new_take`, `test_repeated_edits_keep_stale_take_and_late_regeneration`, `test_resuming_replacement_does_not_restore_revoked_parent_permission`, `test_standalone_partial_run_pins_guarded_audio_through_later_selection`, `test_standalone_regeneration_can_be_regenerated_again`.
- `test_voice_reference_setup` (4): `test_explicit_pins_select_older_sample_for_remote_publication`, `test_hash_change_during_provider_configuration_is_checked_before_upload`, `test_native_historical_pin_is_rejected_without_registration_or_revision_change`, `test_native_registration_reuse_and_endpoint_text_invalidation`.
- `test_web_cli_database_lifetime` (1): `test_native_fifteen_command_round_trip_disposes_before_every_output`.
- `test_web_generation_first_regeneration` (2): `test_adopted_response_regenerates_first_try`, `test_effective_speech_decides_audio_stale`.
- `test_web_generation_regeneration` (5): `test_cloud_failure_saves_other_wave_results_without_extra_retry`, `test_cloud_pause_saves_current_wave_and_resume_keeps_its_takes`, `test_failed_replacement_keeps_previous_active_take_and_wav`, `test_stale_or_missing_artifact_takes_do_not_satisfy_resume_checkpoint`, `test_terminal_parent_filled_by_grouped_regeneration_becomes_completed`.
- `test_web_voice_delete_confirmation` (5): `test_native_unpublish_requires_confirmation_and_retains_samples` (5 parameters).

## Where each group went

| Group | Ticket |
|---|---|
| Contamination, missing actor | Story 3 |
| `web-07` not finishing | Story 4 |
| Platform, `sqlite:///C%3A`, dubbing prefetch test (J1) | Story 7 |
| A1, A2, E1 | Story 8 |
| B1 to B9 | Story 9 |
| The other stale clusters, `test_web_api_shutdown`, `test_epub_narration`, `test_xtts_training_process` | Story 10 |
| Real bug 1 | `backlog/bug-alternate-take-model-refused-on-audio-cpp.md` |
| Real bug 2 | `backlog/bug-saved-preview-settings-fail-generation.md` |
| `test_web_database_efficiency` | `backlog/bug-assembly-checks-access-for-every-segment.md` |
| Security log on Windows | `backlog/bug-security-log-drops-events-on-windows.md` |
| Windows path length | `backlog/bug-long-data-paths-break-generation-on-windows.md` |

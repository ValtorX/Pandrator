---
title: 'The remaining out-of-date tests match the product'
type: chore
ticket: '10'
created: '2026-11-10'
status: built
baseline_revision: 'e52b79cc966127c52febf1f8a97a38eb0f64a424'
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

**Problem:** 51 tests fail on every platform because they lag deliberate product changes. They are clusters D1, D2, G1 to G5, H1, I1, K1, M1, N1 and O1 of the failure classification, plus `test_web_api_shutdown` (4, Linux only), `test_epub_narration` and `test_xtts_training_process` (Linux only). Fork run `42812800328` fails exactly this set, and so does a local Windows run at the baseline (45, with O1 depending on test order).

**Approach:** Change tests only, keeping each test's protection. Tripwire literals move to the current value, stubs gain the new arguments faithfully, retargeted patches must still be reachable, and refusals stay asserted with their new code or wording. Every read-only verdict was proved before planning, and none showed a product fault, so no backlog bug is filed.

**Verify (from the ticket):** All these tests pass on Windows and on fork CI, or skip with a stated reason on Windows where only Linux can run them, and each read-only verdict's outcome is recorded.

**Decisions:**

- Owner, 2026-11-10: proceed to implementation without a separate approval if the spec has no open questions. Nothing goes to `deferred-work.md`.
- `test_epub_narration` fails because EbookLib 0.21, which the `>=0.18,<1` range allows, no longer generates a `page-list` nav, while 0.20 (`pixi.lock`) did. The fixture builds its own page-list, so the test is independent of the library version.

## Boundaries & Constraints

**Always:** Test files only, plus the findings file on `fork-files`. Keep each file's working-tree line endings. Linux runs every changed test with its full assertions; nothing new is skipped. Run the changed files under SQLAlchemy 2.0.52 and again under 2.1.8.

**Never:** No product, migration, lane, CI or dependency change. Don't loosen an assertion to `ANY` beyond `settings_snapshot`. No staging or committing.

</frozen-after-approval>

## Code Map

Proved diffs from planning sit in the session scratchpad, at `C:\Users\Valtor\AppData\Local\Temp\claude\d9026c854974c4b3\3c319c1f-bd85-47bc-b72b-99a2d5c3a8da\scratchpad\proofs\` (`g1-literals`, `g2-mocks`, `g3-doubles`, `g4-behaviour`). Each applies cleanly at the baseline (`git apply --check`), touches only test files and passed its files in full under 2.0.52. All paths below are under `tests/`.

- **G1** `test_web_backend_architecture.py`: 413 and 406 routes, 48 registry entries plus the `system` domain (`noop`, `application_services.py:215`), and the `secret_redactor` field with an identity assert. All came in `68c444a1`.
- **G5**: Qwen 350 (`ab435066`, `narration-segmentation.md:197`) and Breeze 450 in `test_tts_generation_limits.py`. In `test_audio_cpp_preset_catalogue_wiring.py`, dated Kitten packages equal the 124-voice family list, and older ones keep 48 voices that are a subset of it.
- **G2**: the four public-link tools join the allowlist in `test_mcp_architecture.py`, and the two that are fenced by `expected_revision` join its exemption set. Counts go from 187 to 230 in 3 files. `test_mcp_server.py` adds `PUBLIC_LINK_OPERATIONS`. `test_mcp_sounds.py` adds the `workspace_id` parameter, and the I1 stub returns `ToolOutcome(result={})`. `test_mcp_voice_catalog_workspace.py` asserts that the shared base has no scope roots and that Promote's own `workspace_id` is validated.
- **G3** `test_project_speech_reference_store.py`, `test_voice_provider_job_effects.py`: `SCHEMA_HEAD >= TARGET` (zero-padded ids sort correctly), and the fresh database upgrades explicitly to `TARGET`. A refused downgrade leaves the database at `SCHEMA_HEAD`.
- **D1**: the catalogue call is asserted in full, with only `settings_snapshot` as `ANY`. Preview sends `approved_only=True, allow_host_adc=False` (`performance_routes.py:416`). The legacy voice catalogue sends `False`/`True` (`voice_catalog_routes.py:160`).
- **D2**: patch `_default_service_config_templates` (wrapped) in `test_generation_cast_runtime.py` and `test_session_view_performance.py`.
- **H1** `test_http_mutation_ownership.py`, `test_web_automation_security.py`: match `reviewed_{stage}_r{n}_*.srt`. The rollback test's absence check becomes an empty glob, so it is no longer vacuous.
- **G4**: a read-only key gets 403 `scope_denied`, and an `app.run` key gets 404 for the missing session. OpenAPI names `app.run` (`http_lifecycle.py:360-366`).
- **I1**:
  - `test_audio_pause_policy`, `test_tts_catalogue_policy`: the stubs gain `workspace_id` and `view`.
  - `test_catalogue_discovery_and_run_views`: a real session, then `refresh_tts_catalog` before `configure_tts`, because catalogue reads became passive.
  - `test_sound_export_integration`: the stubs gain `by_role`, `attached_sources`, `subtitle_mode` and `selected_subtitles`; the tests write `source.mp4` and the mixed WAV, and patch `reserve_output_space`.
  - `test_voice_list_workspace_access`: patch `pandrator.web.voice_sample_transcription.resolve_voice_sample_transcription_settings`.
- **K1**: write to `tmp_path`; nothing reads those files back.
- **M1**: the `[lease]` job stays `running`. Lease fencing is deliberate, as `test_job_terminal_lease_fencing` asserts, and the cleanup assertions after it pass.
- **N1** `test_mcp_voice_integration.py`: set the session to the `fixture_tts` provider and mark the voice ready for it before casting, because `generation_cast_runtime.py:465` refuses unlinked voices.
- **O1** `test_sound_composition.py`: build a fresh app in `tmp_path`. The session-scoped `case` collects actor rows, and 0074 refuses to drop them by design.
- `test_voice_reference_setup.py`: add `host_execution_actor` and the regex "sample changed before provider upload".
- `test_epub_narration.py`: the `_ensure_nav_page_list` helper.
- **`test_xtts_training_process.py:73`** (not in the diffs): `train()` runs in a new thread, so it has no model acquisition authority. `require_model_acquisition()` (`68c444a1`) refuses before the launch. Wrap the call in `host_model_acquisition_scope()` from `pandrator.logic.model_acquisition`. Proved by a probe: unscoped, it raises `ModelAcquisitionDenied` with no launch; scoped, it reaches `Popen`.
- **`test_web_api_shutdown.py:244`** (not in the diffs): a replay returns `public_project_settings(project_generation_run(...))` (`generation_routes.py:49,632`), and so does the first response. Compare the body with `public_project_settings(receipt)`. The stored receipt stays full (`generation_history_reads.py:43`).

## Tasks & Acceptance

**Execution:**
- [x] The 29 test files in the four diffs -- apply each with `git apply`, then rerun its files -- these are the proved edits.
- [x] `tests/test_voice_list_workspace_access.py` -- check that the retargeted `unexpected` patch can fire: temporarily call the resolver on the named GET path and confirm the test fails, then revert -- so the patch is not vacuous.
- [x] `tests/test_xtts_training_process.py`, `tests/test_web_api_shutdown.py` -- the two edits above -- Linux-only tests that CI settles.
- [x] `findings-ci-failure-classification.md` (on `fork-files`) -- add "Resolved by story 10": for each cluster, the verdict as proved, the edit, the EbookLib drift, and that no product fault was found. Mark the story-10 row resolved in `findings-ci-failures.md`.

**Acceptance Criteria:**
- Given the 31 files, when they run on Windows under SQLAlchemy 2.0.52 and again under 2.1.8, then nothing fails, and the only skips are the existing Linux-only ones, each with its reason.
- Given each lane that holds a changed file, when it runs through `scripts/test_lanes.py run`, then none of these tests fails.
- Given a fork CI run, which needs a push the owner approves, then these tests pass on Ubuntu and Windows.

## Implementation Notes

- The plan runs to about 2,300 tokens, over the 1,600 guide. The work was one goal the epic had already sized for one session, so it was kept whole rather than split.
- Baseline at `e52b79cc`, Windows, `<uv env>`, SQLAlchemy 2.0.52: 45 of these tests fail. The 46th, O1, fails only when an earlier test in the session-scoped `case` has made a request. Fork run `42812800328`'s failures in these files are exactly this set, with `test_web_api_shutdown` (4) and the xtts test failing on Ubuntu only.
- The implementer applied the four diffs and made the two Linux-only edits. Line endings stayed CRLF.
- The vacuity check on the voice-list patch held: a temporary call to the resolver on the named GET path made the test fail with "A passive voice-list read reached a hydration path", and the product file was restored.
- After the review patches, 34 test files changed (+249, -102): the 31 planned, plus 3 files holding the four vacuous subtitle absence checks fixed in review. All 34 pass under SQLAlchemy 2.0.52 and again under 2.1.8: 614 passed, 17 skipped, 0 failed. Each skip is an existing Windows-only one (POSIX SIGTERM, POSIX process group, symlink).
- `test_sound_composition` passed 3 serial runs and one with `-n 2`.
- Lanes, through `test_lanes.py run` with SQLAlchemy 2.0.52, covering every lane that holds a changed file:
  - `fast-xdist`, `mcp-serial` and web `01`, `02`, `03`, `06`, `12`, `17`, `18` and `19`: no failures.
  - `web-04-serial`: 1 failure, `test_web_database_efficiency::test_assembly_select_count_does_not_scale_with_segments`. That is F1, the author's choice already filed as `backlog/bug-assembly-checks-access-for-every-segment`, and it isn't one of this story's tests.
  - All but `mcp-serial`, `web-01` and `web-18` ran before the review patches. The three files the patches touched were rerun directly.
- `test_lanes.py check` and `ruff check tests` pass.
- No product fault was found, so no backlog bug was filed.
- Nothing is staged or committed. The findings edits sit uncommitted on `fork-files`.

## Plan Change Log

## Review Triage Log

Pass 1, quick lens: 0 high, 1 medium, 5 low, 1 false, 0 maybe-false. Nothing deferred.

| Finding | Verdict | Route | Evidence and action |
|---|---|---|---|
| Four tests still assert that a fixed `reviewed_*_rN.srt` is absent, which is vacuous since H1's UUID suffix (`test_subtitle_passage_review:643`, `test_subtitle_review_turns:1015,1338`, `test_web_subtitle_review:520`) | medium | patch | Confirmed: each compares `not destination.exists()` with a name the product never writes, so a cleanup regression would pass. Same cause as H1, so it is handled here. Each now asserts that no `reviewed_<stage>_r<n>_*.srt` exists. The sentinel in `test_subtitle_review_preparation:314` still shows that unrelated files are kept, so it is unchanged. |
| G3 comments claim later revisions "must not undo" the change, which nothing asserts | low | patch | Confirmed. The old `SCHEMA_HEAD == TARGET` was only a tripwire, and the TARGET-level comparison is kept. Both comments now say only what is checked. |
| The catalogue refresh stub sends an `Idempotency-Key` the real client never sends | low | patch | Confirmed: `refresh_tts_catalog` sends no key (`application_voices.py:622-640`). The header was dropped, and the test passes. |
| `findings-ci-failures.md` lines 58, 59 and 92 still read as open | low | patch | Annotated as resolved by story 10, with the causes. |
| The 2.1.8 and repeat-run evidence is missing from the findings | low | patch | Added to "Resolved by story 10". This plan's notes record the same results. |
| AC3, fork CI, is not met | false | reject | Not a defect in the change: it needs a push the owner approves. It is recorded as pending. |
| Unchecked Execution boxes | low | reject | The fix is an edit to this plan. The boxes are now ticked as bookkeeping. |

## Verification

`<uv env>` from `AGENTS.md`; pixi isn't installed. Use PowerShell or bash with `PYTEST_ADDOPTS` unset.

**Commands:**
- `<uv env> --with sqlalchemy==2.0.52 python -m pytest -q -rs <the 31 files>`, then again `--with sqlalchemy==2.1.8` -- expected: 0 failed.
- `<uv env> python -m pytest -q tests/test_sound_composition.py` three times, plus `-n 2` -- expected: passes every time.
- `<uv env> python scripts/test_lanes.py run <lane>` for `fast-xdist`, `services` and each `web-*` lane holding a changed file -- expected: none of these tests fails.
- `<uv env> python scripts/test_lanes.py check`, `uvx ruff@0.17.3 check tests` -- expected: pass.

**Manual checks:**
- On fork CI, `test_web_api_shutdown` and `test_xtts_training_process` pass on Ubuntu. The owner approves the push.

## Code Review

### 2026-11-10

**Code review complete.** 0 `decision-needed`, 6 `patch`, 0 `defer`, 15 rejected. Full diff `e52b79cc..3b85faf0` (34 files), lenses: blind hunter, edge case hunter, verification gap (no gaps), intent alignment.

- [x] [Review][Patch] Kitten preset check keys on a date-in-name heuristic instead of the catalogue's curated list, and a subset check misses reordering [tests/test_audio_cpp_preset_catalogue_wiring.py:19]
- [x] [Review][Patch] Revision-drift refusal shows no absence of provider side effects (no upload, no link write) [tests/test_voice_reference_setup.py:211]
- [x] [Review][Patch] Discovery counts are checked only at the end, so discovery inside `configure_tts` would go unnoticed [tests/test_catalogue_discovery_and_run_views.py:96]
- [x] [Review][Patch] Session creation status is not asserted, so a setup failure surfaces as a KeyError [tests/test_catalogue_discovery_and_run_views.py:318]
- [x] [Review][Patch] Comment claims every lifecycle write declares its own workspace root and refuses a session root, but only Promote is checked [tests/test_mcp_voice_catalog_workspace.py:362]
- [x] [Review][Patch] Findings record cites the pre-review run (31 files, 547 passed) instead of the final 34 files, 614 passed [findings-ci-failure-classification.md:205]

Rejected:

- Absence-only `reviewed_*_rN_*.srt` globs could pass vacuously (blind, edge): false. One write site (`subtitle_review.py:1731`), whose format is pinned positively by `test_subtitle_review_preparation.py:422` and `test_web_automation_security`, and the cleanup tests assert the denied unlink hit the real file.
- Evidence files written to `tmp_path` are never read: low. No behaviour lost, and the fix would add assertions beyond the story.
- Lease-loss branch checks only the status: false. The same test asserts cleanup and no publication, and reclaim is covered by `test_job_terminal_lease_fencing`.
- `settings_snapshot=ANY` and stubs that ignore `workspace_id`/`view`: low. ANY is the plan's stated boundary, and the stubs sit in tests about other subjects.
- `SCHEMA_HEAD >= TARGET` compares strings and does not prove ancestry (blind, edge): low. Ids are zero-padded `NNNN`, and `command.upgrade(..., TARGET)` already fails if TARGET is off the chain.
- MCP count 230 hard-coded in three files: false. Literal counts are the repo's deliberate tripwire, and the allowlist test covers the mutation split.
- No 80-character accepted case for Promote's `workspace_id`: low. Same gap as the parametrized sibling, not this change.
- `test_web_api_shutdown` builds the expected body with `public_project_settings`: low. Its subject is replay identity with the first response.
- `test_mcp_voice_integration` marks the voice ready by a direct database write: low. A setup shortcut; the fix would need a full provider publish path.
- 450 and 350 literals not tied to their source tables: false. Tripwire literals are the plan's approach.
- Read-only clusters proved only by passing test-only edits: false. That is the triage's own V standard, and each verdict cites its commit or sibling test.
- Findings record does not mark R against V per cluster: low. The table records one verdict per cluster, as Verify asks.
- No fork CI run shown: false. It needs an owner-approved push and is recorded as pending.
- `test_catalogue_discovery_and_run_views` no longer covers configure-time discovery: false. `configure_tts` no longer refreshes; catalogue reads became passive by design.

All six patches applied. The four changed test files give 49 passed under SQLAlchemy 2.0.52 and again under 2.1.8 (Windows, `<uv env>`), and `uvx ruff@0.17.3 check tests` passes. Nothing is staged or committed.

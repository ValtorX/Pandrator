---
title: 'Shared audio identity work costs far less'
type: bugfix
ticket: '2'
created: '2026-11-11'
status: done
baseline_revision: '0ed54b0764eaae756fcbf935cf3f3bbdfe2ca91d'
route: full
route_source: auto
risk: high
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Under casting, every cast span's audio identity rebuilds and deep-copies the whole TTS service catalogue, because `apply_resolved_binding` calls `resolve_capabilities(settings)` without the request's catalogue cache. That is 86-97% of every slow read, admission and opening check in story 1's baseline (`baseline-2-1.md`). Once removed, the next cost is SQL: each retained cast span re-proves the same pin against the database (`validate_retained_cast_resolution`: session access, `session_project`, `check_speech_input`), about 30 statements per span.

**Approach:** Pass the request or job's existing catalogue cache through the cast-binding path, so the catalogue is built once per distinct settings input. Remember, within one identity context or one cast freeze, which retained pins have already passed their database proof, so each is proven once. Identities stay identical for every segment, and nothing is cached across requests or jobs.

## Boundaries & Constraints

**Always:** Every identity a segment had before still comes out byte-identical (`settings_hash`, all reference hashes, `performance_request_hash`, `performance_error`). Caches live only as long as one `AudioIdentityContext` or one `freeze_cast_snapshot` call. Only a successful proof is remembered: a failing check runs again and raises exactly as before, in the same order. Settings-dependent checks (adapter, model, voice mode, alias) still run on every call. New parameters are optional keywords, so every other caller behaves as today. Regression tests count queries and catalogue builds, with no wall-clock assertions.

**Never:** Cache identities, capabilities or proofs across requests or jobs (the catalogue freezes clock-derived pricing, and access must be re-checked per request). Change take reuse rules, the identity schema or the default API responses. Touch synthesis-time rendering (`workflow_generation_execution` render paths), the listing's page logic (story 3), the summary paths (story 4), admission's other costs (story 8) or the write transaction (story 9). Stage or commit.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Cast plan, retained pins | Segments with narration only, one character, two characters, a language override, repeated texts | Same identities as an uncached, unmemoized pass; catalogue built a bounded number of times, not per span; statements do not grow per retained span | No error expected |
| Pin no longer valid | A retained pin's artifact deleted | Same `performance_error` identity as before, for every segment using it | Failure not memoized; each use re-checks and fails the same way |
| Access denied | Caller lacks project read | Same `AccessDenied` path as before | Not memoized |
| Settings differ, same pin | Same pin under a different model or adapter | Rejected as incompatible, as before | Settings checks are never memoized |

</frozen-after-approval>

## Code Map

- `pandrator/web/generation_cast_runtime.py:522` `apply_resolved_binding` -- line 559 `resolve_capabilities(settings)` uncached: the root cause (profiled: 245 calls, 41.8 of 43.3 s in a small non-active listing). Add keyword `_service_config_cache=None` and pass it on.
- same file `:191` `validate_retained_cast_resolution` -- line 205 `require_current_session_access`, 266-269 `session_project` + `check_speech_input` / `_pending_proof`. These depend only on `session_id`, `pin`, `retained_binding`, `_pending_revision_id`. Add keyword `_proofs: dict | None = None`: on a hit for `("access", session_id)` skip the access check; on a hit for `("input", session_id, _pending_revision_id, canonical_json(pin), canonical_json(retained_binding))` skip lines 266-269. Record only after success. Everything else, including the returned `result`, is unchanged. `canonical_json` is already imported here (used at line 885).
- same file `:679` `freeze_cast_snapshot` -- already defaults a cache (695-696). Pass it to both `apply_resolved_binding` calls (815, 962) and to `resolve_capabilities(selected)` at 876. Make one local `proofs: dict = {}` and pass it to the carried `validate_retained_cast_resolution` at 831.
- same file `:1004` `segment_render_parts` -- add keyword `_service_config_cache=None`, pass to `apply_resolved_binding` in both the v1 lambda (1051) and v2 `apply_at_slot` (1065).
- `pandrator/web/generation_audio_identity.py:301` `AudioIdentityContext` -- add `self._retained_proofs: dict = {}` in `__init__`. In `_with_performance` (545-557) pass `_service_config_cache=self._service_config_cache` to `segment_render_parts` and `_proofs=self._retained_proofs` to `validate_retained_cast_resolution`. In `for_segment` pass `_proofs` at 739 and the cache to `apply_resolved_binding` at 768.
- `pandrator/logic/speech_performance.py:260` `resolve_capabilities` -- reuse as is; its cached path is already what `resolve_binding` and `validate_retained_cast_resolution` use.
- `pandrator/logic/tts_service_catalogue.py:1162` `_shared_service_configs` -- do not change; `_default_service_config_templates` is the build counter existing tests patch.
- `tests/test_cast_retention_integration.py:67` `cast_project` fixture -- reuse: audio.cpp, casting on, retained pins for `fred` and `wilma`, dialogue XML. Pattern for actor scope and snapshot: lines 245-248.
- `tests/test_session_view_performance.py:164,963` -- statement-log and catalogue-build counting patterns.
- Not changed: `generation_recast.py` (benefits through the context), `project_readiness.py`, render-time callers.

## Tasks & Acceptance

**Execution:**
- [x] `tests/test_audio_identity_cost.py` -- new. Fixture: `cast_project` plus a plan of 12 segments, two of each kind (narration only, `fred` dialogue, `fred` and `wilma`, `wilma`, a `de` language segment, a segment whose speech XML names a character with no cast so it falls back). Test 1: `plan_audio_identities` over the revision equals a reference pass made one segment at a time, each with a fresh `AudioIdentityContext` and with `generation_cast_runtime.resolve_capabilities` patched to ignore any cache; identities hold retained hashes, no `performance_error`, and at least 3 distinct `settings_hash` values. Test 2: in that pass, `_default_service_config_templates` runs at most a small constant number of times, and the statement count for 12 segments exceeds the count for 6 by at most a small per-segment constant; record the pre-change counts in the test docstring. Test 3: a deleted retained pin artifact yields the same `performance_error` identity for each segment that uses it in both passes. Write the tests first and run them on the unchanged code to record the before numbers. -- the ticket's proof.
- [x] `scripts/test_lanes.py` -- add the new file to `web-20-serial` beside `test_cast_retention_integration.py`.
- [x] `pandrator/web/generation_cast_runtime.py` -- the cache and proof parameters above.
- [x] `pandrator/web/generation_audio_identity.py` -- pass the context's cache and proof memo.
- [x] `docs/release-notes-next.md` -- a Fixes line: on a long cast book, segment listings, plan status and history, admission and the opening check no longer spend minutes rebuilding the speech provider catalogue.
- [x] Timing -- run `large_project_timing.py` on fresh copies of `synthetic` and `real` under `%USERPROFILE%\pandrator-perf\work-2-2\` and append an "After story 2" section to `baseline-2-1.md` with the same tables and the before figures beside them.

**Acceptance Criteria:**
- Given the varied cast fixture, when identities are computed with and without the change's caching, then every segment's identity is identical.
- Given the same fixture, when the identity pass runs, then catalogue builds stay bounded and statements do not grow per retained span, where the unchanged code failed both bounds.
- Given fresh copies of both data roots, when the timing script runs, then large segment listings, plan status and revision history, admission and the opening check are each far below their story 1 baseline, and the results are recorded in `baseline-2-1.md`.
- Given the focused test files and checks below, when run, then they pass.

## Implementation Notes

- Tests written first and run on the unchanged code: the 12-segment cast plan made 11 catalogue builds and 271 statements (139 for the first six segments); after the change 1 build and 75 statements (63).
- The matrix audit found no test for the access-denied and settings-mismatch rows; three direct tests of `validate_retained_cast_resolution` with a shared memo now cover them (a repeated success skips both proofs, a settings mismatch still raises, a failed access or pin proof is never recorded).
- Pre-existing and left alone: once one segment's performance freeze fails in an `AudioIdentityContext`, `performance_states[revision_id]` keeps that error for every later segment of the revision. Test 3 compares the whole-plan pass exactly for that reason.
- Timing (fresh copies, `--skip-burst`, both roots at once, real copy medians): large listing 266-281 s to 6.2-6.6 s; large plan status and revision history over 900 s to about 42 s; large non-active revision 579 s to 20 s; small listings under 0.5 s; small plan status 61.5 s to 1.6 s; admission 703-710 s to 97-105 s; opening check 399-404 s to 86-89 s. Recorded in `baseline-2-1.md`, "After story 2". Admission and the check miss the proposed 60 s and 45 s targets (stories 8 and 9 own them); large plan status, history and non-active revision stay over 15 s (story 4).

## Plan Change Log

## Review Triage Log

Pass 1, quick lens. 0 high, 1 medium, 4 low, 0 false, 0 maybe-false.

- low, rejected: the `("access", session_id)` memo means a job whose lease is lost partway through an identity pass no longer gets `AccessDenied` (swallowed into `performance_error`) on later spans. Under a lease, `require_current_session_access` re-proves the job row against the clock (`job_authorization._prove_active_job_actor`). Nothing is published on the stale proof: take publication and heartbeats check `_owns_lease` (`jobs.py:1251,1287,1339`), and synthesis-time cast validation is not memoized. The only effect is that a run that has already lost its lease (rare since `415aaa8a`) ends at its next lease check, not at the opening check. The fix would add a branch that brings back per-span lease SQL in exactly the jobs this story speeds up.
- medium, patch: the `freeze_cast_snapshot` edits were not compared against an uncached reference or counted, because the fixture freezes once under the changed code before any pass. Adding a test that freezes twice on the same revision, with and without the reference patches, and compares the outputs and catalogue builds.
- low, patch: test 1 did not assert that identities carry the retained pins, as the plan requires. Asserting that the fred-only and wilma-only `voice_reference_hash` values are built from each pin's `content_hash`.
- low, patch: `validate_retained_cast_resolution` built the memo key (two `canonical_json` calls) even with no memo, so existing callers paid for it. Moving the key inside the memo branch.
- low, patch: the release note said "once per request"; the memo lasts one identity pass or one cast freeze, and admission and the opening check are jobs. Rewording.

## Design Notes

- Measured during planning on the small synthetic project under cProfile: plan status 274 s profiled with 16,934 statements; with only the catalogue cache threaded (monkeypatched) 10 s, still 16,934 statements, of which `validate_retained_cast_resolution` takes 4.7 s over 688 calls, mostly SQL (`check_speech_input._graph`, `session_project`).
- Why the proof memo is safe: the identity pass runs in one database session and one request or job, so the access check and pin graph cannot change for it, and the context already caches workspace, voices and resolutions on the same terms. Settings-dependent checks stay per call because they differ by span.
- Cross-request caching is ruled out here, as the epic left to this story: `_service_config_cache_key` requires a request-local cache for pricing, and in-request caching already removes the dominant cost.

## Verification

**Commands:**
- `<uv env> python -m pytest -q tests/test_audio_identity_cost.py tests/test_cast_retention_integration.py tests/test_generation_cast_runtime.py tests/test_generation_audio_identity.py tests/test_generation_identity_settings_copy.py tests/test_session_view_performance.py tests/test_speech_plan_audio_reuse.py tests/test_generation_recast.py tests/test_cast_snapshot_retention.py tests/test_segment_retention_runtime.py` -- expected: pass, under SQLAlchemy 2.0.52 and again 2.1.8
- `<uv env> python scripts/test_lanes.py run web-20-serial` -- expected: pass
- `<uv env> python scripts/test_lanes.py run fast-xdist` -- expected: pass
- `<uv env> python scripts/test_lanes.py check` -- expected: pass
- `uvx ruff@0.17.3 check .` and `<uv env> --with sqlalchemy==2.0.52 python scripts/check_types.py` -- expected: clean except the known `nemo_normalizer.py:98`
- `<uv env> python scripts/check_docs.py` -- expected: pass
- `<uv env> python _bmad-output/scripts/large_project_timing.py --root <fresh copy> --timeout 900 --job-timeout 3600 --skip-burst` -- expected: table printed, exit 0

## Code Review

### 2026-11-11

Code review complete. 0 decision-needed, 8 patch, 0 defer, 9 rejected. Lenses: blind-hunter, edge-case-hunter, verification-gap, intent-alignment (none failed).

- [x] [Review][Patch] apply_resolved_binding call sites outside the threaded set still rebuild the catalogue per cast span (freeze_boundaries with a voice-change pause, apply_segment_voice via segment_performance_settings, preview_render_parts) [pandrator/web/speech_boundaries.py:195]
- [x] [Review][Patch] Carried-freeze proof memo is unguarded by any test [pandrator/web/generation_cast_runtime.py:855]
- [x] [Review][Patch] Listing's live-material mode and the pending-proof memo path are untested [tests/test_audio_identity_cost.py:390]
- [x] [Review][Patch] The test's uncached reference and its comments misdescribe the old code [tests/test_audio_identity_cost.py:327]
- [x] [Review][Patch] Test module docstring names private planning [tests/test_audio_identity_cost.py:3]
- [x] [Review][Patch] reference_pass and _freeze_fresh manage patches by hand [tests/test_audio_identity_cost.py:322]
- [x] [Review][Patch] `_proofs` memo should be a set [pandrator/web/generation_cast_runtime.py:200]
- [x] [Review][Patch] Release note uses internal terms [docs/release-notes-next.md:9]

Applied 2026-11-11:
- F1: `apply_segment_voice` and `segment_performance_settings` take an optional `_service_config_cache`. The identity pass, the admission freeze loop (`speech_plan_workspace.py`) and `freeze_boundaries` pass one cache, and `preview_render_parts` passes its existing one. Synthesis-time callers are unchanged. Two new tests failed on the reviewed code: 13 builds for 12 segment-level voices and 10 with a voice-change pause. Each now has at most 1-2.
- F2: the carried freeze test counts `check_speech_input`: 2 proofs, against 10 without the memo.
- F3: the identity test compares a live-material context, as the listing uses one. A new test shows a pending proof is consulted once per revision and pin, and that a declined one falls through to the graph check once.
- F4-F6: the reference docstring and measured comment are corrected, the story reference is gone, and the patches use `ExitStack`.
- F7: `_proofs` is a `set[tuple[Any, ...]]`; its docstring says a hit skips only the access and pin proofs.
- F8: the release note is reworded for users.
- Checks (uv env, Windows): the focused files below plus every test of the newly touched functions (24 files) gave 554 passed, 1 xfailed under SQLAlchemy 2.0.52 and again under 2.1.8. `fast-xdist`: 3673 passed. Ruff clean; types clean except the known `nemo_normalizer.py:98`; `test_lanes.py check`, `check_docs.py` and `vulture` pass. Timing was not rerun after the patches.
- Expected conflict: upstream PR #125 (lukaszliniewicz/Pandrator) adds `self._revision_plan_rows` to `AudioIdentityContext.__init__` beside `_retained_proofs`. Expect a small conflict there when upstream is merged. The changes do not otherwise overlap, and their gains add up.

Rejected:
- false: the access memo key omits the actor; every context and freeze is function-local inside one session and one actor scope.
- low: the live segment-slot path in for_segment re-proves without the memo; segment slots each hold their own reference and are reached only after a segment voice changes, and the fix needs another parameter through resolve_project_cast_binding.
- false: no test that a run's per-segment context catches a mid-run pin deletion; the diff does not touch that site and the memo cannot cross contexts.
- low: cost bounds are loose; they still separate 2 from 22 statements per segment, with headroom for SQLAlchemy 2.0/2.1 differences.
- false: admission, the opening check and large plan reads miss their targets; stays with stories 4, 8 and 9, which own those targets.
- low: "query count falls" checked as a bound; the pre-change counts are recorded as the plan required, and a same-run before needs the old code.
- low: identities compared with a simulated before; it covers exactly the two mechanisms the change adds.
- false: the freeze change exceeds the literal for_segment scope; the plan's Code Map names the freeze.
- low: the access proof once per context hides a lease lost mid-pass; already rejected in the Review Triage Log above.

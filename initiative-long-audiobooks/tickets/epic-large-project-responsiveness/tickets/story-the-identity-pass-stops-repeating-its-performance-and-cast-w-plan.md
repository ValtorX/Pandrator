---
title: 'The identity pass stops repeating its performance and cast work'
type: bugfix
ticket: '3'
created: '2026-11-11'
status: done
baseline_revision: 'c5c0dcfb8ebe77335dedd60061bdb46dd9abb164'
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

**Problem:** After story 2, the identity pass still repeats CPU work per segment. Profiled on a fresh copy of the real database at `c5c0dcfb`, the large non-active revision read (15.9 s unprofiled, 65.6 s profiled) spends 15.1 s in `resolve_tts_language_support` (8.3 s rebuilding `audio_cpp_catalogue.package_metadata`, 6.3 s copying endpoint projections), 11.2 s deep-copying generation controls in `build_render_parts._control_values` (twice per segment), and 6.0 s in the cast freeze's own per-segment copies. In the page of 100, about 14 of the 16.7 s of deep copies are in the cast freeze.

**Approach:** Remember each segment-independent language-support result for the length of one pass, in the cache the pass already carries, and stop deep-copying controls that the identity pass and the cast freeze only read. Identities stay identical, and nothing is cached across requests or jobs.

## Boundaries & Constraints

**Always:** Every segment's identity comes out byte-identical (`settings_hash`, every reference hash, `performance_request_hash`, `performance_error`), and the frozen cast snapshot is equal to the one the old code froze. Memos live only in the `_service_config_cache` of one `AudioIdentityContext` or one cast freeze. A failing lookup is not remembered: it runs again and raises as before. New parameters are optional keywords, so every other caller, including synthesis, behaves as today. A caller's controls are never mutated. Regression tests count calls and copies, with no wall-clock assertions.

**Never:** Cache across requests or jobs, or memoize `package_metadata` process-wide. Change identity schema, take reuse, or default API responses. Touch the listing's page logic (story 5), plan status running the pass for every revision (story 4), admission's JSON decoding (story 9) or the opening-check transaction (story 10). Stage or commit.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Varied cast plan | Narration only, one character, two characters, a `de` language segment, an uncast character, repeated kinds | Identities and frozen snapshot equal to a pass without the memo and with copied controls; package-metadata and language lookups and controls copies do not grow per segment | No error expected |
| Package without a TTS route | audio.cpp model whose package has no `tts` language record | Same `ValueError` as before on every call | Not remembered |
| Same settings, different model or endpoint | Two spans differing only in model, service or endpoint | Each gets its own language support | Key includes every input the lookup reads |
| Unknown model | Model with no package (falls through to `tts_language_support`) | Same record as before | No error expected |

</frozen-after-approval>

## Code Map

- `pandrator/logic/tts_language_preflight.py:181` `resolve_tts_language_support` -- the language cost. When `endpoint is None` and `_service_config_cache is not None`, memoize the whole result under `("tts_language_support", catalogue._service_config_cache_key(settings), *str(settings.get(k) or "") for k in service, tts_service, openai_audio_endpoint, audio_cpp_base_url, silero_model, elevenlabs_model, xtts_model, model)`. Those are every setting `_selected_endpoint`, `_selected_model` and `resolve_openai_audio_endpoint` read; the catalogue key covers `openai_audio_endpoints_json`, `provider_configs`, `service_configs`. Store a deep copy, return a deep copy, wrap so a `None` result is a hit too. A raise is never stored. Same pattern as `speech_performance.resolve_capabilities:272-286`.
- `pandrator/logic/speech_performance.py:647` `compile_performance` -- already passes the cache to `validate_tts_language`; no change.
- `pandrator/logic/audio_cpp_catalogue.py:424` `package_metadata` -- do not change; tests count its calls.
- `pandrator/web/generation_rendering.py:100` `_control_values`, `:483` `build_render_parts` -- add keyword `_read_only_controls: bool = False`. When true and `controls is not None`, use `dict(controls)` instead of `deepcopy`. Safe because build_render_parts only reads control values: `parse_speech_markup`/`_character_catalog`, `_with_character_delivery` (builds new spans), `resolve_cast_voice` (validates into new dicts, returns a deep-copied binding), and `character_labels` strings; parts never reference controls.
- `pandrator/web/generation_cast_runtime.py:703` `freeze_cast_snapshot` -- pass `_read_only_controls=True` at 1001. At 761, when the segment has no `controls_by_segment` entry and no carried entry, use a shallow `dict(shared)` of one `shared = deepcopy(controls)` made before the loop; only top-level keys are ever set on `scoped_controls` (765, 835), and every stored copy is still `deepcopy`'d (799, 836, 1028). Keep the deep copies for `controls_by_segment` and carried controls.
- same file `:1034` `segment_render_parts` -- add keyword `_read_only_controls=False`, pass to both `build_render_parts` calls (1074, 1101).
- `pandrator/web/generation_audio_identity.py:560` `_with_performance` -- pass `_read_only_controls=True` to `segment_render_parts`; the pinned snapshot is shared across segments, so this also guards that nothing writes into it.
- `tests/test_audio_identity_cost.py` -- story 2's fixture `varied_plan`, `reference_pass`, `measured_pass`, `_freeze_fresh`, `_reference_patches`; extend, do not fork.
- Not changed: `_copy_inspection_settings` (6.2 s, settings copies), `resolve_cast_voice` re-validating characters per span (4.3 s); see Design Notes.

## Tasks & Acceptance

**Execution:**
- [ ] `tests/test_audio_identity_cost.py` -- first, on unchanged code, record in a docstring: for 12 and for 6 segments, `package_metadata` calls, `resolve_tts_language_support` calls that reach `_selected_endpoint`, and deep copies of controls dicts (a mapping with `characters` and `cast`) made by `generation_rendering` and `generation_cast_runtime` (patch each module's `deepcopy` with a counting wrapper). Then: extend `_reference_patches` so the reference drops the language memo (call the original without the cache) and forces `_read_only_controls=False`; the existing equality and freeze tests then cover the change. Add: (a) package-metadata calls and controls copies are equal for 12 and 6 segments, plus a small constant; (b) a freeze leaves `get_generation_controls` output and the passed `controls_by_segment` unchanged, and two freezes produce equal snapshots; (c) a package without a `tts` record raises the same `ValueError` on each of two calls in one cache; (d) two settings that differ only in model, service, endpoint or base URL get different support records.
- [ ] `pandrator/logic/tts_language_preflight.py` -- the memo.
- [ ] `pandrator/web/generation_rendering.py` -- `_read_only_controls`.
- [ ] `pandrator/web/generation_cast_runtime.py` -- shared controls copy in the freeze, flag through `segment_render_parts`.
- [ ] `pandrator/web/generation_audio_identity.py` -- pass the flag.
- [ ] `docs/release-notes-next.md` -- extend story 2's Fixes line: long cast books read revisions, check and admit faster still.
- [ ] Timing -- on fresh copies under `%USERPROFILE%\pandrator-perf\work-2-3\`, profile the non-active revision and page of 100 after the change, then run `large_project_timing.py --skip-burst` on a fresh `real` copy and append an "After story 3" section to `baseline-2-1.md` with the replan figures beside. If the non-active revision is above 7.95 s, profile again and remove the next identity-pass copy or lookup that repeats per segment (the inspection settings copy, then per-span character validation) until it is at or below, recording each in Implementation Notes.

**Acceptance Criteria:**
- Given the varied cast fixture, when identities and frozen snapshots are computed with and without the memo and the shared controls, then they are identical.
- Given the same fixture, when the pass runs over 6 and 12 segments, then package-metadata lookups, endpoint lookups for language support and controls copies stay constant, where the unchanged code grew per segment.
- Given a fresh copy of the real data root, when the timing script runs, then the large non-active revision read takes 7.95 s or less (median of 3), and the results are in `baseline-2-1.md`.
- Given the focused tests and checks below, when run under SQLAlchemy 2.0.52 and 2.1.8, then they pass.

## Implementation Notes

- Plan's three changes as written: language-support memo in `resolve_tts_language_support` (`_resolve_tts_language_support` is the old body), `_read_only_controls` through `build_render_parts`, `segment_render_parts`, the freeze and `_with_performance`, and one shared `deepcopy(controls)` in the freeze (shallow per segment). The freeze's `scoped_controls` is annotated `Any` because the typecheck otherwise infers `Mapping | None` for the retained-resolution map.
- First timing (large non-active revision, median of 3, fresh copy of `real`): 10.68 s, above 7.95 s. Profile showed `_copy_inspection_settings` 5.65 s over 25,622 calls (125 flat top-level settings, deep-copied one by one), `resolve_capabilities` cache hits 2.4 s, `_memoized_material_settings` 0.9 s.
- Step 1: `_copy_inspection_settings` copies the flat settings with the same result as `deepcopy(settings, memo)` but takes immutable values without a call, and `resolve_capabilities` hits and `_memoized_material_settings` use a new `speech_performance.copy_record` (same idea, plain dicts only). New equality and aliasing tests in `tests/test_generation_identity_settings_copy.py`. Result 8.87 s, still above.
- Step 2 (per-span character validation, as the plan allowed): `resolve_cast_voice` takes an optional `_validated` memo that remembers the validated characters and cast of the last controls it saw, matched by object identity and only passed together with `_read_only_controls` (`build_render_parts(_validated_controls=...)`, `segment_render_parts`, the freeze's local dict, and `AudioIdentityContext._validated_controls`). A failed validation is not stored. Tests in `tests/test_audio_identity_cost.py` count `_character_model` calls (reference 42, pass 3 for 12 segments) and check identity invalidation and failures. Result 6.50 s on the quick run and 6.62 s in the full run.
- Not done, still repeats per segment: `parse_speech_markup` and `_character_catalog` (speech markup, 4.6 s of the 18.2 s profile), `apply_resolved_binding` and `validate_retained_cast_resolution` per span. Left for the stories that own admission and plan status.
- Verification of the later steps is the equality tests already in `tests/test_audio_identity_cost.py` (reference vs measured identities and frozen snapshots, carried and fresh) plus the focused and listed-file runs under SQLAlchemy 2.0.52 and 2.1.8.

## Plan Change Log

## Review Triage Log

| # | Finding (quick) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | `test_freezing_neither_mutates_the_controls_nor_changes_the_snapshot`: the `get_generation_controls(...) == before` check cannot fail | medium | patch (grouped with 2) | `_public` (`generation_controls.py:75-87`) returns fresh deep copies and the freeze deep-copies again into `shared_controls`, so a nested write leaking between segments through `dict(shared_controls)` would pass; `frozen[0] == frozen[1]` repeats the same leak. |
| 2 | `_reference_patches` still runs the new shallow `dict(shared_controls)`, `copy_record` and the flat inspection copy, so AC1's "without the shared controls" comparison is never made | medium | patch (grouped with 1) | The reference forces only `_read_only_controls=False` and drops the two memos; the freeze body at `generation_cast_runtime.py:776-777` and the copy helpers are the same on both sides. Same root cause as 1: no test compares against per-segment deep copies. Production code read by inspection shows no nested write, so this is a test gap. |
| 3 | `test_language_support_is_keyed_by_every_setting_it_reads` lacks `tts_service`, `xtts_model` and `elevenlabs_model` variants | low | patch | All three are in `_LANGUAGE_SUPPORT_SETTING_KEYS` and read by `_selected_endpoint`/`_selected_model`; a key regression on them would go unnoticed. The fix is three more variants. |
| 4 | Freeze counts not compared over 6 and 12 segments | low | reject | The freeze always covers the whole revision, so a 6-segment comparison needs a second fixture; its bound of at most 4 controls copies and at most 2 lookups over 12 segments of 6 kinds already shows no growth per segment, and the identity pass is compared at 6 and 12 (`full == half`). |

Patches 1-3 applied in `tests/test_audio_identity_cost.py` only: the reference now gives each segment its own deep copy of the controls (a `dict` stand-in whose `isinstance` defers to the builtin), copies records with plain `deepcopy` and drops the immutable-leaf shortcut; the mutation test checks the object the freeze received; six more key variants. Reverified: focused files under SQLAlchemy 2.0.52 and 2.1.8, `web-20-serial`, `fast-xdist`, ruff, types, lanes, docs, vulture.

## Design Notes

- Where the copies are (non-active revision, profiled): `_control_values` 11.2 s over 10,334 calls, half from the freeze and half from `segment_render_parts`; `_copy_inspection_settings` 6.2 s; the freeze's own 6.0 s; `get_service_config` projections 5.4 s (removed with the language memo); `family_metadata` 4.7 s (inside `package_metadata`, removed too). The page of 100 is all one freeze. Fixing `_control_values` for both callers is one keyword, so it stays here rather than splitting off the per-segment half.
- Why a pass-scoped memo, not `functools.lru_cache` on `package_metadata`: the ticket forbids caching across requests, and the cache the pass carries already has the right lifetime and is where `resolve_capabilities` memoizes.
- The inspection settings copy and per-span character validation are left unless the timing needs them: the first was story 2's deliberate copy policy for callbacks that set top-level keys; the second needs a memo keyed by controls identity. The timing task brings them in if 7.95 s is not met.

## Verification

**Commands:**
- `<uv env> --with sqlalchemy==2.0.52 python -m pytest -q tests/test_audio_identity_cost.py tests/test_cast_retention_integration.py tests/test_generation_cast_runtime.py tests/test_generation_audio_identity.py tests/test_generation_identity_settings_copy.py tests/test_session_view_performance.py tests/test_speech_plan_audio_reuse.py tests/test_generation_recast.py tests/test_cast_snapshot_retention.py tests/test_segment_retention_runtime.py`, plus every test file that calls `resolve_tts_language_support`, `validate_tts_language`, `build_render_parts` or `segment_render_parts` -- expected: pass, then again with `--with sqlalchemy==2.1.8`
- `<uv env> python scripts/test_lanes.py run web-20-serial` and `run fast-xdist` -- expected: pass
- `uvx ruff@0.17.3 check .`; `<uv env> --with sqlalchemy==2.0.52 python scripts/check_types.py` (only `nemo_normalizer.py:98`); `<uv env> python scripts/test_lanes.py check`; `<uv env> python scripts/check_docs.py`; `<uv env> vulture` -- expected: clean
- `<uv env> python _bmad-output/scripts/large_project_timing.py --root <fresh real copy> --timeout 900 --job-timeout 3600 --skip-burst` -- expected: table printed, exit 0, non-active revision at most 7.95 s

## Code Review

### 2026-11-11

**Code review complete.** 0 `decision-needed`, 10 `patch`, 0 `defer`, 13 rejected. Lenses: blind-hunter, edge-case-hunter, verification-gap, intent-alignment (none failed). Diff: plan baseline `c5c0dcfb` to `877e79b5`.

- [x] [Review][Patch] Language-support key test does not pin the service, tts_service or catalogue components [tests/test_audio_identity_cost.py:699]
- [x] [Review][Patch] No test proves read-only planning leaves the caller's controls and pinned run snapshot unchanged [pandrator/web/generation_rendering.py:100]
- [x] [Review][Patch] No two-segment deferred root-binding freeze test keeps one segment's narrator fallback out of a cast-only sibling [pandrator/web/generation_cast_runtime.py:777]
- [x] [Review][Patch] `_copy_inspection_settings` re-implements `copy_record`, so the reference cannot replay the old single `deepcopy(settings, memo)` [pandrator/web/generation_audio_identity.py:504]
- [x] [Review][Patch] `build_render_parts` docstring states the wrong read-only contract (top-level writes during the call happen; nested values must never change) [pandrator/web/generation_rendering.py:506]
- [x] [Review][Patch] Test docstring reads per-character `_character_model` counts as validations [tests/test_audio_identity_cost.py:9]
- [x] [Review][Patch] Test docstring cites the planning label "story 3" [tests/test_audio_identity_cost.py:14]
- [x] [Review][Patch] Mutation test compares only a key subset of the frozen controls [tests/test_audio_identity_cost.py:665]
- [x] [Review][Patch] No-tts-route test depends on live inventory data (`dots_tts_edit_q8_0`) [tests/test_audio_identity_cost.py:668]
- [x] [Review][Patch] Release note uses internal wording ("read revisions, check and admit") [docs/release-notes-next.md:8]

Rejected:
- Freeze not compared at 6 vs 12, carried freeze not counted (intent-alignment, blind-hunter): low; rejected in the previous triage, the 12-segment bounds fail on per-segment growth, and carried copies stay by design.
- One-slot `_validated` memo thrashes on interleaved per-segment controls (intent-alignment, blind-hunter): low, performance only; a multi-entry memo would pin every per-segment controls copy for the pass; target met.
- No test that nothing is cached across requests (intent-alignment): false; every memo is per call, and no `AudioIdentityContext` caller passes a cache.
- Timing measured on the uncommitted tree (intent-alignment): false; post-review patches were test-only.
- `copy_record` lives in `speech_performance` (blind-hunter): low; a new module for it adds churn.
- `shared_controls` copy wasted in a carried freeze (blind-hunter): low; one copy per freeze.
- Memo hit recomputes the catalogue hash (blind-hunter): low; not a regression, prescribed key, target met.
- Freeze cost bounds too loose (blind-hunter): false; per-segment growth over 12 segments exceeds them.
- `_CopyingDict` shadowing is fragile (blind-hunter): low; a rewrite would fail the reference copies assertion loudly.
- `copy_record` with a pre-seeded memo or a self-referencing record (edge-case-hunter): false; no caller passes a memo and records are JSON-derived.
- Inspection copy with a back-reference to the settings (edge-case-hunter): false; JSON settings cannot reference themselves.
- `_CopyingDict` overcounts other `dict(...)` calls (edge-case-hunter): false; no other `dict()` in `generation_cast_runtime` takes a characters-and-cast mapping.

All 10 patches applied (2026-11-11). Production: `_copy_inspection_settings` now returns `copy_record(settings, memo)` (the reference's `copy_record` patch therefore replays the old `deepcopy(settings, memo)` exactly, and its `IMMUTABLE_LEAVES` patch is gone); the `build_render_parts` docstring states the real read-only contract. Tests: language variants now separate every key component, with a parametrized test proving that dropping any one of them (or the catalogue key) collides; the identity equality test checks the caller's snapshot is unchanged; a new `test_read_only_controls_plan_the_same_parts_and_leave_the_controls_untouched` in `tests/test_generation_rendering.py`; a new two-segment `test_narrator_fallback_of_one_segment_never_leaks_into_cast_only_sibling` in `tests/test_generation_audible_speech_preparation.py` (fails with `scoped_controls = shared_controls`); full frozen-controls equality; the no-tts-route test patches `package_metadata`; docstring and release-note wording. Verified with the uv env: 23 affected test files (720 passed) under SQLAlchemy 2.0.52 and 2.1.8, `fast-xdist` (3673 passed, 11 skipped), ruff, types (only `nemo_normalizer.py:98`), lanes, docs, vulture.

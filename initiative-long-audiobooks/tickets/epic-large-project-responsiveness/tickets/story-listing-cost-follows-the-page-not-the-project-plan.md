---
title: 'Listing cost follows the page, not the project'
type: bugfix
ticket: '5'
created: '2026-11-11'
status: done
baseline_revision: '35e82ff9ef5f282b9553221d5c19f475082bfe53'
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A segment listing pays for the whole plan whatever its `limit` and `fields`. Profiled after story 3 on a copy of the real database, a page of 100 on the 5,167-segment plan spends 7.1 of 8.6 s (cProfile) in one `freeze_generation_performance_snapshot`, 6.7 s of it the cast freeze over every segment, called from `AudioIdentityContext._with_performance` for the page's reuse status. A `fields=id,ordinal,status` page still takes 2.71 s (timed) because the identity pass runs for every listing. SQL is 0.04 s of the page; the two count queries are under 0.01 s each.

**Approach:** Run the identity pass only when the response includes `audio_reuse_reason` or `has_reusable_take`, and when it runs, freeze the cast for the page's segments only. Default and compact responses keep every field and value. Totals stay as they are, because they are already cheap (above).

## Boundaries & Constraints

**Always:** For every page of a plan whose cast resolves, each row's identity, `audio_reuse_reason` and `has_reusable_take` equal what the whole-plan freeze gives today. `effective_take`, `takes`, `take_count` and every other field are computed as today whatever `fields` is. The page-scoped freeze is a new optional keyword; admission, the opening check, recast, plan history and every other caller keep their current freeze. A listing with a selected run reads that run's pinned snapshot as today. Regression tests count calls and statements, with no wall-clock assertions.

**Never:** Change identity schema, take reuse, response fields or OpenAPI. Cache across requests. Touch plan status or history (story 4), admission (story 9), the opening check (story 10), the request lifecycle (stories 6 to 8). Stage or commit.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Id-only page | `fields=id` or `id,ordinal,status`, `view=provenance` | Same rows as today; no identity pass, no cast freeze | No error expected |
| Reuse field requested | `fields` containing `audio_reuse_reason` or `has_reusable_take` | Identity pass for the page's rows only | No error expected |
| Default or compact page | `limit=1` and `limit=50`, any cursor, varied cast plan | Same response as today; the cast freeze covers the page's non-removed rows only | No error expected |
| Page with a removed row | Removed row on the page | The removed row reports what it does today; every other row reports its own status, which can be `reusable` | No error; the removed row's failure no longer marks later rows unknown |
| Cast fails on the page | A page segment's cast voice cannot resolve | Every row reports `audio_identity_unknown`, as today | Same `performance_error` |
| Cast fails elsewhere | A segment outside the page cannot resolve | Page rows report their own identity and real reuse status, which can be `reusable` | No error; the page holding that segment still reports every row `audio_identity_unknown` |

**Decision (owner, 2026-11-11):** A cast voice that fails outside the page no longer marks the page's rows unknown; each page reports its own rows' identity. This matches how a retained-input run of those segments validates (`cast_segment_ids` in `workspace.py:1483`), and is the one case where default response values change.

**Decision (owner, 2026-11-11, at review):** Fix here the older bug where a removed row on a cast-plan page made every later row on that page report `audio_identity_unknown`: the listing works removed rows out after the page's other rows. This is the second case where default response values change.

</frozen-after-approval>

## Code Map

- `pandrator/web/generation_segment_reads.py:160` `list_segments` -- the loop at about :700 calls `audio_identity.for_segment(row)` for every row. Compute `effective_take` as today; compute the identity and the two reuse fields only when `needs_reuse` = (`fields is None and view in {"full", "compact"}`) or (`fields` holds `audio_reuse_reason` or `has_reusable_take`). `project_segments` (`generation_review.py:68`) drops unselected keys, so a skipped pass changes no response. When the pass runs, build `AudioIdentityContext(..., _cast_segment_ids=[row.id for row in rows])`. Leave the assembly-pause freeze (:398-480) alone; it already covers the page plus successors.
- `pandrator/web/generation_audio_identity.py:305` `AudioIdentityContext.__init__` -- new keyword `_cast_segment_ids: Collection[str] | None = None`, stored as a frozenset. `:518` `_with_performance` passes it to `freeze_generation_performance_snapshot` when it freezes (:533); unused when the snapshot is already pinned for the revision. In `for_segment` (:648), a segment outside a set scope raises `RuntimeError` (a programming error, not swallowed as `performance_error`).
- `pandrator/web/generation_performance_snapshot.py:38` -- new keyword `_freeze_segment_ids`, passed to `freeze_cast_snapshot`. Keep `has_block_voice`, `freeze_performance_snapshot` and `semantic_context_units` whole-revision: they decide whether to freeze and give neighbours' context.
- `pandrator/web/generation_cast_runtime.py:703` `freeze_cast_snapshot` -- new keyword `_freeze_segment_ids`; when set, add `GenerationSegment.id.in_(...)` to the loop's SELECT (:753). Do not use `validate_segment_ids`: it still walks every segment's markup. Inside one listing freeze a resolution is memoized by `cast_resolution_key` (binding and slot) through `current_resolutions`, so a page's entries do not depend on rows outside it; the tests check this rather than assume it.
- `tests/test_audio_identity_cost.py` -- story 2 and 3's `varied_plan` (12 segments, casting enabled, live settings), `_count_work`, `measured_pass`; extend here, no new file.

## Tasks & Acceptance

**Execution:**
- [x] `tests/test_audio_identity_cost.py` -- first, on unchanged code, record in the module docstring for `varied_plan` through `services.generation.list_segments`: statements, `build_render_parts` calls and segments the cast freeze visits for (a) `limit=1, fields=["id"]`, (b) `limit=1` default, (c) default page of all 12. Then add: identities of every `limit=1` page and of pages `cursor=0..5, limit=6` with the scope equal the whole-plan context's for the same rows, and the default and compact listing payloads equal those of a reference listing that patches the scope off (`_cast_segment_ids=None`); an id-only page makes no `for_segment` call and no freeze; a `limit=1` default page's freeze visits 1 segment and its `build_render_parts` calls and statements are below the full page's; a reuse field in `fields` runs the pass; `for_segment` outside the scope raises `RuntimeError`; a page whose own segment's cast voice fails reports every row `audio_identity_unknown`, while a page without it (the failing segment elsewhere, for example a `zed` cast to a deleted managed voice) reports its rows' real identities, where the whole-plan reference reports them unknown.
- [x] `pandrator/web/generation_cast_runtime.py`, `generation_performance_snapshot.py`, `generation_audio_identity.py` -- the scope keyword.
- [x] `pandrator/web/generation_segment_reads.py` -- `needs_reuse` and the scoped context.
- [x] `docs/release-notes-next.md` -- extend the long-cast-books Fixes line: segment pages cost what they show.
- [x] Timing -- on fresh copies of `real` and `synthetic` under `%USERPROFILE%\pandrator-perf\work-2-5\`, run `large_project_timing.py --skip-burst --skip-admission` (one root at a time) and append an "After story 5" section to `baseline-2-1.md` with the story 3 figures beside, then profile `segments_limit100` once.

**Acceptance Criteria:**
- Given the varied cast plan, whose cast resolves everywhere, when any page is listed in the default, compact or reuse-field projections, then every row equals today's whole-plan result.
- Given the same plan, when a `limit=1` page with `fields=["id"]` is listed, then it makes no identity pass and no cast freeze and fewer statements than a full page, and a `limit=1` default page freezes one segment.
- Given fresh copies of `real` and `synthetic`, when the timing script runs, then `segments_default`, `segments_compact`, `segments_fields`, `segments_limit100` and `segments_non_active` on the large project are inside 15 s (median of 3) and below their story 3 figures, recorded in `baseline-2-1.md`.
- Given the focused tests and checks below, when run under SQLAlchemy 2.0.52 and 2.1.8, then they pass.

## Implementation Notes

- Implemented as planned: `_freeze_segment_ids` on `freeze_cast_snapshot` and `freeze_generation_performance_snapshot`, `_cast_segment_ids` on `AudioIdentityContext` (frozenset; `RuntimeError` outside it), `needs_reuse` in `list_segments`.
- Baseline on unchanged code (12-segment plan, statements / `build_render_parts` calls / segments frozen): `limit=1, fields=["id"]` 70 / 13 / 12; `limit=1` default 74 / 13 / 12; all 12 default 100 / 24 / 12. After: 24 / 0 / 0, 46 / 2 / 1, and unchanged.
- Deviation: the "cast fails elsewhere" test deletes Fred's retained-pin artifact instead of casting `zed` to a deleted managed voice, because the fixture deletes that managed voice before a new cast could be written. Same path: the whole-plan freeze fails, the page freeze does not.
- Matrix audit added `test_a_page_with_a_removed_row_equals_the_whole_plan_listing`; it was the one matrix row without a test.
- Timing (fresh copies under `work-2-5`, median of 3, large project, real / synthetic): `segments_default` 0.70 / 0.81 s, `segments_compact` 0.40 / 0.51 s, `segments_fields` 0.08 / 0.06 s, `segments_limit100` 0.77 / 0.89 s, `segments_non_active` 0.64 / 0.68 s, against 2.71-3.12 s after story 3. Page of 100 under cProfile: 2.2 s against 8.6 s; its cast freeze 0.18 s against 6.7 s.
- Verified with the uv env: the 11 listed files (239 passed, 1 skipped) under SQLAlchemy 2.0.52 and 2.1.8; the implementer also ran the 36 other files that list segments or freeze the cast under both (892 passed, 3 xfailed), `fast-xdist` (3673 passed, 11 skipped) and `web-20-serial` (641 passed), with xdist. Ruff, types (only `nemo_normalizer.py:98`), lanes, docs, vulture clean.

## Plan Change Log

## Review Triage Log

| # | Finding (quick) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | A removed row on a cast-plan page makes every later row on that page `audio_identity_unknown`; the removed-row test compares with the whole-plan listing, which has the same defect | medium | owner decision (pre-existing; no story owns it; fixing it changes default values the frozen Always says stay as today) | Confirmed by probe on the 12-segment plan with matching takes: all 12 `reusable`; after removing row 1, rows 1-11 `audio_identity_unknown` both scoped and whole-plan. Cause: the removed row has no freeze entry, so `segment_render_parts` raises "Spoken text changed after the cast was frozen", and `_with_performance` stores the error in `performance_states` for the whole revision. `plan_audio_identities` skips removed rows, so admission and the opening check do not hit it. |

Owner chose to fix finding 1 here (frozen Decision, 2026-11-11). Patched directly: `list_segments` collects each row's take and artifact, then works out reuse for non-removed rows first and removed rows last (stable sort on `removed`), so item keys and every non-removed value are as before. The removed-row test (renamed `test_a_removed_row_never_marks_the_rest_of_its_page_unknown`) now also asserts the removed row is `audio_identity_unknown` and every other row `reusable`; the probe showed rows after it unknown on the unpatched code. Release note extended.

## Design Notes

- Why a page freeze is the same as the whole-plan one for its rows: for a listing, `freeze_cast_snapshot` has no carried snapshot, no per-segment controls, no missing-selection or root-binding callback, so each key resolves once through `current_resolutions` and every later user takes a copy; the shared `resolutions` map never sees two values for one key. A segment's entry reads only its own row and the shared controls. The `de` segment of `varied_plan` reuses Fred's key with different part settings, which is what the single-row pages test.
- Why not `validate_segment_ids`: it skips resolution but still runs `remap_markup` and entry building for all 5,167 segments (2.5 s of the 8.6 s profile).
- Totals: `total` and `boundary_flag_count` are two statements, under 0.01 s each in the story 3 profile of a page of 100; folding them buys nothing measurable.
- The second whole-revision `freeze_performance_snapshot` (assembly pauses and identity, 0.43 s each under cProfile) stays; sharing it would couple the pause freeze to the identity pass.

## Verification

**Commands:**
- `<uv env> --with sqlalchemy==2.0.52 python -m pytest -q tests/test_audio_identity_cost.py tests/test_generation_selected_takes.py tests/test_generation_summary.py tests/test_generation_plan_review.py tests/test_generation_search.py tests/test_session_view_performance.py tests/test_speech_plan_audio_reuse.py tests/test_cast_retention_integration.py tests/test_generation_cast_runtime.py tests/test_generation_audio_identity.py tests/test_mcp_application_client.py`, plus every other test file that calls `list_segments`, `generation-segments`, `freeze_cast_snapshot` or `freeze_generation_performance_snapshot` -- expected: pass, then again with `--with sqlalchemy==2.1.8`
- `<uv env> python scripts/test_lanes.py run web-20-serial` and `run fast-xdist` -- expected: pass
- `uvx ruff@0.17.3 check .`; `<uv env> --with sqlalchemy==2.0.52 python scripts/check_types.py` (only `nemo_normalizer.py:98`); `<uv env> python scripts/test_lanes.py check`; `<uv env> python scripts/check_docs.py`; `<uv env> vulture` -- expected: clean
- `<uv env> python _bmad-output/scripts/large_project_timing.py --root <fresh copy> --timeout 900 --skip-burst --skip-admission` for `real` and `synthetic` -- expected: table printed, exit 0, every large listing inside 15 s

## Code Review

### 2026-11-11

Code review complete. 1 `decision-needed`, 4 `patch`, 0 `defer`, 12 rejected. Lenses: blind-hunter, edge-case-hunter, verification-gap, intent-alignment (none failed).

- [x] [Review][Decision] Per-segment identity errors after the freeze still mark later rows unknown — `_with_performance` (`generation_audio_identity.py:646`) caches any per-segment ValueError for the whole revision, so the removed-row sort covers removed rows only. Unverified: in a live listing every page row is validated by the page freeze itself, so the open path is a history listing whose selected run's pinned snapshot skips the freeze, after a retained pin was deleted. Options: (a) probe a selected-run listing of `varied_plan` after deleting Fred's pin; if non-Fred rows after the first Fred row turn unknown, isolate per-segment errors for the listing context only (admission and the opening check unchanged); (b) probe only and record the result here; (c) leave it, since story 3 made the shared failure deliberate. Recommended: (a) — the probe settles the claim cheaply, and a listing-only fix matches how the removed-row case was fixed.
  - Owner chose (a). Probe confirmed it: listing a past run of `varied_plan` after deleting Fred's pin made every row from the first Fred row on `audio_identity_unknown`, Wilma, narration and uncast rows included. Fixed with `_isolate_segment_errors` on `AudioIdentityContext`, set only by `list_segments`: a failure after a clean freeze stays with its row, a failed freeze still marks the whole page. Test: `test_a_failure_after_a_past_runs_freeze_stays_with_its_own_rows` (whole page and each single-row page).
- [x] [Review][Patch] Test `effective_take` on a page without a reuse field, with takes present [tests/test_audio_identity_cost.py:565]
- [x] [Review][Patch] Release note implies per-row isolation; a missing voice on the same page still shows that page unknown [docs/release-notes-next.md:10]
- [x] [Review][Patch] Stale docstrings for the two freeze scopes (`_freeze_segment_ids` vs `cast_segment_ids`) [pandrator/web/generation_cast_runtime.py:717]
- [x] [Review][Patch] Build the AudioIdentityContext only when the page has rows [pandrator/web/generation_segment_reads.py:737]

Rejected:
- Page-dependent reuse answer when a page-mate fails (blind, edge): false — the frozen matrix row "Cast fails on the page" specifies it.
- Removed rows pay a doomed identity computation: low — cost only, short-circuit needs a special identity value.
- "Costs what it shows" holds only for the cast freeze: false — other freezes stay whole-revision by design; pages of 100 measure 0.77-0.89 s.
- `needs_reuse` restates projection rules: low — drift is caught by the compact equality test or costs an unused pass.
- Partial snapshot looks complete: low — private deepcopy, never persisted, guarded in `for_segment`.
- Equivalence tests miss other cast paths: false — a listing freeze has no carried snapshot or missing-selection callback, keys resolve once, other freezes stay whole-revision.
- Docstring counts not asserted: low — exact statement counts are brittle; the plan asks for relative checks.
- MCP default full view computes reuse it drops: low — page-sized cost, no wrong output; fix needs an MCP field map with non-projectable keys.
- No HTTP/MCP test, timing and concurrency absent from diff: false — timing in `baseline-2-1.md` "After story 5"; concurrency belongs to stories 6-8.
- Removed-row test reference is not the pre-change output: false — the owner decided that change and the test asserts the new values.
- Totals not made cheaper: rejected — the frozen Approach keeps totals; the fix would edit the intent.

---
title: 'The run list stops decoding whole run snapshots'
type: bugfix
ticket: '11'
created: '2026-11-12'
status: built
baseline_revision: 'cd3c647303fae2720c12f72270f56363d13cfaac'
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

**Problem:** While a run is active the web UI repeats `GET /api/v1/sessions/<id>/generation-runs?view=compact&limit=25` every 10 s. On the real 5,167-segment project each call costs 2.3-2.5 s (story 9 median), about a quarter of a core per open tab. An in-process profile on a real copy (1.56 s uninstrumented) shows why: the reader hydrates every selected `GenerationRun` with its whole `settings_snapshot_json` (10 runs, 150 MB, up to 80 MB each; 99.9 % is `generation_control_snapshot`, `performance_snapshot`, identities and guards) and every latest `OutputAssembly` with its whole `settings_json` (6 rows, 17.6 MB of pinned take manifests), then deep-copies those manifests in `_assembly_payload`. JSON decoding is 0.82 s and the deep copy 1.0 s, and the compact projection then throws all of it away.

**Approach:** When the list is read with `view=compact`, load each run's snapshot as only the six keys the payload reads, extracted in SQL, and each assembly's settings as only `audio_version_side`, so no full snapshot or manifest is decoded or copied. The compact response stays identical to projecting today's full payload; the full view and single-run reads are unchanged.

## Boundaries & Constraints

**Always:** For every session state, `list_runs(..., view="compact")` projected by `project_generation_run(..., "compact")` equals today's `project_generation_run(list_runs(...), "compact")`, with and without `limit` and `include_repairs`. The snapshot keys read by the payload path are exactly `tts`, `rvc`, `selected_segment_override`, `audio`, `early_repair_parent_run_id`, `regroup_parent_run_id` (consumers: `_run_label`, `_run_payload`, `_snapshot_enables_early_repair`, `select_second_pass`, `generation_run_history._repair_marker`, `_timing_repair_payload`); a key that is absent or JSON `null` may be represented either way, since every consumer uses `.get` or `isinstance`. Narrowed values never leave the reader's own read session and are never flushed. Regression tests count loaded rows or decoded documents, with no wall-clock assertions. Timing results hold timings and counts only: no paths or titles.

**Never:** Changing the full view, `latest_run`, `_run_payload` for single-run routes, the dispatch `list_runs` methods, response fields or OpenAPI. Adding columns, migrations or caches across requests. Narrowing `_history_index` (already SQL-only). Stage or commit.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| UI poll | compact, `limit=25`, runs with large snapshots and assemblies | same compact items as today; no run snapshot or assembly settings decoded whole | none |
| No limit | compact without `limit` | same items as today, same narrowing | none |
| Repairs | early-repair and regroup children, `include_repairs` true and false, malformed markers | same grouping, labels, `timing_repair`, `second_pass` | none |
| Recast | run with `audio_version_json` and before/after assemblies | same `audio_version`, label and `audio_version_assemblies` (with `audio_version_side`) | none |
| Missing keys | snapshot `{}` or keys set to `null` | same label ("Speech generation"), `second_pass` None | none |
| Full view | `view=full` or omitted | unchanged path, full decode as today | none |
| Bad view | `view=other` | 422 as today | validation_error |

</frozen-after-approval>

## Code Map

- `pandrator/web/generation_history_reads.py:1275` `list_runs(session_id, *, include_repairs, limit)` -- add keyword `view: str = "full"`; compact sets a narrow-load flag for both branches (the `limit` branch via `_selected_run_context`, the other via `_run_history_context(session, session_id)`).
- same file `:646` `_selected_run_context` -- the hydration `select(GenerationRun)...where(id.in_(needed_ids))` is where compact narrows runs; pass the flag through to `_run_history_context(..., runs=hydrated)`.
- same file `:368` `_run_history_context` -- its own `select(GenerationRun)` when `runs is None` (narrow under compact), and the latest-assembly `select(OutputAssembly)...` (narrow under compact). Keep the ranking subquery as is.
- Narrowing technique: `select(GenerationRun, *[GenerationRun.settings_snapshot_json[key] for key in keys]).options(defer(GenerationRun.settings_snapshot_json, raiseload=True))`, then `sqlalchemy.orm.attributes.set_committed_value(run, "settings_snapshot_json", {key: value ...})`. Same for `OutputAssembly.settings_json` with `audio_version_side`. `raiseload=True` makes any unplanned read fail loudly instead of lazily decoding. SQLite's JSON parse cache makes the six extracts about one parse per row (real copy: 0.19 s for 150 MB against 0.74 s for `json.loads`). Define the key tuple once as a module constant beside `COMPACT_ASSEMBLY_FIELDS`, built from `EARLY_REPAIR_MARKER_KEY` and `REGROUP_MARKER_KEY`.
- same file `:1028` `_run_payload`, `:809` `_timing_repair_payload`, `:1306` `_assembly_payload`, `:42` `_snapshot_enables_early_repair`; `generation_run_history.py:48` `_repair_marker`; `pandrator/logic/dubbing/passage_regroup.py:225` `select_second_pass` -- consumers; no change.
- `pandrator/web/generation_routes.py:571-578` `generation_run_list` -- pass `view=view` to `generation.list_runs`. The `generation_run_id` branch (`:551`) and `generation_run_detail` stay full.
- `tests/test_session_view_performance.py` -- `_run_history_fixture:339`, `_history_assembly:380`, the `loaded_as_persistent` counting in `test_limited_and_latest_history_hydrate_only_selected_ordinary_runs:583`, and `test_bounded_history_preserves_repairs_grouped_usage_and_malformed_markers:695` (repairs, malformed markers) are the templates. Lane `web-12-serial`.
- `tests/test_catalogue_discovery_and_run_views.py:163` `test_admission_replay_projects_full_receipt_without_duplicate_run` -- already compares the compact list route with the full list minus `settings_snapshot`; extend it with `limit=25`. Lane `web-04-serial`. MCP's `list_generation_runs` also asks for `view="compact"` (same file `:186`), so MCP benefits with no change.
- `_bmad-output/scripts/large_project_timing.py:576` -- `ui_runs_compact` already exists; no change.

## Tasks & Acceptance

**Execution:**
- [ ] `tests/test_session_view_performance.py` -- first, on unchanged code, add an equivalence test: a session with early-repair and regroup children, a malformed marker, a recast run with before/after assemblies, a run whose snapshot is `{}` and one with `null` values, a queued run behind a running job, and snapshots and assembly settings padded with a large sentinel key. For `include_repairs` true/false and `limit` None/2/25, assert `project_generation_run(item, "compact")` over `list_runs(..., view="compact")` equals the same over `list_runs(...)`. Then a load test: with a `loaded_as_persistent` listener, compact reads leave `settings_snapshot_json` and `settings_json` unloaded at load time (absent from the instance `__dict__` before narrowing, checked via the listener), and no returned value contains the sentinel.
- [ ] `pandrator/web/generation_history_reads.py` -- the `view` keyword, the key constant and the narrow loads for runs and assemblies as in the Code Map.
- [ ] `pandrator/web/generation_routes.py` -- pass `view`.
- [ ] `tests/test_catalogue_discovery_and_run_views.py` -- in the replay test, also assert `?view=compact&limit=25` equals `?limit=25` minus `settings_snapshot`.
- [ ] Timing -- copy `%USERPROFILE%\pandrator-perf\real` fresh to `work-2-11\after\real` and run `large_project_timing.py --root <copy> --skip-burst --skip-admission --runs 5`. Append "After story 10: compact run list (story 11)" to `baseline-2-1.md` with `ui_runs_compact` before (story 9's 2.3-2.5 s and this plan's 1.56 s in-process profile) and after, plus the in-process `list_runs` time after.
- [ ] `_bmad-output/fork-release-notes.md` -- one Fixes line: the run history the app polls during generation no longer costs seconds of CPU per refresh on long projects.

**Acceptance Criteria:**
- Given the equivalence fixture, when the compact list is read with and without `limit` and repairs, then every item equals today's compact projection.
- Given runs and assemblies with large settings, when the compact list is read, then no run snapshot or assembly settings document is loaded whole.
- Given a fresh real copy, when the timing script runs the read matrix, then `ui_runs_compact` has a median at most 0.8 s, against 2.3-2.5 s before, and the result is recorded in `baseline-2-1.md`.
- Given the focused tests, when run under SQLAlchemy 2.0.52 and 2.1.8, then they pass.

## Implementation Notes

## Plan Change Log

## Review Triage Log

Before review, the orchestrator fixed one gap in the implementation. An explicit JSON-null `audio_version_side` was narrowed to `{}`, which keys the versions map `"after"` where today it is keyed `None`. A `json_type` column now keeps null distinct from absent. The fixture adds that case, and the test was shown to fail on the earlier narrowing. The orchestrator also replaced the control characters in the baseline section's folder name.

Pass 1, quick lens. 5 findings: 0 high, 1 medium, 4 low, 0 false.

| # | Finding (quick) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | The equivalence fixture cannot catch dropping `audio`, `selected_segment_override` or `rvc` from the key tuple, and `second_pass` is never really compared. | medium | patch | Confirmed. `_create_session` posts `workflow_kind: "audiobook"` (`tests/test_session_view_performance.py:79`), and `select_second_pass` returns `None` for non-voiceover before reading any key. The root's `tts` flag alone enables early repair, and no run has `operation == "rvc"`. Fix: a voiceover variant in which each of the six keys decides an output, plus a test that removing any one key from `COMPACT_RUN_SNAPSHOT_KEYS` changes the compact output. |
| 2 | The sentinel assertion in the equivalence test runs on already-projected items, so it proves nothing. | low | patch | Confirmed: `project_generation_run(..., "compact")` drops `settings_snapshot` and assembly `settings` either way. Delete the assertion. The load test carries the real check, and its no-limit subtest includes the padded assemblies. |
| 3 | The release-notes header does not name story 11 or its upstream status. | low | patch | Confirmed (`fork-release-notes.md:11`). Story 9 kept this header current. |
| 4 | The baseline section does not name its environment. | low | patch | Confirmed: earlier sections say "uv environment, SQLAlchemy 2.0.52" (lines 743, 794). |
| 5 | The full view's in-process "before" figure repeats the after samples. | low | patch | Confirmed. The real before figure is the planning profile on the old code, 1.56-1.62 s (two uninstrumented runs), which also covers the full view because the old code always decoded everything. |

## Design Notes

- Why not narrow the full view too: its `assembly.settings` returns the whole manifest, and single-run routes reuse `_run_payload` with the session's own objects. The run-snapshot narrowing would be safe for the full list, but the ticket scopes the compact view; keeping full untouched keeps the change easy to review and offer upstream.
- Expected after: `_history_index` 0.26 s plus one SQL parse of the selected snapshots (~0.2 s) plus small rows, so about 0.5 s in-process against 1.56 s; the 0.8 s target leaves room for HTTP and the server's other threads.
- `set_committed_value` replaces the attribute without marking the object dirty. The reader's sessions are its own (`snapshot_session`, or `session()` in the no-limit branch) and close before returning, so no narrowed value reaches another caller.

## Verification

**Commands:**
- `<uv env> --with sqlalchemy==2.0.52 python -m pytest -q tests/test_session_view_performance.py tests/test_generation_run_history_projection.py tests/test_regroup_history_batches.py tests/test_generation_summary.py tests/test_catalogue_discovery_and_run_views.py` -- expected: pass, then again with `--with sqlalchemy==2.1.8`
- `<uv env> python scripts/test_lanes.py run web-12-serial` and `run web-04-serial` -- expected: pass
- `uvx ruff@0.17.3 check .`; `<uv env> --with sqlalchemy==2.0.52 python scripts/check_types.py` (only `nemo_normalizer.py:98`); `<uv env> python scripts/test_lanes.py check`; `<uv env> vulture` -- expected: clean
- `<uv env> python _bmad-output/scripts/large_project_timing.py --root <fresh real copy> --skip-burst --skip-admission --runs 5` -- expected: `ui_runs_compact` median at most 0.8 s

The implementer applied all five patches:
- `_compact_voiceover_fixtures()` adds three voiceover sessions. In them, `audio`, `selected_segment_override`, `rvc` and the markers each decide an output, and early repair is enabled by `audio` alone in one session and by the override alone in another.
- The equivalence test loops over all four sessions.
- New test `test_each_compact_snapshot_key_decides_some_compact_output`: removing any one key from `COMPACT_RUN_SNAPSHOT_KEYS` changes the compact output.
- The no-op sentinel assertion is deleted.
- The release-notes header names story 11 as not yet offered upstream.
- The baseline section names its environment and gives 1.56-1.62 s as the before figure for both in-process rows.

The orchestrator sorted one import block that ruff flagged (I001).

Reverified (uv, Python 3.11, Windows):
- The five focused files: 81 passed under SQLAlchemy 2.0.52 and again under 2.1.8.
- `web-12-serial`: 78 passed, 2 skipped. `web-04-serial`: 297 passed.
- Ruff is clean on the repo and on `_bmad-output/scripts`.
- Types report only `nemo_normalizer.py:98`.
- The lane check and vulture are clean.

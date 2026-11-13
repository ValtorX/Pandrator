---
title: "A single revision's detail answers in a few seconds"
type: bugfix
ticket: '13'
created: '2026-11-13'
status: done
baseline_revision: 'f590322c1909f54ea66f246cced4f9f517590cae'
route: full
route_source: auto
risk: medium
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** One speech plan revision's detail (`GET /sessions/<id>/generation-plan/revisions/<revision_id>`, `generation_review.revision_detail`) takes 6.6 s on the real 5,167-segment plan. Every MCP plan-status and history call makes it for the active revision, and the web UI makes it when a revision is opened in the speech plan history. The cost grows with book length, so a book about twice this size would push the MCP status call past the 15 s client timeout.

**Approach:** Cut the per-segment work of that read's audio identity pass within one request, so the response keeps its fields and meaning and the reuse counts and guards stay identical. The target and how far the restructure goes are the open question below.

## Boundaries & Constraints

**Always:** The response keeps its fields and meaning. Reuse counts (`reusable_segment_count`, `stale_segment_count`, `audio_settings_stale_segment_count`, `audio_identity_unknown_segment_count`) are identical for every revision of a varied fixture (cast, edit copies, missing voices, removed segments). Every guard still runs: session access, workspace ownership, voice-library read, retained-pin proofs, `raise_if_abandoned`. A failing check is never remembered. Regression tests count work, with no wall-clock assertions.

**Never:** Change the default or summary responses of plan status or history, the OpenAPI, or any MCP tool. Change what makes a take reusable. Cache anything across requests (unless the open question settles otherwise). Stage or commit.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Active revision, cast plan | Retained pins, multi-speaker markup | Same counts as today; less work counted | No error expected |
| Edit copy / non-active revision | Revision other than the active one | Same counts as today | No error expected |
| Missing managed voice | A cast voice no longer exists | Same `audio_identity_unknown` counts as today | Degrades to unknown, as today |
| Removed segments | Some segments removed | Excluded from counts as today | No error expected |
| Revision of another session | Foreign revision id | 404 as today | `KeyError` path unchanged |

**Decision (owner, 2026-11-13):** Option C: close the story with no code change; this replaces the Approach above. Profiling showed the 3 s target out of reach by trimming (exact in-request memos reach about 6.0-6.3 s, crude upper bounds 5.78 s), and reaching it would need a high-risk restructure of the identity and cast code that upstream also edits. R3 is met (6.6 s, about 2.3x headroom), and on a longer book the MCP tools degrade a detail timeout to a warning with the summary still returned (story 4). The profile is recorded in `baseline-2-1.md`, "After story 12: single revision detail (story 13)".

</frozen-after-approval>

## Code Map

- `pandrator/web/generation_review.py:96` `revision_history`, `:259` `revision_detail` -- the read; builds one `AudioIdentityContext` and calls `for_segment` per completed take.
- `pandrator/web/generation_audio_identity.py:303` `AudioIdentityContext`, `:525` `_with_performance` (freezes the cast for the whole revision once, then `segment_render_parts` and `compile_performance` per segment), `:669` `for_segment`, `:888` `take_reuse_reason`.
- `pandrator/web/generation_cast_runtime.py:81` `remap_markup` (parses twice), `:192` `validate_retained_cast_resolution`, `:414` `resolve_binding` (builds a `TtsProviderRegistry` per call), `:704` `freeze_cast_snapshot` (remap and `build_render_parts` per segment), `:1055` `segment_render_parts`.
- `pandrator/web/generation_rendering.py:489` `build_render_parts`, `:187` `_binding_result`; `pandrator/logic/speech_markup.py:681` `parse_speech_markup` (pure; returns a frozen dataclass).
- `tests/test_audio_identity_cost.py` -- `varied_plan` fixture and reference-pass pattern to reuse for the equivalence and work-count tests.
- `_bmad-output/scripts/large_project_timing.py` -- `revision_active` and `revision_non_active` operations for the real-data timing.

## Tasks & Acceptance

None: closed by the owner's decision with no code change.

## Implementation Notes

- No code changed. The options put to the owner: A, restructure so each segment renders once per request (estimated 3-4 s, not shown feasible, risk high); B, exact trims only (about 6.0-6.3 s); C, close with no code change; D, cache across requests (safe only with every input in the key). The owner chose C.
- Evidence (fresh copy of `real` at `f590322c`, uv env, SQLAlchemy 2.0.52): `revision_active` 6.62 s median in process; the identity pass is 6.3 s of it: the whole-revision cast freeze 2.45 s (remap 0.90 s), each segment rendered again 2.15 s (pin re-validation 0.88 s, parse 0.42 s), `compile_performance` 0.42 s. Each segment's markup is parsed four times and rendered twice. Stub upper bounds: parse memo 6.35 s, pin re-validation memo 5.80 s, all three 5.78 s. Full tables in `baseline-2-1.md`.
- The probe and profiling copy stay outside the repo (`%USERPROFILE%\pandrator-perf\work-2-13\`).
- Upstream follow-up (2026-11-13): the profile went upstream as issue #145. The maintainer answered with #146, which shows option A would change results (the freeze and the identity pass render with different settings, and everything after span resolution reads them), and instead shares the freeze's markup parse and one provider registry per pass. Timed on fresh copies of `real`, alternating twice: `revision_active` 6.55 s to 5.85 s, `revision_non_active` 6.55 s to 5.89 s (medians of 10, about -10%), posted on #146. It is not in 0.14.4; it reaches the fork with a later upstream merge. #146 was merged unchanged the same day (`f449f03f`) and #145 closed as completed; it ships in upstream 0.14.5. Details in `upstream-pr-drafts.md`, section 11.

## Plan Change Log

## Review Triage Log

## Verification

**Commands:**
- `<uv env> --with sqlalchemy==2.0.52 python _bmad-output/scripts/large_project_timing.py --root <fresh copy of real> --timeout 900 --profile --runs 1 --top 40 --only large.revision_active --skip-burst --skip-admission` -- ran: exit 0, 18.28 s under cProfile, 1,386 statements

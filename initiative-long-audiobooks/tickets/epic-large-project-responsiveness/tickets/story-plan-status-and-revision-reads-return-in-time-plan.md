---
title: 'Plan status and revision reads return in time'
type: bugfix
ticket: '4'
created: '2026-11-11'
status: done
baseline_revision: '24c1dd3630d00092eb9b76e32c687309ffc1742e'
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

**Problem:** After story 3, the MCP tools `pandrator_get_speech_plan_status` (16.5 s) and `pandrator_list_speech_plan_revisions` (16.2 s) still miss the 15 s MCP request timeout on the 5,167-segment book, because both request the full history, which runs the audio identity pass for every revision (3 × ~5,000 segments). The `summary=true` variants answer in 0.4 s and 0.02 s, and one revision's detail in 6.6 s; the web UI already uses that pair.

**Approach:** The MCP tools request the summary path, then fetch the full detail only for the active revision when it is in the response, and copy its audio reuse counts onto that item. Identities are computed for the one revision an assistant acts on, not for every revision. Default HTTP responses are unchanged.

## Boundaries & Constraints

**Always:** Each MCP application request stays a separate request under the client's timeout. Only the five reuse fields (`reusable_segment_count`, `stale_segment_count`, `audio_settings_stale_segment_count`, `audio_identity_unknown_segment_count`, `audio_reuse_checked`) are copied from the detail onto items whose `id` equals the active revision id; every other item keeps the summary's nulls and `audio_reuse_checked: false`. The top-level `audio_reuse_checked` stays as the server sent it (false). If the detail request fails with `not_found` or `application_response_timeout`, the tool still returns the summary with a warning naming the code; any other error propagates. Regression tests count calls, with no wall-clock assertions.

**Never:** Change the default (non-summary) HTTP responses of `/generation-plan/status`, `/generation-plan/revisions` or `/revisions/<id>`, or their OpenAPI. Add an MCP tool or change tool input schemas. Cache identities across requests. Touch listing page cost (story 5), admission (story 9) or the opening check (story 10). Stage or commit.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Status, active plan | Summary has items incl. the active id | Calls status with `summary=True`, then detail for the active id once; that item carries the detail's counts, others null | No error expected |
| List, active on page | Revisions page includes the active id | Calls list with `summary=True` plus the same paging, then detail once | No error expected |
| Active not in response | No plan, `active_revision_id` null, or active on another page | Only the summary call; no detail request | No error expected |
| Detail race or slow | Detail raises `not_found` or `application_response_timeout` | Summary returned, item unchecked, one warning with that code | Degrade, do not fail |
| Detail other failure | Detail raises e.g. `forbidden` | Tool fails with that error | Propagates |

</frozen-after-approval>

## Code Map

- `pandrator_mcp/clients/application_generation.py:237` `get_speech_plan_status`, `:278` `list_speech_plan_revisions` -- add keyword `summary: bool = False`; when true send `summary="true"`. Add `get_speech_plan_revision(session_id, revision_id)` -> GET `/api/v1/sessions/{id}/generation-plan/revisions/{revision_id}` (openapi `getSpeechPlanRevision`; returns `{"revision": item, "active_revision_id": ...}`). Quote both ids like the neighbours.
- `pandrator_mcp/tools/generation.py:242` `speech_plan_status`, `:270` `list_speech_plan_revisions` -- call with `summary=True`; a shared helper finds the active id (`active_revision_id`, falling back to `selected_revision_id` for status), skips when no item has it, else fetches the detail and copies the five fields. Return `ToolOutcome(result=..., warnings=[WarningMessage(code=..., message=...)])` (`pandrator_mcp/schemas/common.py:16`, used as in `tools/system.py:136`); without warnings a plain dict or `ToolOutcome` both work with `response()` -- keep the existing result shape (`schema_version`, `session_id` for list).
- `pandrator_mcp/registrations/generation.py:141,207` -- docstrings: status and history are summaries; reuse counts are checked for the active revision only.
- `pandrator_mcp/guides/workflows.md:148` -- one sentence: plan status and revision history report reuse counts for the active revision only; others show `audio_reuse_checked: false`.
- `tests/test_mcp_generation_registration.py:71,91` -- `Case` kwargs gain `summary: True`; canned payloads (`:259`, `:266`) have no active item on the page, so each case still makes exactly one call (`invoke_case` asserts `len(calls) == 1`). Add `get_speech_plan_revision` to `PAYLOADS` only if `create_autospec` needs it for new tests.
- Server, unchanged: `pandrator/web/speech_plan_workspace.py:319` (`summary` -> `grouped_revision_history(include_audio_reuse=False)`), `generation_routes.py:177,199`, `generation_review.py:96` `revision_history`, `:259` `revision_detail` (always full, one revision). `tests/test_speech_plan_history_summary.py` already proves the summary path skips identity work.
- `_bmad-output/scripts/large_project_timing.py:429` `read_matrix` -- add `Operation("revision_active", "GET", f"{session}/generation-plan/revisions/{project.active_revision_id}")` after `revision_non_active`; the MCP tools now cost `plan_status_summary` / `revisions_summary` + `revision_active`. `_bmad-output/scripts/test_large_project_tools.py:350` pins the operation names; add `revision_active` there.

## Tasks & Acceptance

**Execution:**
- [ ] `tests/test_mcp_generation_registration.py` -- update the two `Case` kwargs; add tests with a recording fake application covering every matrix row: call order and kwargs (`summary=True`, paging passed through, detail only for the active id, once), merged counts on the active item only, other items untouched, warnings on `not_found` / `application_response_timeout`, propagation of another code.
- [ ] `pandrator_mcp/clients/application_generation.py` -- `summary` keyword and `get_speech_plan_revision`.
- [ ] `pandrator_mcp/tools/generation.py` -- summary calls and active-revision merge.
- [ ] `pandrator_mcp/registrations/generation.py`, `pandrator_mcp/guides/workflows.md` -- wording.
- [ ] `docs/release-notes-next.md` -- Fixes: MCP plan status and revision history answer quickly on long books, with reuse counts for the active plan.
- [ ] `_bmad-output/scripts/large_project_timing.py` (+ its test) -- `revision_active`.
- [ ] Timing -- fresh copies of `real` and `synthetic` under `%USERPROFILE%\pandrator-perf\work-2-4\`, `large_project_timing.py --timeout 900 --skip-burst --skip-admission`, run one root at a time, then append "After story 4" to `baseline-2-1.md`: summary, `revision_active`, `revision_non_active`, `segments_non_active` and the full variants for reference, on both roots.

**Acceptance Criteria:**
- Given the MCP tools with a fake application, when status or history is requested, then the application is called with `summary=True` and at most one revision detail, for the active revision.
- Given fresh copies of both data roots, when the timing script runs, then `plan_status_summary`, `revisions_summary`, `revision_active`, `revision_non_active` and `segments_non_active` each take 15 s or less (median of 3) on the large project, and the results are in `baseline-2-1.md`.
- Given the focused tests and checks below, when run, then they pass.

## Implementation Notes

- Implemented as planned. `_with_active_reuse_counts` in `pandrator_mcp/tools/generation.py` serves both tools, which now return `ToolOutcome` (same result shape, plus warnings). `get_speech_plan_revision` is new in the MCP client; no tool or input schema changed.
- Timing (`large_project_timing.py --timeout 900 --skip-burst --skip-admission`, fresh copies under `%USERPROFILE%\pandrator-perf\work-2-4\`, uv env, SQLAlchemy 2.0.52), large project, real / synthetic medians: `plan_status_summary` 0.39 / 0.28 s, `revisions_summary` 0.04 / 0.03 s, `revision_active` 6.61 / 6.56 s, `revision_non_active` 6.44 / 6.28 s, `segments_non_active` 2.96 / 3.01 s; full `plan_status` 16.68 / 16.74 s and `revisions` 16.22 / 15.69 s for reference. Recorded in `baseline-2-1.md`, "After story 4".
- The active revision's detail leaves about 2.3x headroom under 15 s; a timeout there degrades to a warning, not a failure.

## Plan Change Log

## Review Triage Log

Pass 1 (quick): 0 high, 0 medium, 7 low, 1 false; 6 patch, 2 rejected.

| # | Finding (quick) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | Status summary also drops repair-batch undo eligibility (`can_undo` etc. null, `undo_checked: false`), unstated | low | patch | `speech_plan_workspace.py:325` passes `include_undo_eligibility=not summary`. No MCP tool undoes a repair batch (`catalog.py` has only recast undo), so nothing in MCP acts on the fields. The intent's "request the summary path" has only this reading, so the fix is to state it in the docstring and guide. |
| 2 | `test_detail_other_failure_propagates` raises `"forbidden"`, not a `FailureCode`, so it passes on a validation crash | low | patch | `errors.py:9` has no `forbidden`; a 403 maps to `scope_denied` (`application.py:510`). Use `scope_denied` and assert the failure JSON's `code`. |
| 3 | No fixture with a null active id and a non-empty page; redundant `!= []` assertion | low | patch | The `isinstance` guard never runs against items; the extra assertion is implied by `len(calls) == 1`. Test-only. |
| 4 | The status tool's `selected_revision_id` fallback is dead code | low | reject | The server sets both to the same value; the plan prescribed the fallback as defensive and it costs one `or`. |
| 5 | List test checks only `rev-3`'s flag, not every untouched item and the top-level flag | low | patch | The status test checks both; the list path does not. Test-only. |
| 6 | `revision_active` placed before `revision_non_active`, not after | false | reject | Operation order changes no measurement; each operation is timed independently. |
| 7 | `read_matrix` docstring still says the full `plan_status` / `revisions` are the MCP tools' requests | low | patch | The MCP tools now send `summary=true` plus the active detail; the full variants stay for reference. |
| 8 | Guide sentence omits that counts appear only when the active revision is on the page, and that a timeout or race leaves it unchecked with a warning | low | patch | `_with_active_reuse_counts` skips the detail when the active item is absent and degrades on `not_found` / `application_response_timeout`. |

Patches 1, 2, 3, 5, 7 and 8 applied (tests, docstring, guide and timing-script docstring only). Reverified with the uv env: the six focused files (106 passed) under SQLAlchemy 2.0.52 and 2.1.8, `_bmad-output/scripts/test_large_project_tools.py` (19 passed), `fast-xdist` (3673 passed, 11 skipped), `mcp-serial` (1427 passed, 4 skipped), ruff, types (only `nemo_normalizer.py:98`), lanes, docs, vulture.

## Design Notes

- Why MCP-side and not a new server mode: the summary and detail endpoints exist, the web UI uses the same pair (`SpeechPlanHistory.svelte:168`, `speech-plan-history-detail.ts`), and the epic forbids changing default responses. A server `audio_reuse=active` parameter would need OpenAPI, generated client and route-count changes for the same result.
- Reuse counts of non-active revisions are no longer reachable through MCP; on the large book they timed out anyway. If an assistant needs them, a revision-detail MCP tool is a separate change (new tool, allowlist and count updates).
- Full HTTP plan status and history stay at about 16 s; nothing in the app requests them by default.

## Verification

**Commands:**
- `<uv env> python -m pytest -q tests/test_mcp_generation_registration.py tests/test_mcp_preview_and_generation.py tests/test_mcp_generation_batch_contract.py tests/test_mcp_server.py tests/test_mcp_architecture.py tests/test_speech_plan_history_summary.py` -- expected: pass
- `<uv env> python scripts/test_lanes.py run fast-xdist` and `run mcp-serial` (holds `test_mcp_generation_registration.py`) -- expected: pass
- `uvx ruff@0.17.3 check .`; `<uv env> --with sqlalchemy==2.0.52 python scripts/check_types.py` (only `nemo_normalizer.py:98`); `<uv env> python scripts/test_lanes.py check`; `<uv env> python scripts/check_docs.py`; `<uv env> vulture` -- expected: clean
- `<uv env> python -m pytest -q _bmad-output/scripts/test_large_project_tools.py` -- expected: pass
- `<uv env> python _bmad-output/scripts/large_project_timing.py --root <fresh copy> --timeout 900 --skip-burst --skip-admission` on `real` and `synthetic` -- expected: exit 0, the five reads at most 15 s

## Code Review

### 2026-11-11

**Code review complete.** 0 `decision-needed`, 8 `patch`, 0 `defer`, 17 rejected (31 raw findings, one rejected in part, from blind-hunter, edge-case-hunter, verification-gap and intent-alignment; no lens failed).

- [x] [Review][Patch] The client's `summary` query parameter and revision-detail URL have no HTTP-level test (medium) [tests/test_mcp_application_client.py]
- [x] [Review][Patch] Release note omits that counts need the active revision listed, that other revisions show none, and that plan status stops reporting repair-batch undo eligibility [docs/release-notes-next.md:9]
- [x] [Review][Patch] Tool descriptions do not explain the top-level `audio_reuse_checked` or the degraded path [pandrator_mcp/registrations/generation.py:141]
- [x] [Review][Patch] Guide does not say where values MCP no longer returns can be found [pandrator_mcp/guides/workflows.md:154]
- [x] [Review][Patch] Guide's "returned page" wording does not fit plan status, which lists the latest 100 entries [pandrator_mcp/guides/workflows.md:155]
- [x] [Review][Patch] Dead `selected_revision_id` fallback in plan status [pandrator_mcp/tools/generation.py:305]
- [x] [Review][Patch] Degraded warning drops the application's error message [pandrator_mcp/tools/generation.py:275]
- [x] [Review][Patch] No test that the detail request carries the tool call's correlation headers [tests/test_mcp_generation_registration.py:255]

Rejected:
- Degrade on `application_unavailable`, `rate_limited` and other retryable codes (two lenses): real, but the frozen intent says any other error propagates.
- Warning codes reuse failure codes: the intent prescribes a warning naming the code.
- Detail without `revision` or reuse keys, and no test for it (two lenses): false; `revision_detail` always returns all five keys or raises 404.
- A timed-out detail keeps the server computing: pre-existing and reduced by this change; stays with story 7, which owns abandoned reads.
- Active revision changes between the two reads (two lenses): false; counts are merged onto the item whose id they belong to.
- Older application without the summary flag or detail route: false; both arrived in bcf75dd3, before v.0.10.0, and a 404 degrades to a warning.
- Detail may still time out on very long books: false; measured 6.61 / 6.56 s, and a timeout degrades as designed.
- Tools' combined latency not recorded (two lenses): false; "After story 4" in `baseline-2-1.md` gives about 7.0 s for status and 6.6 s for history.
- Diff files under misleading paths: false; an artefact of how the review built its diff.
- Full REST rows stay at about 16 s, identity work narrowed by MCP request choice, no opt-in for full counts: settled by the frozen intent (default responses unchanged, no new tool or input).
- Non-active clause rests on earlier work: false; `revision_non_active` and `segments_non_active` are measured in this story's run.
- Response shape now `ToolOutcome`: false as a defect; the result shape is unchanged and warnings ride the existing envelope.

All 8 patches applied: an HTTP-level client test for `summary` and the detail URL (`tests/test_mcp_application_client.py`), a correlation-header test and a warning-message assertion (`tests/test_mcp_generation_registration.py`), the `selected_revision_id` fallback removed and the application's message added to the degraded warning (`pandrator_mcp/tools/generation.py`), and the docstrings, guide and release note reworded. Reverified with the uv env: the seven focused files (144 passed, 1 skipped) under SQLAlchemy 2.0.52 and 2.1.8, ruff, types (only `nemo_normalizer.py:98`), lanes, docs and vulture. Lanes `fast-xdist` and `mcp-serial` were not rerun.

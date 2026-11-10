---
title: 'Access tests match the current role model'
type: chore
ticket: '12'
created: '2026-11-10'
status: built
baseline_revision: '7bf50f216c2d0f3e603b500d1248d2f773e98153'
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

**Problem:** 68 tests in clusters Q4 to Q8 fail on every platform (story 11 already fixed 2 of the 70): 59 fail and 9 error at `7bf50f21`. They lag `68c444a1`, which made project grants read-only ("Project grants are viewing exceptions", `access/project_permissions.py:27-31`), moved seams, and changed several contracts on purpose.

**Approach:** Tests only. A test whose subject is an ordinary user who can edit uses a team `member`, who has content rights, and a loss of authority deletes that member's `WorkspaceMembership`. A test whose subject is the project grant itself now expects the read-only refusal. Mocks follow the moved seams, and expectations follow the deliberate contract changes the investigation proved.

## Boundaries & Constraints

**Always:** Tests only; no product, migration, lane, CI or dependency change. Keep what each test protects: a revocation must still revoke the authority the subject actually uses; a retargeted mock must be on the path; viewer and outsider cases still get refused. Product code runs at head.

**Never:** Give the project-only `editor` workspace rights in the shared fixture (`tests/test_job_access_routes.py`). That would erase the read-only grant the product now enforces. Never delete an assertion without putting the check it made somewhere else or recording why the product no longer has that check.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Edit-capable user | team `member` prepares, runs or exports | Admitted, as the test expects | None |
| Project-grant editor | `editor` with only a `ProjectMembership` | Read and observe pass; prepare, run and export give 403 or `can_prepare` False | None |
| Mid-run loss | member's `WorkspaceMembership` deleted | 404 for later requests; worker publishes nothing | None |
| Deleted token grant | token's project grant removed | 404, as for membership loss (hides existence) | None |
| NULL job workspace | `jobs.workspace_id` set to NULL at head | Database refuses it (NOT NULL from 0075) | `IntegrityError` asserted |

</frozen-after-approval>

## Code Map

Proven by four investigators running each change (SQLAlchemy 2.0.52, Windows). The ready patches are in `C:/Users/Valtor/AppData/Local/Temp/claude/8c61746121f841cf/6bcc8172-ab1f-454e-838f-4e2d27d9e90c/scratchpad/`; `agent_b.patch` there is a mixed snapshot and must not be used.

- Q6, project-only editor (`mpe.patch`, `tap.patch`, `phm.patch`, `mep.patch`; the job-route files are already edited in the tree): `test_media_preview_execution`, `test_artifact_preview_admission`, `test_preview_http_methods` (adds an `("editor", None, False)` reader row), `test_media_execution_policy` (also turns about 10 passing but vacuous `AccessDenied` tests into real checks; leave the editor's own personal-workspace cases alone), `test_authorized_job_routes`, `test_job_command_admission` (project grant now read-only; mint the token with `CredentialResources(project_ids=…)` only, since `credential_resources.py:190-209` refuses workspace resources to a non-member), `test_job_run_admission` export tests.
- Q7: `test_job_run_admission` — a reference-free preview now carries `speech_input=None` (`service_routes.py:940-955`), so `prepare_audio_cpp_voice_reference` is skipped (`workflow_handlers.py:752`); revoke from the `_ensure_qwen_cloned_voice` mock instead. The deleted grant gives 404, as `test_preview_denies_before_receipt_or_settings[grant]` already expects. `test_audio_assembly_access_callback` patches `audio_assembly.load_local_audio_segment` (`audio_assembly.py:681`).
- Q4/Q5 (`jobworkspace_fixes.patch`, CRLF): drop the gone `voice_creation_workspace` patch. Teams get an owner membership for the bootstrap user, and the design route takes `?workspace_id=`. Training runs under `execution_actor_scope(HostActor(...))` because `training.xtts` is privileged. The receipt now carries `workspace_id` (`google_voice_design_routes.py:208`), and teams get a `services.tts` `WorkspaceSetting`. The sound-cache mutation runs under `suspended_triggers`. The media previews use POST (`artifact_routes.py:588-600`). RVC artifacts go in the personal workspace, because `local_tools_policy.py:93-111` uses only the owner's personal one; that is the cause of the 404 the triage couldn't explain, and it is by design.
- Q8 (`agent_b_tests.patch`): sound import now forwards `workspace_id`; the RVC upload stub targets `local_tools_policy.require_execution_actor_in_session`; a `google_voice_scope` stub takes `**kwargs`; bundle import fails inside `register_in_session`, rolled back in one transaction (`bundles.py:736`).

## Tasks & Acceptance

**Execution:**
- [ ] The 14 test files -- apply the patches above (`git apply`), keeping the job-route edits already in the tree -- the investigators' proven fixes.
- [ ] `tests/test_job_library_workspace.py` -- keep the `unscoped` parameter as a check that the database refuses a NULL `workspace_id`, instead of dropping it -- the sound cache can't see that state at head, and the test count stays at 70.
- [ ] `tests/test_administrator_model_jobs.py` -- the denial test asserts that only `pth_artifact_id` and `index_artifact_id` are read, and that nothing resolves before `AdministratorRequired` -- confirms the new read order while keeping "nothing else is read".
- [ ] `tests/test_web_contract_boundaries.py` -- rename the bundle test to say it checks rollback when registration fails -- the product no longer has a missing-artifact check.
- [ ] Rename tests whose names now misdescribe them (e.g. `test_leased_project_editor_and_actual_workspace_owner_can_read`).
- [ ] `findings-ci-failure-classification.md` -- add "Resolved by story 12" with each read-only verdict's outcome and the three ticket notes settled.

**Acceptance Criteria:**
- Given SQLAlchemy 2.0.52 and then 2.1.8 on Windows, when the 14 files run, then none fails, and no test in them that passed at the baseline now fails.
- Given the viewer, outsider and project-grant editor cases, when they run, then each still shows refusal.
- Given a representative revocation test (`test_postmedia_authority_loss…[membership-*]`, `test_bundle_waiting_for_writer_rechecks_membership`), when the product's recheck is removed temporarily, then it fails; after the product is restored, it passes.

## Design Notes

The `[reference]` preview revocation now ends with `error_code` `RuntimeError`, not `AccessDenied`. `preview_tts_voice` wraps every exception raised inside its I/O block (`workflow_handlers.py:782-788`), and the denial now lands inside that block. The message is the same redacted denial, nothing synthesizes, and `error_code` is only the exception's class name: nothing branches on it (`jobs.py:1294-1297`). It is recorded as an observation, not filed as a bug. The test asserts the protection, and asserts the code that each stage reports.

## Verification

**Commands:** (`<uv env>` from `AGENTS.md`)
- `<uv env> --with sqlalchemy==2.0.52 python -m pytest -q -n 4 <the 14 files>` -- expected: no failures.
- Same with `--with sqlalchemy==2.1.8` -- expected: the same.
- `uvx ruff@0.17.3 check tests` and `<uv env> python scripts/test_lanes.py check` -- expected: clean.

**Manual checks:**
- Fork CI on a pushed commit (the owner's go-ahead) -- expected: none of these 70 fail on Ubuntu or Windows.

## Implementation Notes

- Patches applied as listed. `test_administrator_model_jobs` already asserted exactly the two id reads. Renamed: `test_member_cache_miss_enqueues…`, `test_member_can_post_and_observe…`, `test_domain_bundle_accepts_ordinary_member…`, `test_leased_team_member_and_actual_workspace_owner_can_read`, `test_bundle_import_rolls_back_in_one_transaction_when_registration_fails`, `test_project_only_token_grant_is_read_only_and_revocation_hides_the_session`.
- The 14 files give 307 passed under 2.0.52 and under 2.1.8 (baseline: 233 passed, 59 failed, 9 errors, plus 3 new `editor` refusal rows in `test_preview_http_methods`). Ruff and `test_lanes.py check` are clean.
- Matrix: edit-capable `member` (all the `member` tests); project-grant editor refused (`READERS` editor row, `test_domain_bundle_accepts_ordinary_member…`, `test_export_run_checks_roles…`); mid-run loss (`test_postmedia_authority_loss…[membership-*]`, writer-wait); deleted grant 404 (`test_revocation_before_worker…[grant]`); NULL workspace refused (`test_sound_cache_skips…[*-unscoped]`).
- Mutation check (AC 3) results are in the findings' "Resolved by story 12". The bundle has two in-transaction rechecks, and the writer-wait test fails only when both are removed.

## Plan Change Log

## Review Triage Log

Pass 1, quick lens: 0 high, 3 medium, 3 low, 0 false, 0 maybe-false. All routed to patch; nothing deferred.

| Finding | Verdict | Route | Evidence and action |
|---|---|---|---|
| The `app.read`-only scope cases use the project-only `editor`, who is now refused by role, so a missing `app.run` check would go unseen (`test_preview_http_methods` READERS row and POST test, `test_artifact_preview_admission:273`) | medium | patch | True: the new `("editor", None, False)` row shows the editor is refused with full scopes. The subject becomes `member` with `app.read`. |
| `unscoped` `IntegrityError` comes from the jobs `BEFORE UPDATE OF workspace_id` triggers, not NOT NULL | medium | patch | True: 0078:42 and 0079:695 `RAISE(ABORT)` before the NOT NULL check. The update runs under `suspended_triggers` and matches the NOT NULL message. |
| `BoundedPayload` misses the `"workspace_id" in payload` probe, so the "only two keys" claim and the findings note are wrong | medium | patch | True: `workflow_voice.py:1300-1301` runs before `require_local_tool_inputs`. The stub records `__contains__`/`__getitem__`, and the findings note is corrected. |
| `test_raw_binary_voice_reference_query_and_sound_scope_rejection` is misnamed | low | patch | True: the body asserts forwarding. Renamed. |
| The forwarded-after-read test doesn't check the read or its order | low | patch | True: the stub was a bare lambda. It now records the order. |
| `[reference]` id names a stage that no longer runs | low | patch | True: the revocation fires in the `_ensure_qwen_cloned_voice` mock. Renamed. |

All 6 patches applied on 2026-11-10 (`[reference]` is now `[cloned_voice]`). The 14 files give 307 passed under SQLAlchemy 2.0.52 and again under 2.1.8; ruff and `test_lanes.py check` are clean; no product file changed.

## Code Review

### 2026-11-10

**Code review complete.** 0 `decision-needed`, 12 `patch`, 0 `defer`, 10 rejected. Lenses: blind hunter, edge case hunter, verification gap, intent alignment; none failed.

- [x] [Review][Patch] Project-grant admission tests prove only refusal and credit it to the credential grant; the refusal comes from the editor's `ProjectMembership` role. Add a team `member` holding a project-only token or client grant who can run in that project, and reword the comments [tests/test_job_command_admission.py:261]
- [x] [Review][Patch] No revocation fence before the cloned-voice step on the legacy (no `speech_input`) preview path [tests/test_job_run_admission.py:651]
- [x] [Review][Patch] Export hidden-source 404s come from the token's team ceiling, not membership visibility [tests/test_job_run_admission.py:497]
- [x] [Review][Patch] RVC admission shows no refusal for team-workspace inputs [tests/test_job_media_workspace.py:209]
- [x] [Review][Patch] Cache-miss preview POST lacks a project-only editor refusal row [tests/test_artifact_preview_admission.py:271]
- [x] [Review][Patch] Admin RVC denial test name misdescribes what it pins [tests/test_administrator_model_jobs.py:280]
- [x] [Review][Patch] `BoundedPayload` has a dead `__getitem__` branch and misses iteration reads [tests/test_administrator_model_jobs.py:288]
- [x] [Review][Patch] Bundle rollback test injects a product-sounding error message [tests/test_web_contract_boundaries.py:252]
- [x] [Review][Patch] Ordinary preview test does not assert the stored `speech_input` is None [tests/test_job_run_admission.py:151]
- [x] [Review][Patch] Sound-target invalid `workspace_id` and the full forwarded request are untested [tests/test_mcp_voice_normalization_workspace.py:542]
- [x] [Review][Patch] Variables named `editor` hold `member` credentials [tests/test_artifact_preview_admission.py:105]
- [x] [Review][Patch] Three added lines exceed 100 columns [tests/test_administrator_model_jobs.py:69]


All 12 patches applied on 2026-11-10. The 14 files give 313 passed under SQLAlchemy 2.0.52 and again under 2.1.8 (307 before, plus the member token and client rows, the `legacy_reference` revocation case and three project-only editor cache-miss rows). Ruff on `tests` and `test_lanes.py check` are clean. No product file changed.

Rejected:
- low: the host-provenance test no longer checks the database passed to authorization. The session is created inside the product, and checking its bind adds machinery for a gap that is rarely visible.
- low: the single-artifact bundle can't tell one-transaction rollback from per-item cleanup. The session row and the registered artifact row come from the same unit, and both are asserted gone.
- low (two reports): `error_code` `RuntimeError` is pinned for a denial. Nothing branches on `error_code == "AccessDenied"`, a fix would be a product change outside this tests-only story, and the plan records it as an observation.
- low (two reports): the `unscoped` schema check sits inside the sound-cache test. The plan keeps it there on purpose.
- false: "use the `host_execution_actor` fixture". `conftest.py` accepts either form, and the fixture would put the whole test under host scope.
- low: the duplicated bootstrap enrolment in two fixtures. A shared helper adds surface for two uses.
- false: "proof first shown only by green tests". The findings record code citations, the mutation check and each verdict.
- false: "the 70 exist only as renamed successors". The renames are plan tasks.

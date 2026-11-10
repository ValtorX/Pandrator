---
title: 'Fork main carries the lease fix as a tagged fork version'
type: chore
ticket: '1'
created: '2026-11-08'
status: done
baseline_revision: '415aaa8a7d691f3fbb03c22eb5ca0a57ed5ea8b9'
route: oneshot
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-windows-and-install.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Fork `main` is level with upstream at `112501b2` and lacks the lease fix `415aaa8a`, and nothing identifies a build as the fork's. Story 2 needs a tagged fork version to install, and Epic 2's cancel story builds on it.

**Approach:** Fast-forward fork `main` to `415aaa8a`, set a fork version in `pyproject.toml`, leave `web/package.json` at upstream's version, and tag the result. `web/package.json` stays put so the browser assets need no rebuild.

**Verify (from the ticket):** From a clean clone of the tag, the app reports the fork version, the lease fix's tests and `tests/test_version.py` pass, and fork CI on main shows no new failures.

**Decisions (owner, 2026-11-08):**

- The version reaches the API contract: `pandrator/web/identity.py:62` uses it as a schema default, so it lands in `openapi.json` and `web/src/lib/api.generated.ts`, and regenerating those changes 96 static paths. Keep the contract and assets unchanged by giving the schema default the public version (no `+` segment), while the running app reports the full fork version.
- Fork Actions is not enabled yet (0 workflows, 0 runs). The owner enables it before the push. CI on `main` is compared with upstream's runs at `112501b2`, where only `Web migration preview` failed.
- Go-ahead given for the permanent steps: fast-forward `main` to `415aaa8a`, commit version `0.14.2+fork.1`, tag `v.0.14.2+fork.1`, and push `main` and the tag to `origin`, only after verification from a clean clone of the tag passes. Nothing goes to upstream.

</frozen-after-approval>

## Implementation Notes

Oneshot route: under 100 lines of changes, plus release steps.

Investigation facts for implementation:

- `main`, `origin/main` and `upstream/main` are all `112501b2`, so `415aaa8a` fast-forwards. The version work lands only on `main`, so `fix-generation-check-write-lock` stays clean for an upstream PR.
- `pixi.lock` records no version for `pypi: ./`, so it does not change. `pandrator_mcp/compatibility.py:54` matches only a version prefix, so `+fork.1` passes.
- The generator writes CRLF on Windows (`write_text` in text mode), so in an `autocrlf=false` clone `openapi.json` shows as fully changed. Compare with `git diff --ignore-cr-at-eol`, or check from the normal checkout.

Results at `ebe7194d1dc35157ff6b576a22a5b13b3e89fdce` (tag `v.0.14.2+fork.1`, pushed to `origin`):

- Commits: `4c12bc41` (contract) and `ebe7194d` (version). `fix-generation-check-write-lock` stays at `415aaa8a` for an upstream PR.
- Clean LF clone of the tag, uv environment, Windows, Node 22: version and `/api/v1/health` (Flask test client, not a running server) report `0.14.2+fork.1`; `tests/test_version.py` and the 6 lease-fix tests pass; contract has no content change; `check:assets` matches `0.14.2-3ea216416b35`; a rebuild changes 0 static paths; `quality` and ruff pass; type check (run from the working tree before the tag) shows only `nemo_normalizer.py:98`.
- Fast lane: 10 failed, 3624 passed, 3 skipped. All 10 are on the findings' pre-existing list.
- `test_generation_ignores_saved_preview_routing_metadata` fails the same way at `112501b2`, and is not a lease-fix test.
- Fork CI: 8 of 9 runs green. `Web migration preview` failed, with 26 failed and 2 cancelled jobs, all of which also failed or were cancelled at upstream's `112501b2` (27 failed, 2 cancelled). The comparison is by job only: logs and JUnit artifacts need a GitHub sign-in, so a new test failure inside an already failing job is not ruled out.
- The lease-fix tests run in `web-07-serial`, whose jobs were cancelled on both sides at their job time limits (35 minutes on Ubuntu, 45 on Windows), so CI gave no verdict on them.

Test-level CI comparison, added after the owner signed in to GitHub (56 job logs read): supersedes the job-only limit above. All 26 finished failing job pairs have identical failure names and counts, so no test fails on the fork that does not fail at upstream's `112501b2`. Fast-lane passes are 4 higher than upstream on each OS (Ubuntu 3629 against 3625, Windows 3626 against 3622), matching the 4 tests added to `tests/test_version.py`. The cancelled `web-07` lanes can be compared only by progress: 39 failures at 24% on Ubuntu on both sides, and 46 on Windows on both sides (fork at 29%, upstream at 25%). Their logs stop before any summary on three of the four, so the lease-fix tests still have no CI verdict. Failures in CI far exceed the fast-lane list in the findings, which story work on R8 should expect.

For the merge procedure (story 13): an upstream version bump conflicts on `pyproject.toml`'s version line, and `test_fork_build_identifies_itself` fails if the `+fork.N` segment is lost. Regenerated upstream `openapi.json` should keep the public version.

## Verification

**Commands (uv environment from `AGENTS.md`, from a clean clone of the tag):**

- `python -c "from pandrator.version import PANDRATOR_VERSION; print(PANDRATOR_VERSION)"` -- expected: `0.14.2+fork.1`
- `python -m pytest -q tests/test_version.py tests/test_generation_workspace_execution.py` -- expected: pass
- `python scripts/generate_web_contract.py`, then `npm --prefix web run generate-api`, then `git diff --ignore-cr-at-eol --exit-code -- openapi.json web/src/lib/api.generated.ts` -- expected: no difference
- `npm --prefix web run check:assets` -- expected: `0.14.2-3ea216416b35`, unchanged
- `uvx ruff@0.17.3 check .` -- expected: pass
- Type check with `sqlalchemy==2.0.52` -- expected: only the known `nemo_normalizer.py:98`
- `python scripts/test_lanes.py run fast-xdist` with `PYTEST_ADDOPTS=--basetemp=<short path>` -- expected: only the 14 failures known from the findings

**Manual checks:**

- `GET /api/v1/health` on the running clone reports `0.14.2+fork.1`.
- Fork CI on `main` against upstream's runs at `112501b2`, after the owner enables Actions and the push runs.

## Review Triage Log

Quick review, one pass: 3 low, 1 false.

- Low, patched: nothing pinned the fork version, so a merge resolved to a plain version would pass unnoticed. Added `test_fork_build_identifies_itself`.
- Low, patched: this plan's CI check was conditional and cited a removed question. Reworded.
- Low, rejected: no `docs/release-notes-next.md` line for the fork version. The footer and health endpoint do show `0.14.2+fork.1`, but upstream rewrites that file each release, so a fork-only line would conflict at every merge. Reported to the owner instead.
- False: release steps outstanding. The plan orders them after verification, which had not been reached.

## Code Review

### 2026-11-10

**Code review complete.** 0 `decision-needed`, 2 `patch`, 0 `defer`, 15 rejected. Full diff `415aaa8a..ebe7194d` (the tag `v.0.14.2+fork.1`), lenses: blind hunter, edge case hunter, verification gap, intent alignment; none failed. Both patches are tests only, so they land on `epic-fork-releases` and the tag stays as released.

- [x] [Review][Patch] The live identity route's full fork version is not pinned; only the model default is, so serving the public version would pass every test [tests/test_web_work_api.py:711]
- [x] [Review][Patch] MCP compatibility negotiation is never fed a `+fork` version, so a stricter version parse would silently skip the 0.8.16 baseline check for fork builds [tests/test_mcp_doctor.py]

Applied and verified, Windows, `<uv env>`: `test_web_work_api.py`, `test_mcp_doctor.py` and `test_version.py` give 27 passed under SQLAlchemy 2.0.52 and again under 2.1.8; `uvx ruff@0.17.3 check tests` passes. Each new check fails under the regression it guards: a full-match version regex fails both MCP tests, and an identity built with the public version fails the route test.

**Lease-fix tests on CI.** The results above left them without a CI verdict, because `web-07` was cancelled at its time limit. Story 4 moved them to `web-23-serial`. Fork run `42851994115` at `b6de4a35`, whose product code is identical to the tag's, runs all six on Ubuntu and Windows and none fails. The file's only failure there is `test_generation_ignores_saved_preview_routing_metadata`, which also fails at `112501b2` and is `backlog/bug-saved-preview-settings-fail-generation`.

Rejected:

- The contract's schema default (`0.14.2`) differs from the served version (blind, intent): false. The owner's frozen decision chose this split to keep the contract and assets unchanged.
- `public_version` turns `0+unknown` into `0` (blind, edge): false. The fallback needs no `pyproject.toml` and no installed metadata; the contract generator always runs from a checkout, and the runtime keeps the full value.
- The browser build still reports `0.14.2`: false. The frozen intent keeps `web/package.json` at upstream's version.
- No release note: low. Rejected in the first review and reported to the owner, since upstream rewrites that file each release.
- A version bump invalidates in-flight plans and previews: low. Existing behaviour for any version change.
- The fork-only assertion sits in a shared test file (blind, edge): false. It lands in `ebe7194d`, a fork-only commit, and catching a merge that drops `+fork.N` is its purpose.
- `test_version.py` imports the web stack: low, speed only.
- The contract test overlaps `check-web-contract`: low. That check is the regenerating gate in CI.
- `openapi.json` missing outside a checkout: false. Tests run only from a checkout.
- A client could fill a missing `application_version` from the schema default: false. The server always serializes the field, and no client reads the default.
- The diff touches more than `pyproject.toml`: false. The owner's decision required the contract split.
- The fork marker is absent from the contract and browser surfaces: false. Deliberate, per the frozen decisions.
- The clean clone and CI on `main` are not in the diff: false. Both are recorded in the results above.

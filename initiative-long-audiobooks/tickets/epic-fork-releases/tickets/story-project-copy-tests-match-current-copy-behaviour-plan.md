---
title: 'Project copy tests match current copy behaviour'
type: chore
ticket: '9'
created: '2026-11-10'
status: done
baseline_revision: '0a36fe8f0fa07887e1784d321162153d35ad4ef7'
route: oneshot
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

**Problem:** 30 tests in 9 project copy test files fail on every platform (clusters B1 to B9 of the failure classification) because their fixtures, literals and budgets lag the product: copy state version 15, required destination model choices, two-pass capture in the shared helper, voiceover becoming copyable, deliberate tolerance of null pins and non-current segment slots, Manager-only purge, and the read-only `timing_flags` on media-edit cues.

**Approach:** Tests only, one targeted edit per cluster, keeping each test's protection: tripwire literals move to 15 rather than disappearing, counted captures stay single-pass so call and SQL budgets keep their current numbers, and tests that expected blocking now assert the tolerated case is tolerated, plus a still-blocking case where one exists.

**Verify (from the ticket):** All 30 tests pass on Windows and on fork CI, including `test_project_copy_flow`, which holds the contract these tests lagged.

**Decisions:**

- B4 and B6 were run before planning, at `0a36fe8f`: B4 fails with `copy_graph_invalid` for version 5, B6 with `4 <= 3`, both as the triage read them.
- No product, migration or helper-semantics change; `capture_with_connections` keeps capturing twice when no choices are given.

</frozen-after-approval>

## Implementation Notes

Oneshot: about 45 test lines across 9 test files and one support helper, each change mechanical and located by the triage and a traceback run at the baseline (30 failed, 217 passed in those 9 files).

Per cluster, under `tests/`:

- **B1** `test_project_copy_evidence.py:288` `_capture_for` -- call `capture_with_connections(case, session, ...)` with `**kwargs` passed through. The SQL-counted capture at `:621` passes `destination_connections=snapshot_destination_connections(unreferenced)` so it captures once and `len(evidence_sql) == 1` stays. `test_fingerprinted_snapshot_with_unprojected_evidence_fails_before_insert` (`:788`) fails in `_stage` with `copy_graph_invalid`; confirm it passes with the helper, otherwise find its cause.
- **B2** `project_copy_test_support.py:162` -- add `"performance_plans"` (`PERFORMANCE_FAMILIES`) to the popped families.
- **B3** `test_copy_connection_contracts.py:241-255` -- parametrise 1 to 15, treat 6 to 15 as carrying `destination_connections` (here and in `state()` at `:83`, matching `copy_connections.py:486`), and pin `COPY_STATE_VERSION == 15`. `test_project_copy_knowledge.py:175` pins 15 and sets `len(state["rows"])` to the measured current count. `test_project_output_copy.py:696` pins 15.
- **B4** `test_project_copy_knowledge.py:683` -- use `99` instead of `5`.
- **B5** `test_project_copy_snapshot.py:692` -- use `"audiobook"`.
- **B6** `test_project_copy_snapshot.py:780` -- take one capture outside the patch, then the counted capture with `destination_connections=snapshot_destination_connections(first)`; keep `<= 3`.
- **B7** `test_project_cast_copy.py:322-330` -- the orphan cast slot still blocks; the `segment:` slot of a non-current segment must leave the capture ready with the marker absent from `state_json`. `test_project_speech_plan_copy.py:437` -- delete the reference row instead of nulling its pin, so the cast is unretained (null pins are kept on purpose, `copy_snapshot.py:1408,1449`).
- **B8** `test_project_sound_copy.py:182` -- assert Bob's purge of `target` is refused with 403, then purge with the destination workspace's Manager (`access/purge_policy.py:23`).
- **B9** `test_project_copy_media.py:239,270` -- compare cues with `timing_flags` removed from the media-edit read (`media_edit.py:605-613`); stored cues are unchanged.
- Findings: add "Resolved by story 9" to `findings-ci-failure-classification.md` on `fork-files`, covering B1 to B9 and the B4 and B6 runs.

Built:

- Implemented directly as listed above, in 10 files (+51, -22). Edits kept each file's CRLF working-tree endings; `git diff` shows only the changed lines.
- B3: the knowledge row count is 31 at version 15, measured: 27 plus `SOUND_FAMILIES` (3) and `performance_plans`.
- B7 cast: the non-current `segment:` slot now asserts ready, no unsupported count and no marker; the orphan cast slot still blocks with a count of 1. Both passed as written.
- B1's loose match, `test_fingerprinted_snapshot_with_unprojected_evidence_fails_before_insert`, passes with the helper change alone.
- B8: Bob's `purge-preview` returns 403; `u.owner` (the team's creator, from `tests/test_evidence_access_routes.py:85` `team`) trashes and purges.
- Results on Windows, `<uv env>`, Python 3.11, `PYTEST_ADDOPTS` unset, `-n 6`: the 9 files plus `test_project_copy_flow` and `test_project_copy_publication` give 283 passed under SQLAlchemy 2.0.52 and again under 2.1.8 (baseline: the 30 failed). The 9 other files importing the changed helpers give 192 passed and 1 failed; that one, `test_project_copy_media_flow`, is W1's path length (xdist's `popen-gwN` folder) and passes serially, as its lane runs it. `test_lanes.py check` and `ruff check tests` pass. FFmpeg wasn't checked.
- Fork CI isn't run: it needs a push the owner approves.
- **The ticket's Verify is met on fork run `42812800328`, at `20b9ee08bb597ec097742990069e64cd9e857272`.** `web-07-serial`, which holds all 10 copy files, gives 880 passed and none failed on Ubuntu (14.5 minutes) and on Windows (21.3 minutes), with SQLAlchemy 2.1.8 installed from ranges. 880 is story 4's 875 plus the 5 added versions in the binding test. The run's conclusion is `failure`: 25 other Python lanes, holding other stories' known clusters, and 2 E2E jobs failed; this story touched none of their files.
- After review: B8 keeps Bob trashing the copy, adds a `_trash` helper, and asserts his purge POST is refused with 403, using the owner's valid impact token, as well as his preview. B4 uses `COPY_STATE_VERSION + 1`. Rerun: 283 passed under 2.0.52 and 2.1.8; ruff passes. The diff is now 10 files, +64 and -24. The "Resolved by story 9" section is in the findings file, uncommitted on `fork-files`.

## Plan Change Log

## Review Triage Log

Pass 1, quick lens: 0 high, 1 medium, 2 low, 1 false, 0 maybe-false. Nothing deferred.

| Finding | Verdict | Route | Evidence and action |
|---|---|---|---|
| B8 checks only the purge preview, not the purge POST, and Bob no longer trashes the copy | medium | patch | Confirmed: the POST has its own `require_session_action(..., PURGE)` guard (`session_purge_routes.py:31-33`). Bob now trashes the copy, and his POST with the owner's valid token is asserted to return 403. |
| "Resolved by story 9" missing from the findings file | low | patch | It was planned for after the review; now written. |
| B4's `99` can go stale the way `5` did | low | patch | One-token correction: `COPY_STATE_VERSION + 1`. |
| Fork CI half of the ticket's Verify not met | false | reject | Not a defect in the change: it needs a push the owner approves, and is recorded as pending. |

## Verification

`<uv env>` from `AGENTS.md` with `--with sqlalchemy==2.0.52`, then again with `--with sqlalchemy==2.1.8` (CI installs from ranges); `pixi` isn't installed. Unset `PYTEST_ADDOPTS`.

**Commands:**
- `<uv env> python -m pytest -q tests/test_project_copy_evidence.py tests/test_project_copy_knowledge.py tests/test_copy_connection_contracts.py tests/test_project_output_copy.py tests/test_project_copy_snapshot.py tests/test_project_cast_copy.py tests/test_project_speech_plan_copy.py tests/test_project_sound_copy.py tests/test_project_copy_media.py tests/test_project_copy_flow.py` -- expected: all pass, under both SQLAlchemy versions.
- `<uv env> python -m pytest -q tests/test_project_copy_publication.py` and any other file importing `prepare_legacy_copy_state` or the changed helpers -- expected: no new failures.
- `<uv env> python scripts/test_lanes.py check` and `uvx ruff@0.17.3 check tests` -- expected: pass.

**Manual checks:**
- Fork CI needs a push the owner approves; the owner confirms the 30 pass there.

## Code Review

### 2026-11-10

Full review of `0a36fe8f..20b9ee08` with four lenses (blind hunter, edge case hunter, verification gap, intent alignment); none failed, and the verification-gap lens found no gaps. **Code review complete.** 0 `decision-needed`, 3 `patch`, 0 `defer`, 16 rejected.

- [x] [Review][Patch] The cast test's comment called a segment slot naming no segment "non-current revision history", and its `blocked` variable held a ready capture. `copy_snapshot.py:372-375` omits every `segment:%` slot. The comment now says so, and the variable is `captured`. [tests/test_project_cast_copy.py:320]
- [x] [Review][Patch] The chunking test took binding choices from a capture it never checked was ready; it now asserts readiness first, as the evidence test does. [tests/test_project_copy_snapshot.py:774]
- [x] [Review][Patch] The `prepare_legacy_copy_state` docstring named only the v6 bindings; it now also names the dropped row families. [tests/project_copy_test_support.py:161]

After the patches, on Windows through the `<uv env>`: the 9 files plus `test_project_copy_flow` and `test_project_copy_publication` give 283 passed under SQLAlchemy 2.0.52. The three changed test files give 85 passed under 2.1.8. Ruff passes on the changed files.

Rejected:

- Derive the binding test's versions from `SUPPORTED_COPY_STATE_VERSIONS` (false): the literal list is the frozen-contract guard, and the `== 15` tripwire forces a revisit at the next bump. Replacing the count of 31 with a key set (low) would add bulk for a rarely seen message.
- `state()` uses `>= 6` while the product uses a closed set (false): they agree for versions 1 to 15. The durable codec rejects other versions first.
- B4 lost a supported-older-version case (false): the test always expected `copy_input_invalid`, and 5 failed with `copy_graph_invalid`.
- Version-blind family stripping in `prepare_legacy_copy_state` (false): every caller targets version 1, 2 or 3.
- Speech-plan test lost null-pin coverage (false): `test_project_cast_copy.py:340-354` asserts a null pin is retained.
- Purge test gaps (false):
  - Role coverage is in `test_purge_workspace_http.py:104-146`.
  - The repository's purge tests assert the status code only, and Bob's requests can only be refused on permission.
  - The owner's purge requires the trashed copy.
  - `_purge` does use `f`.
- `timing_flags` could vanish in a copy (false): they are computed on read (`media_edit.py:604-618`), never stored.
- The speech-plan test might delete a non-cast slot (false): the test's own `generation_plans` blocker only occurs when a cast slot is removed.
- `_stored_cues` raises on non-mapping cues (false): this fixture's cues are all mappings.
- Intent lens (false, descriptive):
  - The Verify outcome lives outside the diff; it is recorded here and in the findings file.
  - Bound single-pass budgets are the frozen intent's decision.
  - The 5 added version IDs are intended coverage.
  - The B7 and B8 assertions follow the Approach.
  - B4 must name an unsupported version, so it cannot be a literal.
  - The R8 adjudication is in the classification.

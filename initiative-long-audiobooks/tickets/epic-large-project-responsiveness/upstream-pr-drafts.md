# Upstream PR drafts

PR bodies for this epic's finished stories, on branches built 2026-11-11 from upstream `main` at
`fb72c018` (v.0.14.3) in the worktree `C:\Users\Valtor\Source\Pandrator-pr`. Each branch picks the
epic's commits with `git cherry-pick -n`, leaves out the `docs/release-notes-next.md` hunk (the
maintainer writes it; each body carries a suggested note instead) and passes
`_bmad-output/scripts/check_pr_branch.py` (exit 0). Pushed and opened 2026-11-11 on the owner's
go-ahead as upstream #126 (identity), #127 (MCP) and #128 (listing, on top of #126); the bodies
below show `#IDENTITY` where the opened ones say #126. The maintainer merged all three as written, with no comments, on 2026-11-12 (`14fb5d0c`, `bc356959`, `87afec8e` on upstream `main`); their branches are deleted. Upstream also merged its own #130, which regenerates the stale 0.14.3 contract.

| Order | Branch | Head | Picked from | Size against `upstream/main` |
|---|---|---|---|---|
| 1 | `identity-pass-builds-shared-work-once` | `89abef28` | `7f2b631d`, `c5c0dcfb`, `24c1dd36`, then review fixes `dec600c5`, `bf246115`, `bfd43a5e`, `89abef28` (fork `b2300cc1`, `cffc0728`, `dd462b8f`, `349d9073`) | sizes: see the PRs |
| 2 | `mcp-plan-reads-use-revision-summaries` | `970bbaf9` | `35e82ff9`, then review fixes `a2d978f2`, `970bbaf9` (fork `4d6559a0`, `8051c406`) | sizes: see the PRs |
| 3 | `segment-listing-cost-follows-the-page` | `b169fec1` | branch 1, then `bd700684`, `51848d06` (rebased), then `a3328a21` (rebased as `b169fec1`; fork `b807b4cd`) | sizes: see the PRs |

Differences from the epic's commits, all deliberate:

- `scripts/test_lanes.py`: the new test file joins upstream's `web-07-04-serial`, since upstream
  split the fork's `web-07` lane.
- `tests/test_audio_identity_cost.py`: the trailing blank line is dropped (in `bade0a9a`).
- `e71c7d06`'s message no longer mentions the release note it no longer carries.
- No fork-only test helpers come along: upstream's `test_mcp_application_client.py` keeps its plain
  `symlink_to`, where the fork uses `symlink_or_skip`.

Open them in this order, filling in the identity PR's number in the third body. Commands, as used
for #121 to #123:

```bash
git -C ../Pandrator-pr push origin <branch>
gh pr create --repo lukaszliniewicz/Pandrator --base main --head ValtorX:<branch> --title "<title>" --body-file <body>
```

Timings come from `baseline-2-1.md` in this folder: a copy of the owner's real 5,167-segment cast
book (a synthetic book seeded to its shape agrees within 2-18%), medians of three runs, measured on
the fork's 0.14.2-based tree before the 0.14.3 merge. 0.14.3's own identity change (#125) works on
the SQL side, which was under 1% of these times, so they were not measured again.

---

## 1. `identity-pass-builds-shared-work-once`

**Title:** Build the speech catalogue once per identity pass on cast plans

**Body:**

### Summary
On a long book with casting on, everything that works out audio identities takes minutes: segment listings, speech plan status and revision history, a non-active revision, generation admission and the opening check. On a 5,167-segment book a segment listing took 257-281 s, plan status and revision history ran past 900 s, admission took about 700 s and the opening check about 400 s. Clients such as the MCP server give up after 15 s while the server keeps working; after one minute of four clients abandoning listings, the server answered nothing, `auth/status` included, for about an hour.

### Reproduction
1. Take a plan with casting on: a narrator and characters bound to retained voice references (audio.cpp voices here), and speaker parts in the dialogue. A few thousand segments show it clearly.
2. List the plan's segments, or read its plan status.

`tests/test_audio_identity_cost.py` counts the work on a 12-segment cast plan: 11 catalogue builds and 271 statements before, 1 build and 75 statements after.

### Root cause
- Each cast span's binding resolved its model capabilities without the request's catalogue cache, so every span rebuilt and deep-copied the whole TTS service catalogue (`tts_service_catalogue._shared_service_configs`). Under cProfile that was 86-97% of every slow operation; SQL was under 1%.
- Each retained cast pin was proven against the database again for every span.
- With those gone, the next costs were per segment too: performance compilation looked up each voice's supported languages (`resolve_tts_language_support` through `audio_cpp_catalogue.package_metadata`) for every segment, and the identity pass and the cast freeze deep-copied controls they only read. One page of 100 made 17.6 million `copy.deepcopy` calls.

### Fix
- `4acb8282`: pass the request's or job's catalogue cache through `apply_resolved_binding` and `segment_render_parts`, and remember successful pin proofs for one identity context or cast freeze.
- `3fbc66ea`: do the same for a segment's own frozen voice, voice-change pauses and `preview_render_parts`. Synthesis-time callers are unchanged.
- `bade0a9a`: remember language support for one identity pass or cast freeze, read the controls the pass never changes instead of copying them per segment, copy flat settings records without a call per immutable value, and reuse a pass's validated characters and cast.

Identities and frozen cast snapshots are unchanged. The tests compare each one with a reference pass that still does the per-span work. This complements #125, which keeps the plan and revision rows alive for the SQL side; this PR removes the per-span Python work.

### Timings
5,167-segment cast book, medians of three runs:

| Operation | Before | After |
|---|---|---|
| Segment listing, default | 281 s | 3.1 s |
| Segment listing, page of 100 | 268 s | 3.0 s |
| Plan status | over 900 s | 16.5 s |
| Revision history | over 900 s | 16.2 s |
| Non-active revision | 579 s | 6.6 s |
| Admission, 1 / 15 segments | 703 s / 710 s | 60 s / 60 s |
| Opening check, 1 / 15 segments | 404 s / 399 s | 53 s / 53 s |

On a 189-segment book, listings went from 12.7 s to about 0.4 s and plan status and revision history from about 62 s to under 1.6 s. The full plan status and history stay over 15 s on the large book; a separate PR moves the MCP tools to the summary routes.

### Tests
- `tests/test_audio_identity_cost.py` (new, in `web-07-04-serial`) counts catalogue builds, pin proofs, language lookups, controls copies and statements, and checks every identity and frozen snapshot against the reference pass.
- New cases in `test_generation_audible_speech_preparation.py`, `test_generation_cast_source_compatibility.py`, `test_generation_identity_settings_copy.py` and `test_generation_rendering.py`.
- On Windows: ruff, basedpyright (only the existing `nemo_normalizer.py:98` import on win32), vulture and `test_lanes.py check` pass. `web-03-serial` 285 passed, `web-07-04-serial` 300 passed. The fast lane passes apart from 4 existing symlink tests that need a Windows privilege (`WinError 1314`), which this PR does not touch.

### Suggested release note
On a long book with a cast, listing segments, reading the speech plan's status and history, and starting a generation run are much faster. They no longer rebuild the speech provider list for every character line, and they check each saved character voice once rather than once per line. They also stop re-reading each voice's supported languages and copying the character list for every segment, so opening an older speech plan version, checking the speech plan and starting a run are faster still.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

## 2. `mcp-plan-reads-use-revision-summaries`

**Title:** Answer MCP plan status and revision history from summaries

**Body:**

### Summary
`pandrator_get_speech_plan_status` and `pandrator_list_speech_plan_revisions` request the full payloads, which work out audio reuse for every revision. On a long book that runs far past the MCP client's 15 s timeout: on a 5,167-segment cast book, over 900 s each, and still about 16 s with the identity-cost PR (#IDENTITY).

### Reproduction
On a book with a few thousand segments and casting on, call either tool. It times out; the server keeps working after the client gives up.

### Root cause
Both tools use the default HTTP responses, which run the audio identity pass for every revision the plan has, although callers need reuse counts for the active revision only.

### Fix
- Both tools request the summary variants, then fetch the detail of the active revision only, in a separate request, and copy its five audio reuse fields onto that item.
- Other revisions report `audio_reuse_checked: false`. A timed-out or missing active detail becomes a warning instead of failing the call.
- Plan status no longer reports whether a repair batch can be undone; the web app's speech plan history still shows it.
- `pandrator_mcp/guides/workflows.md` describes the new fields.

Default HTTP responses are unchanged, so the web app sees no difference.

### Timings
5,167-segment cast book, with #IDENTITY, medians of three runs: a status call is now the summary (0.39 s) plus the active revision (6.6 s), about 7 s in all; a history call is about 6.6 s. Each request stays under 15 s. Without #IDENTITY the active revision's detail is still minutes on a cast book, so this PR helps most after it, although it does not depend on it in code.

### Tests
- New cases in `tests/test_mcp_generation_registration.py` and `tests/test_mcp_application_client.py` cover the summary flag reaching the HTTP request, counts merged for the active revision only, no detail request without an active item, a race or timeout degrading to a warning, other failures propagating, and the detail request sharing the call's correlation headers.
- On Windows: ruff, basedpyright (only the existing `nemo_normalizer.py:98`), vulture and `test_lanes.py check` pass. `mcp-serial` passes apart from 3 existing symlink tests that need a Windows privilege (`WinError 1314`).

### Suggested release note
MCP plan status and revision history answer quickly on long books. They now return revision summaries: audio reuse counts are given for the active plan when it is listed, other plan versions show none, and plan status no longer reports whether a repair batch can be undone. The web app's speech plan history still shows both.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

## 3. `segment-listing-cost-follows-the-page`

**Title:** Make a segment listing pay for its own page, not the whole plan

**Body:**

**Depends on #IDENTITY.** The first three commits are that PR; this PR adds the last two, `6ffc186a` and `e71c7d06`. Once #IDENTITY is merged, the diff shrinks to those.

### Summary
With #IDENTITY, a segment listing on a 5,167-segment cast book still takes about 3 s whatever the page size or fields, because it works out audio reuse for the whole revision. Abandoned listings still pile up: measured with the first two of #IDENTITY's commits, four MCP clients giving up at 15 s for one minute kept the server busy enough that `auth/status` then took 66 s to answer.

### Reproduction
On a long cast book, list one segment with `fields=["id"]`. It costs as much as listing every segment with reuse fields.

### Root cause
`list_segments` runs the audio identity pass for every listing, including ones whose response shows no reuse field. That pass freezes the cast for every segment of the revision; profiled, the whole-plan `freeze_cast_snapshot` was about 6.7 s of the 8.6 s a page of 100 took under cProfile.

### Fix
- `6ffc186a`: run the identity pass only when the response carries `audio_reuse_reason` or `has_reusable_take`, and then freeze the cast for the page's segments only. Admission, the opening check, recast and plan history keep the whole-revision freeze.
- `e71c7d06`: when a past run's frozen snapshot names a voice deleted since, keep that failure with the row that uses it, after a clean freeze. A failed freeze still marks the whole page unknown, and admission and the opening check keep the shared failure. An empty page skips the pass.

Two reuse answers change:
- A cast voice that fails outside the page no longer marks the page's rows unknown. The page that holds the failing segment still reports all its rows unknown, which matches how a retained-input run validates its own segments.
- A removed row is worked out after the rest of its page, so its own failure no longer marks the rows after it unknown.

### Timings
5,167-segment cast book, medians of three runs:

| Listing | With #IDENTITY | With this PR |
|---|---|---|
| Default | 3.12 s | 0.70 s |
| Compact | 2.72 s | 0.40 s |
| `fields` without reuse fields | 2.71 s | 0.08 s |
| Page of 100 | 3.02 s | 0.77 s |
| Non-active revision | 2.95 s | 0.64 s |

Profiled, a page of 100 makes 210 statements (0.025 s of SQL), and its cast freeze takes 0.18 s instead of about 6.7 s. Small-book listings are all under 0.35 s.

On the 12-segment cast plan in `tests/test_audio_identity_cost.py`:

| Page | Statements | `build_render_parts` calls | Segments the cast freeze visits |
|---|---|---|---|
| `limit=1, fields=["id"]`, before | 70 | 13 | 12 |
| `limit=1, fields=["id"]`, after | 24 | 0 | 0 |
| `limit=1`, default, before | 74 | 13 | 12 |
| `limit=1`, default, after | 46 | 2 | 1 |
| Default page of all 12, before and after | 100 | 24 | 12 |

### Tests
- New cases in `tests/test_audio_identity_cost.py` count the work per page and cover both reuse changes, the deleted-voice row, an empty page and `effective_take` on a page without reuse fields.
- On Windows, with #IDENTITY: ruff, basedpyright (only the existing `nemo_normalizer.py:98`), vulture and `test_lanes.py check` pass. `web-03-serial` 285 passed, `web-07-04-serial` 300 passed. The fast lane passes apart from 4 existing symlink tests that need a Windows privilege (`WinError 1314`).

### Suggested release note
A page of segments now costs what it shows: a page that leaves out the audio reuse fields skips the voice and audio-reuse checks, and a page that includes them checks only its own segments. A character voice that is missing elsewhere in the book no longer marks the page's audio reuse unknown; a missing voice used on the page itself still does, for the whole page. A removed segment, or a voice deleted since a past run you are reviewing, no longer makes the other segments on the same page show their audio reuse as unknown.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Review rounds (2026-11-12): CodeRabbit's two Mapping findings are fixed and answered on their threads. Two pre-emptive reviews (identity and listing; MCP) found nothing major; the fixes taken are `bfd43a5e`, `a3328a21` and `a2d978f2`. Not taken: a lazy `shared_controls` copy and skipping unused `reuse_inputs` (small wins, more risk), widening `resolve_capabilities` to `Mapping` (upstream's signature, cascades into typed callees), the error text inside MCP warnings (matches `system.py`), and a detail/summary race check (narrow).

Third CodeRabbit round (2026-11-12): it resolved all three Mapping threads after checking the fixes against the code. It then raised two small points, both fixed, tested and answered: `copy_record` kept mutable keys as the original objects where `deepcopy` copies them through the memo (#128 thread, fixed in #126 as `89abef28`); and `test_incomplete_detail_merges_nothing_and_warns` did not assert which revision was requested (#127 review nitpick, fixed in `970bbaf9`, answered with a PR comment because it had no thread). #128 was rebased onto `89abef28` and force-pushed with lease; its own commits are now `4224255b`, `5d1ae9db` and `b169fec1`.

---

## 4. `routine-requests-stop-taking-the-write-lock`

Story 6. Branch head `0d300f9a`, from upstream `main` at `fb72c018`: `9658a55b` and `ebe3b3b1` picked from fork `f7d62018` and `58fcaada`, then the pre-review fix `0d300f9a`. 5 files, +1,013 / -168. Passes `check_pr_branch.py`; no release-notes hunk to leave out. Independent of #126 to #128. Pushed and opened 2026-11-12 on the owner's go-ahead as upstream #129; the fix `0d300f9a` is on the fork's epic branch as `79e2feea`. CodeRabbit's first review (2026-11-12) raised two findings, both fixed in `d2b1c176` and answered in their threads: the busy-batch leader spooled the whole batch serially (now each request spools its own event), and a request stalled past the interval could rewind a newer time (the claim and touch now reread the clock). Its follow-up on the clock fix (a stall after the new clock read) is fixed in `220ce47e` and answered in its thread: the claim reads the clock under its lock and the touch reads it with the writer held. After story 7 was committed (`d3b511f6`), `d2b1c176` and `220ce47e` went onto the epic branch as `728f4ff1` and `3b8708b4`, pushed 2026-11-12; CodeRabbit had resolved both #129 threads by then. The maintainer merged #129 as written, with no comments, on 2026-11-12 (`c2b6fe1a`); its branch is deleted, and fork `main` recorded the merge as `53ef49f2`. Before/after timings: `%USERPROFILE%\pandrator-perf\work-2-6-pr\` (`before-stopped.log` and `after.log`, results JSON beside them; the before run was stopped while waiting for upstream's server to go idle, after its burst and after-load reads were saved).

**Title:** Stop routine requests from taking the database write lock

**Body:**

### Summary
Under read load, ordinary authenticated requests queue for SQLite's single writer. On a burst against a 5,167-segment book, 35 audit events went to the pending audit file instead of the database, and replaying them failed 76 times. `auth/status` took a median of 1.43 s. Nothing in those requests needs to write except their audit row.

### Reproduction
1. Serve a large project and send a few heavy reads (segment listings) that clients abandon after 15 s.
2. At the same time, send light authenticated requests: 24 clients calling `GET /api/v1/auth/status` with a bearer token every 0.5 s.
3. The server log fills with "Audit writer busy; event saved to pending audit spool" and "Pending audit event retained after replay failure".

### Root cause
- `AuthService.resolve_api_token` checks every bearer token inside `immediate_session()`, a `BEGIN IMMEDIATE` transaction, only to set `last_used_at`. Every bearer request therefore holds the one writer across the hash check and the account queries.
- `AuditService.record` then writes each request's audit row in its own write transaction with a 250 ms busy timeout. Under load these writers queue behind each other and behind the token writes, and an audit write that waits too long is spooled.

### Fix
- `9658a55b`: resolve bearer tokens in a read snapshot, as browser sessions already are. Every authorization check still runs on every request: expiry, revocation, instance, origin, scopes, account state and the automation client's state. `last_used_at` (token and client) is written only when it is at least 60 s old, as a short conditional UPDATE whose busy failure is logged and ignored. Audit rows are written by group commit: concurrent requests' events share one write transaction per batch, and each request still returns only after its own event is committed or spooled, so no event is lost. A held writer spools the batch as before; a non-busy failure in a batch is retried per event, so only that event's request fails.
- `ebe3b3b1`: one request per token per interval in each process attempts the touch, so a touch skipped while the writer is busy is not retried by every following request. The conditional UPDATE still settles races between processes.
- `0d300f9a`: requests read the clock in an order that can differ from the order they touch. Only a stored time more than one interval ahead now counts as a clock step back, so an earlier clock never retouches or moves `last_used_at` backwards.

User-visible: a token's "last used" time now updates at most once a minute.

### Timings
The same burst on a synthetic 5,167-segment book (4 heavy clients abandoning segment listings at 15 s, plus 24 light clients sending `auth/status` every 0.5 s, for 60 s), before and after this PR, both on top of 0.14.3:

| | 0.14.3 | With this PR |
|---|---|---|
| Audit events spooled | 35 | 0 |
| Failed spool replays | 76 | 0 |
| `auth/status` requests answered in 60 s | 397 | 795 |
| `auth/status` median | 1.43 s | 0.42 s |
| `auth/status` timeouts at 30 s | 24 | 24 |

The remaining timeouts, and the server staying busy after the burst, come from the heavy listings: each takes minutes on 0.14.3 and keeps running after its client gives up. #126 and #128 remove that cost. With all three on our fork, a heavier burst (53 heavy and 2,425 light requests) answered every light request, the slowest in 0.44 s, with no spools and no locked errors.

### Tests
- New `tests/test_routine_request_writes.py` (in `web-06-serial`): token reads within the interval open no write transaction; a due touch writes once; concurrent resolutions in one process touch once and across two processes update once; a busy touch still authenticates and is not retried; earlier clocks neither retouch nor rewind; invalid tokens never write; group commit shares transactions, keeps each event's actor, spools every event of a held batch, raises only in the request whose spool or event failed, bounds batch size and prunes once per batch.
- `test_account_authentication_integration.py`: the expiry-after-writer test now covers enrollment codes only, since bearer tokens no longer take a writer; token expiry is tested in the new file.
- On Windows (uv, Python 3.11): the six auth and audit test files, 84 passed under SQLAlchemy 2.1.8 and 2.0.52. `web-06-serial` under 2.1.8: 478 passed, 10 skipped, 1 failed, which is an existing symlink test that needs a Windows privilege (`WinError 1314`). ruff, vulture and `test_lanes.py check` pass; basedpyright reports only the existing `nemo_normalizer.py:98` on win32.

### Suggested release note
Busy reads no longer fail with "database is locked" or push audit records into the pending audit file in bulk. Checking a request's API token no longer takes the database's single writer, and requests that arrive together now write their audit records in one shared write instead of queueing for one each. While another write holds the database, audit records still go to the pending file and are replayed later. An API token's "last used" time now updates at most once a minute.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

## 5. `abandoned-reads-stop-stalling-the-server`

Story 7. From upstream `main` at `c2b6fe1a` (with #126 to #129): `8e23dbcc` and `6a7308ad` picked from fork `d3b511f6` and `3d1c9fdd`, then the pre-review fix `c38388e3` and two fixes from a CodeRabbit review on a fork-internal PR (ValtorX/Pandrator#1 into the `upstream-main` mirror, 2026-11-12): `fb3f518a` and `ea77744a`. The fork review's second pass found nothing more, and both threads were resolved by CodeRabbit. The three fixes are on the epic branch as `23f5dde2`, `a102e1f1` and `b9df5a5c`. Opened 2026-11-12 on the owner's go-ahead as upstream #132 (5 commits, 11 files, +705 / -9); the fork PR was closed without merging. Passes `check_pr_branch.py`; no release-notes hunk. Before/after timings: `%USERPROFILE%\pandrator-perf\work-2-7-pr\`. Pre-review (2026-11-12): no major findings and no regressions; taken: socket timeouts and cleanup in the real-waitress tests, a comment naming the waitress internals the channel relies on (checked against 3.0.2), and two wording fixes. Not taken: rejecting a duplicate `channel_request_lookahead` (only our two call sites use the factory) and guarding the token reset (no context in which it can fail was found).

**Title:** Stop reads whose client gave up from stalling the server

**Body:**

### Summary
A read whose client has given up keeps running to the end. When an MCP client times out on a few heavy reads, their work keeps every server thread busy and new requests wait behind it. On a 5,167-segment book with #126 to #129, a 60 s burst of `plan_status` reads that four clients abandoned at 15 s left `GET /api/v1/auth/status` taking 292 s to answer, and the server stayed busy for another minute after that.

### Reproduction
1. Serve a large project (a few thousand segments, casting on).
2. From 4 clients, request `GET .../generation-plan/status` repeatedly for 60 s, each giving up after 15 s.
3. Right after, request `GET /api/v1/auth/status` and a small project's segment listing. They wait for the abandoned reads to finish.

### Root cause
Waitress runs with `channel_request_lookahead = 0`, its default. It then stops reading a connection while that connection's request is queued or running, so it never notices that the client left: a queued request still starts, and a running one runs to the end. The heavy reads spend their time in the audio identity pass and the cast freeze, which never check whether anyone is still waiting.

### Fix
- `8e23dbcc`: the server is built through a shared `create_api_server` with `channel_request_lookahead = 1`, so waitress watches the connection and skips a queued request whose client already left. For `GET` and `HEAD` requests, `waitress.client_disconnected` is bound as a per-request probe, and `raise_if_abandoned()` checkpoints at the top of each segment step of the identity pass and the cast freeze stop a read whose client is gone. It answers 499 `client_closed_request`, which no client reads, and is audited as usual. Everywhere else the checkpoints do nothing: mutations, worker jobs, admission and the opening check run as before.
- `6a7308ad`: lookahead would also let waitress skip a queued mutation whose client left. The server's channel now reads a connection only while a `GET` or `HEAD` heads its queue, so a queued change still runs to its commit as it did before.
- `c38388e3`: the real-socket tests time out instead of hanging and close their sockets on failure; a comment names the waitress internals the channel relies on, checked against waitress 3.0.2.
- `fb3f518a`: waitress before 3.0.1 has a request-smuggling race that applies only when `channel_request_lookahead` is above 0 (CVE-2024-49768, GHSA-9298-4cf8-g4wj), so both packages now require `waitress>=3.0.1,<4`; 3.0.1 also fixes a disconnect busy-loop in any configuration (CVE-2024-49769). `requirements.txt` and the two ranges recorded in `pixi.lock` follow; the lock already resolves 3.0.2.
- `ea77744a`: the real-waitress test waits for the queued channel to disconnect before releasing the worker, so it cannot serve the abandoned request first and fail spuriously.

### Timings
The same burst on a synthetic 5,167-segment book (4 clients, `plan_status` for 60 s, abandoned at 15 s, all 16 abandoned), then the small project's reads, on upstream `main` with and without this PR:

| After the burst | Upstream `main` | With this PR |
|---|---|---|
| `auth/status` | 292 s | 0.06 s |
| Small segment listing, default | 3.09 s | 0.44 s |
| Small segment page of 100 | 5.39 s | 0.28 s |
| Small `plan_status` | 4.72 s | 0.87 s |
| Server still busy after those reads | 56.7 s | 0 s |

With this PR every read after the burst matched its speed before the burst. Neither run recorded audit spools or locked-database errors.

### Tests
- New `tests/test_abandoned_reads.py` (in `web-07-04-serial`): the checkpoint with no probe, a connected and a departed client, and a probe that raises; the identity pass, the segment listing and the whole-plan cast freeze stop within one segment of the client leaving; each heavy read answers 499 and is audited once; mutations and worker threads never see a probe; and against a real waitress server, a closed client is reported to its running read, a queued read whose client left is skipped, and a queued mutation whose client left still runs.
- On Windows (uv, Python 3.11): the story's six test files give 142 passed and 6 skipped under SQLAlchemy 2.1.8 and 2.0.52; `web-07-04-serial` 324 passed; `tests/test_abandoned_reads.py` 22 passed after the last fix, and its three real-waitress tests passed three runs in a row. ruff, vulture, `test_lanes.py check` and `generate_requirements.py --check` pass; basedpyright reports only the existing `nemo_normalizer.py:98` on win32.

### Suggested release note
Listing segments, plan status and plan history now stop partway through the book once their client has given up, and a read still waiting its turn never starts. Heavy MCP reads that time out no longer stall the server. A change whose client gave up still runs. The server now requires waitress 3.0.1 or later, which fixes a request-smuggling flaw that this setting would otherwise expose.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

## 6. `heavy-reads-stop-holding-the-wal-back`

Story 8. From upstream `main` at `c2b6fe1a`: `534163ca` and `099d6a2a` picked from fork `99a0574d` and `7018e743`, then the pre-review fix `72276832`. Passes `check_pr_branch.py`; no release-notes hunk. Independent of #132. Before/after: `%USERPROFILE%\pandrator-perf\work-2-8-pr\`. Pre-review (2026-11-12): no major findings and no regressions; taken: the size-limit comment's account of when truncation happens, a race-free WAL size helper, a page-size check and size range instead of an exact byte count in the under-limit test, and a note that the search test guards a future switch to streaming. Not taken: the lane entry's position. CodeRabbit's review on the fork-internal PR ValtorX/Pandrator#2 (2026-11-12) found nothing. The pre-review fix is on the epic branch as `26d18b53`. Opened 2026-11-12 on the owner's go-ahead as upstream #134 (3 commits, 5 files, +170 / -1); the fork PR was closed without merging.

**Title:** Shrink the WAL back to a size limit once heavy read load ends

**Body:**

### Summary
After heavy read load the SQLite write-ahead log keeps its high-water size. On a 5,167-segment book a 60 s burst grew it from 0 to 40 MiB, and it stayed at 40 MiB after the load ended. A long-running install carried a 59 MB WAL.

### Reproduction
1. Serve a large project and run a read burst: 4 clients requesting `GET .../generation-plan/status` for 60 s and giving up at 15 s, plus 24 clients requesting `GET /api/v1/auth/status` every 0.5 s.
2. Watch `pandrator.sqlite3-wal` during and after the burst. It grows while the heavy reads keep the checkpoint from completing, and never shrinks afterwards.

### Root cause
While readers hold old snapshots, SQLite's auto-checkpoint cannot complete, so the WAL keeps growing. Once they finish, SQLite reuses the log from its start but never truncates the file unless `journal_size_limit` is set, and the application does not set it.

### Fix
- `534163ca`: every engine connection sets `PRAGMA journal_size_limit` to 8 MiB (about two auto-checkpoint cycles at the default 4 KiB pages), so the first write that restarts the log after a complete checkpoint truncates it back to the limit. WAL mode, `busy_timeout`, `foreign_keys` and the default auto-checkpoint are unchanged. The segment search also reads its rows in full before matching, so no heavy read keeps a statement open across per-row work.
- `099d6a2a`: the WAL growth loop in the new tests is bounded, and a search test gets the short Windows temp root.
- `72276832`: the size-limit comment says exactly when truncation happens; the tests read the WAL size without a check-then-stat race and assert a size range rather than an exact byte count.

### Timings
The burst above on a synthetic 5,167-segment book, WAL size sampled every second, on upstream `main` with and without this PR:

| WAL size | Upstream `main` | With this PR |
|---|---|---|
| Start | 0.04 MiB | 0.04 MiB |
| Peak | 40.1 MiB | 37.5 MiB |
| After the follow-up reads | 40.1 MiB | 8.0 MiB |
| Settled | 40.1 MiB | 8.0 MiB |

Neither run had audit spools or locked-database errors. On both sides the light requests timed out 24 times and the server stayed busy about a minute after the burst, because the abandoned heavy reads keep running; #132 addresses that.

### Tests
- New `tests/test_wal_size.py` (in `web-18-serial`): a new connection reports the limit; the WAL shrinks to the limit once its readers finish; a WAL under the limit is not truncated; and the segment search holds no statement open while matching. `test_web_database_bootstrap_lifetime.py` expects the new pragma.
- On Windows (uv, Python 3.11): `test_wal_size.py`, `test_generation_search.py` and `test_web_database_bootstrap_lifetime.py` give 36 passed under SQLAlchemy 2.1.8 and 2.0.52, and the WAL tests passed three runs in a row. `web-18-serial` under 2.1.8: 711 passed, 2 skipped, 1 failed, an existing symlink test that needs a Windows privilege (`WinError 1314`). ruff, vulture and `test_lanes.py check` pass; basedpyright reports only the existing `nemo_normalizer.py:98` on win32.

### Suggested release note
After heavy use, the database's write-ahead log now shrinks back to a small size the next time the app saves anything, instead of staying at its largest.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

## 7. `edit-copy-reconcile-finds-takes-in-sql`

Story 9. From upstream `main` at `c2b6fe1a`: `bae43ede` and `f60f9736` picked from fork `09cf5616` and `0d5076fd`, then the pre-review fix `e80ee132` and `faac4fdc`, from CodeRabbit's one nitpick on the fork-internal PR ValtorX/Pandrator#3 (2026-11-12). CodeRabbit confirmed the fix and its incremental review found nothing more. `e80ee132` and `faac4fdc` are on the epic branch as `0790ae13` and `cd3c6473`, after story 10. Passes `check_pr_branch.py`; no release-notes hunk. Independent of #132 and #134. Evidence: `%USERPROFILE%\pandrator-perf\work-2-9-pr\` (`before.log`, `after2.log`; `after.log` overlapped a test run and is not quoted). Pre-review (2026-11-12): the SQL candidate query is equivalent to the old selection; taken: ordering a target's existing takes, unpacking the candidate ids by name, capturing the candidate query by its `status ||` filter and explaining why the plan check holds on any SQLite version. Not taken: an exact statement count (could flake on pragma reads), a `created_at` tie test (ids are UUIDs, so not deterministic), renaming `_BATCH`. Upstream `main` moved to `7be2ff5a` (#133) and the maintainer opened #131 meanwhile; neither touches this PR's files, and the branch merges cleanly with both. Opened 2026-11-12 on the owner's go-ahead as upstream #136 (4 commits, 2 files, +322 / -20); the fork PR was closed without merging.

**Title:** Find edit-copy takes to inherit in SQL before loading any rows

**Body:**

### Summary
Starting generation on an edited copy of a long speech plan holds SQLite's single writer for about 1.7 s in admission's final transaction. Every other request that needs to write in that window waits out its busy timeout, and audit events are spooled. Most of the hold is the edit-copy reconcile loading every segment and take of the revision and its ancestors, usually to find nothing to copy.

### Reproduction
1. Take a long plan (about 5,000 segments here) that is an edit copy of an earlier revision with audio.
2. Admit a generation run of 1 or 15 segments and time admission's final write transaction (`BEGIN IMMEDIATE` to commit).

### Root cause
`inherit_edit_copy_audio` loaded every segment of the target revision with their takes, and every segment, take and artifact of its ancestors, then matched them in Python. It runs inside admission's write transaction, so the writer is held while those rows are read and hydrated.

### Fix
- `bae43ede`: one query finds the candidates (source segment, take and target segment) in SQL: completed or stale takes of the ancestors' segments, paired with the target segment at the same ordinal that is not removed and does not already hold that artifact, in the same order as before. Only those rows are loaded, in batches of 500 ids. A reconcile with nothing to copy loads no segment or take rows. The remaining checks and the copy itself are unchanged. The status filter compares `status || ''` so the planner starts from the revision's segments rather than the global status index.
- `f60f9736`: tests across batch boundaries and a test that pins the query plan.
- `e80ee132`: a target's existing takes are loaded in creation order, so which take counts as current never depends on row order; the candidate ids are unpacked by name.
- `faac4fdc`: the query-plan test also rejects a full scan of `audio_takes`, which would cost as much as the status index.

### Timings
Admission's commit transaction on a copy of a real 5,167-segment edited book, median of 3 runs, on upstream `main` with and without this PR:

| Segments admitted | Upstream `main` | With this PR |
|---|---|---|
| 1 | 1.68 s | 0.72 s |
| 15 | 1.75 s | 0.71 s |

The whole admission job went from about 10.3 s to 9.2-9.4 s.

### Tests
- `tests/test_generation_edit_audio.py`: the reconcile copies the same takes, with the same fields and segment status, as a verbatim copy of the previous algorithm across several ancestors and batch sizes; a reconcile with nothing to copy hydrates no segment or take; and the candidate query uses neither the global status indexes nor a full scan of the takes.
- On Windows (uv, Python 3.11): `test_generation_edit_audio.py` and `test_generation_admission.py` give 60 passed under SQLAlchemy 2.1.8 and 2.0.52; `web-13-serial` 61 passed. ruff, vulture and `test_lanes.py check` pass; basedpyright reports only the existing `nemo_normalizer.py:98` on win32.

### Suggested release note
Starting generation on an edited long plan no longer blocks other requests' writes for over a second.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

## 8. Story 10: not offered, delivered upstream by #131

Story 10 (`999f1571` on the epic branch) moves the workflow start's freeze and identity pass out of the writer. Upstream's own #131, opened by the maintainer 2026-11-12, makes the same change to `run_reviewable_generation` and also covers regroup, early repair and a cancel during the pass, so no PR is built. On the owner's go-ahead, a comment on #131 (2026-11-12, https://github.com/lukaszliniewicz/Pandrator/pull/131#issuecomment-6840942439) gave story 10's writer-hold timings from `baseline-2-1.md` (9.475 s to 0.170 s median, 3 runs) as supporting evidence, said we would not open a competing PR, and offered the stored-run equivalence test (`test_reviewable_start_stores_what_the_single_writer_order_stored`) as a follow-up once #131 merges. Story 10's other tests (writer free through the check, changed inputs refused, revision replaced during the check) overlap #131's. When #131 reaches fork `main`, take upstream's side in `workflow_generation_start.py`, `test_web_workflow_handlers.py` and `test_workflow_generation_language_preflight.py`, then keep story 10's extra tests only where they still pass.

---

## 9. `compact-run-list-reads-only-its-keys`

Story 11. From upstream `main` at `769c9c7d`: `0c7d0e6f` and `037bb583` picked from the epic branch's `a32ea0d0` and `f7d1e865`, then the pre-review fix `5164d564`. Passes `check_pr_branch.py`; no release-notes hunk. Offered while upstream's 0.14.4 release PR #142 is open, in case it fits that release; neither #142 nor any other open upstream PR touches these files. CodeRabbit's review of the fork-internal PR ValtorX/Pandrator#4 (2026-11-13) found nothing ("No actionable comments"). Opened 2026-11-13 on the owner's go-ahead as upstream #144 (3 commits, 6 files, +473 / -32); the fork PR was closed without merging, and `5164d564` is on the epic branch as `f590322c`. The maintainer merged it unchanged into `release/0.14.4` ("this is a lovely one"), credited it in the 0.14.4 release changes, and cancelled the PR's own CI because #142's run covered the same commits; #144 was marked merged when #142 merged on 2026-11-13 (`1a2b7b1a`). Evidence: `%USERPROFILE%\pandrator-perf\work-2-11-pr\` (`run.sh`, `before.log`, `after.log`, test and lane logs). Pre-review (2026-11-13, independent subagent, nothing critical or major): taken in `5164d564` were rejecting an unknown `view` (a direct caller passing anything but `full` or `compact` silently got the full read; a test shows the old code missed it), a docstring sentence naming where SQLite's extract differs from `json.loads` (a non-object snapshot, top-level `NaN`, integers beyond 64 bits; no writer produces them) and moving the null-side comment beside its column. Not taken: returning projected payloads from compact `list_runs` (the plan keeps projection in the route, as for the full view); calling the SQL-text check secondary in a comment (it already asserts a positive control on the full view, and the `loaded_as_persistent` check carries the main assertion); dropping the back-reference comments in `passage_regroup.py` and `generation_run_history.py` (story 11's code review added them so a new key read there joins the constant).

**Title:** Load only the keys the compact run list reads

**Body:**

### Summary
While a generation run is active, the web UI polls `GET /api/v1/sessions/<id>/generation-runs?view=compact&limit=25` every 10 s. On a 5,167-segment cast book each poll takes about 1.7 s of server CPU (up to 2.5 s while admission runs), so every open tab keeps a large share of a core busy for as long as generation runs. MCP's `list_generation_runs` asks for the same compact view.

### Reproduction
1. Take a long cast project with several generation runs (here 10 runs whose settings snapshots total 150 MB, the largest 80 MB) and a few output assemblies.
2. Request `GET /api/v1/sessions/<id>/generation-runs?view=compact&limit=25` repeatedly and time it.

### Root cause
The run-list reader hydrates every selected `GenerationRun` with its whole `settings_snapshot_json`, and every latest `OutputAssembly` with its whole `settings_json` (pinned take manifests, 17.6 MB here), then deep-copies the manifests in `_assembly_payload`. The compact projection then drops both. Almost all of each snapshot is the generation control and performance snapshots, audio identities and guards, which no compact field uses. In a profile, JSON decoding was 0.82 s and the deep copy 1.0 s of a 1.56 s call.

### Fix
- `0c7d0e6f`: `list_runs` takes the route's `view`. Under `view=compact` it selects each run's snapshot as only the six keys the payload reads (`tts`, `rvc`, `selected_segment_override`, `audio` and the early-repair and regroup markers), extracted in SQL, and each assembly's settings as only `audio_version_side`. The whole columns are deferred with `raiseload`, so an unplanned read fails loudly rather than decoding them, and the narrowed values are set with `set_committed_value`, so they are never dirty and nothing is written back. An explicit JSON `null` side stays distinct from an absent one, as the versions map keys them differently. The full view, single-run reads and the dispatch readers are unchanged.
- `037bb583`: a route test for `view=compact` with and without `limit`; both compact tests re-read the stored columns afterwards; the key tuple names its readers, and the readers outside the module point back to it.
- `5164d564`: `list_runs` rejects an unknown view instead of silently reading everything; the docstring names the cases where SQLite's extract differs from `json.loads`, none of which any writer produces.

### Timings
`GET .../generation-runs?view=compact&limit=25` on a copy of a real 5,167-segment cast book (10 runs, 150 MB of run snapshots), 5 calls each through the running server, on upstream `main` (`769c9c7d`) with and without this PR:

| Project | Upstream `main` | With this PR |
|---|---|---|
| Large (5,167 segments) | 1.71 s median (1.60-2.00 s) | 0.63 s median (0.57-0.65 s) |
| Small (189 segments) | 0.06 s | 0.05 s |

Once the UI has the whole history it drops `limit`; that compact read went from 1.62 s to 0.30 s on the same copy in our fork. The full view is unchanged (1.5-1.9 s).

### Tests
- `tests/test_session_view_performance.py`: for audiobook and voiceover sessions with early-repair and regroup children, a malformed marker, a recast run with before and after assemblies, empty and `null` snapshots and a queued run, the compact list with and without `limit` and repairs equals projecting the full list; removing any one key from `COMPACT_RUN_SNAPSHOT_KEYS` changes some compact output; and neither the reader nor the route loads or selects a whole snapshot or assembly settings column, with a positive control on the full view. The stored columns are unchanged afterwards.
- `tests/test_catalogue_discovery_and_run_views.py`: the admission-replay test also compares `?view=compact&limit=25` with `?limit=25`.
- `test_unknown_run_list_view_is_rejected`: `list_runs` refuses a view other than `full` or `compact`.
- On Windows (uv, Python 3.11): the five run-history files (`test_session_view_performance.py`, `test_generation_run_history_projection.py`, `test_regroup_history_batches.py`, `test_generation_summary.py`, `test_catalogue_discovery_and_run_views.py`) give 82 passed under SQLAlchemy 2.1.8 and 2.0.52; `web-04-serial` 297 passed; `web-12-serial` 79 passed, with the 2 symlink tests in `test_session_output_storage.py` failing on `WinError 1314` (no symlink privilege), as on `main`. ruff, vulture and `test_lanes.py check` pass; basedpyright reports only the existing `nemo_normalizer.py:98` on win32.

### Suggested release note
The run history the app polls during generation no longer costs seconds of CPU per refresh on long projects.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

## 10. Story 12: audit result, as a comment on merged #134

Posted 2026-11-13 on the owner's go-ahead: https://github.com/lukaszliniewicz/Pandrator/pull/134#issuecomment-6853083894. Story 12 changed no code (owner decision 2026-11-13, in its plan), so nothing is offered as a PR. #134's body said the segment search now reads its rows in full "so no heavy read keeps a statement open across per-row work"; story 12 finished that audit across the app. The comment records the one remaining site and why it was kept, beside the WAL work upstream merged in #137 and #139. Figures come from story 12's plan (scratch database, uv environment, best of 5 and 7 runs). Posting needs the owner's go-ahead.

**Comment:**

A follow-up to this PR's note that, with the segment search reading its rows in full, no heavy read keeps a statement open across per-row work. We audited the rest of the app for it, since such a read holds a WAL read mark and a checkpoint cannot copy past it.

With SQLAlchemy 2.0 and 2.1, ORM selects fetch every row on first fetch. So only Core table selects, `text()`, raw `sqlite3` cursors and `yield_per` can keep a statement open. Every app site using one either reads in full first or only copies rows, with one exception: `JobQueue._next_claimable_job_id` streams candidates with `yield_per=128` and checks each one's resource keys while the statement is open. It runs on every worker poll while any resource is claimed.

We measured it on 5,000 queued jobs and kept it:

| | Statement open | Total |
|---|---|---|
| Current scan, first job claimable | 1.3 ms | 1.3 ms |
| Current scan, every job blocked | 10.8 ms | 10.8 ms |
| One full `.all()` read, first job claimable | 8.3 ms | 8.3 ms |
| One full `.all()` read, every job blocked | 8.1 ms | 11.1 ms |

Keyset pages of 128, fetched in full, would close the statement before each page's checks but cost up to 4 times as much per poll. With every job blocked, that was 42.9 ms against 10.6 ms at 5,000 jobs, because each page sorts the remaining candidates again. A full read only helps when everything is blocked, and holds the statement open 6 times longer in the common case. The per-row work is in-memory set checks, so the current scan is already the cheapest option.

Nothing to change; we're recording it so the next person looking at WAL growth doesn't have to re-measure it.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

## 11. Story 13: profile of a revision's detail, as an upstream issue

Posted 2026-11-13 on the owner's go-ahead as upstream issue #145: https://github.com/lukaszliniewicz/Pandrator/issues/145. Story 13 changed no code (owner decision 2026-11-13, option C, in its plan). Its profile is offered as an issue, not a PR: upstream's maintainer is changing the identity and cast code it covers (#125, #131), and the one finding that matters to that work, each segment rendered twice per request, only shows when profiling a long cast book. Figures come from `baseline-2-1.md`, "After story 12: single revision detail (story 13)", measured at fork `f590322c`. The profiled files (`generation_review.py`, `generation_audio_identity.py`, `generation_cast_runtime.py`, `generation_rendering.py`, `speech_markup.py`) are identical on upstream `main` `769c9c7d`. Posting needs the owner's go-ahead.

**Title:** Performance note: a revision's detail renders each segment twice (6.6 s on a 5,167-segment cast book)

**Body:**

Not a bug, and nothing here needs action now. We profiled this read while working on large-project responsiveness and decided against changing it ourselves, so we're recording what we found for whoever next restructures the identity or cast-freeze code.

### What we measured
`GET /api/v1/sessions/<id>/generation-plan/revisions/<revision_id>` for the active revision of a real 5,167-segment cast book takes 6.6 s (6.48-6.69 s over three calls, in process through the app's test client). Since #127, every MCP plan-status and history call makes this read for the active revision, and the web UI makes it when a revision is opened in the speech plan history. SQL is 0.05 s of it (1,386 statements); 6.3 s is the audio identity pass behind the reuse counts. The cost grows with the number of segments.

| Phase | Wall | Calls |
|---|---|---|
| Whole-revision cast freeze (`freeze_cast_snapshot`) | 2.45 s | 1 |
| of which `remap_markup` (two markup parses each) | 0.90 s | 5,167 |
| of which `build_render_parts` | about 1.3 s | 5,167 |
| Per-segment render again (`segment_render_parts`) | 2.15 s | 5,167 |
| of which `validate_retained_cast_resolution` | 0.88 s | 6,424 |
| of which markup parse | 0.42 s | 5,167 |
| `apply_resolved_binding` (both renders) | 0.68 s | 17,978 |
| `compile_performance` | 0.42 s | 6,400 |

So each segment's markup is parsed four times and its parts are rendered twice per request: once in the cast freeze and once in the identity pass.

### What we tried
Crude in-request memos, as upper bounds rather than designs (the reuse counts were identical in each):

| Memoized within the request | Median |
|---|---|
| nothing | 6.62 s |
| markup parse | 6.35 s |
| retained-pin re-validation (keyed by binding only, which is not a safe key, since the check reads per-segment settings) | 5.80 s |
| parse, remap and re-validation together | 5.78 s |

Trimming the repeats inside the current structure gets about 6.0-6.3 s safely.

### What would make a real difference
Rendering each segment once per request: the identity pass would reuse the cast freeze's parse and binding resolution instead of rendering again. That likely means splitting `build_render_parts` into a part that doesn't depend on the segment's settings and a part that applies them. We estimate 3-4 s for this read, but didn't show it's feasible. A cache across requests would only be safe with every input in its key: the settings snapshot, segment rows, cast controls, performance plans, speech references, the voice library and its files, retained inputs and the provider catalogue.

### Why we stopped
At 6.6 s the read is well inside the MCP client's 15 s timeout, and if a longer book pushed it past that, the MCP status tool already returns the summary with a warning instead of failing. The restructure is a large change in code you're actively editing, so it seemed better to describe it than to send it.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

### Upstream's answer: #146

The maintainer answered #145 the same day with #146 ("Share the cast freeze's parse and one registry with the identity pass", `a7190f98` on `main` `769c9c7d`, not part of the 0.14.4 release). Rendering each segment once, as the issue suggested, would change results: the cast freeze renders with the stored runtime settings, and the identity pass with the adapted settings plus the take override, performance, semantic context and the recast overlay, and everything after span resolution reads those settings. The settings-independent part is about 5% of the request. #146 instead shares the freeze's markup parse through a memo keyed on every parser input, and builds one provider registry per pass for retained-pin checks; he measured 10-12% less CPU on a synthetic 3,000-segment book.

On the owner's go-ahead (2026-11-13) we timed it on fresh copies of `real`, alternating `main` and the PR twice, 5 calls each (`%USERPROFILE%\pandrator-perf\work-146\`), and posted the result on #146: https://github.com/lukaszliniewicz/Pandrator/pull/146#issuecomment-6857090066.

| | `main` | #146 | Change |
|---|---|---|---|
| `revision_active`, median of 10 | 6.55 s (6.42-6.83 s) | 5.85 s (5.73-5.95 s) | -10.7% |
| `revision_non_active`, median of 10 | 6.55 s (6.45-6.71 s) | 5.89 s (5.76-6.05 s) | -10.1% |

Response bodies were not compared; #146's own tests check that identities and reuse are unchanged. #146 reaches the fork with whichever upstream merge follows its merge.

The maintainer merged #146 unchanged on 2026-11-13 (`5997edb5`, merged as `f449f03f`; `git range-diff` shows it identical to the `a7190f98` we timed, only rebased onto 0.14.4) and closed #145 as completed, quoting our timing (6.55 s to 5.85 s, -10.7%) and saying it ships in 0.14.5. It reaches the fork with the 0.14.5 merge.

## 12. Story 15: five focused branches (built, not pushed)

Built 2026-11-13 from upstream `main` at `f449f03f` in `%USERPROFILE%\Source\Pandrator-pr`, by cherry-picking the epic branch's commits. Each branch has its code review's fix picked onto the change it belongs to. All five pass `check_pr_branch.py`, and no release-notes hunk is involved. They are independent of each other; the one conflict, a neighbouring line in `scripts/test_lanes.py` on the bulk-copy branch, was resolved by keeping only that branch's line. Nothing is pushed, and no PR is opened without the owner's go-ahead. Still to do by section 3: the pre-review (step 5), the fork PR review (step 8), drafting the bodies (step 9) and, for the speed claims, before and after runs on upstream `main`. Story 15's before runs were made on `9f0d0441`, whose product code equals `f449f03f` except `version.py` and `identity.py`; see `baseline-2-1.md`, "Story 15".

| Branch | Commits (from epic branch) | Size | Timing to quote |
|---|---|---|---|
| `abandoned-recast-previews-stop` | `9ded97d3` (`c01fc544`) | 8 files, +294 / -7 | preview 317-348 s to about 5 s; an abandoned preview answers 499 within about 1 s |
| `selection-save-reads-its-own-lineage` | `bb0be4d6` (`230b5d73`), `da1fe078` (`19179f5d`) | 2 files, +136 / -10 | selection save writer hold 0.541 to 0.133 s |
| `prepare-text-builds-plan-rows-first` | `398cd4fe` (`59e69e9f`) | 9 files, +542 / -117 | plan store writer hold 0.779 to 0.199 s |
| `missing-only-start-copies-takes-in-bulk` | `50bc2c02` (`4d3ea915`), `a84601d5` (`ec5b3c81`) | 4 files, +447 / -37 | missing-only commit hold 3.734 to 1.204 s |
| `segment-listing-leaves-out-large-operations` | `02f2aef9` (`c810a683`, without the rebuilt `pandrator/web/static/`), `b3f52f1b` (`ce005f3f`) | 14 files, +267 / -7 | one-row page 876,840 to 3,640 bytes |

The listing branch keeps the regenerated `openapi.json` and `api.generated.ts`, because its new query parameter changes them. It leaves out the rebuilt `pandrator/web/static/`: `npm --prefix web run build` regenerates it, and the PR body should say so.

### Drafts (2026-11-14)

Fork-internal CodeRabbit reviews (2026-11-14): ValtorX/Pandrator#5 to #9. #8 and #9 came back with no actionable comments and were opened upstream on the owner's go-ahead as lukaszliniewicz/Pandrator#148 (missing-only bulk copy) and #149 (segment listing), from upstream `main` `f449f03f`; their fork PRs were closed without merging. #5 (1 Minor), #6 (1 Minor) and #7 (1 Major and 1 Minor, both marked unverified) are being fixed by the 2.15 session on their PR branches.

Round 2 (2026-11-14): the 2.15 session fixed CodeRabbit's findings on #5 (`5826e097`), #6 (`d6b24102`) and #7 (`34933587`), each with a test that fails without it, cherry-picked onto the epic branch (`5cb8b3a0`, `b0f38e4b`, `472a3ed6`). Replies went into #5's and #6's finding threads and a PR comment on #7; the re-reviews came back with no actionable comments and both threads were resolved. The two timings the fixes could move were re-measured with `admission_writer_holds.py`, 3 runs each, on fresh copies of `real`, upstream `main` `f449f03f` against each PR branch alone (`%USERPROFILE%\pandrator-perf\work-2-15-pr-round2-timing\`): selection save 0.559 / 0.560 / 0.584 s to 0.079 / 0.019 / 0.019 s (median 0.560 to 0.019 s), plan store 0.742 / 0.769 / 0.782 s to 0.200 / 0.130 / 0.196 s (median 0.769 to 0.196 s). The bodies below carry the fix bullets and these per-branch timings.

Opened upstream on the owner's go-ahead (2026-11-14), from upstream `main` `f449f03f`: #150 (recast previews, `5826e097`), #151 (selection save, `d6b24102`) and #152 (Prepare text rows, `34933587`); the fork PRs were closed without merging. #151 was retitled "Walk only the chosen artifact's lineage when saving a stage selection" right after opening, because the final code walks the lineage in any session rather than narrowing it to the session. All five story 15 changes are now open upstream: #148 to #152.

Upstream CI on #149 failed `web-12` (both OS) on upstream's facade test, whose keyword set lacked the new `operation` keyword; fixed by `c0750bc5` (pushed to the PR branch, epic `073695cd`) with a comment on #149 that also explains the expected "Frontend and contract" failure (static bundle left out).

Drafted from the plan, `baseline-2-1.md` ("Story 14", "Story 15") and each branch's diff; every number checked against those records. Each Timings lead says what "with" was measured on: 2.15's branch carrying all five changes, none of which touches another's transaction.

### Draft 1. abandoned-recast-previews-stop

Commits: `9ded97d3` Stop an abandoned recast preview and cut its per-call cost
Files: 8 files changed, 294 insertions(+), 7 deletions(-)

**Title:** Stop an abandoned recast preview and cut its per-call cost

**Body:**

#### Summary
A recast preview whose client has gone keeps running, and each one that runs makes every other request slower. After a few of them the app needs a restart. This PR makes the preview stop when its client leaves, and makes each preview call much cheaper.

#### Reproduction
1. Open a long cast project (about 5,000 segments here).
2. Start `POST /api/v1/sessions/<id>/generation-recast/preview` and close the connection after a second (the Cast panel's refresh does this, and so does an MCP client with its 15 s limit).
3. Watch the server: the preview keeps running to the end. Meanwhile a selection save, which takes 0.8 s on a fresh copy, took 28.66 s with one abandoned preview in flight. Over 45 minutes of UI and MCP use, the plan status summary went from 20 s to 120 s timeouts with 11 previews still running 380-2,492 s after their clients left.

#### Root cause
- The preview is a read-only POST, so neither the disconnect probe nor waitress's disconnect channel watched it. Both only watched GET and HEAD. `prepare_recast` also never checked whether its request was abandoned.
- Each segment rebuilt and deep-copied the TTS service catalogue and re-proved every retained pin, because `generation_recast._effective` did not pass the context's catalogue cache, proof memo or provider registry on. One preview took 317-348 s (about 300,000 statements).
- A CPU-bound preview makes every other thread wait for the GIL between SQLite steps, so row-heavy requests and writer holds stretch 10-40 times per running preview. Once previews fill the 12 waitress threads, even trivial requests queue.

#### Fix
- `9ded97d3`: `abandoned_requests.watches_disconnect(method, path)` names GET, HEAD and the recast preview POST. `http_lifecycle` (probe binding) and `api_server.ReadDisconnectChannel` both use it, so the read-only POST is watched like a read, and `prepare_recast` calls `raise_if_abandoned()` before each segment. `generation_recast._effective` now passes the context's catalogue cache to `apply_resolved_binding`, and its proof memo and provider registry to `resolve_project_cast_binding` and `validate_retained_cast_resolution`. `resolve_project_cast_binding` forwards the memo and proves its own access check and slot-lookup guard once per pass.
- `5826e097`: the target-building pass, which deep-copies each target's controls (thousands with `include_missing=True`), also calls `raise_if_abandoned()` at each segment, so a departed client stops it there too.

#### Timings
One recast preview on a copy of a real 5,167-segment cast book, with the app run as installed (waitress with 12 threads and a separate worker). Without: 0.14.4 as released. With: 0.14.4 plus #146, this change and the four other focused changes we are offering separately; none of the others touches the preview path.

| Request | Without this PR | With this PR |
|---|---|---|
| Preview, client that waits | 317-348 s, about 300,000 statements | 4.99-5.02 s, 10,507 statements |
| MCP `pandrator_preview_recast` | timed out at 15 s | 4.95 s and 5.03 s |
| Preview whose client left after 1 s | ran to the end | answered 499 at 1.014 s, 433 statements |

A 28-minute workload (UI polls and bursts, MCP reads, a preview and a regenerate every 3 minutes), on the same two builds: with the changes, the plan status summary stayed at a median of 0.54-1.17 s per 5-minute window and the waitress queue peaked at 3; on 0.14.4 the same windows went 20 s, 66 s, 111 s, then 120 s timeouts, with the queue peaking at 85. That comparison credits the combination, though the abandoned previews were what stalled 0.14.4: the same workload with only this PR's catalogue-cache part applied stayed at 1.8-3.5 s.

#### Tests
- `tests/test_generation_recast_cost.py` (new):
  - `test_preview_matches_the_old_preview_with_bounded_work`: the preview gives the same result as the old per-call path on a 12-segment cast plan, with at most 4 catalogue builds (was 14), 2 pin checks (was 20) and 2 access checks (was 40).
  - `test_preview_stops_at_the_next_segment_once_its_client_leaves`: the loop stops at the next segment.
  - `test_preview_stops_in_the_target_pass_once_its_client_leaves`: the target pass stops too; it fails without `5826e097`.
  - `test_an_abandoned_preview_post_answers_499`: the route answers 499.
- `tests/test_abandoned_reads.py`:
  - `test_waitress_reports_a_closed_client_to_a_read_only_post_preview`: a real waitress server reports the departure to the preview POST.
  - `test_only_reads_and_read_only_previews_watch_for_a_departed_client`: the predicate watches GET, HEAD and the preview POST, and no other method or path.
- The new test file is added to a lane in `scripts/test_lanes.py`.

#### Suggested release note
A recast preview whose client has gone now stops instead of running for minutes, and each preview is far cheaper, so the app stays responsive during long Cast and MCP sessions.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

### Draft 2. selection-save-reads-its-own-lineage

Commits: `bb0be4d6` Narrow the stage selection save to the session's own lineage; `da1fe078` Follow a stage lineage through artifacts that have no session
Files: 2 files changed, 136 insertions(+), 10 deletions(-)

**Title:** Walk only the chosen artifact's lineage when saving a stage selection

**Body:**

#### Summary
Saving a stage selection holds SQLite's single writer for about 0.54 s on a large project. Most of that time goes to loading every artifact edge in the database and every artifact of the session. This PR walks only the chosen artifact's own lineage.

#### Reproduction
1. Take a large project (here 15,324 session artifacts, and 46,267 artifact edges in the database).
2. Save a stage selection (`choose_artifact`) and time the route's write transaction.

#### Root cause
`_edges` selected every `ArtifactEdge` in the database, and `choose_artifact` loaded whole `Artifact` rows for the session when it needs only `id` and `role`. Both ran while the writer was held.

#### Fix
- `bb0be4d6`: `choose_artifact` joins the edges to this session's artifacts and reads only artifact ids and roles. `_edges` takes a `session_id` for this. The other callers (`trash_stage_artifact`, `select_source_path`, `rerun_impact`) still read every edge, as before.
- `da1fe078`: `Artifact.session_id` is nullable, and the unscoped walk continued through an ancestor that has no session. The scoped read dropped those edges and so stopped there, restoring fewer stages. It now also keeps edges whose child has no session.
- `d6b24102`: a session-scoped read still lost a lineage that leaves the session and comes back (A in this session, B in another, C in this one again: choosing C lost A). `choose_artifact` now walks the chosen artifact's ancestors and descendants one frontier at a time, by `child_artifact_id IN frontier` and `parent_artifact_id IN frontier`, whatever session each artifact belongs to. It reads only the reachable edges through the indexes and gives the same result as the full-graph walk. `_edges` is back to its original form for the other callers.

#### Timings
The selection save's writer hold on a copy of a real project with 15,324 artifacts, 3 runs each, on upstream `main` (`f449f03f`) and on this branch:

| | Upstream `main` | This PR |
|---|---|---|
| Writer hold, runs | 0.559 / 0.560 / 0.584 s | 0.079 / 0.019 / 0.019 s |
| Writer hold, median | 0.560 s | 0.019 s |

#### Tests
`tests/test_web_artifact_selection.py` gains:
- `test_selection_walk_reads_only_its_sessions_edges_and_artifact_roles`: the walk loads no edge from another session and no whole artifact, with the same result as before.
- `test_selection_walk_passes_through_a_session_less_ancestor`: the lineage is followed through an ancestor with no session. It fails without the second commit.
- `test_selection_of_an_unknown_or_wrong_role_artifact_is_refused`: an unknown artifact gives 404 and a wrong role gives 422, as before.

#### Suggested release note
Saving a stage selection on a large project barely holds the database writer, so other requests no longer wait for it.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

### Draft 3. prepare-text-builds-plan-rows-first

Commits: `398cd4fe` Build the plan's segment rows before Prepare text takes the writer
Files: 9 files changed, 542 insertions(+), 117 deletions(-)

**Title:** Build the plan's segment rows before Prepare text takes the writer

**Body:**

#### Summary
Re-running Prepare text on a long book holds SQLite's single writer for about 0.78 s while it stores the speech plan. Every other write waits that long. This PR builds the segment rows before the write transaction starts and stores them with one bulk insert.

#### Reproduction
1. Take a book with about 5,000 segments and run Prepare text again.
2. Time the write transaction that stores the plan (`store_generation_plan`).

#### Root cause
`store_generation_plan` normalised the records, remapped and parsed markup, and then added each segment one at a time with a flush per segment (5,174 flushes for 5,167 records), and rewrote `speech_plan_json` after the flush. All of it ran inside Prepare text's only write transaction.

#### Fix
- `398cd4fe`: `generation_plan_store.prepare_generation_plan_rows(...)` normalises the records, remaps and parses markup and computes the final `speech_plan_json`, with segment ids assigned up front (they are generated client-side). `store_generation_plan` accepts the prepared rows and inserts them in one bulk insert. If the `characters` it reads under the writer differ from the ones the rows were built from, it rebuilds them there; rows built for other records are refused. Prepare text builds the rows before `immediate_session` and passes them in through an optional `_prepare_generation_plan_rows` on `SourceWorkflowContext` (default `None`, so a context built without it stores as before). Other callers that already hold a writer work unchanged and get the bulk insert. The plan's digest and the stored rows are unchanged. The bulk insert uses `render_nulls=True`, because without it SQLAlchemy's ORM bulk insert splits rows whose `None` columns differ into separate batches.
- `34933587`: prepared rows carry `records_key`, a fingerprint of the cleaned records and the settings, and the store rebuilds them under the writer when it no longer matches (a list changed in place, other settings, or equal content in another list), so a stale preparation still stores correct rows. Rows a committed store inserted are marked consumed and rebuilt with fresh segment ids if stored again, for example with `force_new=True`; after a rollback they stay usable. The commit is observed on the session's connection, because `Database.immediate_session` commits the connection rather than the Session.

#### Timings
The plan store's write transaction for 5,167 records on a copy of a real book, 3 runs each, on upstream `main` (`f449f03f`) and on this branch:

| | Upstream `main` | This PR |
|---|---|---|
| Writer hold, runs | 0.742 / 0.769 / 0.782 s | 0.200 / 0.130 / 0.196 s |
| Writer hold, median | 0.769 s | 0.196 s |

In a run of the whole app with Prepare text jobs running, the job's writer hold was 0.29-0.34 s with this PR (1.5-1.8 s without it).

#### Tests
- `tests/test_generation_plan_store_bulk.py` (new):
  - `test_bulk_store_gives_the_reference_rows_with_one_insert`: the rows equal those of a reference copy of the old per-segment store, inserted with one statement and no per-segment flush.
  - `test_prepared_rows_are_stored_without_parsing_markup_in_the_writer`
  - `test_prepared_rows_are_rebuilt_when_the_characters_changed`
  - `test_prepared_rows_for_other_records_are_refused`
  - `test_identical_content_keeps_the_active_revision`
- `tests/test_generation_plan_store_bulk.py` also gains five tests for `34933587` (rows rebuilt when the records or settings changed, and rows already stored rebuilt with fresh ids but reusable after a rollback); each fails on the previous store.
- `tests/test_web_workflow_handlers.py`: `test_prepare_text_builds_plan_rows_with_the_writer_free` writes and commits from another connection with a zero busy timeout while Prepare text builds its rows, and checks that the stored segment ids are the prepared ones. It also checks that a fault while building rows inserts none.
- `tests/test_source_preparation_workspace_access.py`: the mid-write fault test now stores through the bulk path and still checks that the segment rows roll back.
- `tests/test_web_backend_architecture.py` and `scripts/test_lanes.py` are adjusted for the new file and module shape.

#### Suggested release note
Re-running Prepare text on a long book holds the database writer for about a quarter of the time, so other requests stall less while it runs.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

### Draft 4. missing-only-start-copies-takes-in-bulk

Commits: `50bc2c02` Copy preserved takes in bulk when a missing-only run starts; `a84601d5` Test preserved-take copies across chunks and every conflict check
Files: 4 files changed, 447 insertions(+), 37 deletions(-)

**Title:** Copy preserved takes in bulk when a missing-only run starts

**Body:**

#### Summary
Starting a missing-only run on a long book clones every preserved take inside admission's final write transaction, one take at a time. This PR does the copies in bulk and cuts that transaction from 3.734 s to 1.204 s (median of 3). It does not make the hold short: the commit still takes over a second, for the reasons below.

#### Reproduction
1. Take a book of about 5,167 segments in which 5,159 have a completed take and 8 are missing.
2. Start a missing-only run and time admission's final write transaction (`BEGIN IMMEDIATE` to commit).

#### Root cause
`start_in_session` cloned each preserved take with its own reads (three `session.get` calls), an UPDATE and an INSERT, with autoflush, inside the write transaction. For 5,159 takes that is about 15,500 point reads and 15,600 flushes.

#### Fix
- `50bc2c02`: the checks read the takes with their segment status in chunks of 500, from the row state that is already collected and rechecked in the writer. The sources are made inactive with one chunked `UPDATE ... WHERE id IN` per chunk, and the clones go in with one bulk insert. The reuse checks keep their order: `take_reuse_reason` gains a column-value twin, `stored_take_reuse_reason`, for callers that load columns only, and stale-only mode still checks identity. A row that changed before the write still gives the existing guarded-start conflict.
- `a84601d5`: tests across several chunks and each conflict check (see below). The reference loop keeps the revision check it had, and the docstring says the copies go in one INSERT.

#### Timings
Admission's commit transaction for a missing-only start (8 missing, 5,159 preserved) on a copy of a real 5,167-segment book, 3 runs each, on upstream `main` with and without this PR. "With" was measured on a branch carrying this change and four other focused changes we are offering separately; none of them touches this transaction:

| | Upstream `main` | With this PR |
|---|---|---|
| Commit transaction, runs | 3.734 / 3.611 / 3.764 s | 1.184 / 1.252 / 1.204 s |
| Commit transaction, median | 3.734 s | 1.204 s |
| Whole admission job (wall) | 11.9-12.2 s | 9.5-9.8 s |

Both runs preserved the same 5,159 takes: 5,159 copies made active and their sources made inactive.

The hold is still over a second. In a profiled run the copy took 0.53 s: 0.247 s for the insert of 5,159 rows (each has five secondary indexes and four foreign keys), 0.119 s for the 11 chunked updates and 0.078 s to read the revision's active takes. The rest of the commit transaction (snapshot guards, row-state collection, resource keys, the stored snapshot's hash and copy, edit-copy inheritance) is about 0.65 s on its own. Going lower needs the copies out of the commit transaction, or no physical copies at all. Either changes how a run owns its takes, and this PR does not do that.

#### Tests
`tests/test_generation_preserved_take_clones.py` (new) compares the result with a reference copy of the old per-take loop:
- `test_missing_only_copies_equal_the_per_take_loop` and `test_stale_only_copies_equal_the_per_take_loop`: same takes, status, artifact, kind, settings hash and duration. The source takes differ in these fields, so a copy of constants would be caught.
- `test_copies_spanning_several_chunks_equal_the_per_take_loop`: more than one chunk of 500.
- `test_copies_below_the_chunk_size_cost_one_update_and_one_insert`: statement counts, with no per-take read.
- `test_no_copies_when_nothing_is_preserved`.
- Conflicts: `test_a_take_missing_from_the_rechecked_state_conflicts`, `test_an_active_take_with_another_artifact_conflicts`, `test_a_deleted_artifact_conflicts`, `test_a_take_of_another_revision_conflicts` and `test_a_stale_only_take_whose_identity_no_longer_matches_conflicts`, plus `test_the_rechecked_state_itself_clones_without_conflict`.

#### Suggested release note
Starting a missing-only run on a long book holds the database writer for about a third of the time it did.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

### Draft 5. segment-listing-leaves-out-large-operations

Commits: `02f2aef9` Leave large operation values out of the segment listing by default; `b3f52f1b` Let MCP ask a segment listing for the whole operation
Files: 14 files changed, 267 insertions(+), 7 deletions(-)

**Title:** Leave large operation values out of the segment listing by default

**Body:**

#### Summary
After a restore, every page of the segment listing carries the revision's whole `operation_json`, which makes even a one-segment page about 877 KB. This PR leaves large values out by default, names what it left out, and lets a caller ask for everything.

The rebuilt static bundle (`pandrator/web/static/`) is left out of this PR. It is regenerated with `npm --prefix web run build`. The regenerated `openapi.json` and `web/src/lib/api.generated.ts` are included, because the new query parameter changes them.

#### Reproduction
1. Take a large project whose active revision comes from a restore.
2. Request `GET /api/v1/sessions/<id>/generation-segments?limit=1` (or list one segment through MCP).

#### Root cause
A restore's `operation_json` carries a per-segment `mapping` and `lineage`. The listing put the whole value on every page: 873,410 of the 876,840 bytes of a one-row page.

#### Fix
- `02f2aef9`: the listing takes `operation=summary|full` (default `summary`). Summary leaves out each top-level `operation_json` value whose compact JSON is over 4 KB and names the keys in a sorted `operation_json_omitted`. The rule is by size, not by key, so later large values are covered. `action`, `reason`, `batch` and the revision ids stay. `full` returns everything with `operation_json_omitted: []`; any other value gives 422. The parameter is added to the route, the OpenAPI description and the typed client (`api-models.ts` also gains `operation_json_omitted`), and MCP passes the omitted keys through.
- `b3f52f1b`: the MCP `list_generation_segments` tool takes the same `operation` option (default `summary`), passes it to the API and names it in the tool description. Its shorter views also name every operation key they leave out, not only the keys the server omitted.

#### Timings
Response size of one segment (`limit=1`) of a large project, in process, on upstream `main` with and without this PR:

| Request | Upstream `main` | With this PR |
|---|---|---|
| default | 876,840 bytes (`operation_json` 873,410) | 3,640 bytes (`operation_json` 163; `lineage` and `mapping` omitted) |
| `view=compact` | 874,384 bytes | 1,184 bytes |
| `operation=full` | parameter ignored, 876,840 bytes | 876,868 bytes, whole `operation_json` |
| `operation=bogus` | ignored, 200 | 422 |

#### Tests
`tests/test_generation_segment_operation_summary.py` (new):
- `test_default_page_omits_large_operation_values_and_names_them`
- `test_full_operation_on_request`
- `test_an_unknown_operation_value_is_refused`
- `test_small_operations_and_empty_plans_lose_nothing`
- `test_summary_rule_is_by_size_not_by_key`
- `test_mcp_asks_for_the_whole_operation_only_on_request`
- `test_mcp_full_view_passes_the_server_summary_and_its_omissions`
- `test_mcp_short_views_name_every_key_they_leave_out`
- `test_mcp_full_operation_is_kept_whole_in_a_short_view`
- `test_mcp_client_sends_operation_only_when_full`

`tests/test_mcp_preview_and_generation.py` is updated for the new field.

#### Suggested release note
The segment listing no longer sends a restore's large per-segment maps on every page. Pass `operation=full` to get them.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

### Round 2 (2026-11-14): fixes for #5, #6 and #7

Pushed to the fork PR branches; the owner answers the CodeRabbit threads.

| Fork PR | Branch | New head | Fix commit | On the epic branch |
|---|---|---|---|---|
| #5 | `abandoned-recast-previews-stop` | `5826e097` | Stop an abandoned recast preview in its target pass too | `5cb8b3a0` |
| #6 | `selection-save-reads-its-own-lineage` | `d6b24102` | Walk a selection's own lineage frontier by frontier, in any session | `b0f38e4b` |
| #7 | `prepare-text-builds-plan-rows-first` | `34933587` | Rebuild prepared plan rows that no longer match or were already stored | `472a3ed6` |

Draft 1's Fix and Tests sections need the new commit and `test_preview_stops_in_the_target_pass_once_its_client_leaves`. Drafts for #6 and #7 should describe the frontier walk, and the fingerprint with consumed rows, instead of the scoped edge read and the list-identity check. Details are in story 15's plan, Code Review, 2026-11-14.

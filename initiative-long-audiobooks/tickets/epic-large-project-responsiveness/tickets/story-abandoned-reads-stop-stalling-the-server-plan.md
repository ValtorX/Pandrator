---
title: 'Abandoned reads stop stalling the server'
type: bugfix
ticket: '7'
created: '2026-11-12'
status: done
baseline_revision: '79e2feeae8a39f73675c3e93bba3b90f8d22213e'
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

**Problem:** A request whose client gave up keeps running to the end. Waitress runs `channel_request_lookahead = 0` (its default), so it stops reading a socket while that socket's request is queued or running and never notices the client left. On 2026-11-11, four MCP clients abandoned 16 large listings at 15 s; their work filled the 12 threads and `/api/v1/auth/status` took 66 s to answer (the first baseline, before stories 3 to 5, saw an hour). Stories 3 to 5 made each heavy read cheaper, but full plan status and revision history still take about 16 s alone, so a burst of them still occupies every thread for minutes after its clients are gone.

**Approach:** Turn on waitress's client-disconnect detection (`channel_request_lookahead = 1`). Waitress then skips a queued request whose client already disconnected. A running `GET`/`HEAD` stops at cooperative checkpoints at the top of each segment step of the identity pass and the cast freeze, which is where the heavy reads spend their time. It answers status 499 `client_closed_request`, which no client reads, and the request is audited as usual.

## Boundaries & Constraints

**Always:** Checkpoints act only inside a `GET` or `HEAD` request served by waitress. Everywhere else they are no-ops: mutations, worker jobs, admission, the opening check, tests without the environ key, and the Flask test client. An abandoned request still runs `after_request`, writing one audit event with status 499 and outcome `failed`, and its teardown unbinds everything it bound. Responses to clients that stay connected are unchanged in fields, status and meaning. Production (`serve_api`) and the timing harness use the same lookahead constant. Tests count calls and statements and use bounded waits. They make no wall-clock assertions.

**Never:** A server-side deadline, a cap on concurrent heavy reads, or rejecting requests with 503. Killing threads, raising `BaseException`, or interrupting a request between a write and its commit. Changing the Manager's waitress server, `scripts/run_web_e2e_server.py`, OpenAPI, or the MCP clients. Touching the WAL or checkpoints (story 8), admission or the opening check (stories 9 to 12).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Client stays | heavy `GET`, client waits | full response as today; checkpoints cost one context-var read each | none |
| Client leaves mid-read | heavy `GET` running; client closes its socket | the next checkpoint raises `RequestAbandoned`; 499 with code `client_closed_request`, one audit event, an INFO log line with the request id and route; the thread is free | none |
| Client leaves while queued | all threads busy; a queued request's client closes | waitress never runs it: no app call, no audit event | none |
| Mutation | `POST`/`PUT`/`PATCH`/`DELETE` whose client leaves | runs to completion as today | none |
| No waitress | environ lacks `waitress.client_disconnected` | checkpoints are no-ops | none |
| Worker thread | identity pass in a job | checkpoints are no-ops (no probe bound in that thread) | none |
| Probe raises | `client_disconnected()` itself raises | treated as still connected; the request continues | logged once at DEBUG |

</frozen-after-approval>

## Code Map

- `pandrator/web/api_server.py:32-55` -- `serve_api` calls `create_server(...)`. Add `REQUEST_LOOKAHEAD = 1` and pass `channel_request_lookahead=REQUEST_LOOKAHEAD`. `cli.py:484` is the only production caller and needs no change.
- Waitress 3.0.2 behaviour this rests on: `channel.py:139-151`. With `readable()` true while `len(requests) <= lookahead`, the main loop reads the socket during a request, and `handle_read` sets `connected = False` on EOF (`:166-169`). `service()` skips a task when `not self.connected` (`:427-430`). `task.py:566-568` puts `environ["waitress.client_disconnected"] = channel.check_client_disconnected`, which is a bare attribute read.
- `pandrator/web/http_lifecycle.py:575-582` -- `_request_context` already stores per-request state in `request.environ` under module keys (`_ACQUISITION_CONTEXT_ENVIRON_KEY`). Bind the probe there for `GET`/`HEAD` and keep the context-var token in environ. `:673-693` -- `_restore_execution_actor` teardown resets it, in the same nested `try/finally` style. `:764-825` -- add `@app.errorhandler(RequestAbandoned)` beside `AccessDenied`. Flask picks it over `Exception` by MRO, so `testing=True` does not re-raise it. `error_response(code, message, status)` at `:158`. `audit.py:159-163` maps 499 to `failed` and clamps the status to 100-599.
- `pandrator/web/generation_audio_identity.py:668` -- `AudioIdentityContext.for_segment`: checkpoint as its first statement. This covers the listing (`generation_segment_reads.py:771`), revision history (`generation_review.py:196`), plan status and `plan_audio_identities` (`:915-928`).
- `pandrator/web/generation_cast_runtime.py:765` -- `for segment in session.scalars(segment_query):` inside `freeze_cast_snapshot`: checkpoint at the top of the loop body. The whole-plan freeze was 90% of a large page before story 5 and still runs whole for plan status.
- Broad handlers that would swallow the exception: `speech_plan_audio_reuse.py:358, 413` (`except Exception` around `for_segment`). They sit on the plan-rebuild write path (`speech_plan_workspace.py:626`, a POST), where checkpoints are unarmed. No other `except Exception` sits on the heavy read files.
- Import cycles fail basedpyright. The new module imports only the standard library.
- Test templates: `tests/test_audio_identity_cost.py` (12-segment cast plan, `services.generation.list_segments`, `for_segment` call counting; lane `web-07-04-serial`, `scripts/test_lanes.py:665`), and `tests/test_session_view_performance.py:51-175` (plan seeding through the real app). For the real-socket case, follow `tests/test_web_contract_boundaries.py:141-170` (`create_server(..., port=0, threads=1)`, `task_dispatcher.shutdown`, `server.close()`). `tests/test_web_api_shutdown.py:290-360` mocks `create_server` inside `serve_api`.
- `_bmad-output/scripts/large_project_timing.py:371-401` -- `serve_child` builds its own `create_server(..., threads=12)`. `:1239-1250` -- the burst always uses `segments_default`, which now takes 0.7 s and is no longer heavy. `:1108-1148` -- argparse. `_bmad-output/scripts/test_large_project_tools.py` holds the harness tests.

## Tasks & Acceptance

**Execution:**
- [ ] `pandrator/web/abandoned_requests.py` (new, standard library only) -- `RequestAbandoned(Exception)`; a `ContextVar` holding the probe or `None`; `bind_disconnect_probe(probe) -> Token` and `reset_disconnect_probe(token)`; `raise_if_abandoned()`, which calls the probe when one is bound and raises on `True`. A probe that raises counts as connected and is logged at DEBUG once per request. Rationale: one dependency-free checkpoint shared by every heavy loop.
- [ ] `pandrator/web/api_server.py` -- `REQUEST_LOOKAHEAD = 1`, passed to `create_server`.
- [ ] `pandrator/web/http_lifecycle.py` -- in `_request_context`, for `GET`/`HEAD` with a callable `waitress.client_disconnected`, bind it and keep the token in environ. Reset it in teardown. Add the `RequestAbandoned` handler: an INFO log with `_request_log_context()`, then `error_response("client_closed_request", "The client closed the connection before the response was ready.", 499)`.
- [ ] `pandrator/web/generation_audio_identity.py`, `pandrator/web/generation_cast_runtime.py` -- one `raise_if_abandoned()` each, at the locations in the Code Map. If the harness burst shows an abandoned plan-status or history read still running more than about 2 s after its client leaves, add checkpoints at the top of other per-segment or per-revision read loops it profiles to, never between a write and its commit, and record each one in Implementation Notes.
- [ ] `tests/test_abandoned_reads.py` (new) -- unit tests of the module (no probe, probe false, probe true, probe raises). Through the 12-segment cast fixture with a bound probe that turns true after N calls: `list_segments` raises `RequestAbandoned` after at most N+1 `for_segment` calls, and a whole-plan cast freeze stops within one segment. Through the Flask test client with `environ_overrides={"waitress.client_disconnected": ...}`: a heavy `GET` with an always-true probe gets 499 and code `client_closed_request`, and writes one audit event with status 499. The same `GET` with an always-false probe gets 200 and the same body as without the key. A `POST` route's handler sees no probe bound. After each request no probe stays bound. Real waitress (`create_server` with `channel_request_lookahead=REQUEST_LOOKAHEAD`, `threads=1`, port 0, served in a thread): an app blocked on an event sees `client_disconnected()` become true after its client closes. A second client's request, queued behind it and closed before it was served, never reaches the app. `serve_api` passes `REQUEST_LOOKAHEAD` (mocked `create_server`).
- [ ] `scripts/test_lanes.py` -- add the new test file to `web-07-04-serial` next to `tests/test_audio_identity_cost.py`.
- [ ] `_bmad-output/scripts/large_project_timing.py` -- `serve_child` passes `channel_request_lookahead=REQUEST_LOOKAHEAD`, imported from `pandrator.web.api_server`. Add `--burst-operation` (default `segments_default`, any `read_matrix` name) and record it in the burst results. Cover the option in `test_large_project_tools.py`.
- [ ] `_bmad-output/fork-release-notes.md` -- one Fixes line: a read whose client gave up stops early, and a queued one never runs, so heavy MCP reads that time out no longer stall the server.

**Acceptance Criteria:**
- Given the synthetic root, when the harness runs a 60 s burst of `plan_status` (4 clients, abandoned at 15 s), then `/api/v1/auth/status` and the small project's default listing right after it answer as fast as before the burst (within 2x their pre-burst medians), and the server is idle within 5 s of the burst ending.
- Given a fresh copy of `real`, when the same burst runs, then the same holds.
- Given the existing listing, plan-read, audit and shutdown tests, when run, then they pass unchanged.

## Implementation Notes

- No checkpoints beyond the two in the Code Map were needed: with them, a 60 s `plan_status` burst (4 clients, abandoned at 15 s) left the server idle by the end of the burst on both `synthetic` and `real`.
- `abandoned_requests.py` keeps the probe and a once-per-request "failure logged" flag in one context-var value. `http_lifecycle.py` resets the probe last in `_restore_execution_actor`, after the actor stack and the lifecycle globals, so the other unbinds happen regardless.
- `tests/test_abandoned_reads.py` imports the cast fixtures from `test_audio_identity_cost` together with its autouse `no_network` guard. The two real-waitress tests call `monkeypatch.undo()` first, because they need loopback sockets (review patch).
- The harness gained `burst_operation()` as well as `--burst-operation`. An unknown name stops with the list of valid names right after the projects are selected, before the server starts (review patch).
- Matrix audit (step 3): the "Worker thread" row had no test. `test_a_worker_thread_never_sees_the_request_threads_probe` now runs the identity pass in a fresh thread while a true probe is bound in the test thread.
- Baseline timings were taken by running a copy of the harness with `REQUEST_LOOKAHEAD = 0` (waitress's default) against a second fresh copy of each root.
- AC evidence: one run per root with `--only small,burst --burst-operation plan_status`, 4 clients for 60 s abandoning at 15 s, so all 16 requests on each root were abandoned. Files are in `%USERPROFILE%\pandrator-perf\results\story7-{syn,real}-{after,baseline}-*.json`. Small-project medians, before → after the burst:

  | | `auth_status` | `segments_default` | Busy after the after-load reads |
  |---|---|---|---|
  | synthetic, this change | 0.022 → 0.028 s (1.3x) | 0.256 → 0.481 s (1.9x, max 0.74 s) | 0.0 s |
  | `real`, this change | 0.023 → 0.018 s | 0.245 → 0.197 s | 0.0 s |
  | synthetic, lookahead 0 | 0.005 → 278.5 s | 0.274 → 5.48 s | 58.2 s |
  | `real`, lookahead 0 | 0.017 → 283.2 s | 0.279 → 4.43 s | 22.3 s |

  With this change, 4 requests were still in flight the instant the burst ended (`inflight_after`), but the after-load reads that followed found no busy time. Neither run recorded audit spools or locked records. On the synthetic root the listing ratio of 1.9x sits close to the 2x limit, and it comes from a single run.

## Plan Change Log

## Review Triage Log

Pass 1, quick lens. 6 findings: 0 high, 0 medium, 5 low, 1 rejected by rule.

- low, rejected: the abandoned-request INFO line is dropped by the production log level. `serve_api`'s `basicConfig()` leaves the root at WARNING, and this is the app logger's only INFO call. The matrix asks for an INFO line, and that is what the code emits. The durable record is the request's audit event with status 499. Making it visible means changing the server's logging setup, which is more than a direct fix for something operators rarely look at.
- low, patch: an unknown `--burst-operation` was rejected only when the burst started, after every earlier phase of a run that can take hours. It is now resolved right after `select_projects`.
- rejected (the fix edits this plan): the evidence for AC1 and AC2 was not recorded. The figures were checked against the result files and are now in Implementation Notes.
- low, patch: the new test module dropped the cast fixture chain's autouse `no_network` guard. It is now imported, and the two real-waitress tests undo it before opening loopback sockets.
- low, patch: the module docstring claimed SQL statement counts. It now names what is counted.
- low, patch: the cast-freeze assertion `parts.call_count <= stopped_after` also passed at 0. It now pins the count.

## Design Notes

Why lookahead and checkpoints, not a deadline or a cap. Every stall measured so far came from work nobody would read. A deadline would also cut off a client still waiting on a slow but legitimate read. A cap needs a 503 path, which changes default responses, and the MCP client does not retry. Disconnect detection stops exactly the abandoned work. The client libraries in play (httpx in the MCP, `requests` in the harness, browser `fetch` on abort) all close the socket when they time out.

Accepted edge: with lookahead on, a client that half-closes its socket (`shutdown(SHUT_WR)`) after sending a request looks disconnected and gets no response. None of the clients above does that.

Checkpoint sketch:

```python
def raise_if_abandoned() -> None:
    probe = _PROBE.get()
    if probe is not None and _disconnected(probe):  # _disconnected swallows probe errors
        raise RequestAbandoned
```

## Verification

`<uv env>` is as defined in `AGENTS.md`.

**Commands:**
- `<uv env> python -m pytest -q tests/test_abandoned_reads.py tests/test_audio_identity_cost.py tests/test_web_api_shutdown.py tests/test_routine_request_writes.py tests/test_session_view_performance.py` -- expected: pass under SQLAlchemy 2.1.8 and 2.0.52
- `<uv env> python scripts/test_lanes.py run web-07-04-serial` -- expected: pass
- `uvx ruff@0.17.3 check .`, `<uv env> vulture`, `<uv env> python scripts/test_lanes.py check`, and `<uv env> --with sqlalchemy==2.0.52 python scripts/check_types.py` -- expected: clean except `nemo_normalizer.py:98`
- `<uv env> python -m pytest -q _bmad-output/scripts/test_large_project_tools.py` -- expected: pass
- `<uv env> python _bmad-output/scripts/large_project_timing.py --root %USERPROFILE%\pandrator-perf\<fresh copy> --only small,burst --burst-operation plan_status --timeout 900` on the synthetic root and on a fresh copy of `real`, with the baseline commit's result for comparison -- expected: as the first two ACs say

## Code Review

### 2026-11-12

0 `decision-needed`, 4 `patch`, 0 `defer`, 15 rejected. Lenses: blind-hunter, edge-case-hunter, verification-gap (no gaps), intent-alignment.

- [x] [Review][Patch] Lookahead lets waitress drop a queued mutation whose client already left [pandrator/web/api_server.py:16]
- [x] [Review][Patch] The release note and tests claim more than the two checkpoints cover [_bmad-output/fork-release-notes.md:17]
- [x] [Review][Patch] list_segments test's N + 1 bound silently depends on the shared fixture size [tests/test_abandoned_reads.py:384]
- [x] [Review][Patch] Harness rejects a root-specific --burst-operation even when no burst runs [_bmad-output/scripts/large_project_timing.py:1191]

Rejected:

- false: the Verify observable has no recorded run. Implementation Notes record one harness run per root, with result files.
- false: R2's 15 s bound under load from clients that keep waiting is not addressed. A cap or deadline is in the plan's Never list.
- low: a proxy, or a client that does not close its socket, never trips the probe. The fallback is the old behaviour, not a regression, and httpx closes the socket on timeout.
- false: only GET and HEAD are armed. That is the frozen intent.
- false: the "stops early" wording was raised only to note that it is consistent.
- false: the E2E server does not use the lookahead. The plan's Never list forbids changing `scripts/run_web_e2e_server.py`.
- false: the read-only POST preview is never armed. Arming only GET and HEAD is the frozen intent.
- false: queued requests that waitress skips leave no audit row or log. The frozen matrix specifies "no app call, no audit event".
- low / false: the harness has no 499 counter, which is rarely visible and would need a new counter path. The drain comments are still accurate.
- false: 499 is audited as `failed`. The frozen Boundaries specify that.
- false: a failing probe is logged at DEBUG once per request. The frozen matrix specifies that.
- low: a half-closed client is treated as gone. That edge is already accepted in Design Notes, and no client in play half-closes. Raised twice, by blind-hunter and edge-case-hunter.
- low: the waitress tests escape `no_network` with `monkeypatch.undo()`. No other fixture on those tests uses monkeypatch, a future break would fail loudly, and the fix would restructure the tests.
- false: queued reads that are skipped leave no audit (edge-case-hunter's version of the item above). The matrix specifies it.

Applied:

- **Queued mutations.** `api_server.create_api_server` builds the server for `serve_api` and for the harness. It installs `ReadDisconnectChannel`, which stops reading a connection while a non-GET/HEAD request heads its queue. Waitress therefore cannot see a mutation's client leave, and the mutation runs. A pipelined mutation queued behind a read can still be dropped along with that read; RFC 9112 tells clients not to pipeline non-idempotent requests.
  - New test: `test_waitress_still_runs_a_queued_mutation_whose_client_left`. With the old `readable()` restored, it fails with a timeout, so it pins the defect.
  - New test: `test_api_server_listeners_use_the_read_only_disconnect_channel`.
- **Release note.** The note now names the listing, plan status and plan history, and says that a change still runs.
- **Plan status and revisions tests.** `test_each_heavy_read_of_a_cast_plan_with_takes_answers_499` covers the listing, plan status and revisions on the 12-segment cast plan once it has matching takes. Each of these reads reaches a checkpoint only once takes exist; a plain plan with no takes answers 200 even with the probe set.
- **list_segments test.** The test now asserts its precondition `max(N) < 2 * len(plan.ids)`.
- **Harness.** It resolves `--burst-operation` only when the burst phase is selected.

Checks:

- Under `<uv env>`, `tests/test_abandoned_reads.py`, `test_audio_identity_cost.py`, `test_web_api_shutdown.py`, `test_routine_request_writes.py`, `test_session_view_performance.py` and `test_web_contract_boundaries.py` give 142 passed and 6 skipped under SQLAlchemy 2.1.8, and the same under 2.0.52.
- Lane `web-07-04-serial`: 324 passed.
- Harness tests: 25 passed.
- ruff, vulture and `test_lanes.py check` are clean. Typecheck shows only `nemo_normalizer.py:98`.
- The timing burst was not rerun after these patches. They change only how mutations are queued, and the burst sends reads.

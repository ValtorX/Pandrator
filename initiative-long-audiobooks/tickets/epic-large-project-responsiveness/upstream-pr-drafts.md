# Upstream PR drafts

PR bodies for this epic's finished stories, on branches built 2026-11-11 from upstream `main` at
`fb72c018` (v.0.14.3) in the worktree `C:\Users\Valtor\Source\Pandrator-pr`. Each branch picks the
epic's commits with `git cherry-pick -n`, leaves out the `docs/release-notes-next.md` hunk (the
maintainer writes it; each body carries a suggested note instead) and passes
`_bmad-output/scripts/check_pr_branch.py` (exit 0). Pushed and opened 2026-11-11 on the owner's
go-ahead as upstream #126 (identity), #127 (MCP) and #128 (listing, on top of #126); the bodies
below show `#IDENTITY` where the opened ones say #126.

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

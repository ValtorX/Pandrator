---
title: "Large-project responsiveness: baseline"
story: "Reproduce the stall and record a baseline"
epic: epic-large-project-responsiveness
measured: 2026-11-11
checkout: 0ed54b07
---

# Large-project responsiveness: baseline

Story 1 of the epic. Every number here comes from the harness in
`_bmad-output/scripts/`, run against the checkout at `0ed54b07` on two data
roots: a synthetic root seeded by `large_project_seed.py` and a local copy of
the owner's real data. Results hold timings, counts, statuses and function or
SQL names only.

## Summary

- **The stall reproduces on demand.** On the 5,167-segment book every full
  read is far over 15 s on both datasets: segment listings 257-281 s whatever
  the `limit`, `fields` or `view`; plan status and revision history over
  900 s (26-31 minutes of server time each); a non-active revision 579-683
  s. Only the `summary=true` variants and `auth/status` are fast.
- **The small book is slow too.** 189 segments take 12-13 s to list (15 s
  for 100 rows) and 54-62 s for plan status and revision history, because the
  cost is per segment of the whole revision, not per row returned.
- **Admission and the opening check reproduce the live numbers.** Admission
  703 s (real copy) and 754 s (synthetic) for one segment, 710 s and 766 s for
  15; the opening check 404 s and 434 s, 399 s and 431 s. The live install's
  baselines are 640-741 s and 328-452 s.
- **One cause dominates.** Under casting, each cast span's audio identity
  rebuilds and deep-copies the whole TTS service catalogue
  (`tts_service_catalogue._shared_service_configs`): 86-97% of the profiled
  time of every slow operation. SQL is under 1% of the read time.
- **Abandoned reads pile up.** A client timeout does not stop the server's
  work. One minute of four clients giving up on the large listing at 15 s
  left all 12 server threads busy, and `auth/status` itself then timed out at
  900 s.
- **The synthetic project agrees with the real copy** on every verdict but
  one in a single full run of the final seed, within 2-18% on the timings,
  after four seed versions. The exception is the small 100-row listing, which
  sits on the 15 s line (real 15.5 s, synthetic 14.7 s); see Synthetic
  against real.
- **Proposed targets:** admission ≤ 60 s and the opening check ≤ 45 s on the
  5,167-segment book, for 1 to 15 segments.

## How it was measured

- **Server.** `large_project_timing.py` starts the checkout's app on the data
  root in a child process (`create_app(testing=False)`, waitress with 12
  threads, one in-process worker, GPU probe patched out), on a free port that
  is never 8097 or 8098. It mints an API token in-process on that root and
  calls the endpoints over HTTP with the MCP tools' default parameters.
- **Read matrix.** For the large and the small project: `auth/status`; segment
  listing (`cursor=0&limit=50&audio_version_side=after`) as default,
  `view=compact&take_history=selected`, `fields=id,ordinal,status`,
  `limit=100` and the most recent non-active revision; plan status with and
  without `summary=true`; revision history (`limit=50`) with and without
  `summary=true`; and the non-active revision's detail. Three runs each; an
  operation slower than 60 s runs once. The request timeout is 900 s rather
  than the default 120 s, so most slow reads report a real duration.
- **Abandoned work.** A client timeout does not stop the server. The harness
  wraps the app in a request counter, and after a timeout it waits until the
  server is idle before the next measurement, recording how long the
  abandoned request kept running ("busy ... more").
- **Order.** Small reads, large reads, the two admissions, then a 60 s burst
  of four clients repeating the large default listing and giving up at 15 s,
  like the MCP. The small reads run again immediately after the burst, while
  its abandoned requests are still running.
- **Admission and opening check.** `POST generation-admissions` with
  `operation=regenerate` for the large project's first 1 and first 15
  segments. Admission time is the admission job's `finished_at -
  started_at`. The opening check runs from the child run's progress event
  "Checking speech plan and voice references" to "Loading speech provider
  settings", read from the job's event log. The harness holds the run at the
  second event and cancels it through the API, so no TTS engine runs.
  `auth/status` is sampled every 2 s while each admission and check runs.
  The real copy's full run (and seed 3's) also sampled the small project's
  default listing every 2 s; because that read is CPU-heavy it slowed the
  jobs, so the real admission figures come from a separate clean run
  (`--skip-reads --skip-burst`), and the script now samples the listing only
  when `--listing-interval` asks for it. Seed 4's full run sampled only
  `auth/status`.
- **Verdict.** "slow" when any run took over 15 s (the MCP timeout) or timed
  out; otherwise "fast".
- **Profile mode** (`--profile`) runs the same requests in-process through
  Flask's test client under cProfile, with a SQLAlchemy listener timing every
  statement, and runs the admission and the opening check with
  `Worker.run_once()`. cProfile slows Python code roughly 4x here, so profile
  seconds are for ranking, not for comparison with the timing tables.

## Environment

- Windows 11, 16 logical CPUs, uv environment (`<uv env>` from AGENTS.md):
  Python 3.11.15, SQLAlchemy 2.1.8 (CI's version, not `pixi.lock`'s 2.0.52),
  SQLite 3.53.1, Flask 3.1.3, waitress 3.0.2.
- The owner's app was not running during any run: nothing listened on 8097 or
  8098, and every results file records `owner_app_listening: false`.
- Several harness processes ran at once (up to five: the two timing runs, two
  profile runs and a seed). Each is single-threaded Python and the machine has
  16 logical CPUs, so they competed little, but the timings are not from an
  idle machine.

## Data

The real copy was taken from the install's `data/` folder with the app closed:
`pandrator.sqlite3` with its `-wal` and `-shm` files, `voices/`, `artifacts/`,
and `migration-web-v1.json`. The marker file was not in the agreed list but is
required: without it, startup treats the database as an unfinished legacy
import and moves it aside for an empty one, which the first attempt did to a
throwaway copy. The timing script now refuses a root without it. Take audio
under `sessions/` (5.1 GB) was not copied; reuse decisions read only artifact
metadata, so no measured path reads those files. Each run used a fresh copy
of `%USERPROFILE%\pandrator-perf\real\`, never the copy itself.

Shape counts, from `large_project_seed.py --measure` (counts only). "Large"
is the plan closest to 5,000 segments and "small" the one closest to 189; the
harness picks them the same way on both roots.

| | Real copy | Synthetic (final seed) |
|---|---|---|
| Large plan, revisions (segments) | 3 (4,894; 5,167; 5,167 active) | 3 (4,893; 5,167; 5,167 active) |
| Large plan, takes on those revisions | 4,894; 11,144; 11,161 | 4,893; 11,165; 11,180 |
| Large plan, optimized segments (active) | 15 | 15 |
| Large plan, cast slots (retained references) | 27 (narrator, 2 categories, 24 characters) | 27 (same split) |
| Large plan, adopted speaker plans | on revisions 2 and 3 (4 plans in all) | on revisions 2 and 3 (2 plans) |
| Large plan, segments with 1 / 2 / 3 dialogue speakers | 1,556 / 223 / 1 | 1,137 / 132 / 0 (short segments cannot take a dialogue part, so the 30.1% and 4.3% draws land lower) |
| Small plan, revisions (segments) | 4 (191; 189; 200 draft, 11 removed, no audio; 189 active) | 4 (191; 189; 200 draft, 11 removed, no audio; 189 active) |
| Small plan, takes (rev. 1, 2, active) | 1,146; 378; 379 | 1,146; 378; 379 |
| Small plan, cast slots | 7 | 7 |
| Third plan (uncast, one revision) | 11,424 segments, 3,131 takes | 11,424 segments, 3,084 takes |
| Segment text, mean / max characters | 50.6 / 475 (large) | 52.8 / 476 (large) |
| Casting enabled, an audio.cpp model | yes (large and small) | yes (large and small) |
| Voices with one reviewed sample each | 37 | 37 |
| All segments / takes / artifacts | 27,422 / 32,235 / 19,799 | 27,420 / 32,225 / 20,787 |
| Takes per segment (1 / 2 / 3 / 6) | 8,025 / 9,080 / 1,629 / 191 | 7,977 / 9,037 / 1,672 / 191 |
| Generation runs; jobs; job events | 26; 252; 105,540 | none |

## Results

Cells give median / max over three runs, or one run where the first took over
60 s. "busy ... more" is how long the server kept working on the abandoned
request after the client's 900 s timeout. The real column is one full run on
a fresh copy of the real data; the synthetic column is one full run of the
final seed (seed 4) on a fresh copy of `synthetic`, from the patched scripts,
11:00-15:01 UTC, with a profile run on another copy overlapping it
throughout. Server start-up took 3.1-3.5 s on every root.

### Reads, small project (189 segments)

| Operation | Real copy | Verdict | Synthetic | Verdict | Agree |
|---|---|---|---|---|---|
| `auth_status` | 0.02 s / 0.03 s | fast | 0.02 s / 0.03 s | fast | yes |
| `segments_default` | 12.75 s / 12.81 s | fast | 12.14 s / 12.19 s | fast | yes |
| `segments_compact` | 12.74 s / 12.79 s | fast | 12.11 s / 12.12 s | fast | yes |
| `segments_fields` | 12.73 s / 12.73 s | fast | 12.01 s / 12.09 s | fast | yes |
| `segments_limit100` | 15.50 s / 15.54 s | slow | 14.70 s / 14.73 s | fast | **no**, borderline (see below) |
| `segments_non_active` | 9.84 s / 10.01 s | fast | 9.10 s / 9.14 s | fast | yes |
| `plan_status` | 61.52 s (1 run) | slow | 53.77 s / 54.09 s | slow | yes |
| `plan_status_summary` | 0.12 s / 0.17 s | fast | 0.11 s / 0.11 s | fast | yes |
| `revisions` | 62.16 s (1 run) | slow | 53.78 s / 54.20 s | slow | yes |
| `revisions_summary` | 0.03 s / 0.03 s | fast | 0.03 s / 0.03 s | fast | yes |
| `revision_non_active` | 0.11 s / 0.17 s | fast | 0.09 s / 0.11 s | fast | yes |

The listings sit just under the 15 s limit with 50 rows and around it with
100: R2's small-project part fails, or nearly fails, without any load.

### Reads, large project (5,167 segments)

| Operation | Real copy | Verdict | Synthetic | Verdict | Agree |
|---|---|---|---|---|---|
| `auth_status` | 0.02 s / 0.03 s | fast | 0.01 s / 0.03 s | fast | yes |
| `segments_default` | 281.21 s (1 run) | slow | 260.39 s (1 run) | slow | yes |
| `segments_compact` | 273.05 s (1 run) | slow | 257.40 s (1 run) | slow | yes |
| `segments_fields` | 266.44 s (1 run) | slow | 257.93 s (1 run) | slow | yes |
| `segments_limit100` | 268.45 s (1 run) | slow | 263.45 s (1 run) | slow | yes |
| `segments_non_active` | 275.06 s (1 run) | slow | 259.99 s (1 run) | slow | yes |
| `plan_status` | timed out at 900 s, busy 666 s more | slow | timed out at 900 s, busy 746 s more | slow | yes |
| `plan_status_summary` | 0.40 s / 0.42 s | fast | 0.34 s / 0.40 s | fast | yes |
| `revisions` | timed out at 900 s, busy 623 s more | slow | timed out at 900 s, busy 968 s more | slow | yes |
| `revisions_summary` | 0.03 s / 0.03 s | fast | 0.02 s / 0.02 s | fast | yes |
| `revision_non_active` | 579.42 s (1 run) | slow | 683.10 s (1 run) | slow | yes |

### Admission and opening check, large project

Only `auth/status` sampled every 2 s beside the jobs. The real column comes
from a separate clean run (`--skip-reads --skip-burst`), because the real
copy's full run also sampled a small listing (below); the synthetic column is
the full seed-4 run.

| Segments | | Real copy | Synthetic |
|---|---|---|---|
| 1 | `POST generation-admissions` | 0.22 s, 202 | 0.09 s, 202 |
| 1 | admission job (queue wait) | 703.4 s (0.04 s) | 753.8 s (0.20 s) |
| 1 | opening check (run queue wait) | 403.7 s (0.11 s) | 433.9 s (0.13 s) |
| 1 | cancel request at the provider line | 0.18 s; run canceled | 0.07 s; run canceled |
| 1 | `auth/status` during both, median / max | 0.08 s / 0.77 s (516 samples) | 0.06 s / 0.53 s (560) |
| 15 | admission job | 710.2 s | 765.6 s |
| 15 | opening check | 398.6 s | 430.9 s |
| 15 | `auth/status` during both, median / max | 0.09 s / 0.80 s (514) | 0.06 s / 0.61 s (565) |

An earlier clean run of seed 4 alone, overlapping other harness runs rather
than a profile, measured 713.4 s / 409.1 s (1 segment) and 724.6 s / 402.2 s
(15). The 6-8% spread between the two seed-4 runs is machine load.

With a small-project listing also sampled every 2 s (the real copy's full run,
and seed 3's, before the script stopped sampling it by default):

| Segments | | Real copy | Synthetic (seed 3) |
|---|---|---|---|
| 1 | admission / opening check | 1,595.5 s / 1,296.4 s | 1,767.9 s / 1,401.0 s |
| 15 | admission / opening check | 1,629.8 s / 1,135.3 s | 1,714.5 s / 1,217.4 s |
| 1 | small listing during, median / max | 18.6 s / 53.6 s (129 samples) | 20.6 s / 50.7 s (133) |
| 15 | small listing during, median / max | 17.3 s / 57.3 s (128) | 18.5 s / 55.2 s (130) |
| 1 and 15 | `auth/status` during, max | 0.59 s and 1.54 s | 0.77 s and 0.53 s |

One heavy read beside the job makes admission 2.3-2.5x and the check
2.8-3.4x slower: both are CPU-bound Python in one process, so they share the
interpreter. The listing itself goes from 13 s to a median of 17-21 s and a
maximum of 57 s. No `database is locked` failure and no read error occurred
in any run. The server logged "Audit writer busy; event saved to pending
audit spool" once in the clean real admission run, twice in seed 3's full
run and once in seed 1's run (R5 territory, story 5).

### After a burst of abandoned reads

Four clients repeated the large default listing for 60 s, each giving up at
15 s: 16 requests, all abandoned. Every one of them kept running. With 12
server threads, the 16 requests filled the pool and queued the rest, and the
in-flight counter itself could not answer within 30 s.

| Operation, small project, right after the burst | Real copy | Verdict | Synthetic | Verdict | Agree |
|---|---|---|---|---|---|
| `auth_status` | timed out at 900 s | slow | timed out at 900 s | slow | yes |
| `segments_default` | timed out at 900 s | slow | timed out at 900 s | slow | yes |
| `segments_compact` | timed out at 900 s | slow | timed out at 900 s | slow | yes |
| `segments_fields` | timed out at 900 s | slow | timed out at 900 s | slow | yes |
| `segments_limit100` | 152.87 s (1 run) | slow | timed out at 900 s | slow | yes |
| `segments_non_active` | 93.95 s (1 run) | slow | 29.19 s / 32.54 s | slow | yes |
| `plan_status` | 539.50 s (1 run) | slow | 430.89 s (1 run) | slow | yes |
| `plan_status_summary` | 7.57 s / 7.81 s | fast | 8.07 s / 9.17 s | fast | yes |
| `revisions` | 455.63 s (1 run) | slow | 396.75 s (1 run) | slow | yes |
| `revisions_summary` | 0.60 s / 0.70 s | fast | 0.66 s / 0.66 s | fast | yes |
| `revision_non_active` | 1.00 s / 1.09 s | fast | 1.08 s / 1.31 s | fast | yes |

The server answered nothing for about an hour after one minute of abandoned
MCP reads: the first four (real) or five (synthetic) small reads each waited
out the full 900 s, and those abandoned reads joined the backlog in turn. It
was idle again 46 s (real) and 513 s (synthetic) after the last after-load
read. No restart was needed, but no client would have waited that long.

## Synthetic against real

The seed went through four versions. Each was timed against the real copy,
and each change answered a gap in slow or fast verdicts.

| Seed | What it had | Large listing | Small `limit100` | Small `revision_non_active` | Admission / check (1 seg.) | Verdicts against real |
|---|---|---|---|---|---|---|
| 1, plain | Real shape counts, no casting, default TTS settings | 0.3 s | 0.3 s | 0.2 s | 68 s / 66 s | Every slow read came out fast |
| 2, cast | + audio.cpp voices, cast with narrator, 2 categories and 24 characters as retained references, casting on | 223 s | 12.3 s | 16.7 s | not run | Small `limit100` fast (real 15.5 s, slow); large plan status timed out as on real |
| 3, cast + speakers | + adopted speaker plans with dialogue XML on the last earlier and the active revision; the real model setting, empty narrator voice, no generation prompt | 296 s | 17.2 s | 22.1 s | 1,768 s / 1,401 s, with a sampled listing | Small `revision_non_active` slow (real 0.1 s, fast) |
| 4, final | + the small plan's draft revision (200 segments, 11 removed, no audio) before the restore | 260 s | 14.7 s (16.3 s in an earlier small-only run) | 0.1 s | 754 s / 434 s | All agree except small `limit100`, on the 15 s line |

- Seed 1 shows the stall needs casting: without cast resolution, identities
  are cheap and every read is fast.
- Seed 2 gets the large reads slow but runs about 20% fast, and the small
  100-row page lands under 15 s where the real one is over. The real book's
  speaker plan adds dialogue parts, so more spans per segment and more
  catalogue rebuilds.
- Seed 3 matches the large reads within 5% and most small reads within 11%,
  but the harness picks each plan's most recent non-active revision, which on
  the real small plan is a draft with no audio. Seed 3 had none, so its
  non-active revision was a full revision with takes: 22 s where the real one
  answers in 0.1 s.
- Seed 4 adds the draft and is the seed's default. Its full run agrees on
  every verdict but one, including after the burst.
- **Remaining gap: small `segments_limit100`.** Real 15.5 s (slow), seed 4
  14.7 s (fast) in its full run and 16.3 s (slow) in an earlier small-only
  run. The row sits within 5% of the 15 s limit, so machine load alone flips
  it: the real full run shared the machine with up to five harness processes,
  seed 4's full run with one. Seed 4 also has fewer dialogue segments than the
  real book (1,269 against 1,780 segments with a speaker part, because short
  synthetic segments cannot take one), and dialogue parts are what the cost
  scales with. Raising `one_speaker_fraction` and `two_speaker_fraction` so
  the drawn counts match the real ones is the next seed change; it was not
  made here, so the final run stays the single run of one seed.

Remaining differences, none of which changes a verdict: seed 4's small
listings run 5-8% and its small plan status and history 13% faster than
real; its large listings 2-7% faster, while its large non-active revision
detail runs 18% slower; its admission 7-8% and opening check 7-8% slower. After the
burst the two roots drift further apart, as expected when the timing depends
on which abandoned requests still hold the interpreter.

## Where the time goes

Profile mode, on copies of both roots. Seconds are under cProfile (about 3.3x
to 4.5x the timed duration) and serve for ranking. cProfile follows only the
main thread. SQL time, though, is measured on the engine, so it also counts
the worker's lease monitor, which reads its job row every 0.2 s from another
thread; that is where most "jobs" SELECTs in the admission and check profiles
come from, and they are not per-segment work.

One mechanism dominates every slow operation: computing a segment's audio
identity under casting rebuilds the TTS service catalogue
(`logic.tts_service_catalogue._shared_service_configs`) for each cast span.
Every operation in the read matrix and both admission sizes has a profile;
"real" rows come from copies of the real data, "seed 3" and "seed 4" rows
from the synthetic roots (the large plan is the same in both seeds).

| Operation | Profile | Profiled | Catalogue rebuilds: share, calls | SQL statements, seconds |
|---|---|---|---|---|
| large `segments_default` | real | 1,223 s | 97%, 6,614 | 1,870, 1.5 s |
| large `segments_default` | seed 3 | 1,255 s | 97%, 6,991 | 2,105, 1.8 s |
| large `segments_compact` | seed 4 | 1,217 s | 97%, 6,991 | 2,096, 1.6 s |
| large `segments_fields` | seed 4 | 1,216 s | 97%, 6,991 | 2,095, 1.8 s |
| large `segments_limit100` | seed 4 | 1,546 s | 97%, 7,207 | 3,296, 2.1 s |
| large `segments_non_active` | real | 1,210 s | 97%, 6,614 | 1,896, 1.7 s |
| large `plan_status` | real | 6,682 s | 95%, 70,916 | 427,297, 16.8 s |
| large `plan_status` | seed 3 | 6,989 s | 95%, 72,453 | 435,331, 18.3 s |
| large `revisions` | seed 4 | 8,394 s | 95%, 72,451 | 435,341, 20.3 s |
| large `revision_non_active` | real | 2,494 s | 95%, 25,719 | 154,817, 6.3 s |
| large `plan_status_summary` | real | 0.9 s | none | 117, 0.02 s |
| small `segments_default` | seed 4 | 68.7 s | 97%, 472 | 1,335, 0.1 s |
| small `segments_compact` | seed 4 | 72.8 s | 97%, 472 | 1,326, 0.1 s |
| small `segments_fields` | seed 4 | 70.1 s | 97%, 472 | 1,325, 0.1 s |
| small `segments_limit100` | seed 4 | 87.9 s | 97%, 670 | 2,457, 0.2 s |
| small `segments_non_active` | seed 4 | 50.8 s | 98%, 351 | 970, 0.1 s |
| small `plan_status` | seed 4 | 274.5 s | 96%, 2,742 | 16,961, 0.7 s |
| small `revisions` | seed 4 | 285.9 s | 96%, 2,740 | 16,834, 0.8 s |
| small `plan_status_summary`, `revisions_summary`, `revision_non_active`, `auth_status` | seed 4 (and real for `revision_non_active`) | 0.01-0.6 s | 2 or none | 20-155, under 0.01 s |
| admission, 1 segment | real | 2,602 s | 91%, 25,708 | 200,241, 188 s (mostly the lease monitor) |
| admission, 1 segment | seed 3 | 2,654 s | 92%, 26,376 | 205,163, 190 s (same) |
| admission, 15 segments | seed 4 | 2,675 s | 92%, 26,404 | 205,320, 195 s (same) |
| opening check, 1 segment | real | 1,323 s | 87%, 19,301 | 191,422, 83 s (same) |
| opening check, 1 segment | seed 3 | 1,424 s | 86%, 19,801 | 196,509, 86 s (same) |
| opening check, 15 segments | seed 4 | 1,354 s | 88%, 19,801 | 196,246, 77 s (same) |

The large plan's `auth_status` and `revisions_summary` were not profiled;
both answer in 0.03 s and do no identity work.

What the profiles show, per operation:

- **Segment listing, every variant.** `limit`, `fields` and `view` do not
  change the cost: default, compact and fields profiles are identical (6,991
  rebuilds each on the large plan, 472 on the small one). Even for a page of
  50, the read freezes a cast snapshot for the whole revision, and the cast
  resolution of every span goes through `generation_rendering._binding_result`
  → `tts_service_catalogue.get_service_config` without the request's cache,
  so `_shared_service_configs` rebuilds the catalogue each time: about 1.3
  rebuilds per segment. A page of 100 adds per-row identities on top (7,207
  rebuilds large, 670 small), which is why it is the slowest listing. Inside
  the rebuild, `copy.deepcopy` takes about 550 s of own time (1.1 billion
  calls), `preset_voice_metadata.decorate_preset_voices` 98 s and
  `speech_capabilities.decorate_service_capabilities` 14 s. SQL is under 0.2%
  of the time. The small plan pays the same per segment of its revision.
- **Plan status and revision history (full).** Both run
  `grouped_revision_history` / `revision_history` with audio reuse, which
  computes identities for every segment of every revision: 15,227
  `_with_performance` calls and about 72,000 catalogue rebuilds on the large
  plan in each, 2,740 on the small one. Secondary:
  `validate_retained_cast_resolution` (17,744 calls, 126 s in the real plan
  status profile) repeats project, session and token lookups per cast span
  (124,212 `session_project` calls, each two or three SELECTs), which is why
  the statement count reaches 435,000. The plan status handler also loads
  every active segment (`speech_plan_workspace.py:413-425`) but that is
  negligible next to the identities.
- **Non-active revision detail.** The same identity pass for one revision of
  5,167 segments (25,719 rebuilds). The small project's non-active revision is
  a draft without audio, so it does no identity work and answers in 0.1 s.
- **Summary variants and `auth/status`.** Under 1 s: no identity work, at
  most two catalogue builds.
- **Admission.** `prepare_start` computes the identity of all 5,167 segments
  even to regenerate one, then freezes the cast snapshot, so it pays the
  catalogue rebuild about 5 times per segment. 1 and 15 segments cost the
  same (26,376 and 26,404 rebuilds).
- **Opening check.** `plan_audio_identities` over the whole revision: 5,167
  `for_segment` calls and 19,301-19,801 catalogue rebuilds (86-88%). Next:
  `validate_retained_cast_resolution` (6,400-6,568 calls, about 60 s
  profiled) and JSON decoding of rows (about 141,000 decodes, 68-80 s own
  time profiled; the run's settings snapshot alone is 7.9 MB). 1 and 15
  segments cost the same.
- **Abandoned reads.** A request the client gave up on keeps running to the
  end: timed-out large plan status and revision history kept the server busy
  10-16 more minutes. See the burst results above.

## Proposed targets

Measured without competing reads, on the 5,167-segment plan:

| | Live install (epic baseline) | Real copy, this checkout | Synthetic (seed 4, clean run) | Proposed target |
|---|---|---|---|---|
| Admission, 1 segment | 640-741 s | 703 s | 713 s | **≤ 60 s** |
| Admission, 15 segments | 640-741 s | 710 s | 725 s | **≤ 60 s** |
| Opening check, 1 segment | 328-452 s | 404 s | 409 s | **≤ 45 s** |
| Opening check, 15 segments | 328-452 s | 399 s | 402 s | **≤ 45 s** |

Reasoning:

- Both are dominated by the per-span catalogue rebuild: 91% of the real
  copy's profiled admission (2,373 of 2,602 s) and 87% of its profiled check
  (1,146 of 1,323 s). Story 2 removes it by building the catalogue once per
  distinct settings input within a request or job, which
  `AudioIdentityContext` already intends with its `_service_config_cache`
  but the cast path does not pass on.
- cProfile profiles only the thread it runs on, so the residual outside the
  rebuild is all main-thread work; the lease monitor's SQL, counted in the
  statement totals, is not part of it. The residual is 229 s of the profiled
  admission and 177 s of the profiled check. Scaled by each job's profile
  overhead (2,602 / 703 = 3.7x and 1,323 / 404 = 3.3x), removing the
  catalogue rebuild alone is estimated to leave about **62 s of admission**
  and **54 s of opening check**: just over the first target and well over the
  second.
- What that residual is made of, from the same real-copy profiles:
  - admission: JSON decoding of rows (75 s own time, 315,316 decodes),
    `generation_cast_runtime.validate_retained_cast_resolution` (58 s, 6,424
    calls, of which `require_current_session_access` 27 s and
    `speech_inputs._graph` 24 s are per-span lookups), and the rest of cast
    binding in `generation_rendering._binding_result` outside the catalogue;
  - opening check: JSON decoding (68 s own time, 141,405 decodes; the run's
    settings snapshot alone is 7.9 MB), `validate_retained_cast_resolution`
    (58 s, 6,400 calls), `speech_performance.compile_performance` (17 s) and
    `tts_language_preflight.resolve_tts_language_support` (16 s).
- The targets stay about ten times faster than today: admission ≤ 60 s for 1
  to 15 segments, the opening check ≤ 45 s. Meeting them needs the catalogue
  fix plus a cut in those secondary costs: per-span retained-resolution
  lookups done once per binding rather than per span, and the large JSON
  snapshot decoded once rather than per use. If the measured times land well
  below the targets, story 8 should tighten the admission target.
- Neither job is a request, so the 15 s MCP limit does not apply to them. It
  does apply to the reads sampled while they run (R6): `auth/status` stayed
  under 1 s throughout (max 0.8 s), while a small-project listing sampled
  every 2 s took 13-60 s and slowed the job it ran beside by about 2.3x, so a
  heavy read beside admission is itself a stall (story 8).
- Admission and check cost the same for 1 and 15 segments because both
  compute the identity of the whole plan. A cheaper path for a small
  regenerate (identities only for the selected segments where that is safe)
  would change both by orders of magnitude, but "a take is only reused when
  its audio identity truly matches" may require the whole plan. The targets
  above do not assume it.

## Limits of this baseline

- The large reads ran once each: every one is far over the 60 s repeat limit,
  so their "median" is one sample. Small reads ran three times; their spread
  was under 0.5 s.
- Two large reads, full plan status and full revision history, timed out at
  900 s on both roots, so their total server time is the 900 s plus the busy
  time recorded after it.
- Profiles are spread over four runs and three roots (real, seed 3, seed 4),
  because one large plan status or revision history takes about two hours
  under cProfile. Each operation has at least one profile; the large plan's
  `auth_status` and `revisions_summary` have none (they answer in 0.03 s).
  Two early profile runs were stopped after their plan status section, before
  the script saved after each operation, so the real large `segments_non_active`
  and `plan_status` and seed 3's large `segments_default` and `plan_status`
  sections exist only in the printed output the cost table was taken from;
  the rest are in `results/` beside the data roots. The script now rewrites
  its results file after every operation.
- The database is a copy without take audio. No timed path opens take files,
  so this should not matter, but the live install could differ in disk cache
  and WAL size.
- The checkout is `0ed54b07` with SQLAlchemy 2.1.8 from ranges, not the
  installed fork build or `pixi.lock`'s versions. The admission and check
  times nonetheless land inside the live install's 640-741 s and 328-452 s.
- Timings come from a machine running other harness processes (up to five
  CPU-bound Python processes on 16 logical CPUs). The real copy's full run
  overlapped with up to four others; seed 4's full run overlapped with the
  seed 4 profile run for its whole length (11:00-15:01 UTC); the real clean
  admission run overlapped with the two earlier timing runs and two
  profiles. Load moves timings by several percent, enough to flip the small
  100-row listing, which sits on the 15 s line.
- The synthetic seed has no job history (the real copy has 252 jobs and
  105,540 job events), fewer dialogue segments (see Data), and two adopted
  speaker plans where the real large plan has four plans in various states.
  The fewer dialogue segments probably explain why seed 4 runs 2-13% faster
  than real on most reads, and may decide the borderline small 100-row
  listing; the others changed no verdict.

## Rerunning

```
<uv env> python _bmad-output/scripts/large_project_seed.py --root %USERPROFILE%\pandrator-perf\synthetic
<uv env> python _bmad-output/scripts/large_project_timing.py --root %USERPROFILE%\pandrator-perf\synthetic --timeout 900 --job-timeout 3600
<uv env> python _bmad-output/scripts/large_project_timing.py --root %USERPROFILE%\pandrator-perf\synthetic --profile --only small,large.segments_compact,large.segments_fields,large.segments_limit100,large.revisions,admission --admission-sizes 15
<uv env> python _bmad-output/scripts/large_project_seed.py --measure <copy of a data root>
```

The second command is seed 4's full run, the third its profile run, each
started on its own fresh copy of `synthetic` (the timing script refuses a
root with queued or running jobs, and a run leaves its jobs finished, but
startup still writes to the root). Seeding takes about 25 minutes, most of
it computing the app's audio identity for every seeded take. The full timing
run took 4 h 1 min (11:00-15:01 UTC), about an hour of it waiting out the
after-burst reads. The profile run took 4 h 49 min (11:00-15:49 UTC), 2 h 20
min of it the large revision history. `--only` takes operation names,
`small` or `large`, `small.<operation>` or `large.<operation>`, `admission`
and `burst`. Results are rewritten after each operation, so a stopped run
keeps what it measured. Time a copy of a real root, never the original: startup migrates,
reconciles and writes to it. Results go to `results/` beside the data root.

## After story 2

Story 2 passes the request's catalogue cache through the cast-binding path and
remembers, within one identity context or one cast freeze, which retained pins
already passed their database proof. Measured on the story 2 working tree on
top of `0ed54b07` (uncommitted), with `large_project_timing.py --skip-burst
--timeout 900 --job-timeout 3600` on fresh copies of `real` and `synthetic`
(seed 4) under `%USERPROFILE%\pandrator-perf\work-2-2\after-real` and
`after-synthetic`, run at the same time on one machine (uv environment,
SQLAlchemy 2.0.52, no other harness process). Each cell gives the median over
three runs; the before column is the story 1 table above (real copy, then
synthetic), and a timeout counts as 900 s.

### Reads, small project (189 segments)

| Operation | Before, real / synthetic | After, real | After, synthetic |
|---|---|---|---|
| `auth_status` | 0.02 s / 0.02 s | 0.02 s | 0.02 s |
| `segments_default` | 12.75 s / 12.14 s | 0.42 s | 0.41 s |
| `segments_compact` | 12.74 s / 12.11 s | 0.32 s | 0.34 s |
| `segments_fields` | 12.73 s / 12.01 s | 0.38 s | 0.33 s |
| `segments_limit100` | 15.50 s / 14.70 s | 0.46 s | 0.50 s |
| `segments_non_active` | 9.84 s / 9.10 s | 0.27 s | 0.23 s |
| `plan_status` | 61.52 s / 53.77 s | 1.58 s | 1.43 s |
| `plan_status_summary` | 0.12 s / 0.11 s | 0.12 s | 0.11 s |
| `revisions` | 62.16 s / 53.78 s | 1.47 s | 1.27 s |
| `revisions_summary` | 0.03 s / 0.03 s | 0.03 s | 0.03 s |
| `revision_non_active` | 0.11 s / 0.09 s | 0.09 s | 0.10 s |

### Reads, large project (5,167 segments)

| Operation | Before, real / synthetic | After, real | After, synthetic |
|---|---|---|---|
| `auth_status` | 0.02 s / 0.01 s | 0.01 s | 0.02 s |
| `segments_default` | 281.21 s / 260.39 s | 6.39 s | 6.64 s |
| `segments_compact` | 273.05 s / 257.40 s | 6.15 s | 6.26 s |
| `segments_fields` | 266.44 s / 257.93 s | 6.25 s | 6.33 s |
| `segments_limit100` | 268.45 s / 263.45 s | 6.60 s | 6.70 s |
| `segments_non_active` | 275.06 s / 259.99 s | 6.39 s | 6.48 s |
| `plan_status` | over 900 s (timed out, busy 666 s more) / over 900 s (busy 746 s more) | 42.63 s | 43.27 s |
| `plan_status_summary` | 0.40 s / 0.34 s | 0.40 s | 0.29 s |
| `revisions` | over 900 s (busy 623 s more) / over 900 s (busy 968 s more) | 41.83 s | 42.54 s |
| `revisions_summary` | 0.03 s / 0.02 s | 0.03 s | 0.03 s |
| `revision_non_active` | 579.42 s / 683.10 s | 20.11 s | 20.17 s |

### Admission and opening check, large project

| Segments | | Before, real / synthetic | After, real | After, synthetic |
|---|---|---|---|---|
| 1 | admission job | 703.4 s / 753.8 s | 104.8 s | 103.6 s |
| 1 | opening check | 403.7 s / 433.9 s | 85.8 s | 85.6 s |
| 1 | `auth/status` during, median / max | 0.08 s / 0.77 s | 0.12 s / 1.32 s | 0.12 s / 0.20 s |
| 15 | admission job | 710.2 s / 765.6 s | 97.0 s | 94.9 s |
| 15 | opening check | 398.6 s / 430.9 s | 88.8 s | 86.8 s |
| 15 | `auth/status` during, median / max | 0.09 s / 0.80 s | 0.09 s / 0.24 s | 0.09 s / 0.21 s |

All requests answered 200 and both roots agree within 2-3% on every row (the
admission job within 1-3%, the opening check within 2%).

### What this says

- **Every read that was over its limit by two orders of magnitude is down by
  40 to 100 times or more.** Large listings 260-281 s to 6.1-6.7 s, small
  listings 9-15 s to under 0.5 s (all under 15 s), small plan status and
  revision history 54-62 s to 1.3-1.6 s, large non-active revision 579-683 s
  to 20 s.
- **Large plan status and revision history went from a 900 s timeout (26-31
  minutes of server time) to about 42 s.** Both still run over the 15 s MCP
  limit; the large non-active revision (20 s) too, and the large listing (6.4
  s) is inside it only by a factor of 2.3. The cost that remains is the
  per-segment Python work in identity computation, which was not profiled for
  this section; stories 3 and 4 own the listing's page logic and the summary
  paths.
- **Admission is 7x faster (about 95-105 s) and the opening check 4.7-5x
  faster (about 86-89 s).** Neither meets the proposed targets (admission
  at most 60 s, opening check at most 45 s), and the 62 s and 54 s the
  baseline estimated for removing only the catalogue rebuild were
  optimistic by about 1.6x on both. The remaining time is the secondary costs
  named in the baseline (JSON decoding of large rows and the settings
  snapshot, per-span cast binding outside the catalogue, performance
  compilation), which story 8 owns; if story 8 holds the targets, this is the
  starting point to profile from.
- **Statements no longer grow per retained span.** The regression test
  `tests/test_audio_identity_cost.py` records it on a 12-segment cast plan:
  11 catalogue builds and 271 statements before, 1 build and 75 statements
  after (139 for the first six segments before, 63 after).
- The baseline's profile-share claims (catalogue rebuild 86-97% of every
  slow read) were not re-measured with `--profile`; the timings above are the
  evidence, so the surviving cost is unattributed here.
- Not measured: the burst of abandoned reads (run with `--skip-burst`), which
  does not depend on this change.

## Replan

Measured 2026-11-11 for the epic's re-slice, on the committed story 2 code
(`c5c0dcfb`, after its review patches), with `large_project_timing.py
--timeout 900 --job-timeout 3600` and the burst on, on a fresh copy of `real`
under `%USERPROFILE%\pandrator-perf\work-replan\` (uv environment, SQLAlchemy
2.0.52). Only one root ran at a time, unlike "After story 2", where both ran
at once; that and the review patches are why admission and the check are
lower here. Medians over three runs. A second fresh copy was profiled
(`--profile --runs 1`) for the large page of 100, plan status, the non-active
revision and admission of 1 segment.

Story numbers in the sections above are from before the re-slice: their
stories 3 to 12 are now 5, 4, 6, 7, 8, 9, 10, 11, 12 and 13, and a new story 3
cuts the shared identity-pass cost found below (epic Notes, 2026-11-11).

| Operation, large project | After story 2 (real) | Replan (real) |
|---|---|---|
| `segments_default` | 6.39 s | 6.28 s |
| `segments_limit100` | 6.60 s | 6.32 s |
| `plan_status` | 42.63 s | 46.50 s |
| `revisions` | 41.83 s | 42.12 s |
| `revision_non_active` | 20.11 s | 15.87 s |
| Admission, 1 / 15 segments | 104.8 s / 97.0 s | 70.6 s / 83.9 s |
| Opening check, 1 / 15 segments | 85.8 s / 88.8 s | 69.4 s / 65.8 s |

Small-project reads are unchanged (all under 1.6 s). During admission
`auth/status` stayed under 0.8 s.

**Burst.** Four clients sent default large listings for 60 s and gave up at
15 s: all 16 were abandoned, since four at once take longer than 15 s each.
The first request after it, `auth/status`, took 65.7 s, and small default
listings took 6.7-7.2 s instead of 0.4 s; the server was idle again by the end
of the after-load reads. No restart was needed, but R4 and R2's after-load
part are not met.

**Where the time goes now** (profiled; SQL is 0.04-3.7 s of each):

- A page of 100 (25 s profiled): about 90% is one whole-plan
  `freeze_cast_snapshot`, with about 17.6 million `copy.deepcopy` calls.
- Plan status (176 s profiled): the identity pass runs for all 3 revisions
  (15,228 `_with_performance` calls). Within it, `compile_performance` (46 s),
  through `resolve_tts_language_support` and `audio_cpp_catalogue.package_metadata`,
  and the cast freezes (67 s) lead.
- Non-active revision (64 s profiled), admission (152 s) and opening check
  (109 s): the same pattern for one pass, with `compile_performance` and its
  language lookup run 6,400 times per pass.

## After story 3

Story 3 remembers each segment-independent language-support result for the
length of one identity pass or cast freeze, stops deep-copying the controls
that the identity pass and the cast freeze only read, and (the next copies the
timing task required, see the story's Implementation Notes) copies the flat
inspection settings and capability records without a call per immutable
value and remembers a pass's validated characters and cast by object
identity. Measured on the story 3 working tree on top of `c5c0dcfb`
(uncommitted), with `large_project_timing.py --skip-burst --timeout 900
--job-timeout 3600` on a fresh copy of `real` under
`%USERPROFILE%\pandrator-perf\work-2-3\final-timing` (uv environment,
SQLAlchemy 2.0.52, one root running at a time, no other harness process).
Medians over three runs; the Replan column is the section above.

| Operation, large project | Replan (real) | After story 3 (real) |
|---|---|---|
| `segments_default` | 6.28 s | 3.12 s |
| `segments_compact` | n/a | 2.72 s |
| `segments_fields` | n/a | 2.71 s |
| `segments_limit100` | 6.32 s | 3.02 s |
| `segments_non_active` | n/a | 2.95 s |
| `plan_status` | 46.50 s | 16.47 s |
| `plan_status_summary` | n/a | 0.39 s |
| `revisions` | 42.12 s | 16.23 s |
| `revisions_summary` | n/a | 0.02 s |
| `revision_non_active` | 15.87 s | 6.62 s |
| Admission, 1 / 15 segments | 70.6 s / 83.9 s | 59.9 s / 60.0 s |
| Opening check, 1 / 15 segments | 69.4 s / 65.8 s | 52.8 s / 53.2 s |

The large non-active revision is at 6.62 s against the 7.95 s the story set
(the slowest of the three runs was 6.67 s). Small-project reads are unchanged (all under
1.1 s); `auth/status` stayed under 0.3 s during both admissions.

**First measurement, before the extra copies were removed** (the three
changes the plan named only, on a fresh copy of `real`, medians of three):
`revision_non_active` 10.68 s, `plan_status` 26.09 s, `revisions` 26.14 s,
`segments_limit100` 4.50 s, admission 73.3 s / 67.1 s, opening check 61.4 s /
57.9 s. That missed 7.95 s, so the profile below was taken and the next
repeating copies were removed.

**Profile after the change** (`--profile --runs 1`, fresh copy of `real`):

- Large non-active revision: 18.2 s under cProfile, against 64 s at the
  Replan and 30.9 s after only the plan's three changes. `copy.deepcopy` calls
  fell from 12.9 million to 3.5 million, of which 417,000 are `_deepcopy_dict`
  nodes; `resolve_tts_language_support` no longer appears among the costs.
- Page of 100: 8.6 s under cProfile against 25 s, with 2.2 million
  `copy.deepcopy` calls against 17.6 million. About 6.7 s of it is the one
  whole-plan `freeze_cast_snapshot`.
- What is left in both is the markup work for each segment
  (`parse_speech_markup`, `_character_catalog`, `remap_markup`, per span
  `apply_resolved_binding` and `validate_retained_cast_resolution`), which
  later stories own.

**Equivalence.** The regression tests in `tests/test_audio_identity_cost.py`
compare every identity and the frozen cast snapshot with a reference pass that
drops the language memo, copies the controls and re-validates the characters
for every span. On the 12-segment cast plan that reference makes 14
`package_metadata` calls, 14 language-support endpoint lookups, 12 controls
copies and 42 character validations; the identity pass now makes 1, 1, 0 and 3,
the same for 6 segments as for 12.

## After story 4

Story 4 makes the MCP tools `pandrator_get_speech_plan_status` and
`pandrator_list_speech_plan_revisions` request the summary variants, then fetch
the detail of the active revision only (a separate request) and copy its five
audio reuse fields onto that item. Default HTTP responses are unchanged, so the
full `plan_status` and `revisions` stay at about 16 s and nothing requests them
by default. Measured on the story 4 working tree on top of `24c1dd36`
(uncommitted), with `large_project_timing.py --timeout 900 --skip-burst
--skip-admission` on fresh copies of `real` and `synthetic` under
`%USERPROFILE%\pandrator-perf\work-2-4\` (uv environment, SQLAlchemy 2.0.52,
one root running at a time). Medians over three runs, large project.

| Operation | real | synthetic |
|---|---|---|
| `plan_status_summary` | 0.39 s | 0.28 s |
| `revisions_summary` | 0.04 s | 0.03 s |
| `revision_active` | 6.61 s | 6.56 s |
| `revision_non_active` | 6.44 s | 6.28 s |
| `segments_non_active` | 2.96 s | 3.01 s |
| `plan_status` (full, reference) | 16.68 s | 16.74 s |
| `revisions` (full, reference) | 16.22 s | 15.69 s |

An MCP status call now costs `plan_status_summary` + `revision_active` (about
7.0 s on `real`), and a history call `revisions_summary` + `revision_active`
(about 6.6 s), each request separately under the 15 s client timeout. The five
operations the acceptance criterion names are all at most 6.61 s.

## After story 5

Story 5 stops a segment listing from paying for the whole plan. The identity
pass (the page's audio reuse status) now runs only when the response carries
`audio_reuse_reason` or `has_reusable_take` (the default and compact views, or a
`fields` list naming one of them), and when it runs it freezes the cast for the
page's segments, not every segment of the revision. Measured on the story 5
working tree on top of `35e82ff9` (uncommitted), with
`large_project_timing.py --timeout 900 --skip-burst --skip-admission` on fresh
copies of `real` and `synthetic` under
`%USERPROFILE%\pandrator-perf\work-2-5\` (uv environment, SQLAlchemy 2.0.52,
one root running at a time, no other harness process). Medians over three runs,
large project; the story 3 column is the "After story 3" section above.

| Operation, large project | Story 3 (real) | real | synthetic |
|---|---|---|---|
| `segments_default` | 3.12 s | 0.70 s | 0.81 s |
| `segments_compact` | 2.72 s | 0.40 s | 0.51 s |
| `segments_fields` | 2.71 s | 0.08 s | 0.06 s |
| `segments_limit100` | 3.02 s | 0.77 s | 0.89 s |
| `segments_non_active` | 2.95 s | 0.64 s | 0.68 s |
| `plan_status` (not in scope) | 16.47 s | 16.70 s | 16.54 s |
| `revisions` (not in scope) | 16.23 s | 16.18 s | 16.20 s |
| `revision_non_active` (not in scope) | 6.62 s | 6.49 s | 6.64 s |

All five listings are far inside 15 s and below their story 3 figures.
Small-project segment listings are all under 0.35 s. The plan
status and revision history reads are untouched by this story.

**Profile of `segments_limit100`** (`--profile --runs 1 --only
large.segments_limit100`, fresh copy of `real`): 2.2 s under cProfile against
8.6 s after story 3, with 210 statements (0.025 s in SQL). The cast freeze
(`freeze_cast_snapshot`) is 0.18 s of it, called once for the page's 100
segments, against about 6.7 s for the whole-plan freeze. What remains is
`for_segment` (0.88 s, 100 calls), two `freeze_performance_snapshot` calls
(0.94 s: the assembly-pause and identity freezes, which story 5 leaves whole by
design), and `copy.deepcopy` (1.2 million calls).

**Work the regression tests count** (`tests/test_audio_identity_cost.py`,
12-segment cast plan, through `services.generation.list_segments`):

| Page | Statements | `build_render_parts` calls | Segments the cast freeze visits |
|---|---|---|---|
| `limit=1, fields=["id"]`, before | 70 | 13 | 12 |
| `limit=1, fields=["id"]`, after | 24 | 0 | 0 |
| `limit=1`, default, before | 74 | 13 | 12 |
| `limit=1`, default, after | 46 | 2 | 1 |
| Default page of all 12, before and after | 100 | 24 | 12 |

**One behaviour change** (owner decision, 2026-11-11): a cast voice that fails
to resolve for a segment outside the page no longer marks the page's rows
`audio_identity_unknown`; each page reports its own rows' identity, and the
page that holds the failing segment still reports all its rows unknown. This
matches how a retained-input run validates its own segments.


## After story 8: admission (story 9)

Story 9 measured admission again at `26d18b53` (stories 2 to 8 in) and
removed the one long writer hold it found. Timings and counts only. Fresh
copies of `real` and `synthetic` under `work-2-9/before/` and
`work-2-9/after/`, one root at a time, no profiler, SQLAlchemy 2.0.52 under
the uv environment. The timing script ran with `--skip-reads --skip-burst
--admission-sizes 1,15`; the writer-holds script with its default sizes
(1 and 15) and `--runs 3`. Admission is the job that prepares and queues the run; the
check is the run's opening speech plan and voice check.

### What changed

`inherit_edit_copy_audio`, run inside admission's final write transaction,
reconciled an edit copy by loading every segment and take of the revision and
of its ancestors, to find that nothing needed copying. It now selects its
candidates in SQL (ancestor takes whose artifact is not yet on the target
segment) and hydrates only those, so a reconcile with nothing to copy loads no
segment or take row. The same takes are copied, in the same order, with the
same status and activation. A regression test keeps the old algorithm as a
reference and compares both on an edit copy over two ancestors, and another
counts loaded rows and `SELECT` statements.

### Writer holds (`admission_writer_holds.py`, fresh `real` copy, 3 runs per size)

Admission's commit transaction is the writer hold that decides whether other
requests wait: SQLite has one writer, and a request whose audit write waits
longer than the 250 ms busy timeout spools its audit event.

| | Before (planning measurement) | After, run 1 / 2 / 3 | After, median |
|---|---|---|---|
| Commit transaction, 1 segment | 1.51-1.52 s | 0.69 / 0.69 / (0.67, fallback) s | 0.69 s |
| Commit transaction, 15 segments | 1.51-1.52 s | 0.68 / 0.63 / 0.62 s | 0.63 s |
| `inherit_edit_copy_audio` inside it | 0.83-0.93 s | 0.05 s | 0.05 s |
| Writer holds over 250 ms per admission | 1 | 1 | 1 |
| Writer holds over 250 ms in the check | 0 | 0 | 0 |

These are from the second measurement (`work-2-9/after/real-holds-2`), taken
after review made the script certify each run. A run counts only when its
admission job succeeded and the commit transaction's pieces were recorded. In
about one run in six the pieces were not recorded. That run's longest
admission hold was then kept as a fallback and left out of the median. The
first measurement (0.78 / 0.62 / 0.60 s and 0.62 / 0.68 / 0.80 s, medians
0.62 s and 0.68 s) had one such run, 1 segment run 3, which it counted
without saying so.

The code review found the cause. The script kept its hold state per thread.
When the credentials cache reloaded through its own session during a hold,
the reload's commit ended the recorded hold early. In admission that happens
while `start_in_session` enqueues the check job (`jobs._event`, which redacts
the job event through the credentials store). A probe on the seeded root saw
it in 2 of 12 runs. The script now keys each hold on the connection that
began it. A cut-short hold ended before `start_in_session` returned, so it
was never a certified run, and the medians above stand. The excluded 0.67 s
run was most likely the commit transaction cut short.

Target: median at most 0.75 s. Met, with little margin: single runs in the
first measurement reached 0.78 and 0.80 s, because the remaining pieces vary
with machine load. They are
the transaction's guards and the stored run snapshot, which this story keeps:
`snapshot_guard` twice (0.07-0.16 s each), `deepcopy` and `stable_hash` (three
calls each, 0.10-0.17 s each), `session.flush` (0.07 s over 65 calls), the
settings receipt (0.06 s). The check's writers stay under 0.16 s.

### Admission with the UI's reads sampled (`large_project_timing.py`)

`--listing-interval 2 --ui-interval 10` samples the small project's listing
every 2 s and, every 10 s, the large project's run list, plan status summary,
generation summary and first listing page, beside `auth/status` every 2 s,
during admission and the check. The run list, plan status summary and first
listing page each decode or list a large plan, so the sampler itself slows the
admission it measures; the generation summary is light.

| Run, root | Admission 1 / 15 segments | Check 1 / 15 | Audit spools |
|---|---|---|---|
| Before, `real`, clean | 13.4 / 13.6 s | 5.2 / 4.8 s | 5 |
| After, `real`, clean | 9.6 / 9.3 s | 4.2 / 4.0 s | 1 |
| Before, `real`, small listing every 2 s | 15.4 / 15.6 s | 6.0 / 5.9 s | 7 |
| After, `real`, listing and UI reads sampled | 16.9 / 17.4 s | 5.1 / 5.0 s | 10 |
| Before, `synthetic`, clean | 11.7 / 12.6 s | 5.0 / 5.3 s | 2 |
| After, `synthetic`, clean | 10.7 / 11.1 s | 4.7 / 4.6 s | 2 |
| Before, `synthetic`, small listing every 2 s | 16.2 / 17.1 s | 6.8 / 6.9 s | 5 |
| After, `synthetic`, listing and UI reads sampled | 14.8 / 15.7 s | 5.5 / 5.1 s | 9 |

Every admission is far inside the 60 s target, and inside the 30 s the owner
set afterwards. The sampled "after" rows add
the four large reads, which the "before" rows did not sample, so their
admission times and spool counts are not comparable with the "before" rows;
a clean run is: the real copy's spools fell from 5 to 1. The remaining spools
in a sampled run come from the commit transaction's 0.6-0.8 s, which is still
longer than the 250 ms busy timeout, and from the sampled heavy reads
themselves.

Reads sampled during admission and the check (median / max, seconds; samples
per size are 2 to 14, so the maxima are the figures to read):

| Read | `real`, 1 segment | `real`, 15 | `synthetic`, 1 | `synthetic`, 15 |
|---|---|---|---|---|
| `auth/status` | 0.04 / 0.42 | 0.03 / 0.53 | 0.03 / 0.11 | 0.03 / 0.41 |
| Small project listing | 0.99 / 3.35 | 0.96 / 3.43 | 0.96 / 1.24 | 0.57 / 1.35 |
| Large run list (compact, 25) | 2.34 / 2.48 | 2.33 / 3.10 | 0.17 / 0.26 | 0.19 / 0.58 |
| Large plan status summary | 3.69 / 4.30 | 4.01 / 4.15 | 2.70 / 3.55 | 2.59 / 3.35 |
| Large generation summary | 0.06 / 0.57 | 0.03 / 0.57 | 0.06 / 0.62 | 0.06 / 0.20 |
| Large first listing page | 4.47 / 5.52 | 4.64 / 5.01 | 3.03 / 3.73 | 3.21 / 3.91 |

The slowest sampled read is 5.5 s, inside the 15 s limit. No sampled read
timed out, and no response was a database-locked error (database-locked
records: 0 in every run).

### Why reads stalled during admission

No writer is held while admission prepares: story 1 found none, and the writer
holds above confirm one long hold per admission. The only long writer hold is
the commit transaction, which this story cut from about 1.5 s to a median of
0.63-0.69 s by removing the edit-copy reconcile's full scan. Story 1's stall came from
heavy reads sharing one interpreter with the admission, not from a lock: a
listing or plan status read decodes the plan in Python, and the admission's own
preparation does the same, so together they take the one core's time in turns.
Admission itself already met its target before this story.

### Owner decisions (2026-11-12)

- The web UI repeats `generation-runs?view=compact&limit=25` every 10 s while
  a run is active. On the large real project each call decodes up to 25 run
  snapshots: 2.3-2.5 s (3.1 s at worst) of one core, which is a quarter of
  that core for as long as a tab is open. It is inside 15 s. The owner made
  it a new story 12 in this epic.
- Admission now takes 9-11 s with nothing else running, and at most 17.4 s
  under the sampled load measured here. The owner tightened the admission
  target from 60 s to 30 s.

## After story 9: the reviewable route (story 10)

The workflow's `generate_audio` stage starts its run through the reviewable
generation route. It froze the speech snapshot and ran the whole-plan identity
pass inside one `BEGIN IMMEDIATE` transaction. Now both run in a plain session
with no writer open, and a short write transaction re-proves the revision,
compares a fresh `snapshot_guard` against the one taken before the freeze and
inserts the run.

Measured with `admission_writer_holds.py --route reviewable --runs 3` (the run
itself stubbed out) on a fresh `real` copy, large plan of 5,167 segments. The
payload is built as the `generate_audio` stage builds it: the flattened
`settings`, `resolved_settings_snapshot` and `settings_hash` come from
`WorkflowService.resolve_stage`, and the revision's own `_source_artifact_id`
is kept as the source (the stage picks a different source on this copy).

| | route writer holds | longest hold per run | median | holds over 250 ms containing the freeze or identity pass |
| --- | --- | --- | --- | --- |
| Before | 1 per run | 9.545, 9.475, 9.457 s | 9.475 s | 3 of 3 runs |
| After | 1 per run | 0.170, 0.179, 0.103 s | 0.170 s | 0 |

The target (median at most 0.75 s, no slow hold containing the check) is met.
The tool times only `BEGIN IMMEDIATE` transactions, so a write from the plain session
(an implicit `BEGIN`) would not show in it. What shows the plain session takes no
writer is the regression test, whose zero-busy-timeout writes from another
connection commit during the freeze and the identity pass.
The one remaining hold is the insert transaction, with a `snapshot_guard`
inside it. An earlier run of the tool called the route with `settings: {}` and
no resolved snapshot, so the freeze and identity pass ran over an empty-settings
snapshot. It understated the workflow: before 0.805, 0.800, 0.800 s (median
0.800 s), after 0.145, 0.141, 0.080 s (median 0.141 s). The figures above
replace it.

Opening check, `large_project_timing.py --skip-reads --skip-burst
--admission-sizes 1,15` on fresh copies, against the 45 s target (the original
baseline was 328-452 s):

| Dataset | Admission size | Opening check |
| --- | --- | --- |
| real | 1 | 4.0 s |
| real | 15 | 4.0 s |
| synthetic | 1 | 4.0 s |
| synthetic | 15 | 4.0 s |

The opening check is run by `run_generation` after admission and was not
changed here; the route's own change does not alter it, so these figures
confirm it still meets the target.

## After story 10: compact run list (story 11)

`GET .../generation-runs?view=compact&limit=25` used to hydrate every selected run
with its whole `settings_snapshot_json` and every latest assembly with its whole
`settings_json`, then deep-copy the manifests, only for the compact projection to
discard them. A compact read now loads only the six snapshot keys the payload reads
and the assembly's `audio_version_side`, extracted in SQL. The full view and
single-run reads are unchanged.

`large_project_timing.py --skip-burst --skip-admission --runs 5` on a fresh copy of
`real` (`work-2-11/after/real`), in the uv environment with SQLAlchemy 2.0.52, no
profiler. The in-process rows time `list_runs(limit=25)` on the session with the
largest snapshots, three calls each.

| Operation | Before | After |
| --- | --- | --- |
| `ui_runs_compact` (large) median | 2.3-2.5 s (story 9) | 0.65 s (max 0.68 s) |
| `list_runs(limit=25, view=compact)` in process | 1.56-1.62 s | 0.56 s (0.558, 0.571, 0.557) |
| `list_runs(limit=25)` full view in process | 1.56-1.62 s | 1.5-1.65 s (1.652, 1.498, 1.597) |

The in-process before figure is the planning profile on the old code: two
uninstrumented runs of `list_runs(limit=25)`, which decoded everything for either
view. The full view is unchanged by this story.

The target (median at most 0.8 s) is met. The other operations are unchanged by this
story (`plan_status` 16.6 s and `revisions` 16.4 s remain slow).

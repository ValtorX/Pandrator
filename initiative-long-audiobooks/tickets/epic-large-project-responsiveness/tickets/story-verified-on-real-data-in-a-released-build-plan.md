---
title: "Verified on real data in a released build"
type: verification
ticket: 14
created: '2026-11-13'
status: done
baseline_revision: '5fd1df192c5ddbabda8a856c65c47762245a9fba'
route: oneshot
risk: medium
---

## Intent

Verify every line of the epic on the owner's released fork build, `v.0.14.4+fork.1` (`5fd1df19`: upstream 0.14.4, which carries every epic 2 fix that changed code, plus fork-only test and release tooling), installed with `fork_slot.py` on the stock 30-second lease. The timing harness runs on fresh copies of the real data; a small regenerate and a cancel during the opening check run on the live app with its real engine.

## What was done

- The fork merged upstream 0.14.4 as `0.14.4+fork.1` (only the version conflicted; the contract, typed client and static bundle regenerated identical to upstream's), CI was green after rerunning two flaky E2E tests (`output.spec.ts:269`, `review-workspaces.spec.ts:172`), and `v.0.14.4+fork.1` was tagged and installed. `/api/v1/health` reported `0.14.4+fork.1` with the migration complete.
- A fresh copy of the real data was taken with the app stopped, through SQLite's backup API (`PRAGMA integrity_check` ok). The previous copy is kept as `real-2026-11-10`.
- The harness ran three times on fresh copies of it, from the installed commit, with the owner's app running beside it: A, the read matrix and admission with the UI's reads sampled; B, the burst on `plan_status` abandoned at 15 s; C, admission with `--cancel-during-check`.
- On the live app, through MCP: a 2-segment regenerate with the real engine. The owner cancelled a run during its opening check from the UI. Results, timelines and the reads used are in `baseline-2-1.md`, "Story 14: verified on real data in a released build".

## Result

Every requirement met (`baseline-2-1.md` has the table): listings 0.40-0.77 s (R1), small listing 0.19-0.28 s (R2), MCP status 0.38 s plus 6.33 s for the active revision's detail (R3), first `auth/status` 0.03 s after 16 abandoned `plan_status` reads (R4), no `database is locked` and the WAL settling at 0.7 MiB (R5), admission 11.8 / 12.5 s with the UI's reads sampled against 30 s (R6), the opening check 4.5 s against 45 s with no route holding the writer through it (R7), and a cancel during the check closing job and run as cancelled 0.4-3.5 s after the request in the harness and 0.75 s on the live app with no TTS request sent (R8). The small regenerate generated 2 segments in 16 s with no verification warnings.

## Findings

- New story 15 in this epic, from what the run showed outside the paths the epic had fixed: re-running Segment narration held the writer for 20-50 s (the stage selection save and the Prepare text job), MCP's `regenerate_segments` timed out at 15 s on first use while its admission took 5.5 s, every MCP segment-listing page of the book carried its active revision's 904 KB restore `operation_json`, and a missing-only admission's write with 5,159 preserved-take clones is unmeasured.
- For epic 4 (R15, R16): a re-run of Segment narration made a new plan revision that reused none of the book's audio although 5,150 of its 5,167 segments kept their text at the same position, and the UI's Run again gave no feedback, skipped its confirmation and let repeated clicks start a revision each. Recorded in epic 4's notes.
- Not defects: the cancel MCP sent itself arrived after a one-segment run had finished and was logged `job.cancel_ignored`, because a model-driven round trip is slower than the 4.7 s check; the owner's cancel from the UI verified R8. The tray's "Open Pandrator" opens the plain address, so an expired browser sign-in showed an owner-password form on an install with no password; signing in through "Open setup / recovery" then "Open Pandrator" works (upstream Manager 0.9.31 behaviour).
- The owner's book was left as it was before the check: plan revision 3 active with audio for all 5,167 segments, its prepared-text input set back to v2, and segment 0's take restored after the MCP test.

# Upstream PR drafts

These are the PR bodies for the three branches built on 2026-11-10 from upstream `main` at `83d4835d` (after #120 merged). Opened 2026-11-10 as upstream #121 (temp root), #122 (LF rules) and #123 (lease fix), from branches pushed to `origin`. The worktree `C:\Users\Valtor\Source\Pandrator-pr` holds them.

Each branch passes `_bmad-output/scripts/check_pr_branch.py` (exit 0). Pushing each branch to `origin` and opening its PR against `lukaszliniewicz/Pandrator:main` is the owner's step. Build each branch again first if upstream has moved.

If the owner approves, open a PR with:

```bash
git -C ../Pandrator-pr push origin <branch>
gh pr create --repo lukaszliniewicz/Pandrator --base main --head ValtorX:<branch> --title "<title>" --body-file <body>
```

---

## 1. `keep-generation-heartbeats-during-identity-check`

Branch head `2f643fac`, picked from fork `415aaa8a`. The `docs/release-notes-next.md` hunk is left out, because the maintainer writes release notes in release commits.

**Title:** Keep generation heartbeats alive during the opening identity check

**Body:**

### Summary
On a very long book, a generation run fails about 31 seconds in with "A voice reference changed after generation was queued", although nothing changed.

### Reproduction
1. Queue a generation run on a book with several thousand segments, on the stock 30-second job lease.
2. Watch the job. It stays on "Checking speech plan and voice references" until its lease expires, then fails.

`test_identity_check_keeps_heartbeats_and_renewed_leases_working` reproduces this with a slow identity check.

### Root cause
`_run_generation` sets the run to `running`. The next query autoflushes, so the speech-plan and voice-identity check then runs while holding the SQLite write lock. On a long plan that check takes minutes. During that time the job's own heartbeat cannot commit, and the lease expires. Voice access is then denied, the identity check swallows the denial, and the run is reported as a changed voice reference.

### Fix
- Commit the `running` status in a short transaction.
- Run the check in a plain session with no writer open. It cannot use a pinned snapshot, because the lease proof must see renewed leases.
- Prove the lease again before reporting changed inputs, so a lost lease is no longer reported as a voice change.
- Clear pause and cancel requests when the check fails the run.

### Tests
- New tests in `tests/test_generation_workspace_execution.py`. All 21 tests in that file pass on Windows with a short `--basetemp`. Without one, five existing tests fail on path length, which PR 3 fixes.
- Field result: on a real book of about 5,000 segments, the check took 6 min 55 s on the stock 30-second lease and then generated. Before the fix it failed at about 31 s.

---

## 2. `keep-browser-build-files-lf`

Branch head `b81cce4d`, picked from fork `3b4484ce` and `0a07679d`. Both applied cleanly.

**Title:** Keep browser build inputs and outputs LF on every platform

**Body:**

### Summary
On a Windows checkout with `core.autocrlf=true`, the browser checks and build don't match CI.

### Reproduction
On such a checkout:
1. Run `npm --prefix web run quality`. Prettier reports hundreds of files.
2. Run `npm --prefix web run build`. About 300 paths under `pandrator/web/static` change.

### Root cause
The build's version hash in `web/svelte.config.js` covers raw source bytes. CRLF copies of `web/` therefore hash, format and build differently from CI's Ubuntu rebuild.

### Fix
- `.gitattributes` forces LF on `web/`, `pandrator/web/static/` and `pandrator_manager/recovery_ui/static/model-groups.js`.
- `check-static-assets.mjs` refuses a checkout whose resolved line endings are CRLF. It prints the refresh steps, because an existing checkout keeps its CRLF copies until they are checked out again.
- `test_frontend_architecture.py` guards the rules.

### Tests
On this branch, in a Windows checkout refreshed to LF:
- `npm ci` and `npm run build` succeed.
- `check:assets` reports "Browser assets match source".
- `git status` shows no change under `pandrator/web/static`.
- `test_frontend_architecture.py`: 11 passed.

---

## 3. `windows-test-temp-root`

Branch head `57a1b9b7`. Fork commits `0ce71320`, `a5918f5b` and `f6aefbe2`, squashed into one commit with a message that describes the final design.

**Title:** Give Windows test runs a short temp root

**Body:**

### Summary
Without Windows long paths enabled, tests that build session bundles, takes or generation output fail with `FileNotFoundError` under pytest's default temp root, unless `--basetemp` is set by hand.

### Reproduction
On Windows without long paths, run:

```bash
python -m pytest tests/test_generation_workspace_execution.py
```

On upstream `main` (`83d4835d`): 5 failed, 10 passed. Each failure is a missing `tts-*.wav` under `%TEMP%\pytest-of-<user>\...`.

### Root cause
pytest's default root under `%TEMP%`, plus the nested session, generation and take folders, exceeds 260 characters.

### Fix
- On Windows, a run with no `--basetemp` and no `PYTEST_DEBUG_TEMPROOT` gets its own folder under `<temp drive>:\tmp\pt`.
- The root is restricted to the current user.
- Each run holds a lock file that pruning respects, and only the newest `tmp_path_retention_count` finished runs are kept.
- A foreign or network root falls back to pytest's default with a warning.
- Other platforms and an explicit `--basetemp` are unchanged.

### Tests
- `tests/test_windows_temp_root.py` is new and is added to a lane.
- The same generation test file passes 15 of 15 with no options.
- The temp-root and two voice test files: 55 passed.
- `scripts/test_lanes.py check` passes.

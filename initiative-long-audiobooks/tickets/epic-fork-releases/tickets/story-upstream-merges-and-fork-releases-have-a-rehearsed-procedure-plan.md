---
title: 'Upstream merges and fork releases have a rehearsed procedure'
type: chore
ticket: '13'
created: '2026-11-10'
status: done
baseline_revision: 'ddbf21a990f5c8206d08261b3e4208898cfa258b'
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/tickets/story-fork-main-carries-the-lease-fix-as-a-tagged-fork-version-plan.md'
  - '{project-root}/_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/tickets/story-browser-checks-and-builds-match-ci-from-a-windows-checkout-plan.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing written says how upstream changes reach fork `main`, how generated files are rebuilt after them, how a `+fork.N` version and tag are cut, or how an upstream PR branch stays free of fork-only changes. Without it each merge and release is improvised, and a regenerated contract or a lost `+fork` segment slips through.

**Approach:** Write the procedure as a fork-only document on `fork-files`, with a small check that reports fork-only changes on a PR branch, point `AGENTS.md` at it, then rehearse it on throwaway scratch branches and record the results.

**Verify (from the ticket):** Following the procedure on a scratch branch merges current upstream main with regenerated files passing their checks, and a scratch PR branch's diff against upstream main shows no fork-only change.

**Decisions (owner, 2026-11-10):**

- `upstream/main` is still `112501b2`, already in fork `main`, so the rehearsal merges `upstream/fix/contextual-early-repair-20261109` as a stand-in for upstream's next main, into a scratch branch from fork `main`.
- Commits and merges are allowed on the two throwaway scratch branches only, in a temporary worktree. Both branches and the worktree are deleted afterwards and never pushed. Nothing else is committed.
- Story 2 is done, so the release procedure includes the install step through `fork_slot.py`.
- The owner submits nothing upstream until this initiative is done. The PR-branch procedure and check are still written and rehearsed, for later use.
- The plan stays whole at about 2,400 tokens.

## Boundaries & Constraints

**Always:** The procedure covers three paths: upstream into fork `main` (preview, merge, conflict rules, regeneration in `AGENTS.md`'s order, checks, CI comparison), cutting a fork version (`X.Y.Z+fork.N` in `pyproject.toml` only, in its own commit, `web/package.json` untouched, contract keeps the public version, annotated tag `v.X.Y.Z+fork.N`), and upstream PR branches (from `upstream/main`, picked commits, the check). Every permanent step (merge into `main`, commit, tag, push) is marked as needing the owner's go-ahead. It says pixi is optional and that `pixi.lock` then can't be updated. Scratch branches and worktrees are deleted after the rehearsal and never pushed.

**Never:** Change product code, tests or generated files on a code branch. Put the procedure, the check or any fork-only file on a code branch. Merge, tag or push anything real. Put local paths or the book's title in the procedure.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Clean PR branch | Branch from `upstream/main` + a picked fix | Check prints no finding, exits 0 | — |
| Fork-only path | Diff adds `_bmad*/`, `.agents/`, `.claude/`, `CLAUDE.md`, `AGENTS.md` or `skills-lock.json` | Check names the path, exits 1 | — |
| Fork version | An added line contains `+fork` | Check names file and line, exits 1 | — |
| Local path | An added line contains a Windows user path | Check names file and line, exits 1 | — |
| Upstream version bump | Merge conflicts on `pyproject.toml`'s version | Procedure: take upstream's version, append `+fork.1` | `test_fork_build_identifies_itself` fails if lost |
| Generated file conflict | Both sides changed `openapi.json`, `api.generated.ts` or `static/` | Procedure: take upstream's copy, regenerate | Never hand-merge |

</frozen-after-approval>

## Code Map

- `_bmad-output/README.md` -- fork-files guide; gains a pointer to the new document.
- `/AGENTS.md` Policy, upstream PR bullets -- gains one line pointing at the procedure; copy to `_bmad-output/backup/repo-instructions.backup.txt` (identical).
- `AGENTS.md` "Generated files" table -- regeneration order and checks the procedure reuses; don't duplicate its rows, refer to them.
- `pyproject.toml:7` -- fork version line `0.14.2+fork.1`; `tests/test_version.py::test_fork_build_identifies_itself` pins `+fork.N`.
- `pandrator/version.py` `public_version` and `pandrator/web/identity.py` -- contract carries the public version (story 1, `4c12bc41`); regeneration after a merge must leave `openapi.json` without `+fork`.
- Fork-only commits on code branches: `ebe7194d` (version), `84645183` (fork-version tests), `4c12bc41` (contract split, kept fork-side). Candidates: `git cherry -v upstream/main main`.
- Tags: `v.0.14.2+fork.1` is annotated; upstream's `v.0.14.2` is lightweight.
- `docs/release-notes-next.md` -- upstream rewrites it each release; on conflict take upstream's, fork adds no lines.
- `web/scripts/check-static-assets.mjs` and the LF pitfall in `AGENTS.md` -- the static rebuild must happen from an LF checkout on Node 24 (story 5).
- Tools here: Node 24.22.2 system, uv; no pixi. Git 2.52 (`git merge-tree --write-tree` previews conflicts without a worktree). `core.longpaths=true`.
- Upstream state 2026-11-10 (refetched after story 2): `upstream/main` = `112501b2`, already in fork `main`. `upstream/fix/contextual-early-repair-20261109` (now 8 commits, at `44ab2d05`, version still `0.14.2`) merges into fork `main` with 1 conflict (`docs/release-notes-next.md`) and changes `openapi.json`, `api.generated.ts` and 56 static files; into `epic-fork-releases` it conflicts in 78 files, mostly tests stories 3–12 also fixed.
- Install step (story 2, done): `_bmad-output/scripts/fork_slot.py` `install <tag>`, `activate <tag>`, `back`, `status`, documented in `_bmad-output/README.md` "Running a fork build". The release procedure links that section rather than repeating it, and adds that the tag must be pushed to `origin` first because `install` clones from there.
- `f94fd9f6` (story 3 leak fix) cherry-picks cleanly onto `upstream/main`.

## Tasks & Acceptance

**Execution:**
- [ ] `_bmad-output/fork-maintenance.md` -- write the three procedures, the conflict rules from the matrix, and the order of epic branch versus upstream merges -- the document story 14 and later releases follow
- [ ] `_bmad-output/scripts/check_pr_branch.py` -- stdlib-only check run with `uv run --no-project python`; takes a base ref (default `upstream/main`), reads `git diff <base>...HEAD`, reports fork-only paths and added lines with `+fork` or a Windows user path, exits 1 on any -- makes "no fork-only change" provable
- [ ] `_bmad-output/scripts/test_check_pr_branch.py` -- cover each matrix row of the check against a temporary repo -- the check needs its own proof
- [ ] `/AGENTS.md` and `_bmad-output/backup/repo-instructions.backup.txt` -- add the pointer line, copy identically
- [ ] `_bmad-output/README.md` -- link the document
- [ ] Rehearsal -- in a temporary worktree outside the repository, under `%TEMP%` (never at a drive root), with `npm ci --prefix web` and the `<uv env>` from `AGENTS.md` set up inside it: a scratch branch from fork `main` merges the chosen upstream ref by the procedure, regenerates, runs the checks; a scratch branch from `upstream/main` picks `f94fd9f6` and passes the check; record results under Implementation Notes, then delete both

**Acceptance Criteria:**
- Given the scratch merge branch, when each generator in `AGENTS.md`'s table runs, then each check passes and `openapi.json` holds no `+fork`, and the version still reports `+fork.1`.
- Given the scratch PR branch, when the check runs, then it reports nothing; given the same branch with `ebe7194d` also picked, it fails naming `pyproject.toml`.
- Given `git status` on code branches after the work, then only `AGENTS.md` (untracked) changed.

## Implementation Notes

Rehearsal, 2026-11-10, Windows, Node 24.22.2, uv env (not pixi), scratch worktrees under the temp folder, both branches and worktrees deleted, nothing pushed:

- Merge branch: `main` (`ebe7194d`) merged with `upstream/fix/contextual-early-repair-20261109` (`44ab2d05`, 8 commits). One conflict, `docs/release-notes-next.md`, resolved by taking upstream's. Static assets, `openapi.json` and `api.generated.ts` came in as upstream's copies.
- Finding: fork `main` lacks the LF rules (`3b4484ce` sits only on `epic-fork-releases`), so the new worktree checked out 686 CRLF files under the three paths. I cherry-picked `3b4484ce` onto the scratch branch and refreshed the paths per the pitfall; the procedure now says to land the epic branch in `main` first.
- After that: `generate_requirements.py --check`, `generate_web_contract.py` + `generate-api` (empty diff, no `+fork` in either file), `build` (`git status` empty for static), `check:assets` (`0.14.2-3111798f37c1`), `check:manager-models`, `generate_audio_cpp_model_metadata.py --check` and `tests/test_version.py` (8 passed) all passed; version still `0.14.2+fork.1`. Not run: the wider test lanes and CI comparison.
- PR branch: `upstream/main` + `f94fd9f6` gave exit 0 and no output. Adding `ebe7194d` (needed a hand fix of `tests/test_version.py`, since `4c12bc41`'s tests are absent) gave exit 1 with `pyproject.toml:7: added line contains +fork`.
- `uv run --no-project --with pytest python -m pytest -q _bmad-output/scripts/test_check_pr_branch.py`: 11 passed; ruff 0.17.3 clean on `_bmad-output/scripts`.
- Files: `fork-maintenance.md`, `scripts/check_pr_branch.py`, `scripts/test_check_pr_branch.py`, README link, one `AGENTS.md` line with an identical backup (uncommitted, `AGENTS.md` itself is untracked).
- Owner decision, 2026-11-10, after review: keep `fork-maintenance.md`'s merge-order rule. The epic branch lands in `main` before an upstream merge, and upstream is never merged into the epic branch.

## Plan Change Log

## Review Triage Log

Quick review, pass 1: 0 high, 2 medium, 5 low patched, 3 rejected. All patches went to the implementer; none deferred.

- medium, patch: the `pyproject.toml` conflict rule deferred `+fork.1` to the release commit, against the frozen matrix, so a merge without a release left `main` failing `test_fork_build_identifies_itself`. Now resolved to `<upstream>+fork.1` in the merge.
- medium, patch: the static check used `git status --porcelain` during a `--no-commit` merge, which lists staged entries, so it could never be empty. Now `git diff --exit-code` plus an untracked-file check.
- low, patch: regeneration claimed the `AGENTS.md` table order but listed requirements first, and restated every row. Now refers to the table and keeps only the notes that differ.
- low, patch: an added line beginning `++ ` was parsed as a file header, hiding the rest of that file. A header is now recognised only after a `--- ` line.
- low, patch: user git config (`diff.noprefix`, forced colour, external diff, textconv) and `-diff` attributes could make the check pass silently. Both diff calls now force plain output with `--text`.
- low, patch: a git failure exited 1, like a finding. Now exit 2, documented.
- low, patch: the path list said `_bmad*/` but matched two names, and missed nested `AGENTS.md` and `CLAUDE.md`. Both fixed, with tests.
- low, rejected: C-quoted paths with `"`, `\` or control characters escape the path match. Such names are unlikely, and a fix needs `-z` parsing throughout.
- low, rejected (fix edits this plan): the first Verification command lacks `--with pytest`; the commands actually run, recorded above, include it.
- low, rejected (fix edits this plan): the rehearsal notes record `check:manager-models` but not `build:manager-models`, and say nothing of the audio.cpp inventory row. That row needs a specs directory and has no check, so the rehearsal skipped it; the procedure now says so.

## Design Notes

The document lives on `fork-files` because `AGENTS.md` keeps fork-only material off code branches; `AGENTS.md` itself is untracked, so the one pointer line costs nothing upstream. The check is deliberately narrow (paths and markers). It can't recognise a fork-only behaviour change such as `4c12bc41`, so the procedure still says to read each picked commit.

For story 14 (owner discussion, 2026-11-10, after this story was done). The owner withdrew the earlier "nothing upstream until the initiative is done": upstream PRs are back in scope, offered by story 14, each with the owner's go-ahead. `fork-maintenance.md` section 3 records how upstream treats PRs. Candidates, checked against upstream's open release PR #120 (branch `upstream/fix/contextual-early-repair-20261109` at `44ab2d05`):

- Offer, one PR each:
  - The lease fix `415aaa8a`. #120 touches the same files but conflicts only on `docs/release-notes-next.md`, which the PR leaves out. Evidence: on the stock 30-second lease the real book's 5,167-segment opening check ran 6 min 55 s and passed on the first attempt (story 2's notes); before the fix it failed at about 31 s.
  - The LF rules for the browser build, `3b4484ce` and `0a07679d`. No overlap with #120 apart from one conflict in `tests/test_frontend_architecture.py`.
  - The short Windows test temp root, `0ce71320` and `a5918f5b`, plus the follow-up fix that resets explicit ACL entries (story 6's follow-up, `f6aefbe2`). Without it, `test_restrict_leaves_only_the_current_user` fails on GitHub's Windows runners. No overlap of substance.
- Drop as superseded: the lane split `93c0e68e` and the FFmpeg CI changes `e2ca83b8` and `388a1bb7`, because #120 reworks the same workflows and lanes.
- Hold until #120 lands: the leak fix `f94fd9f6` (#120 rewrites 8 of its 10 files) and the test repairs of stories 7 to 12 (78 conflicts with the epic branch). Then rerun them on the new upstream `main` and offer only what still fails, as small PRs grouped by cause.
- File as issues, not PRs: the backlog bugs, such as saved preview settings failing generation and the security log on Windows.
- Never offer: `ebe7194d`, `84645183` or `4c12bc41` (fork version).

## Verification

**Commands:**
- `uv run --no-project python -m pytest -q _bmad-output/scripts/test_check_pr_branch.py` -- expected: pass
- `uvx ruff@0.17.3 check _bmad-output/scripts` -- expected: pass
- `cmp AGENTS.md _bmad-output/backup/repo-instructions.backup.txt` -- expected: identical
- On the scratch merge branch: `python scripts/generate_requirements.py --check`, `python scripts/generate_web_contract.py` then `npm --prefix web run generate-api` then `git diff --exit-code -- openapi.json web/src/lib/api.generated.ts`, `npm --prefix web run build` then `git status --porcelain -- pandrator/web/static`, `npm --prefix web run check:assets`, `check:manager-models`, `python scripts/generate_audio_cpp_model_metadata.py --check`, `python -m pytest -q tests/test_version.py` -- expected: each passes, empty diffs
- On the scratch PR branch: `uv run --no-project python _bmad-output/scripts/check_pr_branch.py` -- expected: exit 0

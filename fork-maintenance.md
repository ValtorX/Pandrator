# Fork maintenance

How upstream changes reach fork `main`, how a fork version is cut, and how an upstream PR branch
stays free of fork-only changes. Rehearsed on scratch branches (2026-11-10); results are in the
story plan for ticket 13.

Steps marked **[owner]** are permanent: a merge into `main`, a commit, a tag or a push. Do them
only with the owner's go-ahead for that piece of work. Everything else is safe to repeat.

Pixi is optional here: use the `<uv env>` and the uv Node 24 recipe from `AGENTS.md`. Without
pixi, `pixi.lock` cannot be updated, so a dependency change that arrives from upstream needs a
pixi run before release; say so in the hand-off.

## 1. Upstream into fork `main`

1. **Fetch and preview.** `git fetch upstream`, then
   `git merge-tree --write-tree --name-only main <ref>` lists conflicts without touching anything.
   `<ref>` is `upstream/main`, or an upstream branch when `main` has not moved.
2. **Order against the epic branch.** The epic branch holds fork-side test and tooling fixes
   (including the LF rules, `3b4484ce`). Land it in `main` **[owner]** before merging upstream.
   Never merge upstream into the epic branch: against a busy upstream branch it conflicted in 78
   files, mostly tests the epic had already fixed. If the epic cannot land first, merge upstream
   into `main` anyway and expect those conflicts when the epic lands.
3. **Merge on a branch.** `git switch -c merge-upstream-<date> main`, then
   `git merge --no-ff --no-commit <ref>` so conflicts can be resolved before the commit. Check
   `git ls-files --eol web pandrator/web/static pandrator_manager/recovery_ui/static/model-groups.js | grep w/crlf`
   is empty. If not, the LF rules are missing or the checkout predates them: refresh as the
   `AGENTS.md` pitfall says, before regenerating.
4. **Conflict rules.**

   | Conflict | Rule |
   | --- | --- |
   | `pyproject.toml` version | Resolve to `<upstream X.Y.Z>+fork.1` in the merge itself. Without the segment `test_fork_build_identifies_itself` fails. |
   | `docs/release-notes-next.md` | Take upstream's. The fork adds no lines. |
   | `openapi.json`, `web/src/lib/api.generated.ts`, `pandrator/web/static/` | Take upstream's copy, then regenerate. Never hand-merge. |
   | Anything else | Resolve by hand, reading both sides. Before taking a side in tests, check whether the epic fixed the same failure. |

5. **Regenerate.** Run each "Regenerate" command of the `AGENTS.md` "Generated files" table, top
   to bottom, from an LF checkout on Node 24. Notes that differ from the table: run
   `npm ci --prefix web` first in a fresh worktree; the audio.cpp inventory row needs a specs
   directory and has no check, so skip it unless the catalogue changed.
6. **Check.** Run each "Check" of that table, with these changes. `check-web-contract` compares
   against the index, so use `git diff --exit-code -- openapi.json web/src/lib/api.generated.ts`
   instead (never stage to silence it). During an uncommitted merge `git status` lists the staged
   entries, so for the assets use `git diff --exit-code -- pandrator/web/static` and
   `git ls-files --others --exclude-standard -- pandrator/web/static` (prints nothing), then
   `npm --prefix web run check:assets`. Also run `grep -c '+fork' openapi.json` (must print 0:
   the contract keeps the public version, `4c12bc41`), `pytest -q tests/test_version.py` (the
   version must still read `+fork.N`), the focused tests for the files the merge touched and
   `uvx ruff@0.17.3 check .`. Candidates to re-read: `git cherry -v upstream/main main`.
7. **Commit the merge** **[owner]**. Regenerated files that changed go in the same merge or a
   follow-up commit, never in a code-only commit.
8. **Compare CI.** Push the branch **[owner]** and compare each failing job with the same job at
   the upstream commit that was merged. A job failing on both sides is not a regression; compare
   failure names, not only job status. Cancelled lanes give no verdict.
9. **Fast-forward `main`** **[owner]**, then continue to section 2 if a release follows.

## 2. Cutting a fork version

1. Start from a `main` that passed section 1 (or the fixes that justify the release).
2. In `pyproject.toml` only, set `version = "X.Y.Z+fork.N"`. `N` restarts at 1 when upstream's
   `X.Y.Z` changes and increments for a fork-only release on the same `X.Y.Z` (a merged upstream bump already carries `+fork.1`, so this step is then only the tag). Leave
   `web/package.json` alone so the browser assets need no rebuild; the contract keeps the public
   version (`pandrator/version.py` `public_version`). Put this change in its own commit
   **[owner]**.
3. Verify: `python -c "from pandrator.version import PANDRATOR_VERSION; print(PANDRATOR_VERSION)"`
   prints the full version; `pytest -q tests/test_version.py` passes; the generated-file checks
   from section 1 are clean; `npm --prefix web run check:assets` is unchanged.
4. Tag the commit: `git tag -a v.X.Y.Z+fork.N -m "Pandrator X.Y.Z+fork.N: <one line>"`
   **[owner]**. Upstream's own tags are lightweight; the fork's are annotated.
5. Push `main` and the tag to `origin` **[owner]**. Nothing goes to `upstream`.
6. Install it: follow "Running a fork build" in `README.md` (`fork_slot.py install <tag>`, then
   `activate`). The tag must be on `origin` first, because `install` clones from there. Check
   `/api/v1/health` for the full version.

## 3. Upstream PR branches

Upstream PRs follow the owner's rule: built with the owner, from upstream, picked commits only.

What upstream's PR history shows (read 2026-11-10; upstream has no CONTRIBUTING file):

- The maintainer usually re-implements a contributed fix in a release commit and closes the PR as
  superseded, with thanks. Count that as success: the PR's value is its evidence.
- The PRs merged were small (1–5 files), one bug each, with a body of Summary, Reproduction, Root
  cause, Fix and Tests, and a test.
- Upstream lands large release batches, so a branch goes stale within days. Build each branch
  just before opening it, and leave out regenerated contract and static files unless the fix needs
  them; say how to regenerate instead.
- PRs that fixed a path upstream had already redesigned were closed. Before offering a fix, check
  that upstream `main`, and any open release PR, still has the problem.
- Commit subjects use upstream's imperative sentence case, with no `fix:` prefix.
- A product bug without a ready, isolated fix goes as an issue with reproduction and root cause.

1. `git fetch upstream`, then `git switch -c <short-name> upstream/main`. Never branch from fork
   `main`.
2. `git cherry-pick <commit>...` only commits that belong upstream. Read each diff: the check
   below sees paths and markers, not behaviour, so a fork-only change such as `4c12bc41` would pass
   it. Fork-only commits on `main` are listed by `git cherry -v upstream/main main`.
3. Run `uv run --no-project python _bmad-output/scripts/check_pr_branch.py` (base defaults to
   `upstream/main`; pass another ref as an argument). It reports fork-only paths (any top-level
   folder starting `_bmad`, `.agents/` and `.claude/` at the root, `skills-lock.json` at the root,
   and `CLAUDE.md` or `AGENTS.md` at any depth), added lines containing
   `+fork`, and added lines containing a Windows user path. Exit 1 means fix the branch; exit 2 means the check could not run (for example no
   `upstream` remote or an unfetched ref) and prints the git error on stderr.
4. Product changes need the usual generated files regenerated on that branch. The procedure, the
   check and any fork-only file never go on a code branch.
5. Pushing the branch and opening the PR are **[owner]** steps.

## Rehearsal

Rehearse on throwaway branches in a temporary worktree under the system temp folder, never at a
drive root, and delete the branches and worktree afterwards without pushing. Tests for the check:
`uv run --no-project --with pytest python -m pytest -q _bmad-output/scripts/test_check_pr_branch.py`.

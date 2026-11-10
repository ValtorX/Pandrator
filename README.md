# Fork-only files

This branch has no history in common with the code branches. It holds files that belong to this
fork and must never reach an upstream pull request: BMad output, the `AGENTS.md` backup and the
BMad setup files that cannot be regenerated.

## Setting up a new clone

Fetch this branch, check it out as a worktree, ignore it along with the local BMad install,
restore `AGENTS.md`, which Git does not track on the code branches, and recreate the `CLAUDE.md`
that loads it:

```bash
git fetch origin fork-files:fork-files
git worktree add _bmad-output fork-files
printf '%s\n' /_bmad-output/ /CLAUDE.md /.agents/ /.claude/ /_bmad/ /skills-lock.json >> .git/info/exclude
cp _bmad-output/backup/repo-instructions.backup.txt AGENTS.md
printf '@AGENTS.md\n' > CLAUDE.md
```

Then install BMad, run `bmad setup` in your agent, and restore this fork's configuration:

```bash
npx skills add bmad-code-org/BMAD-METHOD -y
```

```bash
cp _bmad-output/bmad-setup/config.toml _bmad/config.toml
cp -r _bmad-output/bmad-setup/custom/. _bmad/custom/
```

## BMad setup files

`bmad-setup/` holds copies of `skills-lock.json`, `_bmad/config.toml` and `_bmad/custom/`. The
installed skills and scripts are not kept here because the install recreates them.

`_bmad/custom/config.user.toml` records the active initiative. BMad ignores `*.user.toml` by
default, so this fork empties `_bmad/custom/.gitignore` to keep that file here. Refresh the copy
when the active initiative changes.

The install takes the current BMad, not necessarily the one recorded here. Compare the new
`skills-lock.json` with `bmad-setup/skills-lock.json` to see which skills differ; the lock file
detects a difference but cannot restore an older version. Last recorded version: `6.13.0-next`.

## Committing here

Run Git from this folder or use `git -C _bmad-output`. Commits land on `fork-files`, not on the
code branch checked out in the parent folder.

`backup/repo-instructions.backup.txt` is a copy of the latest `AGENTS.md`.

## Merging upstream and cutting releases

`fork-maintenance.md` is the procedure for merging upstream into fork `main`, cutting a
`+fork.N` version and keeping upstream PR branches free of fork-only changes.
`scripts/check_pr_branch.py` reports fork-only changes on a PR branch; its tests are
`scripts/test_check_pr_branch.py`.

## Running a fork build

`scripts/fork_slot.py` puts a fork tag into a new version slot of the owner's install and
switches the install's pointer (`app/current.json`) to it. It uses only the standard library.
`<workspace>` is the folder that contains `Pandrator/`; pass it with `--workspace` or set
`PANDRATOR_WORKSPACE`. Close both the app and the Manager before `activate` or `back`: the script refuses to
switch (and to rerun pixi in an existing slot) while any process runs from the install root,
and the Manager does too.

```bash
S="uv run --no-project python _bmad-output/scripts/fork_slot.py --workspace <workspace>"
$S install v.0.14.2+fork.1   # clone the tag, prepare its pixi environment; pointer unchanged
$S activate v.0.14.2+fork.1  # back up pointer and database, then switch
$S status                    # active and previous slot, versions, whether the app runs
$S back                      # swap with current.previous.json; run again to go forward
```

Every switch copies `app/current.json` to a timestamped `.bak` and the database to
`data/backups/` first. Slots are named by full commit and are never deleted; their
checked-out files are never changed (only `install` reruns pixi in a slot's environment).

Do not use the Manager's Update or Repair on the app: they re-clone upstream and re-point
`app/current.json`, discarding the fork build. Start the app from the Manager launcher as usual
and check `/api/v1/health` for `0.14.2+fork.1`.

Tests: `uv run --no-project --with pytest python -m pytest -q _bmad-output/scripts/test_fork_slot.py`.

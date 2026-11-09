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

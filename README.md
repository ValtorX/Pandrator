# Fork-only files

This branch has no history in common with the code branches. It holds files that belong to this
fork and must never reach an upstream pull request: BMad output and the `AGENTS.md` backup.

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

## Committing here

Run Git from this folder or use `git -C _bmad-output`. Commits land on `fork-files`, not on the
code branch checked out in the parent folder.

`backup/repo-instructions.backup.txt` is a copy of the latest `AGENTS.md`.

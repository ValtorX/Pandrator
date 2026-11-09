---
type: epic
title: "The fork can be developed on Windows and ships its own builds"
parent: initiative-long-audiobooks
covers: [R20, R21]
sources: []
companions:
  - _bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-windows-and-install.md
after: []
refined: true
risk: high
---

# The fork can be developed on Windows and ships its own builds

## Description

The project's checks and builds work from a Windows checkout, and a build of the fork can be released and installed as the owner's working app, so fixes reach real use without waiting on upstream. The first release carries the lease fix (`415aaa8a`), which lets the install's 19-hour patch go. This epic provides the working dev setup every later epic uses and the release path Epic 2's cancel story and real-data check rely on.

## Outcome

The owner runs a released build of the fork on the real book, on the stock 30-second lease, and develops it from a Windows checkout with checks that mean what they say.

## Requirements

- R1 (parent R20): A versioned build of the fork is cut from fork `main`, and the version it reports identifies it as the fork's.
- R2 (parent R20): The owner's install runs that build by a repeatable procedure that prepares its runtime and can return to the previous version.
- R3 (parent R20): On that build, with no 19-hour lease patch, the real book's generation run gets past the opening check on the stock 30-second lease.
- R4 (parent R20): Upstream changes reach fork `main` by a written, rehearsed procedure, and upstream PR branches stay free of fork-only changes.
- R5 (parent R21): From a Windows checkout, the web quality checks pass and the browser build leaves the tree as clean as CI's rebuild does.
- R6 (parent R21): From a Windows checkout, the test lanes run with no path-length failures and no hand-set options.
- R7 (parent R21): Tests that fail only on Windows pass there, or skip with a stated reason, without losing the protection they give on other platforms.
- R8 (parent R21): Tests that fail on every platform are corrected when the test is out of date, or filed as bugs when the product is wrong.
- R9 (parent R21): A fresh Windows clone reaches passing checks by following a written setup, and any remaining failure is explained there.

## Constraints

- The Manager's refusal of unsigned or wrongly signed self-updates must not be weakened.
- Changes to the owner's machine and install (Node, pixi, Playwright browsers, activating a build) are the owner's to run or authorize. No registry or system setting is changed by an agent.
- Merging, committing, tagging and pushing need the owner's direct go-ahead, per `AGENTS.md`.
- Fork-only files stay on `fork-files`. A change to `/AGENTS.md` also updates `_bmad-output/backup/repo-instructions.backup.txt`.
- A `pandrator/web/static` build is committed only from an LF checkout until story 3 lands.
- `web/package.json` keeps upstream's version, because `web/svelte.config.js:44` puts it in the build's name and changing it would force an asset rebuild.
- Local paths and the book's title stay out of PRs and this public branch.

## Done when

- A fork version is tagged on fork `main`, and the owner's app reports it.
- The real book gets past the opening check on the stock lease, with no 19-hour patch.
- The owner can return to the previous version and forward again by the written procedure.
- From a Windows checkout, lint, types, test lanes, web quality and the browser build pass, and each remaining failure is explained.
- An upstream merge has been rehearsed by the written procedure.
- Fixes useful upstream have been offered as focused PRs.

## Boundaries

- Belongs to siblings: every product fix other than the lease fix.
- Out: a fork Manager, an installer or any distribution to other people. Those would need a fork release key and a signing pipeline, and get their own epic if wanted.
- Out: publishing to PyPI, signing with upstream's release keys, and enabling Windows long paths system-wide.
- Must not change: test protection on Linux; a skip applies only where the behaviour can't exist.

## References

- parent — `_bmad-output/initiative-long-audiobooks/initiative-long-audiobooks.md`, section Requirements
- findings — `_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-windows-and-install.md`

## Notes

- Decision (2026-11-08): Stories 1 and 2 together are the tracer bullet, split because one session couldn't hold both: they take the lease fix through a tagged version, a hand activation and a real run. That is the riskiest path and the owner's first real gain. Story 1 changes no browser assets, so neither waits on the Windows work.
- Decision (2026-11-08): Story 3 stands alone because story 1 leaves the browser assets untouched. Stories 5 and 6 wait on story 4 so they run under its temp path fix. Story 7 waits on stories 2 and 3 because it writes down their asset rebuild and install steps. Story 8 goes last because it documents the result.
- Decision (2026-11-08): Releases are for the owner's own install. The build is a tag on fork `main`, activated by a fork-only script on `fork-files` into a new version slot.
- Decision (2026-11-08): Fork `main` is the release line, because it is the default branch and the CI triggers already cover it.
- Decision (2026-11-08): No refactor sweep, because this epic changes setup, tests and procedures rather than product code.
- Assumption: The Manager accepts a hand-activated slot. Its code reads only the pointer file and slot markers, with `revision` optional, but this hasn't been run; story 2 finds out.

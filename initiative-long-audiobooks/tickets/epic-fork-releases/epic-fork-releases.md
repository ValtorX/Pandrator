---
type: epic
title: "The fork can be developed on Windows and ships its own builds"
parent: initiative-long-audiobooks
covers: [R20, R21]
sources: []
companions: []
after: []
refined: false
risk: high
---

# The fork can be developed on Windows and ships its own builds

## Description

The project's checks and builds work from a Windows checkout, and a build of the fork can be released and installed as the owner's working app, so fixes reach real use without waiting on upstream. The first release carries the lease fix (`415aaa8a`), which lets the install's 19-hour patch go. This epic provides the release line Epic 1's cancel story and real-data check rely on.

## Outcome

The owner runs a released build of the fork on the real book, on the stock 30-second lease.

## Done when

- From a Windows checkout, a documented setup runs the lint, type, test and web checks and the asset build, and each remaining failure is explained.
- A versioned fork build is published from a release line, and its version shows it is the fork's.
- The owner's install runs that build, with a way back to the previous version.
- The real book's generation run gets past the opening check on the stock 30-second lease.
- Upstream changes can be merged into the release line by a written, repeatable procedure.
- Upstream PR branches stay free of fork-only changes.

## Boundaries

- Belongs to siblings: every fix other than the lease fix.
- Out: publishing to PyPI under the existing package names, or signing with upstream's release keys.
- Must not change: the Manager's refusal of unsigned or wrongly signed self-updates.

## References

- parent — `_bmad-output/initiative-long-audiobooks/initiative-long-audiobooks.md`, section Requirements

## Notes

- Windows problems seen so far: default pytest temp paths exceed the path limit (about 27 failures), `pixi` was unavailable, `gh` wasn't on the Git Bash path, a static build from a CRLF checkout fails CI's clean-tree gate, and 3 generation tests and 5 architecture tests fail on clean upstream here, causes not yet separated into Windows-specific or not.
- Open question: Which other Windows dev problems exist? The owner doesn't know yet (2026-11-08), so refining this epic has to find them by running the checks and builds on a Windows checkout.
- Unknown: How the Manager installs a fork build. It clones the app from a hardcoded upstream URL (`pandrator_manager/components/builtin.py:813`), and self-updates must be signed by keys embedded in the Manager (`pandrator_manager/releases/trust.py:25`), so a fork Manager with its own key is one route and installing from a fork checkout another. To settle when this epic is refined.

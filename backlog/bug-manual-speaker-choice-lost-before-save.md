---
id: 6
type: bug
title: "A speaker chosen for selected words is lost before the block is saved"
covers: []
after: []
assignee: ""
refined: false
hitl: false
risk: low
severity: P2
---

# A speaker chosen for selected words is lost before the block is saved

## Description

In the speech annotation workspace, a user selects words, picks a character under "Selected words belong to", marks the text and saves the block. The UI then reports "Block directions and speaker assignments saved", but the saved `speech_xml` has no `<speaker ref="…">` for that character. Phrase directions on the same words are kept. The user believes the assignment is stored when it is not.

## Reproduction

Run the test "manual annotations preserve words, support phrase directions and protect against concurrent saves" in `web/tests/project-ux.spec.ts` (line 174 at `cee8fa56`; the line moves once upstream's extended spec is merged) on Chromium. Use fork `main` at `cee8fa56` or later, before the next upstream merge:

```bash
<uv env> npm.cmd --prefix web run test:e2e -- --project=chromium tests/project-ux.spec.ts -g "manual annotations preserve words"
```

- Actual: `saved.items[0].speech_xml` lacks `<speaker ref="<character>">`. It failed 4 of 4 runs locally on Windows, and on CI run 42908347707.
- Expected: the saved XML assigns the selected words to the chosen character.

## Cause

Upstream fixed this in `5bb47cfc` ("Stabilize 0.14.2 CI contracts and annotated speaker selection"), part of upstream #120, which merged on 2026-11-10.

- In `web/src/lib/PerformancePanel.svelte`, `updateSelection()` re-reads the textarea selection on every `select` event.
- Moving focus to the speaker picker can deliver a late `select` event for the same range. That event recomputes the picker from the existing runs and overwrites the user's explicit choice.
- Upstream now returns early when the range is unchanged, and forces the refresh on preview clicks.
- Upstream also extended the spec so that a failed client-side mark can't pass as a successful save.

## Acceptance Criteria

1. **The chosen speaker is saved**
   **Given** a manual draft with words selected and a character chosen under "Selected words belong to"
   **When** the text is marked and the block saved
   **Then** the saved `speech_xml` assigns those words to that character
2. **The spec passes on fork `main`**
   **Given** fork `main` after upstream `5bb47cfc` is merged in
   **When** `project-ux.spec.ts` runs on Chromium, locally and in fork CI
   **Then** the annotation test passes in both

## References

- findings — `_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-fresh-clone.md`, Failures found, item 3
- upstream fix — `5bb47cfca0e62196b2ec6709fce2a336bc048d47`
- procedure — `_bmad-output/fork-maintenance.md`, merging upstream into fork `main`

## Notes

- Decision (2026-11-10): Resolved by the next upstream merge into fork `main`, not by a fork-side fix, because upstream already carries the fix with its updated spec. No test marker was added: E2E has no strict expected-failure lane, and the merge closes this.

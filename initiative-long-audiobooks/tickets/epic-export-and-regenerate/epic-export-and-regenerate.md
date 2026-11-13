---
type: epic
title: "Partial regenerates reach a clean export"
parent: initiative-long-audiobooks
covers: [R13, R14, R15, R16]
sources: []
companions: []
after: []
refined: false
risk: medium
---

# Partial regenerates reach a clean export

## Description

A partly regenerated book can be assembled and exported from the intended version, and the default plan and source replacement no longer threaten existing takes. This epic settles the partial-assembly path Epic 5 relies on.

## Outcome

Someone who regenerates part of a long book gets a complete, correct export, without losing takes to a default choice.

## Done when

- A `regenerate` run on part of a book assembles into a complete book using existing takes for the other segments.
- With several assembled versions, export names the version it uses and never silently picks an old one.
- The default Generate-audio plan keeps lineage and takes unless the user chooses to re-segment.
- Replacing a source with one of identical text raises no warning and keeps reuse.
- Each fix is in a fork release and verified here, and has been offered upstream as a focused PR.

## Boundaries

- Belongs to siblings: speaker plan carry-over (Epic 3), chapter pause settings (Epic 5).
- Must not change: a source whose text really differs still warns.

## References

- parent — `_bmad-output/initiative-long-audiobooks/initiative-long-audiobooks.md`, section Requirements

## Notes

- Waits on epic-speaker-plan-carry-over because: both change the edit-copy and compatibility handling in `speech_plan_workspace.py` and `generation_topology.py`.
- Evidence (2026-11-13, from epic 2's story 14 on `v.0.14.4+fork.1`): on the real 5,167-segment book, re-running the Segment narration stage from the UI created new plan revisions with no audio. The last one reused none of the book's audio, although 5,150 of its 5,167 segments had the same text at the same position as the active revision (R15, R16). Its prepared text differed from the plan's own input, so the plan showed "This plan was prepared from a different text version" and Generate was disabled until the older prepared text was selected again (R16). The UI's Run again on that stage gave no feedback while each request waited 20-52 s for the writer, skipped its confirmation dialog (no stage-impact request was made), and let repeated clicks through, so four clicks started four Prepare text jobs and three new revisions; nothing was chosen explicitly (R15). The audit timeline and revision ids are in `epic-large-project-responsiveness/baseline-2-1.md`, "Story 14", and that epic's story 15 covers the writer hold.

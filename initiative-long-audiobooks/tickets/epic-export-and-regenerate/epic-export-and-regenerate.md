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

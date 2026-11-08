---
type: epic
title: "Chapter breaks have their own pauses"
parent: initiative-long-audiobooks
covers: [R18, R19]
sources: []
companions: []
after: []
refined: false
risk: low
---

# Chapter breaks have their own pauses

## Description

Chapter breaks use their own pause settings at assembly, with no post-processing, and an assistant can set a segment's pause through MCP.

## Outcome

Someone producing an audiobook gets the chapter pacing they want from settings, with no ffmpeg step afterwards.

## Done when

- Separate settings set the silence before a chapter heading and after the spoken title, and assembly applies them.
- An assistant can set a segment's pause through MCP.
- Each fix is in a fork release and verified here, and has been offered upstream as a focused PR.

## Boundaries

- Belongs to siblings: partial assembly (Epic 3).
- Out: rewriting heading text, such as speaking "1." as "Chapter 1"; setting a title's spoken text covers that.
- Must not change: pauses at non-chapter boundaries.

## References

- parent — `_bmad-output/initiative-long-audiobooks/initiative-long-audiobooks.md`, section Requirements

## Notes

- Waits on epic-speaker-plan-carry-over because: both change the pause rules in `speech_boundaries.py`.
- Waits on epic-export-and-regenerate because: both change `workflow_output_assembly.py`.

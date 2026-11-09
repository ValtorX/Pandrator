---
type: epic
title: "The speaker plan survives edits"
parent: initiative-long-audiobooks
covers: [R9, R10, R11, R12]
sources: []
companions: []
after: []
refined: false
risk: high
---

# The speaker plan survives edits

## Description

Editing a segment keeps the rest of the book's reuse status and adopted speaker plan, across edit-copied and restored revisions. This epic settles the edit-copy handling Epic 4 relies on and the pause rules Epic 5 relies on.

## Outcome

Someone correcting one line of a long book keeps the speaker plan and reusable audio for every other line, with no hand-copied `speech_xml`.

## Done when

- On the synthetic project, editing one segment's spoken text leaves every other segment's reuse status unchanged.
- An `edit_copy` or restored revision keeps the adopted plan for unchanged segments, and `copy_from_id` copies their annotations, including from a stale plan.
- A run with no adopted plan no longer silently assembles dialogue turns with the paragraph pause.
- Each fix is in a fork release and verified here, and has been offered upstream as a focused PR.

## Boundaries

- Belongs to siblings: audio identity speed (Epic 2), export and partial assembly (Epic 4), chapter pause settings (Epic 5).
- Must not change: a segment whose text or speech really changed loses its reuse.

## References

- parent — `_bmad-output/initiative-long-audiobooks/initiative-long-audiobooks.md`, section Requirements

## Notes

- Waits on epic-large-project-responsiveness because: it changes the audio identity path in `generation_audio_identity.py` that per-segment staleness also changes, and it provides the synthetic project.

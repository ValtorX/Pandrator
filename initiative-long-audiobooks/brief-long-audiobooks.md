# Long-audiobook brownfield brief

Copy of the owner's brief with local paths and the book title removed, because this branch is public. The problems were observed on Pandrator 0.14.1 (likely upstream commit `27a4479`), with a SQLite database of about 340 MB, waitress as the server, and an MCP server with a 15 s read timeout.

Goal: fix the performance and robustness problems hit while producing a 5,167-segment multi-voice audiobook and a 189-segment test book, plus add a missing feature, and submit them upstream as focused PRs.

## Problems observed (2026-11-07/08)

### Problem 1. Segment listing is too slow, and timed-out reads pile up (highest priority)

- `list_generation_segments` (MCP) times out after 15 s even with `limit=1`, `fields=[id, ordinal]`, and even for the 189-segment project, once the 5,167-segment project exists. It worked within seconds right after a restart, then degraded.
- Reads of any non-active speech-plan revision, `list_speech_plan_revisions` and `get_speech_plan_status` time out every time.
- Per-row reuse status (`audio_reuse_reason`, `has_reusable_take`) appears to be computed per request and is expensive; pages of 100 time out, pages of 25-40 are OK on the small project.
- Timed-out requests keep running server-side. The API process was observed reading the DB at about 6 MB/s with almost no CPU; waitress queue depth climbs ("Task queue depth is 5").
- Symptoms: `sqlite3.OperationalError: database is locked` on `BEGIN IMMEDIATE`, "Audit writer busy; event saved to pending audit spool" (hundreds), WAL growing (1.7 to 23 MB), even `/api/v1/auth/status` failing. A restart clears it only until the next heavy read.
- Generation admission for 1-15 segments on the 5,167-segment project holds the DB lock about 10 min, blocking all reads and the UI.

### Problem 2. Speaker plan does not survive segment edits

- Editing one segment's spoken text (`optimized_text`) marks the whole adopted speaker (performance) plan stale; every other line drops from `reusable` to `audio_identity_unknown`.
- On a large project, the edit creates an `edit_copy` segment revision (new IDs, takes carried over correctly), but the adopted speaker plan is not carried over.
- `create_performance_plan(copy_from_id=...)` copies nothing (0 annotations) across a restored or edit-copied revision, and is refused (409) when the source plan is stale.
- Generation runs freeze the speaker plan at run start; a run started without an adopted plan assembles every dialogue turn with the 700 ms paragraph pause instead of 250 ms (+7.5 min on the long book).
- Workaround used: copy `speech_xml` of dialogue units from the old plan's `units_json` and apply via `edit_performance_plan` (100 per call), then adopt with `accept_unanalysed=true`.

### Problem 3. Export and workflow pitfalls

- Export fails with "Multiple assembled audio versions are available" unless a `generation_run_id` is supplied; without one, the export plan can silently pick an old assembly. Exports are byte-identical copies of the assembly `.m4b`.
- A `regenerate` run on part of a book ends `partial` ("5152 segment(s) remain"), and can't be assembled (409).
- The default Generate-audio workflow plan re-runs Segment narration (`settings_unverifiable`), which discards segment lineage and all takes.
- After replacing a source with an identical-text EPUB, the UI warns "different text"; re-segmenting is the only offered fix and would destroy reuse.

### Problem 4. Text normalization

- NeMo text normalization unavailable (`nemo_text_processing` not installed), so "1. Chapter Title" is spoken with the "1." swallowed. Fixed per-title by setting spoken text "Chapter One. ...".

## Feature requests

- **Chapter-break pauses:** separate settings for silence before a chapter heading (wanted about 3 s) and after the spoken chapter title (about 1.75 s). Today chapter boundaries only get `paragraph_silence_ms` (700 ms), and there is no per-segment pause control via MCP. Workaround used: post-process the M4B with ffmpeg (stream-copy cuts in silence, insert matching AAC silence, rewrite chapters).

## Owner additions (2026-11-08)

Made after the brief was written, and they override it where they differ.

- Problem 4 is not a software defect: the "1." was spoken. The owner expected "Chapter 1", which the source text doesn't say.
- The fork will release its own builds. Progress cannot depend on upstream approving PRs.
- Dev work needs adjustments to function properly on Windows.

## Suggested order

1. Reproduce Problem 1 on the installed commit with a synthetic 5k-segment session; profile the segment-list endpoint and admission; fix query, caching and transaction scope; consider storage-engine changes only after.
2. Problem 2 speaker-plan carry-over on edit_copy and restore revisions.
3. Problem 3 export and run selection, and the default-plan re-segmentation trap.
4. Chapter-pause feature.

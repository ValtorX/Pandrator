# Fork release notes

User-visible changes that exist only in this fork. Code branches never edit
`docs/release-notes-next.md`: upstream's maintainer writes it in release commits and replaces it
at every release (`AGENTS.md`, Documentation). An upstream PR copies the matching lines here into
its description, and a line moves to "Now in upstream" once an upstream release ships it.

## Unreleased

From `epic-large-project-responsiveness`, stories 2 to 6.

### Fixes

- On a long book with a cast, listing segments, reading the speech plan's status and history, and starting a generation run are much faster. They no longer rebuild the speech provider list for every character line, and they check each saved character voice once rather than once per line. They also stop re-reading each voice's supported languages and copying the character list for every segment, so opening an older speech plan version, checking the speech plan and starting a run are faster still. A page of segments now costs what it shows: a page that leaves out the audio reuse fields skips the voice and audio-reuse checks, and a page that includes them checks only its own segments. A character voice that is missing elsewhere in the book no longer marks the page's audio reuse unknown; a missing voice used on the page itself still does, for the whole page. A removed segment, or a voice deleted since a past run you are reviewing, no longer makes the other segments on the same page show their audio reuse as unknown.
- MCP plan status and revision history answer quickly on long books. They now return revision summaries: audio reuse counts are given for the active plan when it is listed, other plan versions show none, and plan status no longer reports whether a repair batch can be undone. The web app's speech plan history still shows both.
- Busy reads no longer fail with "database is locked" or push audit records into the pending audit file in bulk. Checking a request's API token no longer takes the database's single writer, and requests that arrive together now write their audit records in one shared write instead of queueing for one each. While another write holds the database, audit records still go to the pending file and are replayed later. An API token's "last used" time now updates at most once a minute.

## Now in upstream

- 0.14.2+fork.1, shipped upstream in 0.14.3 as "More reliable long-running generation": a queued audio generation run on a very long book no longer fails about 30 seconds after it starts with "A voice reference changed after generation was queued".

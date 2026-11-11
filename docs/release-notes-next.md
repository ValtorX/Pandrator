# Next release

Changes since the [0.14.2 release notes](release-notes-0.14.2.md).

## Fixes

- A queued audio generation run on a very long book no longer fails about 30 seconds after it starts with "A voice reference changed after generation was queued". The run's opening speech plan and voice check no longer blocks the database while it runs, and a run that really does lose its worker lease is no longer reported as a voice change.
- On a long book with a cast, listing segments, reading the speech plan's status and history, and starting a generation run are much faster. They no longer rebuild the speech provider list for every character line, and they check each saved character voice once rather than once per line. They also stop re-reading each voice's supported languages and copying the character list for every segment, so opening an older speech plan version, checking the speech plan and starting a run are faster still. A page of segments now costs what it shows: a page that leaves out the audio reuse fields skips the voice and audio-reuse checks, and a page that includes them checks only its own segments, so it is also no longer marked unknown because a voice in a different part of the book is missing. A removed segment no longer makes the segments after it on the same page show their audio reuse as unknown.
- MCP plan status and revision history answer quickly on long books. They now return revision summaries: audio reuse counts are given for the active plan when it is listed, other plan versions show none, and plan status no longer reports whether a repair batch can be undone. The web app's speech plan history still shows both.

# Next release

Changes since the [0.14.2 release notes](release-notes-0.14.2.md).

## Fixes

- A queued audio generation run on a very long book no longer fails about 30 seconds after it starts with "A voice reference changed after generation was queued". The run's opening speech plan and voice check no longer blocks the database while it runs, and a run that really does lose its worker lease is no longer reported as a voice change.
- On a long cast book, segment listings, plan status and history, admission and the opening check no longer spend minutes rebuilding the speech provider catalogue for every cast span, and each saved cast voice is checked once per check instead of once per span.

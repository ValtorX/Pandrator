---
title: "The owner's install runs the fork build and the real book"
type: chore
ticket: '2'
created: '2026-11-10'
status: done
baseline_revision: 'f71c9503262351035078c6fd51bbd49b763aad2d'
route: full
route_source: auto
risk: high
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-long-audiobooks/tickets/epic-fork-releases/findings-windows-and-install.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The owner's install runs upstream 0.14.1 with a hand-patched 19-hour lease, because the real book fails the opening check on the stock 30-second lease. The fork's tag `v.0.14.2+fork.1` carries the lease fix, but nothing puts a fork build into the install, and the Manager's Update or Repair only re-clones upstream.

**Approach:** A fork-only script on `fork-files` clones a tag into a new version slot named by its commit, prepares the slot's runtime with the install's own pixi, switches the install's pointer to it, and switches back. The owner then runs the real book on the new slot.

**Verify (from the ticket):** The owner's app reports the fork version, the real book's generation run passes the opening check on the stock 30-second lease, and the script goes back to the previous version and forward again.

## Boundaries & Constraints

**Always:** Take the install by `--workspace` or `PANDRATOR_WORKSPACE`, like the Manager, with the install root at `<workspace>/Pandrator`; no local path in the script. Refuse to switch while any process runs from the install root. Before every switch, back up `app/current.json` with a timestamp and copy the database to `data/backups/` with SQLite's backup API. Write the pointer atomically (temp file, `os.replace`), in the Manager's shape with `revision` added. Never delete or modify an existing slot. Every run prints that the Manager's Update and Repair must not be used on the app, because they re-clone upstream.

**Never:** Touch the Manager, its database or its signing. Change the install while the owner hasn't authorised that step. Edit product code. Delete the patched 0.14.1 slot.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Install a tag | `install v.0.14.2+fork.1` | Slot `app/versions/<full sha>` with LF checkout, `pixi install --locked` done, version printed; pointer unchanged | Clone or pixi failure: exit 1, slot left for inspection, pointer untouched |
| Slot exists | Same tag again | Reuses it after checking markers and the commit, reruns pixi | Markers or commit wrong: exit 1 |
| Activate | `activate <sha or tag>` | Backups made, `current.previous.json` holds the old pointer, `current.json` points at the slot | App running, slot missing or without markers: exit 1, nothing changed |
| Back | `back` | Swaps to the slot in `current.previous.json`; running it again goes forward | No previous pointer: exit 1 |
| Status | `status` | Active and previous slot, version of each, whether the app runs, all slots | — |

</frozen-after-approval>

## Code Map

- `pandrator_manager/components/slots.py:12-77` -- pointer `app/current.json`: `path` must be an existing dir inside `app/versions`; `version`, `revision` optional strings.
- `pandrator_manager/operations/activation_tasks.py:58-133` -- Manager names slots by HEAD sha, sanitised to `[A-Za-z0-9._-]`, ≤80 chars; pointer keys `activated_by`, `component_id: "pandrator"`, `path`, `version`, written sorted with indent 2 (`operations/task_files.py:11-25`).
- `pandrator_manager/components/builtin.py:78-120, 808-814` -- slot accepted when `pyproject.toml` and `pandrator/web/cli.py` exist; a `pandrator-release.json` in the slot would make it a release bundle, so the clone must not have one. No origin or signature check on source slots; no database entry needed (doctor may warn).
- `pandrator_manager/runtime_specs.py:166-232` -- launch is `<root>/bin/pixi.exe run --manifest-path <slot>/pixi.toml --locked python -m pandrator --data-dir <root>/data serve ...`, cwd the slot; env at `<slot>/.pixi/envs/default`, created lazily with a 15-minute start timeout, no `PIXI_CACHE_DIR`. Prepare in the final slot path, because the project installs from `./`.
- Install facts, 2026-11-10: one slot, `27a4479a` (upstream 0.14.1) with `pandrator/web/jobs.py` modified, plus `jobs.py.orig-lease30`. Same `SCHEMA_HEAD` (`0087_public_project_links`) as the tag, and `pixi.toml` and `pixi.lock` are unchanged between them, so going back is schema-safe. Database about 400 MB. No `defaults.tts` rows in `app_settings` or `workspace_settings`, so `bug-saved-preview-settings-fail-generation` does not block the run. Nothing was running.
- Tag `v.0.14.2+fork.1` is `ebe7194d1dc35157ff6b576a22a5b13b3e89fdce` on `origin`.

## Tasks & Acceptance

**Execution:**
- [ ] `_bmad-output/scripts/fork_slot.py` -- stdlib-only CLI run with `uv run --no-project python`: `install`, `activate`, `back`, `status` per the matrix; clone from `--source` (default the fork's `origin` URL) with `-c core.autocrlf=false --depth 1 --branch <tag>`; running check by executable path under the root (PowerShell CIM on Windows, `ps` elsewhere) -- the repeatable procedure R2 needs
- [ ] `_bmad-output/scripts/test_fork_slot.py` -- each matrix row against a temporary install and a local bare repo, with pixi and the running check stubbed -- proves switching and refusal without touching the real install
- [ ] `_bmad-output/README.md` -- a short "Running a fork build" section: the commands in order, the Manager warning, and going back
- [ ] Owner-authorised run (not part of the coding handoff: done after review, only with the owner's go-ahead) -- `install` and `activate` on the real install, then the owner starts the app and runs the real book; record results under Implementation Notes; then `back` and `activate` again, and `status` each time

**Acceptance Criteria:**
- Given the activated slot, when the app starts, then `GET /api/v1/health` reports `0.14.2+fork.1`.
- Given the real book on the new slot with no lease patch, when its generation run starts, then it gets past the opening identity check.
- Given the switch-back, when the app starts, then it reports 0.14.1 from the patched slot; switching forward again reports the fork version.

## Implementation Notes

Coding: `scripts/fork_slot.py`, `scripts/test_fork_slot.py` and the README section on `fork-files`, uncommitted. 17 tests pass and ruff passes (uv, Windows), after the review patches.

Owner-authorised run, 2026-11-10, with the owner's go-ahead for `install` and `activate`:
- `install v.0.14.2+fork.1` cloned from `origin` and created slot `ebe7194d1dc35157ff6b576a22a5b13b3e89fdce`; the install's pixi built `.pixi/envs/default` (`--locked`) and the slot reports `0.14.2+fork.1`. Pixi skipped the GTK post-link scripts (`run-post-link-scripts = false`), its default, which the Manager's lazy install shares.
- `activate v.0.14.2+fork.1` backed up `current.json` and the database (about 400 MB, to `data/backups/`) and switched. `status`: active `ebe7194d` (0.14.2+fork.1), previous `27a4479a` (0.14.1, patched), nothing running.
- The owner started the app: system status and `/api/v1/health` report `0.14.2+fork.1`, managed, Manager compatible.
- Real book, at the owner's request a one-segment regeneration through the MCP instead of the whole book (5,167 segments, plan revision 3). The run's opening identity check covers the whole plan whatever the selection; only a recast narrows it (`workflow_generation_execution.py:283-286`). The slot is clean, with `WORKER_LEASE_SECONDS = 30`; the old slot still holds 68400.
  - `generation.admit` `0029c0a1`: 20:39:59 to 20:52:07 UTC, succeeded.
  - `generation.run` `9a73cc55` (run 10, `419bf95f`): "Checking speech plan and voice references" from 20:52:07 to 20:59:02, 6 min 55 s on the 30-second lease, first attempt, then "Generated segment 1 of 1"; succeeded, `generated: 1`, status `partial` (5,151 remain, as for the same-shaped run 9 on the patched lease). On the stock lease this check used to fail at about 31 s.
  - The MCP's reads and its mutation response timed out at 15 s while admission ran (the epic 2 stall); the mutation had gone through and the idempotent replay returned the same job.
- Back and forward, with the app and the Manager closed for each switch: `back` switched to `27a4479a`, and the owner's start served `/api/v1/health` `0.14.1` from that slot's Python. A second `back` switched to `ebe7194d`, and the next start served `0.14.2+fork.1` from the fork slot's Python. Each switch backed up the pointer and the database first, so `data/backups/` now holds three copies of about 400 MB each, for the owner to prune.
- Every acceptance criterion is met. The install now runs `0.14.2+fork.1`, with the patched 0.14.1 slot kept as the previous one.

## Plan Change Log

## Review Triage Log

Quick review, pass 1: 0 high, 1 medium, 8 low, 1 false; 1 low rejected. All patches went to the implementer; none deferred.

- false: the acceptance criteria aren't met yet. The real run comes after review, with the owner's go-ahead, by the plan's task order.
- medium, patch: `install` reruns pixi in an existing slot without the running check, so it could rewrite a live app's environment. It now refuses while anything runs; the README no longer says slots are never modified.
- low, patch: `status` says "App running" for the Manager's own processes, and the README says to stop only the app. Relabelled; the README now says to close the Manager too.
- low, patch: the Windows process list is decoded with the locale code page, so a non-ASCII path elsewhere could crash the check with a traceback. Now UTF-8, with parse errors turned into a refusal.
- low, patch: the database URI wasn't percent-encoded, and a failed backup left a partial file and a traceback. Now `as_uri()`, with the partial file removed and a clean refusal.
- low, patch: with `current.json` missing, a stale `current.previous.json` survived and `back` went to an unrelated slot. It is now removed in that case.
- low, patch: `--help` and argument errors skipped the Manager warning. Now printed first.
- low, patch: tests cloned from a plain path, where git ignores `--depth`, so the shallow tag clone was never exercised. They now use a `file://` URL.
- low, patch: the pixi stub kept its return code inside the call list. Separated.
- low, patch: matrix gaps: no test for the `pandrator-release.json` refusal, pixi's `cwd`, pixi not running for a bad slot, or no backups when `back` is refused. Added.
- low, rejected: the POSIX `ps` branch reads `argv[0]` and splits on spaces, so it can miss the app. The owner's install is on Windows, and a fix (`/proc/<pid>/exe` or `lsof`) is more than a direct correction.

## Design Notes

`back` swaps `current.json` with `current.previous.json`, so a second `back` goes forward; no history list is kept beyond the timestamped backups. The database copy is the only data safety net, since the Manager's own `data/backups` holds nothing current.

## Verification

**Commands:**
- `uv run --no-project --with pytest python -m pytest -q _bmad-output/scripts/test_fork_slot.py` -- expected: pass
- `uvx ruff@0.17.3 check _bmad-output/scripts` -- expected: pass
- `uv run --no-project python _bmad-output/scripts/fork_slot.py --workspace <workspace> status` -- expected: shows the active slot and that nothing runs

**Manual checks:**
- After `activate`, the owner starts the app from the Manager launcher: the footer and `/api/v1/health` show `0.14.2+fork.1`.
- The real book's generation run passes the opening check; the app log shows no lease expiry.
- `back`, start, version is 0.14.1; `back` again, start, version is `0.14.2+fork.1`.

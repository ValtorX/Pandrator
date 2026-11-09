---
title: 'Browser checks and builds match CI from a Windows checkout'
type: chore
ticket: '5'
created: '2026-11-09'
status: built
baseline_revision: '388a1bb77be93cba21f736b6f0ffebaa809447b0'
route: oneshot
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The LF rules from `3b4484ce` have not been verified on Windows. Until they are, a `pandrator/web/static` build from a Windows checkout may not match CI's Ubuntu rebuild. `AGENTS.md` still says `.gitattributes` has no eol rule, and it gives no way to run the web checks on Node 24 when the system Node is 22.

**Approach:** Verify on the owner's checkout that `npm --prefix web run quality` passes on Node 24 and that `npm --prefix web run build` leaves `pandrator/web/static` unchanged. Then replace the stale pitfall in `AGENTS.md` with what holds now: the LF rules, how to refresh a checkout made before them, and how to run Node 24 through uv. Copy the result to its tracked backup.

</frozen-after-approval>

## Implementation Notes

Oneshot, because the rules already landed in `3b4484ce` (merged at `7e96cf5b`). What is left is verification plus about 6 lines of instructions.

Verified before any change, on the owner's checkout at `388a1bb7` (Windows 11, `core.autocrlf=true`, uv, Node 24.19.0 from `uvx --from "nodejs-wheel==24.*"`; system Node is 22.23.2, and winget offers no Node 24 package, only 22 and LTS 26):
- `web/`, `pandrator/web/static/` and `model-groups.js` have no `w/crlf` file. The owner's checkout has already picked up the rules. 2,104 other tracked files are still CRLF in the working tree, which these rules do not cover.
- `npm ci` and `quality` pass (prettier, eslint, `check:manager-models`, svelte-check 0/0, knip). `npm exec -c "node --version"` shows that child scripts also get 24.19.0.
- `build` writes `0.14.2-3ea216416b35`. Afterwards `git status --porcelain` is empty for the whole tree, `check:assets` matches, and `test:unit` passes 175 of 175.
- A fresh `autocrlf=true` clone at HEAD checks these paths out as LF with a clean status. A clone made at `3b4484ce^` and then moved to HEAD keeps CRLF in all 686 text files, and `git status` stays clean, so nothing shows the drift. Deleting the tracked files and running `git checkout --` on those paths gives LF and a clean status. The pitfall describes this recipe.
- After review, the owner installed Node 24.22.2 through fnm (winget `Schniz.fnm`, set as fnm's default). With its install folder first on PATH, `npm ci`, `quality` (0 svelte-check errors), `build`, `check:assets` and `test:unit` (175 of 175) all pass, and the build leaves `git status` empty. No shell profile loads fnm yet, so a new shell still finds Hermes's Node 22. On Windows `fnm exec` can't start `npm`, which is a `.cmd` shim.
- The owner then installed the Node 24 MSI (24.22.2 in `C:\Program Files\nodejs`, which comes ahead of Hermes's Node 22 on PATH) and restarted. With the system `node`, which both shells now find, `npm ci`, `quality`, `build`, `check:assets` and `test:unit` (175 of 175) all pass, and `git status` stays empty. That meets the story's verify on the owner's checkout with no PATH workaround.
- `pandrator/logic/language_registry.json` is a build input outside `web/**`, and it is CRLF here. That doesn't matter, because Vite parses the JSON and re-emits it, and the build above came out byte-identical. It needs no rule.

## Review Triage Log

Quick review: 5 findings, all real and all patched in the `AGENTS.md` pitfall, with the backup re-copied. None deferred.
- medium, patch: the claim that "`check:assets` cannot catch this drift" was wrong before a rebuild. The tracked `version.json` holds the LF hash, so a CRLF checkout fails, and the script's remedy, "build and commit", is the wrong fix. The bullet now says so, and that the check passes only after a CRLF rebuild.
- medium, patch: the explicit "never commit a `pandrator/web/static/` change from a CRLF checkout" had been dropped. It is restored.
- low, patch: the detection command covered `web` only. It now covers all three paths, with `model-groups.js` given in full.
- low, patch: "Node older than 24" left out 26. It now says "other than 24", citing `engines` `>=24 <25`.
- low, patch: `npm ci` wasn't covered. The bullet now gives the uvx form of `npm ci --prefix web` too.

## Verification

**Commands:**
- `uvx --from "nodejs-wheel==24.*" npm --prefix web run quality` -- expected: exit 0
- `uvx --from "nodejs-wheel==24.*" npm --prefix web run build`, then `git status --porcelain -- pandrator/web/static` -- expected: no output; `version.json` holds `0.14.2-3ea216416b35`
- `git ls-files --eol web pandrator/web/static pandrator_manager/recovery_ui/static/model-groups.js | grep -c w/crlf` -- expected: 0
- `<uv env> python -m pytest -q tests/test_frontend_architecture.py -k lf` -- expected: the LF guard passes
- `cmp AGENTS.md _bmad-output/backup/repo-instructions.backup.txt` -- expected: identical

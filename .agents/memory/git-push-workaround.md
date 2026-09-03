---
name: Git push workaround
description: How to push to GitHub from this workspace — all CLI credentials are dead; use the GitHub connector's Git Data API, and never include .github/workflows changes.
---

# Pushing to GitHub (ThatOtherZach/Yonder)

**State (Sept 2, 2026):** Every stored Git CLI credential is dead — the Replit-managed credential, `GITHUB_PAT`, and `GITHUB_PERSONAL_ACCESS_TOKEN` all return "Invalid username or token" (user confirmed the PATs are expired and won't be renewed). `git push` from the shell always fails.

**Working path:** the GitHub connector (`listConnections("github")[0]`) inside a `"use impure"` CodeExecution block:
1. Upload objects byte-exact via the Git Data API: `POST /git/blobs` (base64), `POST /git/trees` (mode `040000` for subtrees, not `40000`), `POST /git/commits` (raw author/committer parsed from `git cat-file -p`, message with trailing newline stripped — verify the returned SHA equals the local SHA).
2. Move the branch with `conn.proxyFetch("/repos/{owner}/{repo}/git/refs/heads/main", { method: "PATCH", body: {sha, force:false} })`. Octokit `client.request` with `{ref}` path params URL-encodes slashes; prefer proxyFetch with a literal path.
3. Throttle writes ~200ms; burst uploads trip a Cloudflare block.

**Critical constraint — workflow scope:** the connector's OAuth token has `repo` but NOT `workflow` scope. Any ref update whose commit range touches `.github/workflows/**` fails with a misleading 404 (and `POST /merges` fails 403 "Must have admin rights"). GET commit checks on freshly uploaded objects can also transiently 404 — don't trust those as proof objects are missing.

**How to apply:** never commit changes under `.github/workflows/` if they must reach GitHub — they will wedge the entire branch sync. If it happens, rewrite the offending commits without the workflow file (`git read-tree` + `update-index --force-remove` + `commit-tree` with original author/committer raw dates), push the rewritten chain, `git reset --mixed` local main to it. The dropped workflow file stays untracked on disk; only the user (via GitHub web UI or a token with `workflow` scope) can publish it.

**Why:** Sept 2026 sync outage — 12 commits were unpushable for this exact reason; one workflow-file commit blocked the whole fast-forward.

**Residual:** `.github/workflows/published-share-smoke.yml` exists locally (untracked) but not on GitHub.

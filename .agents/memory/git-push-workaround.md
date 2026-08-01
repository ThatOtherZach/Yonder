---
name: Git push workaround
description: Replit's built-in GitHub credential is broken for this repo; push with the PAT secret instead.
---

Replit's account-level Git Provider credential fails for this repo ("Invalid username or token"), and reconnecting in account settings did not fix it. The `gitPush` callback also fails (PUSH_REJECTED).

**How to apply:** push with the user's PAT stored as the `GITHUB_PERSONAL_ACCESS_TOKEN` secret:

```
git -c credential.helper='!f() { echo "username=x-access-token"; echo "password=$GITHUB_PERSONAL_ACCESS_TOKEN"; }; f' push origin main 2>&1 | sed "s|$GITHUB_PERSONAL_ACCESS_TOKEN|***|g"
```

**Why:** the repo contains `.github/workflows/` files, so the token must have the `workflow` scope (the older `GITHUB_PAT` secret lacks it and gets rejected). Always pipe output through the sed mask so the token never appears in logs.

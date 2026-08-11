---
name: Diagnosing stale production builds
description: How to prove whether the live deployment contains a given fix before debugging "production still broken" reports
---

The live deployment serves the workspace snapshot taken at the moment of the publish — git pushes and commits made after publishing change nothing on the live site.

**Why:** Fixes committed minutes after a publish were silently absent from production, reproducing exactly the old failure modes and causing hours of confusion.

**How to apply:** Before debugging a production failure that "should be fixed", compare the deployment boot time (production logs) against the fix commit time. If the boot predates the fix, the build is stale and the only remedy is republishing.

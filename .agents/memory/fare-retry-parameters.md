---
name: Fare retry parameters
description: Why on-demand fare retries must retain the original leg search dimensions.
---

Every fare-missing leg must carry its original cabin and traveler count through persistence, composition, rendering, and the on-demand fare request.

**Why:** Falling back to endpoint defaults can show an economy or one-traveler price for a ticket planned in another cabin or for a group, which is materially misleading.

**How to apply:** Whenever adding or transforming a flight-leg shape, verify that its Check Fares slot and client request preserve route, date, travelers, cabin, and currency.
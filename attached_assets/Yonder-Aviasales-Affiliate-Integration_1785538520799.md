# Yonder → Aviasales Affiliate Integration

## Goal
Replace Google Flights links with Aviasales links that include our affiliate tracking, so we get paid on bookings.

---

## How to get paid

Every Aviasales link **must** include this at the end:

```
?marker=756039.Zza75700ced74b488c8090948-756039&sub_id=YonderFlights
```

### Example

**Before (normal Aviasales link):**
```
https://www.aviasales.com/search/YVR1808MOW28081
```

**After (with tracking):**
```
https://www.aviasales.com/search/YVR1808MOW28081?marker=756039.Zza75700ced74b488c8090948-756039&sub_id=YonderFlights
```

---

## URL Formats the app already needs to support

### One-way
```
https://www.aviasales.com/search/{ORIGIN}{DDMM}{DESTINATION}{PASSENGERS}?marker=756039.Zza75700ced74b488c8090948-756039&sub_id=YonderFlights
```

### Round-trip
```
https://www.aviasales.com/search/{ORIGIN}{DDMM}{DESTINATION}{DDMM}{PASSENGERS}?marker=756039.Zza75700ced74b488c8090948-756039&sub_id=YonderFlights
```

### Multi-city
```
https://www.aviasales.com/search/{ORIGIN1}{DDMM}{ORIGIN2}{DDMM}{ORIGIN3}{DDMM}...{FINAL_DESTINATION}{PASSENGERS}?marker=756039.Zza75700ced74b488c8090948-756039&sub_id=YonderFlights
```

**Example multi-city:**
```
https://www.aviasales.com/search/YVR1808MOW2808YTO2908ROM1?marker=756039.Zza75700ced74b488c8090948-756039&sub_id=YonderFlights
```

---

## Fallback (when full search data is missing)

If we only know the origin (or the full link fails), use:

```
https://www.aviasales.com/?marker=756039.Zza75700ced74b488c8090948-756039&sub_id=YonderFlights&params=YVR1
```

This pre-selects Vancouver + 1 passenger.

---

## Summary for the developer

1. Keep the existing logic that builds the flight search path.
2. Switch the base domain from Google Flights to `https://www.aviasales.com/search/...`
3. Always append:
   ```
   ?marker=756039.Zza75700ced74b488c8090948-756039&sub_id=YonderFlights
   ```
4. That is the only thing required to get paid.

---

**Questions?** Just ask.
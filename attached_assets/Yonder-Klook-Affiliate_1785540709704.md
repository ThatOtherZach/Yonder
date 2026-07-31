# Yonder → Klook Affiliate Integration

## How to get paid on Klook

Wrap any Klook URL like this:

```
https://tp.media/r?marker=756039.YonderKlook&trs=557178&p=4110&u=URL_ENCODED_KLOOK_LINK&campaign_id=137
```

### Example

Normal Klook search:
```
https://www.klook.com/search/result/?query=Tokyo
```

Tracked version (this is what the app should generate):
```
https://tp.media/r?marker=756039.YonderKlook&trs=557178&p=4110&u=https%3A%2F%2Fwww.klook.com%2Fsearch%2Fresult%2F%3Fquery%3DTokyo&campaign_id=137
```

Just replace `Tokyo` with the city name.  
If the city has a space, URL-encode it (e.g. `New%20York`).

That’s it.
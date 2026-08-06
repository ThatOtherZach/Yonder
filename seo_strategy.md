# SEO Strategy — Yonder.City

## Site description
Yonder.City is a vibe-first spontaneous travel finder. Users describe a trip in plain English, pick an emotional "vibe" (chaos, romance, city, etc.), and get AI-curated flight suggestions. They can also save and share flight itineraries with friends.

## Rendering mode
**SSR** — Python 3.11 FastAPI backend with Jinja2 HTML templates. All public-facing HTML is server-rendered. This is good for crawlability: crawlers see full HTML on first response.

## In scope
- Home/Explore page (`/`)
- Shared trip pages (`/t/{share_id}`, `/t/{kind}/{slug}/{share_id}`)
- Saved trips page (`/saved`)
- Packing page (`/packing`)
- Settings page (`/settings`) — lower priority (user-specific)

## Out of scope
- API endpoints (`/api/**`)
- Internal/backend admin routes

## Target audience
Spontaneous travelers looking for last-minute flights and quick getaway holidays driven by mood/vibe rather than fixed destinations. Young, mobile-first, social-sharing audience.

## Primary keywords
- spontaneous flight finder
- vibe travel
- last minute flights
- flight finder by mood
- surprise trip planner

## Dismissed categories
- (None yet)

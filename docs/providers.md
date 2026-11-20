# Data providers: cost and setup guide

This guide lists every external service inky-dash can use, whether it is **free**, what the **free tier** covers, and what **paid tiers** cost. It also covers how to get a key.

> **Prices checked: 2026-11-15.** Providers change pricing often. Confirm on the linked official page before you sign up.
> **Reliability marks:**
> - ✅ confirmed on the provider's own page,
> - ⚠️ taken from third-party sources or older pages, not confirmed officially,
> - ❓ not published.

## How much does inky-dash use?

Estimated volumes for a typical config (weather all day, commute twice a day, map for one route):

| Use | Calls per day | Calls per month |
|---|---|---|
| Weather (refresh every 30 min, 16 h) | ~32 | ~1,000 |
| Routing (3 calls per refresh, every 10 min, 1.5 h) | ~27 | ~600 (weekdays) |
| Traffic alert polling (every 15 min, 06:30–08:45) | ~9 | ~200 (weekdays) |
| Air quality / pollen / UV + warnings (every 30–60 min) | ~30 | ~1,000 |
| Geocoding (once per address, cached forever) | ~0 | < 10 |
| Map tiles (route view cached ≥ 7 days) | ~0 | ~20–100 tiles |

**Every provider below stays within its free tier at these volumes**, often by a factor of 10–100. Each provider also has a `max_daily_calls` guard in the config, so a bug can't run up a bill.

---

## Summary

| Provider | Used for | Free? | Free tier | Paid from | Card needed for free tier? |
|---|---|---|---|---|---|
| [Open-Meteo](#open-meteo-weather) | Weather | ✅ Free (non-commercial) | 10,000 calls/day | Fixed monthly plans, price not published | No (no account at all) |
| [Open-Meteo Air Quality](#open-meteo-weather) | AQI, pollen, UV | ✅ Free (non-commercial) | Shares the Open-Meteo limits above | Same as Open-Meteo | No |
| [MeteoAlarm](#meteoalarm-weather-warnings) | Official weather warnings | ⚠️ Free | No published quota | — | No (no account) |
| [`holidays` library](#holidays-library-public-holidays) | Public holidays | ✅ Free (offline library, MIT) | No API calls | — | No |
| [TomTom](#tomtom-routing-traffic-geocoding) | Routing with traffic, geocoding | ✅ Free tier | 20,000 routing requests/month | ⚠️ ~$0.75 / 1,000 routing requests | ✅ No |
| [HERE](#here-routing-traffic) | Routing with traffic | ⚠️ Free tier | ⚠️ 5,000–30,000 transactions/month (sources disagree) | ⚠️ ~$1.50–2.90 / 1,000 | ❓ |
| [Mapbox](#mapbox-routing-traffic) | Routing with traffic | ✅ Free tier | 100,000 Directions requests/month | ✅ $2.00 / 1,000 | ❓ (check at sign-up) |
| [Google Routes](#google-routes-api-routing-traffic-transit) | Routing with traffic, transit | Free monthly cap | 5,000 traffic-aware requests/month (Pro SKU) | ✅ $10.00 / 1,000 (Pro) | **Yes**, billing account required |
| [OSRM (FOSSGIS)](#osrm-on-fossgis-routing-no-traffic) | Routing without traffic | ✅ Free | Fair use, at most 1 request/s | — (no paid plan) | No (no account) |
| [OpenRouteService](#openrouteservice-routing-no-traffic) | Routing without traffic | ✅ Free | 2,000 directions/day, 40/min | No paid plans (quota increase on request) | No |
| [Nominatim](#nominatim-geocoding) | Geocoding | ✅ Free | Fair use, at most 1 request/s | — | No (no account) |
| [Stadia Maps](#stadia-maps-map-tiles-toner-lite) | Map tiles (Toner Lite) | ✅ Free (non-commercial) | 200,000 credits/month (1 credit per raster tile) | ✅ $20/month (Starter) | ❓ |
| [OpenStreetMap tiles](#openstreetmap-tiles-map-tiles) | Map tiles (standard) | ✅ Free | Fair use under the tile policy | — | No (no account) |

**Recommended minimal setup:** Open-Meteo + Nominatim + OSRM (no keys), plus **one TomTom key** for live traffic, plus **one Stadia key** for the nicer map. Total cost: **€0**.

---

## Open-Meteo (weather)
- **Free:** ✅ yes, for non-commercial use. No API key or account needed.
- **Free tier:** ✅
  - 600 calls/min
  - 5,000/hour
  - 10,000/day
  - 300,000/month (monthly limit not enforced yet)
- **Paid:** ✅ API Standard (1M calls/month), Professional (5M), Enterprise (50M+). These are "fixed monthly prices", ❓ but the amounts aren't published; they're shown in the customer portal. Paid plans are required for commercial use.
- **Setup:** nothing to do.
- Pricing: https://open-meteo.com/en/pricing

The **Air Quality API** (European AQI, pollen for Europe, UV) is part of the same free, keyless service and counts toward the same limits. Docs: https://open-meteo.com/en/docs/air-quality-api

## MeteoAlarm (weather warnings)
- **Free:** ⚠️ yes. Public Atom/CAP feeds per country that aggregate the national weather services' warnings (KMI/IRM for Belgium). No key or account.
- **Free tier:** ⚠️ no published quota. inky-dash polls at most every 15 min and caches the result.
- **Paid:** none.
- **Terms:** ⚠️ check the reuse and attribution terms on the site before relying on it. The national service's own feed is an alternative.
- Site: https://meteoalarm.org

## `holidays` library (public holidays)
- **Free:** ✅ yes. An open-source Python package (MIT) that computes public holidays offline for about 200 countries. No network calls, key or account.
- **Paid:** none.
- Project: https://github.com/vacanza/holidays

## TomTom (routing, traffic, geocoding)
- **Free:** ✅ yes, the "Start building" plan. ✅ **No credit card needed.**
- **Free tier (monthly):** ✅
  - Routing API: **20,000 requests/month**
  - Geocoding: 20,000/month
  - Reverse geocoding: 20,000/month
  - Map tiles (vector/raster): 200,000/month
  - ⚠️ Older pages described this as 2,500 non-tile requests/day. TomTom has since moved to monthly quotas and moved developer accounts to *MyTomTom* (September 2026).
- **Paid:** "Pay as you grow" (usage-based, no upfront costs) or Enterprise (custom).
  - ⚠️ Standard routing is reported at about **$0.75 per 1,000 requests**. The official page lists volume tiers (20K / 1M / 5M / 10M) but the per-1,000 prices weren't visible.
  - ⚠️ Reported: on the free plan, usage above the quota is **blocked, not billed**, unless you add prepaid credit.
- **What inky-dash uses:** Calculate Route with traffic, alternatives, the usual (historic) time, jam sections and departure forecasts. This is the most complete free option.
- **Setup:**
  1. Create an account at https://developer.tomtom.com (MyTomTom).
  2. Create an API key.
  3. Put it in `.env` as `TOMTOM_API_KEY=...`.
- Pricing: https://docs.tomtom.com/pricing

## HERE (routing, traffic)
- **Free:** ⚠️ yes, the Base Plan includes a monthly free allowance.
- **Free tier:** ⚠️ unclear. Third-party sources report 30,000 transactions/month across most APIs, or 5,000/month for routing. HERE's own pricing page currently says "contact us" and publishes no numbers.
- **Paid:** ⚠️ reported at about **$1.50 per 1,000** routing transactions after the free allowance. Partners quote up to about $2.90 per 1,000. A ~6% price increase was reported from April 2026.
- **What inky-dash uses:** Routing v8 with live traffic, typical duration, alternatives (up to 6) and traffic spans.
- **Setup:**
  1. Create an account at https://platform.here.com.
  2. Create a project and an API key.
  3. Put it in `.env` as `HERE_API_KEY=...`.
  4. Check the free allowance shown in your account.
- Pricing: https://www.here.com/get-started/pricing

## Mapbox (routing, traffic)
- **Free:** ✅ yes, a monthly free tier.
- **Free tier:** ✅ **100,000 Directions requests/month**. Traffic-aware `driving-traffic` appears to be included. ✅ Raster tiles: 750,000/month free. Static tiles: 200,000/month free.
- **Paid:** ✅ Directions per 1,000 requests:
  - $2.00 (100,001–500,000)
  - $1.60 (up to 1M)
  - $1.20 (up to 5M)
- **Limits for inky-dash:** at most 2 alternatives. No "usual" time, so inky-dash makes one extra free-flow request as the baseline.
- **Setup:**
  1. Create an account at https://account.mapbox.com.
  2. Copy the default public token, or create a token.
  3. Put it in `.env` as `MAPBOX_TOKEN=...`.
  4. ❓ Check whether your account asks for a card.
- Pricing: https://www.mapbox.com/pricing

## Google Routes API (routing, traffic, transit)
- **Free:** a free monthly cap per SKU, but a **billing account with a payment card is required** even when you stay within the cap.
- **Free tier:** ✅ Compute Routes:
  - **Essentials** (no traffic): 10,000/month
  - **Pro** (any `TRAFFIC_AWARE` / `TRAFFIC_AWARE_OPTIMAL` request): **5,000/month**
  - **Enterprise** (for example two-wheeler or large-vehicle routing): 1,000/month
- **Paid:** ✅ first paid tier per 1,000 requests: Essentials **$5.00**, Pro **$10.00**, Enterprise **$15.00**. These are the most expensive per call, but inky-dash's ~600 requests/month stay well under the Pro cap.
- **What inky-dash uses:** a fallback for car with traffic. Planned for **public transport** later, the only provider here that covers transit broadly.
- **Setup:**
  1. Create a Google Cloud project and enable the "Routes API".
  2. Attach a billing account.
  3. Create an API key **restricted to the Routes API**.
  4. Put it in `.env` as `GOOGLE_MAPS_API_KEY=...`.
  5. Set a **budget alert** (for example €1) in Cloud Billing.
- Pricing: https://developers.google.com/maps/billing-and-pricing/pricing. SKU rules: https://developers.google.com/maps/documentation/routes/usage-and-billing

## OSRM on FOSSGIS (routing, no traffic)
- **Free:** ✅ yes. No key and no account. Run by FOSSGIS e.V. as a community service.
- **Limits:** ✅
  - at most **1 request/second**,
  - a valid User-Agent,
  - visible attribution,
  - fair use with no heavy or scraping use,
  - no uptime guarantee,
  - don't hardcode the URL (it's configurable in inky-dash).
- **Paid:** none. For guaranteed service, self-host OSRM. That's not feasible on a Pi Zero, but possible on a home server.
- **Profiles:** car, bike, foot. **No live traffic**, so inky-dash uses it for bike/walk and as a last-resort car fallback labelled "no live traffic".
- Terms: https://routing.openstreetmap.de/about.html · https://fossgis.de/arbeitsgruppen/osm-server/nutzungsbedingungen

## OpenRouteService (routing, no traffic)
- **Free:** ✅ yes, with a free API key (HeiGIT account).
- **Free tier:** ✅ Standard plan:
  - Directions **2,000/day, 40/min**
  - Matrix 500/day
  - Isochrones 500/day
  - Above the quota, the API returns an error. You're never billed.
- **Paid:** none. ⚠️ No commercial plans; ORS says to contact them for a higher quota. It can also be self-hosted.
- **Setup:**
  1. Sign up at https://account.heigit.org.
  2. Create a token.
  3. Put it in `.env` as `ORS_API_KEY=...`.
- Plans: https://openrouteservice.org/plans/

## Nominatim (geocoding)
- **Free:** ✅ yes. No key and no account. Run by the OpenStreetMap Foundation.
- **Limits:** ✅
  - at most **1 request/second**,
  - an identifying User-Agent (library defaults aren't allowed),
  - results must be cached,
  - periodic or bulk use is discouraged.

  inky-dash geocodes each address once and caches the result forever.
- **Paid:** none. For heavier use, self-host or use a commercial geocoder (TomTom, HERE and Mapbox geocoders are options).
- Policy: https://operations.osmfoundation.org/policies/nominatim/

## Stadia Maps (map tiles, Toner Lite)
- **Free:** ✅ yes, but **non-commercial use only**.
- **Free tier:** ✅ **200,000 credits/month**. Standard raster tiles cost 1 credit each, and inky-dash needs about 100 tiles a month.
- **Paid:** ✅
  - Starter **$20/month** (1M credits)
  - Standard $80/month (7.5M)
  - Professional $250/month (25M)

  Commercial use is allowed from Starter upwards. There's a 14-day Professional trial with no card.
- **Styles used:** Stamen **Toner** (pure black/white, the inky-dash default) and Stamen **Toner Lite** (greyscale, optional). Both are on the same key and cost the same credits.
- **Key:** server-side and device apps need an API key. The keyless localhost mode is for development only.
- **Setup:**
  1. Create an account at https://client.stadiamaps.com.
  2. Create a property and an API key.
  3. Put it in `.env` as `STADIA_API_KEY=...`.
- Pricing: https://stadiamaps.com/pricing/ · Style preview: https://docs.stadiamaps.com/map-styles/stamen-toner/

## OpenStreetMap tiles (map tiles)
- **Free:** ✅ yes, no key, run by the OSM Foundation on donated resources.
- **Rules:** ✅
  - a unique User-Agent (no library default),
  - honour cache headers or cache ≥ 7 days,
  - visible attribution,
  - **no bulk or prefetching**,
  - no offline use.

  The policy targets maps someone is actively viewing, which makes an unattended dashboard a grey area. inky-dash keeps fetches to a minimum.
- **Paid:** none. For heavier use, switch to Stadia, Mapbox raster tiles (750k/month free) or TomTom tiles (200k/month free).
- Policy: https://operations.osmfoundation.org/policies/tiles/

---

## Personal use and keys

Whether you need a key is decided by the provider, not by how you use it. Personal / non-commercial use matters only for **which terms apply**: Open-Meteo and Stadia are free *only* for non-commercial use, while TomTom, Mapbox and Google free tiers have no such restriction.

## Keeping costs at €0
- Use only providers whose free tier **blocks** instead of billing when exceeded (TomTom free plan, ORS), or set **budget alerts** (Google).
- Keep `max_daily_calls` per provider (default 500, Google 200).
- `inky-dash doctor` shows each provider's calls today and this month.

# Parcels — Plan

See `SPEC.md` for the requirements. Tick items off as they land.

## Phase 1 — Working core
- [x] Research the current Google OAuth/Gmail setup docs and the 17TRACK API
- [x] Project scaffold (uv, FastAPI), port 8765 chosen
- [x] `app/parse.py`: per-email stage, carrier, tracking, order, ETA
- [x] `tests/test_parse.py`: fixture emails for Amazon, Royal Mail, Evri, DPD, exceptions → verify: `uv run pytest`
- [x] `app/build.py`: grouping, per-carrier learning, shipment assembly → verify: tests on grouping and learned notches
- [x] `app/db.py`: SQLite schema and queries
- [x] `app/gmail.py`: OAuth flow, MIME→text, backfill and poll
- [x] `app/track17.py` (untested against the live API — needs your key): optional 17TRACK register/poll
- [x] `app/main.py`: API, SSE stream, background sync loop
- [x] `static/`: list, horizontal timeline, detail log, settings, SSE patching, mobile layout → verify: screenshots at 1280px and 390px with fixture data
- [x] Run as a persistent background service on 0.0.0.0:8765 → verify: `curl` from another interface
- [x] `SETUP.md`: step-by-step Google Cloud and Gmail instructions for the owner

## Phase 2 — Real data
- [x] Owner connects Gmail and runs the backfill (Gmail calls paced, with retry on rate limits)
- [x] First tuning round: ignore payment/food/digital senders (PayPal etc.), item names from bodies and subjects, DPD/InPost numbers, carrier mail joined to the shop order it names, SportPursuit format
- [x] Sort by ETA (soonest first) and then by newest order; instant client-side search across every field (`/` focuses it)
- [x] systemd service, so it starts on boot
- [x] Rename any parcel from its opened card (for carrier-only parcels such as Vinted buys); "Reset name" puts the original back. Stored per email in `kv.names`, so it survives rebuilds
- [x] Hide any parcel from its opened card; restore it from Settings → Hidden parcels. Stored per email in `kv.hidden`
- [x] Delivery slots from emails (Amazon, Evri incl. "delivery time updated", DPD, Yodel, Royal Mail "by 5pm"), shown next to the arrival day; the newest email's slot for that day wins
- [x] Delivered parcels stay on Active until midnight of the day they arrived
- [ ] Keep reviewing misclassified or missed emails and tune the rules (fixtures added for each fix)

## Phase 3 — Make it pretty
- [x] Design pass with the impeccable skill: light theme with a light-blue accent, borderless cards with the arrival date top right, uniform thick timelines with line notches that fill left to right, Manrope (self-hosted). Fits 4+ cards on a 1366×657 laptop and on a phone → verify: Playwright screenshots, impeccable detector
- [x] Optional dark theme (Settings → Appearance: Light / Dark / Match device; saved per device, light by default)
- [x] E-ink appearance: black and white, borders instead of shadows, no animation, "x ago" redraws every 10 minutes instead of every minute
- [ ] Kiosk niceties (auto-scroll, dim at night) if wanted

## Decisions log
- **Rebuild-from-facts:** shipments are recomputed from stored per-email facts on every change. This is simple and lets parser fixes apply retroactively. *Ceiling:* fine up to tens of thousands of emails.
- **Polling over Pub/Sub:** one inbox and a 60s poll; Pub/Sub would need a public push endpoint and GCP topic setup.
- **OAuth app published "In production", unverified:** in Testing mode, refresh tokens expire every 7 days. Personal use stays far below the 100-user cap.
- **17TRACK optional:** the free quota is now a one-time 200 numbers, so only active shipments are registered.

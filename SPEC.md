# Parcels — Spec

A self-hosted web app that reads the owner's Gmail, finds every parcel, and shows each one's journey as a horizontal timeline. Single user. Runs in this environment on `0.0.0.0:8765`, exposed via the owner's Cloudflare tunnel (auth handled there).

## Goals (in priority order)
1. **Works first, pretty later.** Simple design, detailed information.
2. Correctly finds deliveries in Gmail, including archived mail, and keeps them up to date automatically.
3. Live UI: data updates appear without a page refresh (kiosk use).
4. Works well on desktop, kiosk displays, and **mobile (≥ 360px wide)**.

## Functional requirements

### F1. Gmail access
- OAuth 2.0 with scope `gmail.readonly` only. Tokens are stored locally in `data/`.
- The owner does setup in the browser: upload the client JSON, click Connect, and approve.
- Fallback for when the redirect can't reach the server: paste the redirected URL back into the app.

### F2. Ingestion
- **Backfill:** search all mail (`in:anywhere`, archived included) for delivery-like emails from the last N years (default 3, cap ~3000 messages).
- **Live:** poll Gmail every 60s for recent matching mail and process only unseen IDs.
- Store per-email *facts* (not full HTML), so the parser can be improved and re-run without refetching.

### F3. Extraction (per email)
- **Stage:** one canonical stage, or one exception, or "unclassified update".
  - Canonical stages: Ordered → Preparing → Label created → Dispatched → With carrier → In transit → Customs → Out for delivery → Ready to collect → **Delivered** (always last).
  - Exceptions: Delayed, Delivery attempted, Action needed, Damaged, **Lost**, **Returned**, **Cancelled** (bold = terminal).
- **Carrier:** from sender domain, tracking-number format, or a mention in the text. UK carriers first (Royal Mail, Parcelforce, Evri, DPD, Yodel, InPost, Amazon), plus UPS, DHL, and FedEx.
- **Identifiers:** tracking numbers (Royal Mail/UPU numbers are check-digit validated), order numbers.
- **ETA:** "arriving Friday", "expected 3 October", "delivery date 03/10/2026", today/tomorrow.
- Item title where the subject quotes it (e.g. Amazon).

### F4. Grouping into shipments
- Emails sharing a tracking number or a merchant-scoped order number belong to one shipment (union-find).
- An email with no identifiers joins the latest identified shipment in the same Gmail thread.
- Shipment IDs stay stable across rebuilds (derived from the earliest email).

### F5. Learning the timeline per carrier/merchant
- For each carrier and merchant, count which stages occur across all past shipments. Stages seen in ≥ 20% of them become that carrier's **notches**.
- Unclassified emails are learned too: their normalised subject becomes a carrier-specific stage once it recurs across ≥ 3 shipments. Its position is the average of where it falls between known stages.
- Unknown carriers start as `Ordered → Delivered`. Notches are added as emails arrive.
- A shipment's timeline is its learned template plus any stage it actually hit, in order. Delivered is always last.

### F6. Status & irregularities
- Current stage = the furthest stage reached.
- An exception is "active" if it is newer than the last normal stage. Terminal exceptions stay unless a later normal stage arrives.
- **Stale:** no update for 21 days and not delivered.

### F7. Tracking API (optional)
- With a 17TRACK API key: register tracking numbers of **active** shipments only, to save quota (200 free one-time on new accounts). Poll every few hours.
- Merge carrier events and the ETA into the shipment.
- Without a key: show a deep link to the carrier's (or 17TRACK's) tracking page.

### F8. UI
- A long list of shipment cards. Filters: Active / Problems / Delivered / All.
- Each card has a header (title, merchant, carrier, tracking #, ETA, status chip), then a **horizontal timeline**:
  - Filled dots for reached notches with dates, a highlighted current notch, and hollow future notches.
  - Exceptions shown as red markers at the point they happened.
- Tap or click a card to expand the detail log: every email (date, subject, snippet, Gmail link) and every carrier event.
- **Live updates via Server-Sent Events.** Cards are patched in place by ID, with no page reload.
- **Mobile:** the timeline stays horizontal. On narrow screens, only the current, first, and last labels are shown, and the detail log has the rest. Touch targets are ≥ 44px and nothing scrolls sideways.
- Settings panel: Gmail status/connect, client JSON upload, 17TRACK key, backfill/rebuild buttons, sync status.

## Non-goals (for now)
- Multi-user support or in-app auth (Cloudflare handles access).
- Gmail push via Pub/Sub (polling is enough for one inbox).
- LLM-based classification (could be added later if the rules miss too much).
- Visual polish (a later pass, using the impeccable skill).

## Tech
- Python 3.13, FastAPI, uvicorn, SQLite (stdlib), google-api-python-client, beautifulsoup4, httpx.
- Frontend: one HTML file plus vanilla JS and CSS. No build step.
- Tests: pytest on the parser and grouping, using fixture emails.

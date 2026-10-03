# Parcels — Setup

The app runs at **`http://0.0.0.0:8765`** on this machine (from other devices on your network: `http://<this machine's LAN IP>:8765`).
Point your Cloudflare tunnel's public hostname at `http://localhost:8765`.

These steps follow Google's current docs (checked 3 Oct 2026). They take about 10 minutes. You need the Google account whose Gmail you want to track.

> Below, `https://YOUR-TUNNEL-HOST` means the public hostname you give the tunnel, e.g. `https://parcels.example.com`.

---

## Step 1 — Create a Google Cloud project
1. Go to <https://console.cloud.google.com/projectcreate>, signed in as your Gmail account.
2. Project name: `Parcels` → **Create**. Make sure it's the selected project (top-left dropdown).

## Step 2 — Enable the Gmail API
1. Open <https://console.cloud.google.com/apis/library/gmail.googleapis.com>.
2. Click **Enable**.

## Step 3 — Configure the OAuth consent screen ("Google Auth Platform")
1. Open **Menu ☰ → Google Auth Platform → Branding**: <https://console.cloud.google.com/auth/branding>.
2. If it says the platform isn't configured, click **Get Started**:
   - **App Information:** App name `Parcels`, User support email = your email → **Next**.
   - **Audience:** choose **External**. "Internal" only exists for Google Workspace organisations, not personal @gmail.com accounts. → **Next**.
   - **Contact Information:** your email → **Next**.
   - **Finish:** tick the Google API Services User Data Policy agreement → **Continue** → **Create**.
3. **Data Access** (left menu: <https://console.cloud.google.com/auth/scopes>) → **Add or Remove Scopes**.
   - In the filter, type `gmail.readonly`, tick **`.../auth/gmail.readonly`** ("View your email messages and settings") → **Update** → **Save**.
   - It's listed under "restricted scopes". That's expected; see Step 5.

## Step 4 — Create the OAuth client
1. **Menu ☰ → Google Auth Platform → Clients**: <https://console.cloud.google.com/auth/clients> → **Create Client**.
2. **Application type:** **Web application**. **Name:** `Parcels`.
3. Under **Authorized redirect URIs**, click **Add URI** and add **both** of these:
   - `https://YOUR-TUNNEL-HOST/auth/callback`
   - `http://localhost:8765/auth/callback` (fallback)

   The exact URI the app expects is also shown in the app's Settings, once you open it through the tunnel.
4. Click **Create**.
5. **Important:** in the popup, click **Download JSON** *now*. Google only shows or downloads the client secret **once, at creation time**. If you lose it, add a new secret to the client, or create a new client.

## Step 5 — Publish the app, so access doesn't expire weekly
Apps left in **Testing** mode get refresh tokens that **expire after 7 days**, which would make you reconnect every week.
1. Go to **Audience**: <https://console.cloud.google.com/auth/audience>.
2. Under *Publishing status*, click **Publish app** → **Confirm**.
3. You do **not** need to submit for verification. The app is for your own use, and unverified apps can be used by up to 100 accounts. The only side effect is a warning screen during sign-in (Step 6).

*(If you'd rather stay in Testing: on the same page, use **Add users** to add your Gmail address, then reconnect every 7 days.)*

## Step 6 — Connect from Parcels
1. Open `https://YOUR-TUNNEL-HOST`. The **Settings** panel opens automatically the first time (it's also behind ⚙).
2. **1. Google OAuth client:** choose the JSON file you downloaded in Step 4.
3. **2. Connect Gmail:** click **Connect Gmail**.
4. Pick your account. You'll see **"Google hasn't verified this app"**. Click **Advanced → Go to Parcels (unsafe)**. It's "unsafe" only because you haven't paid for Google's review of your own app.
5. Allow **"View your email messages and settings"** → **Continue**. You land back on Parcels.

**If the redirect fails** (e.g. you used the localhost URI from another device): the browser shows an error page, but its address bar has `...auth/callback?state=...&code=...`. Copy that whole address into **Settings → "Redirect didn't come back here?"** and click **Finish**.

## Step 7 — Let it learn
- Right after connecting, it searches **all mail, including archived** (not spam/trash or the Promotions/Social tabs), going back **3 years** (adjustable in Settings), capped at 3000 emails. Progress shows next to the title, and parcels appear as they're found.
- After that, it checks for new mail **every 60 seconds**. Open pages update live, without reloading.

## Optional — 17TRACK for carrier scans and estimates
1. Create an account at <https://api.17track.net> → dashboard → **Settings** → copy the **security key**.
2. Paste it into **Settings → 3. Tracking details** → **Save**.
3. Free quota: accounts created since 7 Jan 2026 get a **one-time 200 tracking numbers**. To conserve them, Parcels only registers numbers for parcels that are still active (not delivered, updated within 30 days). It then refreshes them every 3 hours at no extra cost.

---

## Running it
- It runs as the systemd service `parcelwatch` (unit file: `parcelwatch.service`, installed in `/etc/systemd/system/`). It starts on boot and restarts if it crashes.
- Restart / stop / status: `sudo systemctl restart parcelwatch` · `sudo systemctl stop parcelwatch` · `systemctl status parcelwatch`.
- Logs: `/home/user/parcelwatch/data/server.log`.
- After editing the unit file: `sudo cp parcelwatch.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart parcelwatch`.
- Tests: `cd /home/user/parcelwatch && uv run pytest`.
- Your data: `data/parcelwatch.db` (email facts, plus the first 10k characters of each candidate email's text, so parser fixes can re-run without refetching). The token is in `data/token.json`. Delete `data/` to forget everything. To revoke access entirely, go to <https://myaccount.google.com/permissions>.

## Sources
- Gmail API Python quickstart: <https://developers.google.com/workspace/gmail/api/quickstart/python>
- Configure OAuth consent: <https://developers.google.com/workspace/guides/configure-oauth-consent>
- Create credentials: <https://developers.google.com/workspace/guides/create-credentials>
- Refresh token expiry in Testing: <https://developers.google.com/identity/protocols/oauth2>
- Manage app audience (Testing vs production, 100-user cap): <https://support.google.com/cloud/answer/15549945>
- Client secrets shown once: <https://support.google.com/cloud/answer/15549257>
- Web server OAuth flow (redirect URI rules): <https://developers.google.com/identity/protocols/oauth2/web-server>
- 17TRACK API v2.4: <https://api.17track.net/en/doc?version=v2.4>

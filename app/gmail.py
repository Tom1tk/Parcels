"""Gmail OAuth + fetching. Read-only scope; tokens live in data/."""
import base64
import logging
import os
import re
import time
from email.utils import parseaddr

from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .db import DATA

os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # allows the http://localhost paste-back fallback
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CLIENT_FILE = DATA / "client_secret.json"
TOKEN_FILE = DATA / "token.json"
# Words any delivery email will contain at least one of. Promotions/social tabs skipped (marketing noise).
QUERY = ('in:anywhere -in:spam -in:trash -category:promotions -category:social '
         '{order shipped dispatched delivered delivery tracking parcel package courier "on its way" "out for delivery"}')
# Gmail allows 6,000 quota units/min per user and messages.get costs 20, i.e. 300 gets/min.
FETCH_GAP_S = 0.4  # ~150 calls/min; 0.25s still tripped the limit, so leave Gmail plenty of slack
RATE_LIMIT_WAIT_S = 65
log = logging.getLogger("parcelwatch")
_flow: Flow | None = None
_last_fetch = 0.0


def connected() -> bool:
    return TOKEN_FILE.exists()


def auth_url(redirect_uri: str) -> str:
    global _flow
    _flow = Flow.from_client_secrets_file(str(CLIENT_FILE), SCOPES, redirect_uri=redirect_uri)
    url, _ = _flow.authorization_url(access_type="offline", prompt="consent")
    return url


def finish(response_url: str) -> None:
    if not _flow:
        raise RuntimeError("Start the Connect flow first (the app may have restarted).")
    _flow.fetch_token(authorization_response=response_url)
    TOKEN_FILE.write_text(_flow.credentials.to_json())


def _creds() -> Credentials:
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
    return creds


def service():
    return build("gmail", "v1", credentials=_creds(), cache_discovery=False)


def list_ids(svc, query: str, limit: int) -> list[str]:
    ids, token = [], None
    while len(ids) < limit:
        res = _execute(svc.users().messages().list(userId="me", q=query, pageToken=token,
                                                   maxResults=min(500, limit - len(ids))))
        ids += [m["id"] for m in res.get("messages", [])]
        token = res.get("nextPageToken")
        if not token:
            break
    return ids


def _walk(part):
    yield part
    for p in part.get("parts", []):
        yield from _walk(p)


def _decode(part) -> str:
    data = part.get("body", {}).get("data")
    return base64.urlsafe_b64decode(data).decode("utf-8", "replace") if data else ""


def to_text(payload) -> str:
    plain = " ".join(_decode(p) for p in _walk(payload) if p.get("mimeType") == "text/plain")
    html = " ".join(_decode(p) for p in _walk(payload) if p.get("mimeType") == "text/html")
    if html and len(plain.strip()) < 200:  # plain parts are often just "view in browser"
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["style", "script", "head"]):
            t.decompose()
        plain = soup.get_text(" ")
    return re.sub(r"\s+", " ", plain).strip()


def _rate_limited(e: HttpError) -> bool:
    return e.resp.status == 429 or (e.resp.status == 403 and b"ateLimitExceeded" in (e.content or b""))


def _execute(request, attempts: int = 5) -> dict:
    """Run a Gmail API request, paced under the per-user quota and retried when it's exceeded."""
    global _last_fetch
    for attempt in range(attempts):
        time.sleep(max(0.0, _last_fetch + FETCH_GAP_S - time.monotonic()))
        _last_fetch = time.monotonic()
        try:
            return request.execute()
        except HttpError as e:
            if not _rate_limited(e) or attempt == attempts - 1:
                raise
            log.warning("Gmail rate limit hit; waiting %ss", RATE_LIMIT_WAIT_S)
            time.sleep(RATE_LIMIT_WAIT_S)


def fetch(svc, msg_id: str) -> dict:
    m = _execute(svc.users().messages().get(userId="me", id=msg_id, format="full"))
    headers = {h["name"].lower(): h["value"] for h in m["payload"].get("headers", [])}
    name, addr = parseaddr(headers.get("from", ""))
    return {
        "id": m["id"], "thread_id": m["threadId"], "ts": int(m["internalDate"]),
        "sender": f"{name} <{addr}>" if name else addr, "subject": headers.get("subject", ""),
        "snippet": m.get("snippet", ""), "text": to_text(m["payload"])[:10000],
    }

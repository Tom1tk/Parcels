import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from googleapiclient.errors import HttpError

from . import db, gmail, track17
from .build import DAY_MS, apply_owner, build
from .parse import parse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("parcelwatch")
ROOT = Path(__file__).resolve().parent.parent
POLL_SECONDS = 60
TRACK_EVERY_S = 3 * 3600
BACKFILL_MAX = 3000
PARSER_VERSION = hashlib.sha1((ROOT / "app/parse.py").read_bytes()).hexdigest()

state = {"shipments": [], "version": 0, "sync": {"state": "idle", "message": "", "last": None}}
_listeners: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None
_wake = threading.Event()


def publish(kind: str) -> None:
    for q in list(_listeners):
        _loop.call_soon_threadsafe(q.put_nowait, kind)


def set_sync(**kw) -> None:
    state["sync"] = state["sync"] | kw
    publish("sync")


def rebuild() -> None:
    msgs = [m | {"facts": json.loads(m["facts"])} for m in db.all_messages() if m["facts"]]
    state["shipments"] = apply_owner(build(msgs, db.api_data(), marked=db.get("delivered", {})),
                                     db.get("names", {}), set(db.get("hidden", [])))
    state["version"] += 1
    publish("shipments")


def reparse_all() -> None:
    db.run("UPDATE messages SET facts=? WHERE id=?", [
        (json.dumps(f) if (f := parse(m["subject"], m["sender"], m["text"], datetime.fromtimestamp(m["ts"] / 1000))) else None, m["id"])
        for m in db.all_messages()])
    db.put("parser", PARSER_VERSION)


def ingest(svc, ids: list[str], label: str) -> None:
    known = db.known_ids()
    new = [i for i in ids if i not in known]
    for n, mid in enumerate(new, 1):
        try:
            m = gmail.fetch(svc, mid)
        except HttpError as e:
            if e.resp.status == 404:  # deleted between list and get
                continue
            raise
        db.save_message(m, parse(m["subject"], m["sender"], m["text"], datetime.fromtimestamp(m["ts"] / 1000)))
        if n % 50 == 0:
            set_sync(state="syncing", message=f"{label}: {n} / {len(new)} emails")
            rebuild()
    if new:
        rebuild()


def refresh_tracking(key: str) -> None:
    cutoff = time.time() * 1000 - 30 * DAY_MS
    numbers = sorted({t["number"] for s in state["shipments"] if s["status"] in ("active", "problem")
                      and s["updated"] > cutoff for t in s["tracking"] if not t["number"].startswith("TBA")})
    if not numbers:
        return
    registered = {r[0] for r in db.q("SELECT tracking FROM api_meta")}
    track17.register(key, [n for n in numbers if n not in registered])
    now = int(time.time() * 1000)
    db.run("INSERT OR IGNORE INTO api_meta VALUES (?, NULL, 0)", [(n,) for n in numbers])
    for n, info in track17.track(key, numbers).items():
        db.run("INSERT OR IGNORE INTO api_events VALUES (?,?,?,?,?)",
               [(n, e["ts"], e["stage"], e["description"], e["location"]) for e in info["events"]])
        db.run("UPDATE api_meta SET eta=?, polled=? WHERE tracking=?", [(info["eta"], now, n)])
    rebuild()


def sync_loop() -> None:
    last_track = 0.0
    while True:
        try:
            if gmail.connected():
                svc = gmail.service()
                if not db.get("backfilled"):
                    years = db.get("backfill_years", 3)
                    set_sync(state="syncing", message="Searching your mail history…")
                    ingest(svc, gmail.list_ids(svc, f"{gmail.QUERY} newer_than:{years}y", BACKFILL_MAX), "History")
                    db.put("backfilled", True)
                ingest(svc, gmail.list_ids(svc, f"{gmail.QUERY} newer_than:2d", 200), "New mail")
                key = db.get("track17_key")
                if key and time.time() - last_track > TRACK_EVERY_S:
                    set_sync(state="syncing", message="Checking carriers…")
                    refresh_tracking(key)
                    last_track = time.time()
                set_sync(state="idle", message="Up to date", last=int(time.time() * 1000))
        except Exception as e:  # keep the loop alive; surface the error in the UI
            log.exception("sync failed")
            set_sync(state="error", message=f"{type(e).__name__}: {e}"[:300])
        _wake.wait(POLL_SECONDS)
        _wake.clear()


@asynccontextmanager
async def lifespan(_app):
    global _loop
    _loop = asyncio.get_running_loop()
    if db.get("parser") != PARSER_VERSION:
        await asyncio.to_thread(reparse_all)
    await asyncio.to_thread(rebuild)
    threading.Thread(target=sync_loop, daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static/index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/shipments")
def shipments():
    return {"version": state["version"], "shipments": state["shipments"], "sync": state["sync"]}


@app.get("/api/stream")
async def stream():
    q: asyncio.Queue = asyncio.Queue()
    _listeners.add(q)

    async def gen():
        try:
            yield f"event: hello\ndata: {state['version']}\n\n"
            while True:
                try:
                    kind = await asyncio.wait_for(q.get(), 25)
                    yield f"event: {kind}\ndata: {json.dumps(state['sync'] if kind == 'sync' else state['version'])}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # keeps Cloudflare from closing an idle stream
        finally:
            _listeners.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _redirect_uri(request: Request) -> str:
    base = os.environ.get("PUBLIC_URL") or str(request.base_url)
    return base.rstrip("/") + "/auth/callback"


@app.get("/api/status")
def status(request: Request):
    return {"has_client": gmail.CLIENT_FILE.exists(), "connected": gmail.connected(),
            "redirect_uri": _redirect_uri(request), "track17": bool(db.get("track17_key")),
            "backfill_years": db.get("backfill_years", 3), "sync": state["sync"],
            "emails": db.q("SELECT COUNT(*), COUNT(facts) FROM messages")[0][:]}


@app.post("/api/client-secret")
def client_secret(body: dict = Body(...)):
    if not ({"web", "installed"} & body.keys()):
        raise HTTPException(400, "That isn't an OAuth client JSON (expected a 'web' or 'installed' section).")
    gmail.CLIENT_FILE.write_text(json.dumps(body))
    return {"ok": True}


@app.get("/auth/start")
def auth_start(request: Request):
    if not gmail.CLIENT_FILE.exists():
        raise HTTPException(400, "Upload the OAuth client JSON first.")
    return RedirectResponse(gmail.auth_url(_redirect_uri(request)))


def _connected() -> None:
    db.put("backfilled", False)
    _wake.set()


@app.get("/auth/callback")
def auth_callback(request: Request):
    try:
        gmail.finish(str(request.url))
    except Exception as e:
        log.exception("oauth callback failed")
        return HTMLResponse(f"<p>Gmail connection failed: {e}</p><p><a href='/'>Back</a></p>", 400)
    _connected()
    return RedirectResponse("/")


@app.post("/api/auth/paste")
def auth_paste(body: dict = Body(...)):
    try:
        gmail.finish(body.get("url", "").strip())
    except Exception as e:
        raise HTTPException(400, f"Couldn't finish sign-in: {e}")
    _connected()
    return {"ok": True}


@app.post("/api/settings")
def settings(body: dict = Body(...)):
    if "track17_key" in body:
        db.put("track17_key", body["track17_key"].strip() or None)
    if "backfill_years" in body:
        years = int(body["backfill_years"])
        if not 1 <= years <= 20:
            raise HTTPException(400, "Years must be between 1 and 20.")
        db.put("backfill_years", years)
    _wake.set()
    return {"ok": True}


def _shipment(sid: str) -> dict:
    s = next((s for s in state["shipments"] if s["id"] == sid), None)
    if not s:
        raise HTTPException(404, "That parcel isn't there any more. Reload and try again.")
    return s


@app.post("/api/shipments/{sid}/hidden")
def hide(sid: str, body: dict = Body(...)):
    """{"hidden": true} takes a parcel off every list; false brings it back."""
    ids = {e["id"] for e in _shipment(sid)["events"] if e.get("id")}
    kept = [i for i in db.get("hidden", []) if i not in ids]
    db.put("hidden", kept + ([sid] if body.get("hidden") else []))
    rebuild()
    return {"ok": True}


@app.post("/api/shipments/{sid}/delivered")
def mark_delivered(sid: str, body: dict = Body(...)):
    """{"delivered": true} for a parcel that came without a delivered email; false undoes it."""
    ids = {e["id"] for e in _shipment(sid)["events"] if e.get("id")}
    kept = {k: v for k, v in db.get("delivered", {}).items() if k not in ids}
    db.put("delivered", kept | ({sid: int(time.time() * 1000)} if body.get("delivered") else {}))
    rebuild()
    return {"ok": True}


@app.post("/api/shipments/{sid}/name")
def rename(sid: str, body: dict = Body(...)):
    """{"title": "..."} renames a parcel; an empty or missing title puts its own name back."""
    s = _shipment(sid)
    title = str(body.get("title") or "").strip()
    if len(title) > 120:
        raise HTTPException(400, "Keep the name under 120 characters.")
    ids = {e["id"] for e in s["events"] if e.get("id")}
    names = {k: v for k, v in db.get("names", {}).items() if k not in ids}
    db.put("names", names | ({sid: title} if title and title != s.get("original_title", s["title"]) else {}))
    rebuild()
    return {"ok": True}


@app.post("/api/backfill")
def backfill():
    db.put("backfilled", False)
    _wake.set()
    return {"ok": True}


@app.post("/api/rebuild")
def rebuild_now():
    reparse_all()
    rebuild()
    return {"ok": True}

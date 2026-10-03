import json
import os
import sqlite3
import threading
from pathlib import Path

DATA = Path(os.environ.get("PARCELWATCH_DATA") or Path(__file__).resolve().parent.parent / "data")
DATA.mkdir(exist_ok=True)
_db = sqlite3.connect(DATA / "parcelwatch.db", check_same_thread=False)
_db.row_factory = sqlite3.Row
_lock = threading.Lock()  # ponytail: one lock for one user; fine for a single sync thread + web reads
_db.executescript("""
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, thread_id TEXT, ts INTEGER, sender TEXT, subject TEXT,
  snippet TEXT, text TEXT, facts TEXT  -- facts NULL = not a delivery email
);
CREATE TABLE IF NOT EXISTS api_events (
  tracking TEXT, ts INTEGER, stage TEXT, description TEXT, location TEXT,
  PRIMARY KEY (tracking, ts, description)
);
CREATE TABLE IF NOT EXISTS api_meta (tracking TEXT PRIMARY KEY, eta TEXT, polled INTEGER);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
""")


def q(sql: str, args=()) -> list[sqlite3.Row]:
    with _lock:
        return _db.execute(sql, args).fetchall()


def run(sql: str, rows=((),)) -> None:
    with _lock, _db:
        _db.executemany(sql, rows)


def get(key: str, default=None):
    r = q("SELECT value FROM kv WHERE key=?", (key,))
    return json.loads(r[0][0]) if r else default


def put(key: str, value) -> None:
    run("INSERT OR REPLACE INTO kv VALUES (?,?)", [(key, json.dumps(value))])


def known_ids() -> set[str]:
    return {r[0] for r in q("SELECT id FROM messages")}


def save_message(m: dict, facts: dict | None) -> None:
    run("INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?,?)",
        [(m["id"], m["thread_id"], m["ts"], m["sender"], m["subject"], m["snippet"], m["text"],
          json.dumps(facts) if facts else None)])


def all_messages() -> list[dict]:
    return [dict(r) for r in q("SELECT * FROM messages")]


def api_data() -> dict:
    out = {r["tracking"]: {"eta": r["eta"], "events": []} for r in q("SELECT * FROM api_meta")}
    for r in q("SELECT * FROM api_events ORDER BY ts"):
        out.setdefault(r["tracking"], {"eta": None, "events": []})["events"].append(dict(r))
    return out

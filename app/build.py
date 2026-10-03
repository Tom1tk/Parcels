"""Group parsed emails into shipments, learn per-carrier timelines, assemble the view model.

Everything here is a pure function of the stored facts, so the whole set is
rebuilt on each change. ponytail: O(messages); fine for tens of thousands.
"""
import html
import re
import time
from collections import Counter, defaultdict
from datetime import datetime

from .parse import CARRIERS, EXCEPTIONS, GENERIC_TRACK_URL, STAGES, base_domain, domain_of

TEMPLATE_MIN_SHARE = 0.2   # a stage seen in >=20% of a carrier's shipments becomes a notch
LEARN_MIN_SHIPMENTS = 3    # an unclassified subject recurring in >=3 shipments becomes a stage
KEY_MAX_MESSAGES = 25      # an id on more emails than this is boilerplate, not a parcel
STALE_DAYS = 21
GLOBAL_ORDER_LEN = 10      # order numbers this long are unique enough to match across a shop's domains
HINT_WINDOW_DAYS = 30      # a carrier email can join a shop order placed up to this long before it
DAY_MS = 86_400_000


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def _keys(m) -> list[str]:
    f = m["facts"]
    scope = base_domain(domain_of(m["sender"]))
    return [f"t:{n}" for n in f["tracking"]] + [f"o:{'*' if len(o) >= GLOBAL_ORDER_LEN else scope}:{o}" for o in f["orders"]]


def group(messages: list[dict]) -> list[list[dict]]:
    """messages: dicts with id, thread_id, ts, sender, facts. Returns groups, each sorted by ts."""
    msgs = sorted(messages, key=lambda m: m["ts"])
    counts = Counter(k for m in msgs for k in set(_keys(m)))
    uf, keys_of, thread_last = _UF(), {}, {}
    for m in msgs:
        keys = [k for k in _keys(m) if counts[k] <= KEY_MAX_MESSAGES]
        if not keys:  # no ids: ride along with the latest identified email in the thread
            if m["facts"]["weak"] and m["thread_id"] not in thread_last:
                continue
            keys = [thread_last.get(m["thread_id"], f"h:{m['thread_id']}")]
        else:
            thread_last[m["thread_id"]] = keys[0]
        for k in keys[1:]:
            uf.union(k, keys[0])
        keys_of[m["id"]] = keys[0]
    groups = defaultdict(list)
    for m in msgs:
        if m["id"] in keys_of:
            groups[uf.find(keys_of[m["id"]])].append(m)
    return _attach_hinted(list(groups.values()))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _attach_hinted(groups: list[list[dict]]) -> list[list[dict]]:
    """A carrier-only group ("your parcel from Papercut Cards") joins the latest matching shop order before it."""
    shops = [(g, _slug(name), _slug(dom)) for g in groups for name, dom in [_merchant(g)] if name]
    out = []
    for g in groups:
        hint = _slug(next((m["facts"]["hint"] or "" for m in g if m["facts"]["hint"]), ""))
        if len(hint) < 3 or _merchant(g)[0]:
            out.append(g)
            continue
        start = g[0]["ts"]
        match = [sg for sg, name, dom in shops
                 if (hint in name or name in hint or hint in dom)
                 and start - HINT_WINDOW_DAYS * DAY_MS <= sg[0]["ts"] <= start]
        if match:
            target = max(match, key=lambda sg: sg[0]["ts"])
            target.extend(g)
            target.sort(key=lambda m: m["ts"])
        else:
            out.append(g)
    return out


def _norm_subject(s: str) -> str:
    s = re.sub(r"[\"“][^\"”]*[\"”]", "", s.lower())
    s = re.sub(r"\bfrom\s+[\w&'. -]+$", "from …", s)
    s = re.sub(r"[a-z]*\d[\w-]*", "#", s)
    s = re.sub(r"[^\w#…' ]+", " ", s).strip()
    return re.sub(r"^(re|fwd?) ", "", s)[:60]


def _groups_of(carrier: str | None, merchant_dom: str) -> list[str]:
    return [g for g in (carrier and f"c:{carrier}", merchant_dom and f"m:{merchant_dom}") if g]


def _events(msgs: list[dict], api: dict, tracking: list[str]) -> list[dict]:
    ev = [{
        "ts": m["ts"], "stage": m["facts"]["stage"], "source": "email", "id": m["id"],
        "subject": m["subject"], "snippet": html.unescape(m["snippet"] or ""), "sender": m["sender"],
        "norm": _norm_subject(m["subject"]),
    } for m in msgs]
    for n in tracking:
        for e in api.get(n, {}).get("events", []):
            ev.append({"ts": e["ts"], "stage": e["stage"], "source": "carrier", "subject": e["description"],
                       "location": e.get("location"), "tracking": n, "norm": ""})
    return sorted(ev, key=lambda e: e["ts"])


def _carrier(msgs, tracking: dict) -> str | None:
    c = Counter(m["facts"]["carrier"] for m in msgs if m["facts"]["carrier"])
    from_tracking = [v for v in tracking.values() if v]
    return from_tracking[0] if from_tracking else (c.most_common(1)[0][0] if c else None)


def _merchant(msgs) -> tuple[str, str]:
    carrier_doms = {d for _, ds, _, _ in CARRIERS.values() for d in ds} - {d for d in CARRIERS["amazon"][1]}
    for m in msgs:
        dom = domain_of(m["sender"])
        if not any(dom.endswith(d) for d in carrier_doms):
            name = re.sub(r"\s*<.*", "", m["sender"]).strip(' "') or dom
            return name, base_domain(dom)
    return "", ""


def _rank(stage: str, learned: dict) -> float:
    return STAGES[stage][0] if stage in STAGES else learned[stage]["rank"]


def build(messages: list[dict], api: dict | None = None, now_ms: int | None = None) -> list[dict]:
    api = api or {}
    now_ms = now_ms or int(time.time() * 1000)
    raw = []
    for msgs in group(messages):
        tracking = {}
        for m in msgs:
            tracking.update({k: v for k, v in m["facts"]["tracking"].items() if v or k not in tracking})
        carrier = _carrier(msgs, tracking)
        merchant, merchant_dom = _merchant(msgs)
        merchant = merchant or next((m["facts"]["hint"] for m in msgs if m["facts"]["hint"]), "")
        raw.append({"msgs": msgs, "tracking": tracking, "carrier": carrier, "merchant": merchant,
                    "merchant_dom": merchant_dom, "events": _events(msgs, api, list(tracking))})

    learned = _learn_custom(raw)
    for r in raw:
        for e in r["events"]:
            if e["stage"] is None and (key := _custom_key(r, e["norm"])) in learned:
                e["stage"] = key
    templates = _learn_templates(raw)
    return sorted((_assemble(r, templates, learned, api, now_ms) for r in raw), key=_sort_key)


def _sort_key(s: dict) -> tuple:
    """Parcels still on their way with an estimate first, soonest on top; the rest by order date, newest first."""
    if s["eta"] and s["status"] in ("active", "problem"):
        return (0, s["eta"], -s["started"])
    return (1, "", -s["started"])


def _custom_key(r, norm) -> str:
    """A shop's own wording (Vinted's emails sent via Evri) belongs to that shop, not to every parcel the carrier brings."""
    return f"x:{r['merchant_dom'] or _slug(r['merchant']) or r['carrier']}:{norm}"


def _learn_custom(raw) -> dict:
    """Recurring unclassified subjects become carrier-specific stages, placed between their neighbours."""
    seen = defaultdict(list)
    for r in raw:
        known = [(e["ts"], STAGES[e["stage"]][0]) for e in r["events"] if e["stage"] in STAGES]
        for e in r["events"]:
            if e["stage"] is None and e["norm"]:
                before = max((rk for ts, rk in known if ts <= e["ts"]), default=0)
                if before >= STAGES["delivered"][0]:  # "how did your courier do?" follows the journey, isn't part of it
                    continue
                after = min((rk for ts, rk in known if ts > e["ts"] and rk > before), default=STAGES["delivered"][0])
                seen[_custom_key(r, e["norm"])].append((id(r), (before + after) / 2))
    learned = {}
    for key, hits in seen.items():
        if len({h[0] for h in hits}) >= LEARN_MIN_SHIPMENTS:
            label = re.sub(r"^your ([\w]+ ){0,2}?(item|parcel|order|package|delivery|shipment)( has been| is| has| was)? ", "",
                           key.split(":", 2)[2].replace("#", "").strip()).capitalize()
            learned[key] = {"rank": round(sum(h[1] for h in hits) / len(hits), 2), "label": label}
    return learned


def _learn_templates(raw) -> dict[str, list[str]]:
    counts, totals = defaultdict(Counter), Counter()
    for r in raw:
        stages = {e["stage"] for e in r["events"] if e["stage"] and e["stage"] not in EXCEPTIONS}
        if len(stages) < 2:
            continue
        for g in _groups_of(r["carrier"], r["merchant_dom"]):
            totals[g] += 1
            counts[g].update(s for s in stages if g[0] == "m" or s in STAGES or s.startswith(f"x:{r['carrier']}:"))
    return {g: [s for s, c in counts[g].items() if c / totals[g] >= TEMPLATE_MIN_SHARE] for g in totals}


def _assemble(r, templates, learned, api, now_ms) -> dict:
    events, msgs = r["events"], r["msgs"]
    hit = {}
    for e in events:
        if e["stage"] and e["stage"] not in EXCEPTIONS:
            hit.setdefault(e["stage"], e["ts"])
    current = max(hit, key=lambda s: _rank(s, learned)) if hit else None
    cur_rank = _rank(current, learned) if current else -1
    delivered = "delivered" in hit

    template = {"ordered", "delivered"} | set(hit)
    if not delivered:  # finished journeys show only what happened
        for g in _groups_of(r["carrier"], r["merchant_dom"]):
            template |= set(templates.get(g, []))
    notches = []
    for s in sorted(template, key=lambda s: _rank(s, learned)):
        rank = _rank(s, learned)
        label = STAGES[s][1] if s in STAGES else learned[s]["label"]
        notches.append({"key": s, "label": label, "ts": hit.get(s), "reached": rank <= cur_rank,
                        "current": s == current})
    keys = [n["key"] for n in notches]

    last_normal_ts = max(hit.values(), default=0)
    exceptions = []
    for e in events:
        if e["stage"] in EXCEPTIONS:
            label, terminal = EXCEPTIONS[e["stage"]]
            before = max((s for s, ts in hit.items() if ts <= e["ts"]), key=lambda s: _rank(s, learned), default=None)
            exceptions.append({"key": e["stage"], "label": label, "ts": e["ts"], "terminal": terminal,
                               "after": keys.index(before) if before in keys else -1,
                               "active": e["ts"] >= last_normal_ts and not delivered})
    active_exc = [x for x in exceptions if x["active"]]

    updated = events[-1]["ts"]
    if delivered:
        status = "delivered"
    elif any(x["terminal"] for x in active_exc):
        status = active_exc[-1]["key"]
    elif now_ms - updated > STALE_DAYS * DAY_MS:
        status = "stale"
    else:
        status = "problem" if active_exc else "active"

    tracking = list(r["tracking"])
    eta = next((m["facts"]["eta"] for m in reversed(msgs) if m["facts"]["eta"]), None)
    api_eta = next((api[n]["eta"] for n in tracking if api.get(n, {}).get("eta")), None)
    if current == "out_for_delivery":  # it's coming the day it went out; an older email's ETA would contradict that
        eta = _day(max(e["ts"] for e in events if e["stage"] == current))
    elif current in ("delivered", "ready_for_collection"):
        eta = None
    else:
        eta = api_eta or eta
    # newest slot for that day wins: Evri's "delivery time has been updated" replaces the morning's estimate
    window = next(({k: w[k] for k in ("from", "to")} for m in reversed(msgs)
                   if (w := m["facts"].get("window")) and w["day"] == eta), None) if eta else None
    links = []
    for n in tracking:
        tmpl = CARRIERS.get(r["tracking"][n] or r["carrier"] or "", (None,) * 4)[3] or GENERIC_TRACK_URL
        links.append({"number": n, "url": tmpl.format(n=n)})

    items = next((m["facts"]["items"] for m in msgs if m["facts"]["items"]), [])
    title = items[0] + (f" + {len(items) - 1} more" if len(items) > 1 else "") if items \
        else next((m["facts"]["title"] for m in msgs if m["facts"]["title"]), None)
    merchant = r["merchant"] or (CARRIERS[r["carrier"]][0] if r["carrier"] else "Unknown sender")
    return {
        "id": msgs[0]["id"],
        "title": title or merchant,
        "items": items,
        "merchant": merchant,
        "carrier": CARRIERS[r["carrier"]][0] if r["carrier"] else None,
        "tracking": links,
        "orders": sorted({o for m in msgs for o in m["facts"]["orders"]}),
        "eta": eta,
        "window": window,
        "status": status,
        "current": notches[keys.index(current)]["label"] if current else "Update received",
        "notches": notches,
        "exceptions": exceptions,
        "started": events[0]["ts"],
        "updated": updated,
        "events": [{k: v for k, v in e.items() if k != "norm"} | {"label": _label(e["stage"], learned)}
                   for e in reversed(events)],
    }


def _day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).date().isoformat()


def _label(stage, learned) -> str:
    if stage in STAGES:
        return STAGES[stage][1]
    if stage in EXCEPTIONS:
        return EXCEPTIONS[stage][0]
    return learned[stage]["label"] if stage in learned else "Update"


def apply_owner(shipments: list[dict], names: dict[str, str], hidden: set[str]) -> list[dict]:
    """Owner renames and hides, keyed by email id: any of a parcel's emails finds it, so they survive the group gaining an earlier email."""
    out = []
    for s in shipments:
        ids = [e["id"] for e in s["events"] if e.get("id")]
        name = next((names[i] for i in ids if i in names), None)
        extra = ({"title": name, "original_title": s["title"]} if name else {}) | ({"hidden": True} if hidden.intersection(ids) else {})
        out.append(s | extra if extra else s)
    return out

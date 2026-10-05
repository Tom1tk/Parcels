"""Turn one email (subject, sender, text) into delivery facts: carrier, ids, stage, ETA."""
import html
import re
from datetime import date, datetime, timedelta

# Canonical stages, in journey order. "delivered" is always last.
STAGES = {
    "ordered": (0, "Ordered"),
    "processing": (1, "Preparing"),
    "label_created": (2, "Label created"),
    "dispatched": (3, "Dispatched"),
    "with_carrier": (4, "With carrier"),
    "in_transit": (5, "In transit"),
    "customs": (6, "Customs"),
    "out_for_delivery": (8, "Out for delivery"),
    "ready_for_collection": (9, "Ready to collect"),
    "delivered": (10, "Delivered"),
}
# Irregularities. Terminal ones end the journey unless a later normal stage arrives.
EXCEPTIONS = {
    "delayed": ("Delayed", False),
    "failed_attempt": ("Delivery attempted", False),
    "action_required": ("Action needed", False),
    "damaged": ("Damaged", False),
    "lost": ("Lost", True),
    "returned": ("Returned", True),
    "cancelled": ("Cancelled", True),
}

# Order matters: exceptions first, then most-advanced stage first, so
# "delivered" wins over "dispatched" in "your dispatched parcel was delivered".
_RULES = [
    ("cancelled", r"\b(order|item|parcel|delivery|shipment)s?( [#\w-]*\d[\w-]*)? (has been |was |is )?cancell?ed|\bcancell?ation (confirm|of)|^cancell?ed\b"),
    ("returned", r"returned to (the )?sender|being returned|return(ing)? to sender|\breturn (received|processed|has been received)|we('ve| have) received your return|refund (issued|processed)|has been refunded"),
    ("lost", r"\blost in transit|(parcel|package|item|order) (is |has been |was )?(lost|missing)|unable to locate|can(no|')t find your"),
    ("damaged", r"\bdamaged\b"),
    ("failed_attempt", r"missed you|couldn'?t deliver|could not deliver|unable to deliver|(delivery )?attempt(ed)? (to deliver|delivery|was made)|we tried to deliver|delivery (was )?unsuccessful|not able to deliver"),
    ("action_required", r"(fee|charge|duty|vat|postage) (to pay|is due|owed|outstanding)|pay (the |a )?(fee|charge|duty|customs)|action required|(confirm|update) (your )?address|more postage"),
    ("delayed", r"\bdelay(ed)?\b|running late|taking longer|later than expected|is late\b"),
    ("delivered", r"^delivered\b|has been delivered|was delivered|been successfully delivered|we('ve| have) delivered|(?<!be )(?<!not )delivered (to|today|at|in|safely)|left (it |your \w+ )?(in|at|with) (your |a |the )?(safe ?place|porch|neighbou?r|reception|mailbox|letterbox|front door|parcel box)|has arrived\b(?! at)|\bcollected (by you|from)|you('ve| have) collected|successfully collected|now in your hands"),
    ("ready_for_collection", r"(?<!when it's )(?<!when it is )(?<!when your parcel is )(?<!when your item is )ready (for|to) (collect|pick ?up)|ready for collection|available (for|to) (collect|pick ?up)|waiting (for you )?at (the |your |a )?(parcel ?shop|locker|post office|pickup|collection)|at your (chosen )?(locker|pick ?up point|parcel ?shop)"),
    ("out_for_delivery", r"out for delivery|^arriving today|is (coming|arriving) today|will be (delivered|with you) today|(delivery|arrives?) today|on (its|the) way to you today|with (our|your|the) driver|on the van|(your )?\d+[- ]hour (delivery )?window|driver is (nearby|on (their|the) way)"),
    ("customs", r"\bcustoms\b|\bimport (processing|clearance)"),
    ("in_transit", r"in transit|on the move|(arrived|reached|departed|left) (at |the |our )?(\w+ )?(hub|depot|facility|sorting|delivery office|distribution)|at (our|the|a) (\w+ )?(hub|depot|sorting)|being sorted|(arriving|delivery|expected) tomorrow"),
    ("with_carrier", r"(we('ve| have)|has been) (got|received|collected) (your|the) (parcel|package|item)|picked up by|collected by (the )?(courier|carrier|driver)|courier has (received|collected) your|handed (over )?to (the )?(courier|carrier|royal mail|evri|dpd|ups|dhl|yodel|parcelforce)|accepted at|in our network"),
    ("dispatched", r"\b(dispatched|despatched|shipped)\b|has been sent|is on (its|the) way|on its way|has left (our|the) (warehouse|store)|we('ve| have) sent"),
    ("label_created", r"label (created|printed)|shipping label|(we('ve| have)|has been) (been )?told|expecting your ([\w&.' ]{1,30} )?(parcel|package|item|order)|parcel loading|had news from|information received|pre-advice|delivery details (received|from)|will be (sent|handed) (to us|over)"),
    ("processing", r"\bpreparing\b|being prepared|\bpacked\b|being packed|(order|item)s? (is |are )?(being )?processed|getting your order ready|ready to ship|order (is )?in progress|shipment plan"),
    ("ordered", r"order (confirm|received|placed|acknowledg)|thank(s| you) for (your )?(order|purchase|shopping)|we('ve| have) (got|received) your order|your order (with|from|at|#|no|number)|purchase confirm|receipt for your order|^ordered\b|\bnew order\b"),
]
_RULES = [(k, re.compile(p, re.I | re.M)) for k, p in _RULES]

CARRIERS = {
    # key: (display name, sender domains, body mention regex, tracking url template)
    "royal_mail": ("Royal Mail", ("royalmail.com", "royalmail.co.uk"), r"royal ?mail", "https://www.royalmail.com/track-your-item#/tracking-results/{n}"),
    "parcelforce": ("Parcelforce", ("parcelforce.com", "parcelforce.co.uk"), r"parcel ?force", "https://www.parcelforce.com/track-trace?trackNumber={n}"),
    "evri": ("Evri", ("evri.com", "myhermes.co.uk", "hermes-europe.co.uk"), r"\bevri\b|\bhermes\b", "https://www.evri.com/track/parcel/{n}/details"),
    "dpd": ("DPD", ("dpd.co.uk", "dpdlocal.co.uk", "dpd.com"), r"\bdpd\b", "https://track.dpd.co.uk/search?reference={n}"),
    "yodel": ("Yodel", ("yodel.co.uk",), r"\byodel\b", "https://www.yodel.co.uk/tracking/{n}"),
    "ups": ("UPS", ("ups.com",), r"\bups\b", "https://www.ups.com/track?tracknum={n}"),
    "dhl": ("DHL", ("dhl.com", "dhl.co.uk", "dhl.de"), r"\bdhl\b", "https://www.dhl.com/gb-en/home/tracking/tracking-parcel.html?submit=1&tracking-id={n}"),
    "fedex": ("FedEx", ("fedex.com",), r"\bfed ?ex\b", "https://www.fedex.com/fedextrack/?trknbr={n}"),
    "inpost": ("InPost", ("inpost.co.uk", "inpost.pl"), r"\binpost\b", None),
    "amazon": ("Amazon", ("amazon.co.uk", "amazon.com", "amazon.de", "amazon.fr"), r"amazon logistics", None),
}
# Senders whose "orders" are payments, food or digital goods, never parcels.
NON_PARCEL_DOMAINS = {
    "paypal.com", "paypal.co.uk", "deliveroo.co.uk", "deliveroo.com", "just-eat.co.uk", "uber.com",
    "battle.net", "blizzard.com", "steampowered.com", "nintendo.com", "nintendo.co.uk", "gameboost.com", "greeneking.co.uk",
}
GENERIC_TRACK_URL = "https://t.17track.net/en#nums={n}"

# (carrier, regex). Bare-digit formats are only trusted near the word "tracking".
_TRACKING = [
    ("ups", re.compile(r"\b1Z[0-9A-Z]{16}\b")),
    ("amazon", re.compile(r"\bTBA\d{12}\b")),
    ("yodel", re.compile(r"\bJD\d{16,18}\b")),
    ("evri", re.compile(r"\b[HT][0-9A-Z]{15}\b")),
    ("dpd", re.compile(r"\b15\d\d ?\d{4} ?\d{3} ?\d{3}\b")),  # DPD UK prints it as "1550 2188 890 021"
    (None, re.compile(r"\bJJD\d{16,18}\b")),  # InPost/DHL/Yodel share this prefix
]
_S10 = re.compile(r"\b([A-Z]{2})(\d{8})(\d)([A-Z]{2})\b")  # UPU format used by Royal Mail & most posts
_CONTEXT_TRACKING = re.compile(
    r"(?:tracking|track(?:ing)? ?(?:no|number|id|code|ref)|parcel(?: number| no| id)?|consignment(?: number)?|shipment (?:number|id))\.?\s*(?:number|no\.?|id|code|ref(?:erence)?)?\s*(?:is|:|#)?\s*([A-Z0-9]{8,30})\b",
    re.I,
)
_ORDER = re.compile(
    r"\b(\d{3}-\d{7}-\d{7})\b|order\s*(?:number|no\.?|id|ref(?:erence)?|#)\s*[:#]?\s*#?([A-Z0-9][A-Z0-9-]{3,24})\b|order\s*#\s*([A-Z0-9-]{4,25})\b|#(\d{5,12})\b",
    re.I,
)
_ORDER_STOP = {"DETAILS", "SUMMARY", "NUMBER", "STATUS", "TOTAL", "DATE", "CONFIRMATION", "HISTORY"}


def _s10_valid(m) -> bool:
    weights = (8, 6, 4, 2, 3, 5, 9, 7)
    check = 11 - sum(int(d) * w for d, w in zip(m.group(2), weights)) % 11
    check = {10: 0, 11: 5}.get(check, check)
    return check == int(m.group(3))


def domain_of(sender: str) -> str:
    m = re.search(r"@([\w.-]+)", sender or "")
    return m.group(1).lower() if m else ""


def base_domain(domain: str) -> str:
    parts = domain.split(".")
    keep = 3 if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "ac") and len(parts[-1]) == 2 else 2
    return ".".join(parts[-keep:])


def find_carrier(sender: str, text: str, tracking: dict) -> str | None:
    dom = domain_of(sender)
    for key, (_, domains, _, _) in CARRIERS.items():
        if any(dom == d or dom.endswith("." + d) for d in domains):
            return key
    for carrier in tracking.values():
        if carrier:
            return carrier
    # ponytail: first mention wins; a footer listing every courier can mislead this.
    hits = [(m.start(), key) for key, (_, _, rx, _) in CARRIERS.items() if (m := re.search(rx, text, re.I))]
    return min(hits)[1] if hits else None


def find_tracking(text: str) -> dict[str, str | None]:
    """tracking number -> carrier key (or None if unknown)."""
    found: dict[str, str | None] = {}
    for m in _S10.finditer(text):
        if _s10_valid(m):
            found[m.group(0)] = "royal_mail" if m.group(4) == "GB" else None
    for carrier, rx in _TRACKING:
        for m in rx.finditer(text):
            found.setdefault(m.group(0).replace(" ", ""), carrier)
    for m in _CONTEXT_TRACKING.finditer(text):
        n = m.group(1).upper()
        if re.search(r"\d", n) and n not in found and not n.isalpha():
            found[n] = None
    return found


def find_orders(text: str) -> set[str]:
    out = set()
    for m in _ORDER.finditer(text):
        n = next(g for g in m.groups() if g).upper().strip("-")
        if re.search(r"\d", n) and n not in _ORDER_STOP:
            out.add(n)
    return out


def classify(subject: str, text: str) -> str | None:
    """Stage from the subject; fall back to the opening of the body.
    The body's tail is ignored: emails often render the whole progress bar there."""
    for source in (subject, text[:600]):
        for key, rx in _RULES:
            if rx.search(source):
                return key
    return None


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_ETA_CONTEXT = re.compile(
    r"(?:arriving|arrives|expected|estimated delivery|delivery date|due|will be delivered|deliver(?:y|ed)? (?:on|by)|get it|coming)\s*(?:delivery)?\s*(?:date)?\s*(?:on|by|:)?\s*(.{0,40})",
    re.I,
)


def _parse_day(s: str, sent: date) -> date | None:
    s = s.lower()
    if s.startswith("today"):
        return sent
    if s.startswith("tomorrow"):
        return sent + timedelta(days=1)
    m = re.match(r"(?:\w+day,?\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]{3})[a-z]*\.?,?\s*(\d{4})?", s) or None
    if m:
        day, mon, year = int(m.group(1)), m.group(2), m.group(3)
    else:
        m = re.match(r"(?:\w+day,?\s+)?([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?", s)
        if m:
            mon, day, year = m.group(1), int(m.group(2)), m.group(3)
    if m and mon in _MONTHS:
        try:
            d = date(int(year) if year else sent.year, _MONTHS.index(mon) + 1, day)
        except ValueError:
            return None
        return d.replace(year=d.year + 1) if not year and d < sent - timedelta(days=60) else d
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)  # UK d/m/y
    if m:
        try:
            y = int(m.group(3))
            return date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    m = re.match(r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", s)
    if m:
        ahead = (_WEEKDAYS.index(m.group(1)) - sent.weekday()) % 7
        return sent + timedelta(days=ahead)
    return None


def find_eta(subject: str, text: str, sent: datetime) -> str | None:
    for source in (subject, text[:1500]):
        for m in _ETA_CONTEXT.finditer(source):
            d = _parse_day(m.group(1).strip(), sent.date())
            if d and sent.date() - timedelta(days=1) <= d <= sent.date() + timedelta(days=90):
                return d.isoformat()
    return None


_TIME = r"(\d{1,2})(?:[:.](\d{2}))?\s*([ap]\.?m\b\.?)?"
_RANGE = re.compile(_TIME + r"\s*(?:-|–|to|and)\s*" + _TIME, re.I)
_BY = re.compile(r"\bby\s*:?\s*" + _TIME, re.I)
_WINDOW_NEAR = re.compile(r"deliver|arriv|courier|today", re.I)
# opening times, helpline hours, collection slots, "your slot is usually sent by noon"
_WINDOW_NOT = re.compile(r"open|hours|customer|collect|call|inquir|usually|support|▪", re.I)
MAX_WINDOW_MIN = 6 * 60  # "between 7am and 9pm" says nothing


def _minutes(h: str, m: str | None, ap: str | None) -> int | None:
    h, m = int(h), int(m or 0)
    if h > 23 or m > 59 or (ap and not 1 <= h <= 12):
        return None
    ap = (ap or "").lower()[:1]
    return ((h % 12) + (12 if ap == "p" else 0) if ap else h) * 60 + m


def _is_time(m: str | None, ap: str | None) -> bool:
    return bool(m or ap)  # a bare "2" is a count of days, not a time


def _hhmm(n: int) -> str:
    return f"{n // 60:02d}:{n % 60:02d}"


def find_window(subject: str, text: str, sent: datetime) -> dict | None:
    """The delivery slot an email gives ("Arriving today 2:30 pm - 6:30 pm", "estimated by: 5:00pm"), for the day it names."""
    for source in (subject, text[:3000]):
        hits = sorted([(m.start(), "range", m) for m in _RANGE.finditer(source)] + [(m.start(), "by", m) for m in _BY.finditer(source)],
                      key=lambda x: x[0])
        for start, kind, m in hits:
            before = source[max(0, start - 60):start]
            if not _WINDOW_NEAR.search(before) or _WINDOW_NOT.search(before):
                continue
            g = m.groups()
            if kind == "by":
                if not _is_time(g[1], g[2]) or (to := _minutes(*g)) is None:
                    continue
                frm = None
            else:
                if not (_is_time(g[1], g[2]) and _is_time(g[4], g[5])):
                    continue
                to = _minutes(*g[3:])
                frm = _minutes(g[0], g[1], g[2] or g[5])  # "5:30 - 7:30pm" is both pm...
                if frm is not None and to is not None and frm > to and not g[2]:
                    frm = _minutes(g[0], g[1], "am")  # ...but "11:30 - 1:30pm" starts in the morning
                if frm is None or to is None or not 0 < to - frm <= MAX_WINDOW_MIN:
                    continue
            return {"day": find_eta(subject, text, sent) or sent.date().isoformat(),
                    "from": frm if frm is None else _hhmm(frm), "to": _hhmm(to)}
    return None


_QUOTED = (re.compile(r"[\"“]([^\"“”]{3,90})[\"“”]"), re.compile(r"‘(.{3,90})’"))
_SUBJECT_ITEM = re.compile(
    r"^\W*(?:order(?:ed| confirmed| delivered)?|shipped|dispatched|delivered|out for delivery|arriving \w+)\s*:\s*(.{3,})", re.I)
# (anchor before the first item, end of its name). Covers Amazon, Shopify, WooCommerce, M&S, IKEA, Thortful, Magento.
_ITEMS = [(re.compile(a + r"\s*(.{3,200}?)\s*" + b, re.I), multi) for a, b, multi in (
    (r"\*", r"Quantity: \d", True),
    (r"Items in this shipment -*", r"× \d", False),
    (r"Product Quantity Price", r"\d+ £", False),
    (r"\bItem(?:s|\(s\))? in (?:our [\w ]{2,20} warehouse|shipment):", r"Qty:", False),
    (r"Order details(?! are)", r"(?:Product Code|Colour|Size|Qty):", False),
    (r"Items purchased Quantity Subtotal (?:\[[^\]]*\])?", r"£", False),
    (r"order details below:", r"\d+ £", False),
    (r"\bItems Qty(?: Subtotal)?", r"(?:- [A-Z0-9-]{6,} )?\d+ (?:Excl|£|Thank)", False),
)]
MAX_ITEMS = 5


def item_title(subject: str) -> str | None:
    for rx in (*_QUOTED, _SUBJECT_ITEM):
        if m := rx.search(subject):
            return m.group(1).rstrip(".… ")
    return None


def _short(name: str) -> str:
    """Marketplace titles are keyword soup; keep the part before the first separator."""
    if len(name) > 50:
        name = re.split(r" [–|-] |, ", name)[0]
    return name if len(name) <= 80 else name[:79].rstrip() + "…"


def find_items(text: str) -> list[str]:
    for rx, multi in _ITEMS:
        found = [_short(m.group(1)) for m in rx.finditer(text)] if multi else [_short(m.group(1)) for m in [rx.search(text)] if m]
        if found:
            return list(dict.fromkeys(found))[:MAX_ITEMS]
    return []


_NOT_SHOPS = {"New", "Next", "Latest", "Recent", "Online", "Upcoming", "Parcel", "Order", "Delivery", "Return"}


def merchant_hint(subject: str, sender: str, text: str, carrier: str | None) -> str | None:
    """Which shop a carrier email is about: DPD puts it in the sender name, others say "parcel from X"."""
    if not carrier or not any(domain_of(sender).endswith(d) for d in CARRIERS[carrier][1]):
        return None
    name = re.sub(r"\s*<.*", "", sender).strip(' "')
    if m := re.search(r"\bvia (.+)", name):
        return m.group(1)
    if name and "@" not in name and not re.search(CARRIERS[carrier][2], name, re.I):
        return name
    m = re.search(r"\b[Yy]our ([A-Z][\w&'.-]{2,20}) (?:delivery|order|parcel|package)\b", subject)
    if m and m.group(1) not in _NOT_SHOPS and not re.search(CARRIERS[carrier][2], m.group(1), re.I):
        return m.group(1)
    m = re.search(r"(?:parcel|package|order|item) from ([\w&.' -]{2,30}?)(?: is| has| will|[.,!]|$)", f"{subject}. {text[:600]}", re.I)
    return re.sub(r"^\d+\.\s*", "", m.group(1)).strip() if m else None


def clean(text: str) -> str:
    """Undo html leftovers some senders put in their plain-text part: entities, tags, inline CSS."""
    text = html.unescape(text)
    text = re.sub(r"<[^<>]{1,200}>|\{[^{}]*\}|[\u200b-\u200f\u00ad\u034f\ufeff]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_shipping(stage, tracking, orders, carrier, sender) -> bool:
    from_carrier = carrier and carrier != "amazon" and any(
        domain_of(sender).endswith(d) for d in CARRIERS[carrier][1]
    )
    return bool(stage and (tracking or orders or from_carrier)) or bool(from_carrier and tracking)


def parse(subject: str, sender: str, text: str, sent: datetime) -> dict | None:
    """Facts for one email, or None if it isn't about a delivery."""
    if base_domain(domain_of(sender)) in NON_PARCEL_DOMAINS:
        return None
    text = clean(text)
    blob = f"{subject}\n{text}"
    tracking = find_tracking(blob)
    orders = find_orders(blob) - set(tracking)
    stage = classify(subject, text)
    carrier = find_carrier(sender, text, tracking)
    strong = is_shipping(stage, tracking, orders, carrier, sender)
    if not strong and not stage:
        return None
    return {
        "weak": not strong,  # stage only, no ids: kept only if its thread is a known shipment
        "stage": stage,  # None = carrier email we can't classify yet ("update")
        "carrier": carrier,
        "tracking": tracking,
        "orders": sorted(orders),
        "eta": find_eta(subject, text, sent),
        "window": find_window(subject, text, sent),
        "title": item_title(subject),
        "items": find_items(text),
        "hint": merchant_hint(subject, sender, text, carrier),
    }

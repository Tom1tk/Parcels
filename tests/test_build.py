from datetime import datetime

from app.build import build
from app.parse import parse

DAY = 86_400_000
T0 = int(datetime(2026, 9, 1).timestamp() * 1000)
_n = 0


def mail(day, subject, sender, body="", thread=None):
    global _n
    _n += 1
    ts = T0 + int(day * DAY)
    facts = parse(subject, sender, body, datetime.fromtimestamp(ts / 1000))
    assert facts, f"not recognised: {subject}"
    return {"id": f"m{_n}", "thread_id": thread or f"t{_n}", "ts": ts, "sender": sender,
            "subject": subject, "snippet": body[:80], "facts": facts}


AMZ = "Amazon.co.uk <shipment-tracking@amazon.co.uk>"
RM = "Royal Mail <no-reply@royalmail.com>"
SHOP = "Plant Shop <orders@plantshop.co.uk>"


def rm_journey(day, num, order, extra=()):
    return [
        mail(day, f"Order confirmation #{order}", SHOP, f"Order number: {order}"),
        mail(day + 1, "Your order has been dispatched", SHOP, f"Order number: {order}. Royal Mail tracking number {num}"),
        mail(day + 2, "Your item is in transit", RM, f"Tracking number {num}. It has arrived at our hub"),
        *[mail(day + 2.5, s, RM, f"Tracking number {num}") for s in extra],
        mail(day + 3, "Your item has been delivered", RM, f"Tracking number {num}"),
    ]


def test_amazon_chain_groups_by_order():
    order = "205-1234567-7654321"
    ms = [mail(d, f'{s}: "Garden hose 30m"', AMZ, f"Order # {order}")
          for d, s in ((0, "Ordered"), (1, "Shipped"), (2, "Out for delivery"), (2.4, "Delivered"))]
    [s] = build(ms, now_ms=T0 + 3 * DAY)
    assert s["title"] == "Garden hose 30m" and s["status"] == "delivered"
    assert [n["key"] for n in s["notches"]] == ["ordered", "dispatched", "out_for_delivery", "delivered"]
    assert all(n["reached"] for n in s["notches"])


def test_merchant_and_carrier_emails_merge_and_learning_adds_future_notches():
    history = rm_journey(0, "AB123456785GB", "PS1001", extra=["Your item is at the delivery office"]) \
        + rm_journey(10, "AB876543216GB", "PS1002", extra=["Your item is at the delivery office"]) \
        + rm_journey(20, "CD123456785GB", "PS1003", extra=["Your item is at the delivery office"])
    live = [mail(30, "Order confirmation #PS1004", SHOP, "Order number: PS1004"),
            mail(31, "Your order has been dispatched", SHOP, "Order number: PS1004 tracking number EF123456785GB")]
    out = {s["orders"][0]: s for s in build(history + live, now_ms=T0 + 31 * DAY)}
    assert len(out) == 4
    done, cur = out["PS1001"], out["PS1004"]
    assert done["carrier"] == "Royal Mail" and done["merchant"] == "Plant Shop" and done["status"] == "delivered"
    # recurring unclassified subject was learned as its own stage between in-transit and delivered
    assert [n["label"] for n in done["notches"]] == ["Ordered", "Dispatched", "In transit", "At the delivery office", "Delivered"]
    # the live parcel shows learned Royal Mail stages as future notches
    assert [(n["key"], n["reached"]) for n in cur["notches"]][:3] == [("ordered", True), ("dispatched", True), ("in_transit", False)]
    assert cur["notches"][-1]["key"] == "delivered" and cur["status"] == "active"
    assert cur["tracking"][0]["url"].startswith("https://www.royalmail.com/")


def test_unknown_carrier_starts_minimal_and_exceptions():
    shop = "Tiny Co <hello@tinyco.io>"
    ms = [mail(0, "Thanks for your order", shop, "Order number: TC5551"),
          mail(2, "Your parcel is delayed", shop, "Order number: TC5551")]
    [s] = build(ms, now_ms=T0 + 3 * DAY)
    assert [n["key"] for n in s["notches"]] == ["ordered", "delivered"]
    assert s["status"] == "problem" and s["exceptions"][0]["after"] == 0
    ms.append(mail(4, "Your parcel has been lost", shop, "Order number: TC5551"))
    [s] = build(ms, now_ms=T0 + 5 * DAY)
    assert s["status"] == "lost"
    [s] = build(ms[:1], now_ms=T0 + 40 * DAY)
    assert s["status"] == "stale"


def test_thread_followup_without_ids_joins_shipment():
    ms = [mail(0, "Order confirmation", SHOP, "Order number: PS2000", thread="th"),
          mail(1, "Your order has been dispatched", SHOP, "Good news", thread="th")]
    [s] = build(ms, now_ms=T0 + DAY)
    assert s["current"] == "Dispatched"


def test_carrier_email_joins_shop_order_named_in_it():
    shop = "Papercut team <team@papercutcards.com>"
    ms = [mail(0, "Thanks for your order", shop, "Order number: 55512345 Check out your order details below: A5 Card 1 £3.99"),
          mail(1, "Your parcel from Papercut is on its way", RM, "Tracking number XY123456785GB"),
          mail(2, "Your parcel from Papercut has been delivered", RM, "Tracking number XY123456785GB")]
    [s] = build(ms, now_ms=T0 + 3 * DAY)
    assert s["title"] == "A5 Card" and s["merchant"] == "Papercut team" and s["status"] == "delivered"
    assert s["carrier"] == "Royal Mail"


def test_dpd_only_parcel_named_after_shop_and_long_order_spans_domains():
    dpd = [mail(0, "Your Northwind Ltd order will be delivered today", "Northwind Ltd <yourdelivery@dpd.co.uk>",
                "Your parcel: 1550 1112 223 334")]
    [s] = build(dpd, now_ms=T0 + DAY)
    assert s["merchant"] == "Northwind Ltd" and s["tracking"][0]["number"] == "15501112223334"
    uq = [mail(0, "Your order has been packed", "Uniqlo <noreply-uk@uniqlo.eu>", "Order number: 0720001234567890123"),
          mail(1, "Your invoice is here", "UNIQLO <no-reply.eu@ml.store.uniqlo.com>", "Order number: 0720001234567890123 dispatched")]
    assert len(build(uq, now_ms=T0 + 2 * DAY)) == 1


def test_sorted_by_eta_then_newest_order():
    shop = "Tiny Co <hello@tinyco.io>"
    ms = [mail(0, "Thanks for your order", shop, "Order number: TC0001 Arriving 20 September"),
          mail(1, "Thanks for your order", shop, "Order number: TC0002 Arriving 10 September"),
          mail(2, "Thanks for your order", shop, "Order number: TC0003"),
          mail(3, "Thanks for your order", shop, "Order number: TC0004")]
    assert [s["orders"][0] for s in build(ms, now_ms=T0 + 4 * DAY)] == ["TC0002", "TC0001", "TC0004", "TC0003"]


def test_shop_specific_and_after_delivery_subjects_stay_off_other_parcels():
    history = []
    for i, num in enumerate(("H01HYA0012345671", "H01HYA0012345672", "H01HYA0012345673")):
        d = i * 10
        history += [mail(d, "Your parcel is on its way", "Evri <noreply@evri.com>", f"Parcel {num}"),
                    mail(d + 1, "Your Vinted delivery location has been updated", "Evri <noreply@evri.com>", f"Evri parcel {num}"),
                    mail(d + 2, "Your Evri parcel has been delivered", "Evri <noreply@evri.com>", f"Parcel {num}"),
                    mail(d + 3, "How did your courier do?", "Evri <noreply@evri.com>", f"Parcel {num}")]
    order = mail(40, "SportPursuit: New Order # 104455667", "SportPursuit <no-reply@send.sportpursuit.com>",
                  "Order Number: #104455667 Delivery Method: Evri - Evri")
    out = {s["merchant"]: s for s in build(history + [order], now_ms=T0 + 41 * DAY)}
    labels = [n["label"] for n in out["SportPursuit"]["notches"]]
    assert labels[0] == "Ordered" and labels[-1] == "Delivered"
    assert not any("location" in l.lower() or "courier" in l.lower() for l in labels)
    assert "Location has been updated" in [n["label"] for n in out["Vinted"]["notches"]]


def test_gmail_snippets_are_unescaped():
    """Gmail's API hands snippets back HTML-escaped; the page escapes again, so they'd show as &#39;."""
    from app.build import _events
    ev = _events([{"id": "1", "ts": 1, "subject": "s", "snippet": "Salt &amp; pepper it&#39;s", "sender": "a@b",
                         "facts": {"stage": "ordered"}}], {}, [])
    assert ev[0]["snippet"] == "Salt & pepper it's"


def test_renames_and_hides_follow_any_email_of_the_parcel():
    from app.build import apply_owner
    s = {"id": "a", "title": "Evri", "events": [{"id": "a"}, {"id": "b"}, {"source": "api"}]}
    other = {"id": "c", "title": "Royal Mail", "events": [{"id": "c"}]}
    out = apply_owner([s, other], {"b": "Vinted jumper"}, {"b"})
    assert out[0]["title"] == "Vinted jumper" and out[0]["original_title"] == "Evri" and out[0]["hidden"]
    assert out[1] is other and "original_title" not in s and "hidden" not in s  # untouched parcels pass through; input not mutated


def test_out_for_delivery_shows_the_newest_slot_for_that_day():
    num, evri = "H01HYA0012345678", "Evri <noreply@evri.com>"
    ms = [mail(0.3, "Your parcel is on its way", evri, f"Tracking number {num}"),
          mail(1.3, "Your parcel is out for delivery", evri, f"Tracking number {num}. Your courier is delivering your parcel today. Estimated delivery time 18:00 - 20:00")]
    [s] = build(ms, now_ms=T0 + int(1.4 * DAY))
    assert s["eta"] == "2026-09-02" and s["window"] == {"from": "18:00", "to": "20:00"}
    ms.append(mail(1.5, "Your delivery time has been updated", evri, f"Tracking number {num}. Updated delivery time 10:30 - 11:30 Your courier"))
    [s] = build(ms, now_ms=T0 + int(1.6 * DAY))
    assert s["window"] == {"from": "10:30", "to": "11:30"}
    ms.append(mail(1.7, "Your parcel has been delivered", evri, f"Tracking number {num}"))
    [s] = build(ms, now_ms=T0 + 2 * DAY)
    assert s["eta"] is None and s["window"] is None


def test_estimate_from_postage_service_skips_sundays():
    [s] = build([mail(11, "Your order has been dispatched", SHOP,
                      "Order number: 777. Sent via Royal Mail Tracked 48, tracking number AB123456785GB")],
                now_ms=T0 + 11 * DAY)  # handed over Saturday 12 September
    assert s["eta"] == "2026-09-15" and s["eta_guess"]


def test_estimate_from_past_deliveries_else_order_date():
    past = [m for d, n in ((0, "AB123456785GB"), (7, "AB123456799GB"), (14, "AB123456808GB"))
            for m in rm_journey(d, n, f"900{d:02}")]
    new = mail(21, "Order confirmation #55501", SHOP, "Order number: 55501")
    other = mail(21, "Order confirmation #55601", "Gift Shop <hi@gifts.co.uk>", "Order number: 55601")
    by = {s["orders"][-1]: s for s in build(past + [new, other], now_ms=T0 + 21 * DAY)}
    assert by["55501"]["eta"] == "2026-09-25" and by["55501"]["eta_guess"]  # this shop usually takes 3 days from the order
    assert by["55601"]["eta"] is None  # nothing known about it: stays "Ordered"


def test_owner_can_mark_a_parcel_delivered():
    ms = [mail(0, 'Ordered: "Carpet tape"', AMZ, "Order # 205-1234567-0000001"),
          mail(1, 'Dispatched: "Carpet tape"', AMZ, "Order # 205-1234567-0000001")]
    [s] = build(ms, now_ms=T0 + 5 * DAY, marked={ms[0]["id"]: T0 + 4 * DAY})
    assert s["status"] == "delivered" and s["marked_delivered"] and s["eta"] is None
    assert s["notches"][-1] == {"key": "delivered", "label": "Delivered", "ts": T0 + 4 * DAY, "reached": True, "current": True}
    [s] = build(ms, now_ms=T0 + 5 * DAY)
    assert s["status"] == "active" and not s["marked_delivered"]
